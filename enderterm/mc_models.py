from __future__ import annotations

"""Minecraft model/blockstate resolution (no OpenGL/pyglet imports)."""

import json
import math
from dataclasses import dataclass, field

from enderterm.blockstate import _parse_block_state_id
from enderterm.mc_geometry import FACE_DIRS, TextureFace
from enderterm.mc_source import TextureSource


@dataclass(frozen=True, slots=True)
class ResolvedBlockAppearance:
    face_texture_png_by_dir: dict[TextureFace, str]
    face_tintindex_by_dir: dict[TextureFace, int] = field(default_factory=dict)
    rotate_x_deg: int = 0
    rotate_y_deg: int = 0


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    textures: dict[str, str]
    elements: list[dict] | None


@dataclass(frozen=True, slots=True)
class ResolvedBlockModelPart:
    model_ref: str
    model: ResolvedModel
    rotate_x_deg: int
    rotate_y_deg: int
    uvlock: bool = False


@dataclass(frozen=True, slots=True)
class ResolvedBlockModel:
    parts: tuple[ResolvedBlockModelPart, ...]


class MinecraftResourceResolver:
    def __init__(self, source: TextureSource) -> None:
        self.source = source
        self._json_cache: dict[str, dict] = {}
        self._resolved_model_cache: dict[str, ResolvedModel | None] = {}
        self._block_appearance_cache: dict[str, ResolvedBlockAppearance | None] = {}
        self._block_model_cache: dict[str, ResolvedBlockModel | None] = {}
        self._model_internal_face_cull_cache: dict[str, dict[int, frozenset[TextureFace]]] = {}
        self._diagnostics: list[str] = []

    def diagnostics(self) -> tuple[str, ...]:
        """Return recent resolver diagnostics (oldest -> newest)."""
        return tuple(self._diagnostics)

    def _record_diag(self, *, stage: str, ref: str, reason: str) -> None:
        message = f"{stage}:{ref}:{reason}"
        self._diagnostics.append(message)
        if len(self._diagnostics) > 256:
            del self._diagnostics[:-256]

    def _cache_miss(
        self,
        cache: dict[str, object],
        key: str,
        *,
        stage: str,
        reason: str,
    ) -> None:
        cache[str(key)] = None
        self._record_diag(stage=stage, ref=str(key), reason=reason)

    def internal_face_cull_for_model(self, model_ref: str, model: ResolvedModel) -> dict[int, frozenset[TextureFace]]:
        cached = self._model_internal_face_cull_cache.get(model_ref)
        if cached is not None:
            return cached
        elements = model.elements or []
        computed = _compute_internal_face_cull_for_elements(elements)
        self._model_internal_face_cull_cache[model_ref] = computed
        return computed

    def _read_json(self, jar_rel: str) -> dict | None:
        cached = self._json_cache.get(jar_rel)
        if cached is not None:
            return cached
        if not self.source.has(jar_rel):
            self._record_diag(stage="json", ref=jar_rel, reason="missing")
            self._json_cache[jar_rel] = {}
            return None
        try:
            data = self.source.read(jar_rel)
            obj = json.loads(data.decode("utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            self._record_diag(stage="json", ref=jar_rel, reason="invalid")
            obj = {}
        self._json_cache[jar_rel] = obj
        return obj or None

    @staticmethod
    def _parse_ref(ref: str) -> tuple[str, str]:
        if ":" in ref:
            ns, path = ref.split(":", 1)
            return (ns, path)
        return ("minecraft", ref)

    def _model_ref_to_jar_rel(self, model_ref: str) -> str | None:
        ns, path = self._parse_ref(model_ref)
        if ns != "minecraft":
            return None
        return f"assets/minecraft/models/{path}.json"

    def _texture_ref_to_jar_rel(self, tex_ref: str) -> str | None:
        ns, path = self._parse_ref(tex_ref)
        if ns != "minecraft":
            return None
        return f"assets/minecraft/textures/{path}.png"

    def _resolve_model(self, model_ref: str, *, _stack: set[str] | None = None) -> ResolvedModel | None:
        if model_ref in self._resolved_model_cache:
            return self._resolved_model_cache[model_ref]

        if _stack is None:
            _stack = set()
        if model_ref in _stack:
            self._cache_miss(
                self._resolved_model_cache,
                model_ref,
                stage="model",
                reason="cycle",
            )
            return None
        _stack.add(model_ref)

        jar_rel = self._model_ref_to_jar_rel(model_ref)
        if jar_rel is None:
            self._cache_miss(
                self._resolved_model_cache,
                model_ref,
                stage="model",
                reason="non_minecraft_namespace",
            )
            return None
        model = self._read_json(jar_rel)
        if not model:
            self._cache_miss(
                self._resolved_model_cache,
                model_ref,
                stage="model",
                reason="json_missing_or_invalid",
            )
            return None

        parent_ref = model.get("parent")
        parent: ResolvedModel | None = None
        if isinstance(parent_ref, str) and parent_ref:
            parent = self._resolve_model(parent_ref, _stack=_stack)

        textures: dict[str, str] = {}
        elements: list[dict] | None = None
        if parent is not None:
            textures.update(parent.textures)
            elements = parent.elements

        child_textures = model.get("textures")
        if isinstance(child_textures, dict):
            for k, v in child_textures.items():
                if isinstance(k, str) and isinstance(v, str):
                    textures[k] = v

        child_elements = model.get("elements")
        if isinstance(child_elements, list):
            elements = [e for e in child_elements if isinstance(e, dict)]

        resolved = ResolvedModel(textures=textures, elements=elements)
        self._resolved_model_cache[model_ref] = resolved
        return resolved

    @staticmethod
    def _parse_variant_key(key: str) -> dict[str, str]:
        key = key.strip()
        if not key:
            return {}
        out: dict[str, str] = {}
        for part in key.split(","):
            k, _, v = part.partition("=")
            k = k.strip()
            v = v.strip()
            if k:
                out[k] = v
        return out

    def _select_blockstate_variant(self, blockstate: dict, props: dict[str, str]) -> dict | None:
        variants = blockstate.get("variants")
        if isinstance(variants, dict):
            canonical = ",".join(f"{k}={v}" for k, v in sorted(props.items()))
            direct = variants.get(canonical)
            if direct is None:
                direct = variants.get("")
            if isinstance(direct, (dict, list)):
                return direct  # type: ignore[return-value]

            best_key: str | None = None
            best_score = -1
            for k in variants.keys():
                if not isinstance(k, str):
                    continue
                cond = self._parse_variant_key(k)
                if any(props.get(pk) != pv for pk, pv in cond.items()):
                    continue
                score = len(cond)
                if score > best_score:
                    best_score = score
                    best_key = k
            if best_key is not None:
                chosen = variants.get(best_key)
                if isinstance(chosen, (dict, list)):
                    return chosen  # type: ignore[return-value]

        multipart = blockstate.get("multipart")
        if isinstance(multipart, list) and multipart:
            # Best-effort: pick the first unconditional part, else the first part that
            # doesn't contradict our properties. This avoids "guessing filenames"
            # while keeping coverage decent for cube-ish blocks.
            def when_matches(when_obj: object) -> bool:
                if when_obj is None:
                    return True
                if isinstance(when_obj, dict):
                    for k, v in when_obj.items():
                        if not isinstance(k, str):
                            continue
                        if isinstance(v, str):
                            if props.get(k) != v:
                                return False
                        elif isinstance(v, list):
                            if props.get(k) not in {str(x) for x in v}:
                                return False
                    return True
                return False

            for part in multipart:
                if not isinstance(part, dict):
                    continue
                when_obj = part.get("when")
                if when_obj is None:
                    apply_obj = part.get("apply")
                    if isinstance(apply_obj, (dict, list)):
                        return apply_obj  # type: ignore[return-value]
            for part in multipart:
                if not isinstance(part, dict):
                    continue
                if when_matches(part.get("when")):
                    apply_obj = part.get("apply")
                    if isinstance(apply_obj, (dict, list)):
                        return apply_obj  # type: ignore[return-value]

        return None

    @staticmethod
    def _multipart_when_matches(when_obj: object, props: dict[str, str]) -> bool:
        if when_obj is None:
            return True
        if isinstance(when_obj, list):
            return any(MinecraftResourceResolver._multipart_when_matches(item, props) for item in when_obj)
        if not isinstance(when_obj, dict):
            return False

        or_obj = when_obj.get("OR")
        if isinstance(or_obj, list):
            return any(MinecraftResourceResolver._multipart_when_matches(item, props) for item in or_obj)

        for k, v in when_obj.items():
            if k == "OR":
                continue
            if not isinstance(k, str):
                continue
            cur = props.get(k)
            if cur is None:
                return False
            if isinstance(v, str):
                # Minecraft uses `a|b|c` for OR on a property value.
                if cur not in v.split("|"):
                    return False
            elif isinstance(v, list):
                if cur not in {str(x) for x in v}:
                    return False
            else:
                if cur != str(v):
                    return False
        return True

    def _collect_blockstate_applies(self, blockstate: dict, props: dict[str, str]) -> list[dict]:
        variants = blockstate.get("variants")
        if isinstance(variants, dict):
            canonical = ",".join(f"{k}={v}" for k, v in sorted(props.items()))
            direct = variants.get(canonical)
            if direct is None:
                direct = variants.get("")
            variant_obj = direct if isinstance(direct, (dict, list)) else None
            if variant_obj is None:
                best_key: str | None = None
                best_score = -1
                for k in variants.keys():
                    if not isinstance(k, str):
                        continue
                    cond = self._parse_variant_key(k)
                    if any(props.get(pk) != pv for pk, pv in cond.items()):
                        continue
                    score = len(cond)
                    if score > best_score:
                        best_score = score
                        best_key = k
                if best_key is not None:
                    chosen = variants.get(best_key)
                    if isinstance(chosen, (dict, list)):
                        variant_obj = chosen  # type: ignore[assignment]
            variant = self._pick_weighted(variant_obj) if variant_obj is not None else None
            return [variant] if isinstance(variant, dict) else []

        multipart = blockstate.get("multipart")
        if not isinstance(multipart, list) or not multipart:
            return []

        out: list[dict] = []
        for part in multipart:
            if not isinstance(part, dict):
                continue
            if not self._multipart_when_matches(part.get("when"), props):
                continue
            apply_obj = part.get("apply")
            if not isinstance(apply_obj, (dict, list)):
                continue
            apply = self._pick_weighted(apply_obj)
            if isinstance(apply, dict):
                out.append(apply)
        return out

    def _pick_weighted(self, value: dict | list) -> dict | None:
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            best: dict | None = None
            best_weight = -1
            for item in value:
                if not isinstance(item, dict):
                    continue
                w = item.get("weight", 1)
                if not isinstance(w, int):
                    w = 1
                if w > best_weight:
                    best_weight = w
                    best = item
            return best
        return None

    def _resolve_texture_ref(self, texture_ref: str, textures: dict[str, str]) -> str | None:
        cur = texture_ref
        for _ in range(16):
            if not isinstance(cur, str):
                return None
            if not cur.startswith("#"):
                return cur
            key = cur[1:]
            nxt = textures.get(key)
            if not isinstance(nxt, str) or not nxt:
                return None
            cur = nxt
        return None

    def resolve_block_appearance(self, block_state_id: str) -> ResolvedBlockAppearance | None:
        if block_state_id in self._block_appearance_cache:
            return self._block_appearance_cache[block_state_id]

        base, props = _parse_block_state_id(block_state_id)
        ns, name = self._parse_ref(base)
        if ns != "minecraft":
            self._cache_miss(
                self._block_appearance_cache,
                block_state_id,
                stage="appearance",
                reason="non_minecraft_namespace",
            )
            return None

        blockstate_jar = f"assets/minecraft/blockstates/{name}.json"
        blockstate = self._read_json(blockstate_jar)
        if not blockstate:
            self._cache_miss(
                self._block_appearance_cache,
                block_state_id,
                stage="appearance",
                reason="blockstate_missing_or_invalid",
            )
            return None

        variant_obj = self._select_blockstate_variant(blockstate, props)
        variant = self._pick_weighted(variant_obj) if variant_obj is not None else None
        if not variant:
            self._cache_miss(
                self._block_appearance_cache,
                block_state_id,
                stage="appearance",
                reason="variant_missing",
            )
            return None

        model_ref = variant.get("model")
        if not isinstance(model_ref, str) or not model_ref:
            self._cache_miss(
                self._block_appearance_cache,
                block_state_id,
                stage="appearance",
                reason="variant_model_ref_missing",
            )
            return None

        rotate_x = variant.get("x", 0)
        rotate_y = variant.get("y", 0)
        rotate_x = int(rotate_x) if isinstance(rotate_x, (int, float)) else 0
        rotate_y = int(rotate_y) if isinstance(rotate_y, (int, float)) else 0

        model = self._resolve_model(model_ref)
        if model is None or model.elements is None:
            self._cache_miss(
                self._block_appearance_cache,
                block_state_id,
                stage="appearance",
                reason="model_missing_or_no_elements",
            )
            return None

        # Prefer a full-cube element, otherwise fall back to the first element (we
        # render cubes anyway; this is about picking textures).
        chosen: dict | None = None
        for element in model.elements:
            frm = element.get("from")
            to = element.get("to")
            if frm == [0, 0, 0] and to == [16, 16, 16] and "rotation" not in element:
                chosen = element
                break
        if chosen is None:
            chosen = model.elements[0] if model.elements else None
        if not chosen:
            self._cache_miss(
                self._block_appearance_cache,
                block_state_id,
                stage="appearance",
                reason="model_element_missing",
            )
            return None

        faces_obj = chosen.get("faces")
        if not isinstance(faces_obj, dict):
            self._cache_miss(
                self._block_appearance_cache,
                block_state_id,
                stage="appearance",
                reason="faces_missing",
            )
            return None

        face_texture_png_by_dir: dict[TextureFace, str] = {}
        face_tintindex_by_dir: dict[TextureFace, int] = {}
        particle_ref: str | None = None
        if "particle" in model.textures:
            particle_ref = self._resolve_texture_ref(model.textures["particle"], model.textures)

        for face in FACE_DIRS:
            face_def = faces_obj.get(face)
            tex_ref: str | None = None
            if isinstance(face_def, dict):
                raw = face_def.get("texture")
                if isinstance(raw, str) and raw:
                    tex_ref = self._resolve_texture_ref(raw, model.textures)
                tint_obj = face_def.get("tintindex")
                if isinstance(tint_obj, (int, float)):
                    face_tintindex_by_dir[face] = int(tint_obj)
            if tex_ref is None and particle_ref is not None:
                tex_ref = particle_ref
            if tex_ref is None:
                continue
            tex_jar = self._texture_ref_to_jar_rel(tex_ref)
            if tex_jar is None or not self.source.has(tex_jar):
                continue
            face_texture_png_by_dir[face] = tex_jar

        if not face_texture_png_by_dir:
            self._cache_miss(
                self._block_appearance_cache,
                block_state_id,
                stage="appearance",
                reason="no_resolved_textures",
            )
            return None

        resolved = ResolvedBlockAppearance(
            face_texture_png_by_dir=face_texture_png_by_dir,
            face_tintindex_by_dir=face_tintindex_by_dir,
            rotate_x_deg=rotate_x,
            rotate_y_deg=rotate_y,
        )
        self._block_appearance_cache[block_state_id] = resolved
        return resolved

    def resolve_block_model(self, block_state_id: str) -> ResolvedBlockModel | None:
        if block_state_id in self._block_model_cache:
            return self._block_model_cache[block_state_id]

        base, props = _parse_block_state_id(block_state_id)
        ns, name = self._parse_ref(base)
        if ns != "minecraft":
            self._cache_miss(
                self._block_model_cache,
                block_state_id,
                stage="block_model",
                reason="non_minecraft_namespace",
            )
            return None

        blockstate_jar = f"assets/minecraft/blockstates/{name}.json"
        blockstate = self._read_json(blockstate_jar)
        if not blockstate:
            self._cache_miss(
                self._block_model_cache,
                block_state_id,
                stage="block_model",
                reason="blockstate_missing_or_invalid",
            )
            return None

        parts: list[ResolvedBlockModelPart] = []
        for apply in self._collect_blockstate_applies(blockstate, props):
            model_ref = apply.get("model")
            if not isinstance(model_ref, str) or not model_ref:
                continue
            rotate_x = apply.get("x", 0)
            rotate_y = apply.get("y", 0)
            rotate_x = int(rotate_x) if isinstance(rotate_x, (int, float)) else 0
            rotate_y = int(rotate_y) if isinstance(rotate_y, (int, float)) else 0
            uvlock = apply.get("uvlock", False)
            uvlock = bool(uvlock) if isinstance(uvlock, (bool, int)) else False

            model = self._resolve_model(model_ref)
            if model is None or model.elements is None:
                continue
            parts.append(
                ResolvedBlockModelPart(
                    model_ref=model_ref,
                    model=model,
                    rotate_x_deg=rotate_x,
                    rotate_y_deg=rotate_y,
                    uvlock=uvlock,
                )
            )

        if not parts:
            self._cache_miss(
                self._block_model_cache,
                block_state_id,
                stage="block_model",
                reason="no_resolved_parts",
            )
            return None

        resolved = ResolvedBlockModel(parts=tuple(parts))
        self._block_model_cache[block_state_id] = resolved
        return resolved


def _model_is_full_cube(model: ResolvedModel) -> bool:
    elems = model.elements
    if not elems or len(elems) != 1:
        return False
    el = elems[0]
    if not isinstance(el, dict):
        return False
    if "rotation" in el:
        return False
    if el.get("from") != [0, 0, 0] or el.get("to") != [16, 16, 16]:
        return False
    faces = el.get("faces")
    if not isinstance(faces, dict):
        return False
    return all(face in faces for face in FACE_DIRS)


def _block_model_is_full_cube(block_model: ResolvedBlockModel) -> bool:
    parts = block_model.parts
    if len(parts) != 1:
        return False
    return _model_is_full_cube(parts[0].model)


def _block_model_bottom_coverage_frac(block_model: ResolvedBlockModel) -> float:
    # Estimate how much of the 16x16 bottom face is covered by axis-aligned model
    # elements that touch y=0. Used to treat slabs/stairs as "supporting" while
    # ignoring skinny blocks like fences/torches for environment anchoring.
    covered = [False] * 256
    any_elem = False
    for part in block_model.parts:
        if int(part.rotate_x_deg) % 360 != 0:
            continue
        model = part.model
        if not _model_is_axis_aligned(model):
            continue
        elems = model.elements or []
        for el in elems:
            if not isinstance(el, dict):
                continue
            if "rotation" in el:
                continue
            frm = el.get("from")
            to = el.get("to")
            if not (isinstance(frm, list) and isinstance(to, list) and len(frm) == 3 and len(to) == 3):
                continue
            try:
                fx, fy, fz = (float(frm[0]), float(frm[1]), float(frm[2]))
                txe, tye, tze = (float(to[0]), float(to[1]), float(to[2]))
            except (TypeError, ValueError):
                continue
            ymin = min(float(fy), float(tye))
            if abs(ymin) > 1e-6:
                continue
            xmin = min(float(fx), float(txe))
            xmax = max(float(fx), float(txe))
            zmin = min(float(fz), float(tze))
            zmax = max(float(fz), float(tze))
            if xmax <= xmin + 1e-6 or zmax <= zmin + 1e-6:
                continue
            any_elem = True
            x0 = max(0, min(16, int(math.floor(xmin))))
            x1 = max(0, min(16, int(math.ceil(xmax))))
            z0 = max(0, min(16, int(math.floor(zmin))))
            z1 = max(0, min(16, int(math.ceil(zmax))))
            for xi in range(x0, x1):
                for zi in range(z0, z1):
                    covered[int(zi) * 16 + int(xi)] = True
    if not any_elem:
        return 0.0
    return float(sum(1 for v in covered if v)) / 256.0


def _model_is_axis_aligned(model: ResolvedModel) -> bool:
    elems = model.elements
    if not elems:
        return False
    for el in elems:
        if not isinstance(el, dict):
            return False
        if "rotation" in el:
            return False
    return True


def _compute_internal_face_cull_for_elements(elements: list[dict] | None) -> dict[int, frozenset[TextureFace]]:
    # Cull element faces that are fully covered by an adjacent element face. This
    # removes interior faces for common multi-element blocks like stairs.
    #
    # Works in model coords (0..16) and only for axis-aligned elements (no per-element
    # rotation). It's conservative: if we can't prove full coverage, we keep the face.
    if not elements:
        return {}

    eps = 1e-6
    boxes: dict[int, tuple[float, float, float, float, float, float]] = {}
    for idx, el in enumerate(elements):
        if not isinstance(el, dict):
            continue
        if "rotation" in el:
            continue
        frm = el.get("from")
        to = el.get("to")
        if not (isinstance(frm, list) and isinstance(to, list) and len(frm) == 3 and len(to) == 3):
            continue
        try:
            fx, fy, fz = (float(frm[0]), float(frm[1]), float(frm[2]))
            txe, tye, tze = (float(to[0]), float(to[1]), float(to[2]))
        except (TypeError, ValueError):
            continue
        xmin = min(fx, txe)
        xmax = max(fx, txe)
        ymin = min(fy, tye)
        ymax = max(fy, tye)
        zmin = min(fz, tze)
        zmax = max(fz, tze)
        boxes[idx] = (xmin, xmax, ymin, ymax, zmin, zmax)

    if len(boxes) < 2:
        return {}

    def _uniq_sorted(vals: list[float]) -> list[float]:
        if not vals:
            return []
        vals = sorted(vals)
        out = [vals[0]]
        for v in vals[1:]:
            if abs(v - out[-1]) > eps:
                out.append(v)
        return out

    def _covered_by_union(
        *,
        rect_a: tuple[float, float],
        rect_b: tuple[float, float],
        adj_boxes: list[tuple[float, float, float, float]],
    ) -> bool:
        a0, a1 = rect_a
        b0, b1 = rect_b
        if a1 <= a0 + eps or b1 <= b0 + eps:
            return True

        a_breaks = [a0, a1]
        b_breaks = [b0, b1]
        adj_clamped: list[tuple[float, float, float, float]] = []
        for aa0, aa1, bb0, bb1 in adj_boxes:
            ca0 = max(a0, aa0)
            ca1 = min(a1, aa1)
            cb0 = max(b0, bb0)
            cb1 = min(b1, bb1)
            if ca1 <= ca0 + eps or cb1 <= cb0 + eps:
                continue
            adj_clamped.append((ca0, ca1, cb0, cb1))
            a_breaks.extend([ca0, ca1])
            b_breaks.extend([cb0, cb1])

        if not adj_clamped:
            return False

        a_breaks_u = _uniq_sorted(a_breaks)
        b_breaks_u = _uniq_sorted(b_breaks)

        for ia in range(len(a_breaks_u) - 1):
            a_lo = a_breaks_u[ia]
            a_hi = a_breaks_u[ia + 1]
            if a_hi <= a_lo + eps:
                continue
            a_mid = 0.5 * (a_lo + a_hi)
            for ib in range(len(b_breaks_u) - 1):
                b_lo = b_breaks_u[ib]
                b_hi = b_breaks_u[ib + 1]
                if b_hi <= b_lo + eps:
                    continue
                b_mid = 0.5 * (b_lo + b_hi)

                covered = False
                for ca0, ca1, cb0, cb1 in adj_clamped:
                    if (ca0 - eps) <= a_mid <= (ca1 + eps) and (cb0 - eps) <= b_mid <= (cb1 + eps):
                        covered = True
                        break
                if not covered:
                    return False
        return True

    def _face_internal(idx: int, face: TextureFace) -> bool:
        xmin, xmax, ymin, ymax, zmin, zmax = boxes[idx]

        if face == "east":
            plane = xmax
            rect_a = (ymin, ymax)
            rect_b = (zmin, zmax)
            adj = [
                (ob[2], ob[3], ob[4], ob[5])
                for j, ob in boxes.items()
                if j != idx and abs(ob[0] - plane) <= eps and not (ob[3] <= ymin + eps or ob[2] >= ymax - eps or ob[5] <= zmin + eps or ob[4] >= zmax - eps)
            ]
            return _covered_by_union(rect_a=rect_a, rect_b=rect_b, adj_boxes=adj)

        if face == "west":
            plane = xmin
            rect_a = (ymin, ymax)
            rect_b = (zmin, zmax)
            adj = [
                (ob[2], ob[3], ob[4], ob[5])
                for j, ob in boxes.items()
                if j != idx and abs(ob[1] - plane) <= eps and not (ob[3] <= ymin + eps or ob[2] >= ymax - eps or ob[5] <= zmin + eps or ob[4] >= zmax - eps)
            ]
            return _covered_by_union(rect_a=rect_a, rect_b=rect_b, adj_boxes=adj)

        if face == "up":
            plane = ymax
            rect_a = (xmin, xmax)
            rect_b = (zmin, zmax)
            adj = [
                (ob[0], ob[1], ob[4], ob[5])
                for j, ob in boxes.items()
                if j != idx and abs(ob[2] - plane) <= eps and not (ob[1] <= xmin + eps or ob[0] >= xmax - eps or ob[5] <= zmin + eps or ob[4] >= zmax - eps)
            ]
            return _covered_by_union(rect_a=rect_a, rect_b=rect_b, adj_boxes=adj)

        if face == "down":
            plane = ymin
            rect_a = (xmin, xmax)
            rect_b = (zmin, zmax)
            adj = [
                (ob[0], ob[1], ob[4], ob[5])
                for j, ob in boxes.items()
                if j != idx and abs(ob[3] - plane) <= eps and not (ob[1] <= xmin + eps or ob[0] >= xmax - eps or ob[5] <= zmin + eps or ob[4] >= zmax - eps)
            ]
            return _covered_by_union(rect_a=rect_a, rect_b=rect_b, adj_boxes=adj)

        if face == "south":
            plane = zmax
            rect_a = (xmin, xmax)
            rect_b = (ymin, ymax)
            adj = [
                (ob[0], ob[1], ob[2], ob[3])
                for j, ob in boxes.items()
                if j != idx and abs(ob[4] - plane) <= eps and not (ob[1] <= xmin + eps or ob[0] >= xmax - eps or ob[3] <= ymin + eps or ob[2] >= ymax - eps)
            ]
            return _covered_by_union(rect_a=rect_a, rect_b=rect_b, adj_boxes=adj)

        # north
        plane = zmin
        rect_a = (xmin, xmax)
        rect_b = (ymin, ymax)
        adj = [
            (ob[0], ob[1], ob[2], ob[3])
            for j, ob in boxes.items()
            if j != idx and abs(ob[5] - plane) <= eps and not (ob[1] <= xmin + eps or ob[0] >= xmax - eps or ob[3] <= ymin + eps or ob[2] >= ymax - eps)
        ]
        return _covered_by_union(rect_a=rect_a, rect_b=rect_b, adj_boxes=adj)

    out: dict[int, frozenset[TextureFace]] = {}
    for idx in boxes:
        culled: list[TextureFace] = []
        for face in FACE_DIRS:
            if _face_internal(idx, face):
                culled.append(face)
        if culled:
            out[idx] = frozenset(culled)
    return out
