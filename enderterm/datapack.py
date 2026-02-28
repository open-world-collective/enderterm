from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable
import zipfile

import nbtlib

from enderterm.blockstate import _block_id_base, _block_state_id_from_json_state
from enderterm.jigsaw import (
    CappedProcessor,
    PoolDefinition,
    PoolElement,
    ProcessorPipeline,
    ProcessorSpec,
    RuleProcessor,
    RuleSpec,
    StructureTemplate,
    parse_structure_template,
)
from enderterm.structure_nbt import load_nbt_bytes

_PACKSTACK_VENDOR_SOURCE_CACHE_MAX = 4096


def _canonical_rel_from_zip_entry(name: str) -> str | None:
    if name.startswith("data/"):
        return name
    idx = name.find("/data/")
    if idx >= 0:
        return name[idx + 1 :]
    if "/" not in name:
        # Root-level datapack files (e.g. pack.mcmeta, pack.png, and tool-side
        # config like environments.json).
        return name
    return None


def _worldgen_kind_from_canonical_json(canonical_rel: str) -> str | None:
    parts = canonical_rel.split("/", 4)
    if len(parts) != 5 or parts[0] != "data" or parts[2] != "worldgen":
        return None
    if not canonical_rel.endswith(".json"):
        return None
    return parts[3]


def _decode_json_payload(data: bytes) -> object:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return obj


class DatapackSource:
    def __init__(self, datapack_path: Path, zip_file: zipfile.ZipFile | None) -> None:
        self.datapack_path = datapack_path
        self.zip_file = zip_file
        self._zip_name_by_canonical: dict[str, str] = {}
        self._worldgen_json_by_kind: dict[str, list[str]] = {}
        self._json_cache: dict[str, object] = {}
        self._nbt_cache: dict[str, nbtlib.Compound | None] = {}

        self._index_zip_entries()

    def _index_zip_entries(self) -> None:
        if self.zip_file is None:
            return
        for name in self.zip_file.namelist():
            if name.endswith("/"):
                continue
            canonical_rel = _canonical_rel_from_zip_entry(name)
            if canonical_rel is None:
                continue
            self._zip_name_by_canonical.setdefault(canonical_rel, name)
            kind_folder = _worldgen_kind_from_canonical_json(canonical_rel)
            if kind_folder is not None:
                self._worldgen_json_by_kind.setdefault(kind_folder, []).append(canonical_rel)

    def _path_for(self, canonical_rel: str) -> Path:
        return self.datapack_path / canonical_rel

    def _resolve_zip_name(self, canonical_rel: str) -> str | None:
        return self._zip_name_by_canonical.get(canonical_rel)

    def has(self, canonical_rel: str) -> bool:
        if self.zip_file is not None:
            return self._resolve_zip_name(canonical_rel) is not None
        return self._path_for(canonical_rel).is_file()

    def read(self, canonical_rel: str) -> bytes:
        if self.zip_file is not None:
            name = self._resolve_zip_name(canonical_rel)
            if not name:
                raise FileNotFoundError(canonical_rel)
            return self.zip_file.read(name)
        return self._path_for(canonical_rel).read_bytes()

    def read_json(self, canonical_rel: str) -> dict | None:
        cached = self._json_cache.get(canonical_rel)
        if cached is not None:
            return cached or None
        try:
            payload = self.read(canonical_rel)
        except FileNotFoundError:
            self._json_cache[canonical_rel] = {}
            return None
        obj = _decode_json_payload(payload)
        self._json_cache[canonical_rel] = obj
        return obj or None

    def read_nbt(self, canonical_rel: str) -> nbtlib.Compound | None:
        if canonical_rel in self._nbt_cache:
            return self._nbt_cache[canonical_rel]
        if not self.has(canonical_rel):
            self._nbt_cache[canonical_rel] = None
            return None
        try:
            root = load_nbt_bytes(self.read(canonical_rel))
        except Exception:
            # Zip/jar datapacks are immutable: don't keep retrying known-bad
            # payloads.
            if self._should_cache_nbt_failure():
                self._nbt_cache[canonical_rel] = None
            return None
        self._nbt_cache[canonical_rel] = root
        return root

    def _should_cache_nbt_failure(self) -> bool:
        return self.zip_file is not None

    def invalidate(self, canonical_rel: str | None = None) -> None:
        if canonical_rel is None:
            self._json_cache.clear()
            self._nbt_cache.clear()
            return
        self._json_cache.pop(canonical_rel, None)
        self._nbt_cache.pop(canonical_rel, None)

    def write(self, canonical_rel: str, data: bytes) -> None:
        if self.zip_file is not None:
            raise RuntimeError("cannot write into a zip/jar datapack")
        path = self.datapack_path / canonical_rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.invalidate(canonical_rel)


class PackStackSource:
    """Read-only overlay view of multiple datapack sources (topmost wins)."""

    def __init__(self, sources: list[DatapackSource]) -> None:
        self.sources = list(sources)
        self._vendor_source_cache: dict[str, DatapackSource | None] = {}

    def has(self, canonical_rel: str) -> bool:
        return self.resolve_source(canonical_rel) is not None

    def _cache_vendor_source(self, canonical_rel: str, source: DatapackSource | None) -> DatapackSource | None:
        if len(self._vendor_source_cache) >= _PACKSTACK_VENDOR_SOURCE_CACHE_MAX:
            self._vendor_source_cache.clear()
        self._vendor_source_cache[canonical_rel] = source
        return source

    def resolve_source(self, canonical_rel: str) -> DatapackSource | None:
        if not self.sources:
            return None
        # Always check the topmost source (work pack) live so that writes can
        # override any cached vendor mapping.
        top = self.sources[0]
        if top.has(canonical_rel):
            return top

        if canonical_rel in self._vendor_source_cache:
            return self._vendor_source_cache[canonical_rel]

        for src in self.sources[1:]:
            if src.has(canonical_rel):
                return self._cache_vendor_source(canonical_rel, src)
        return self._cache_vendor_source(canonical_rel, None)

    def read(self, canonical_rel: str) -> bytes:
        src = self.resolve_source(canonical_rel)
        if src is None:
            raise FileNotFoundError(canonical_rel)
        return src.read(canonical_rel)

    def read_json(self, canonical_rel: str) -> dict | None:
        src = self.resolve_source(canonical_rel)
        if src is None:
            return None
        return src.read_json(canonical_rel)

    def read_nbt(self, canonical_rel: str) -> nbtlib.Compound | None:
        src = self.resolve_source(canonical_rel)
        if src is None:
            return None
        return src.read_nbt(canonical_rel)


def ensure_datapack_skeleton(datapack_dir: Path, *, description: str = "EnderTerm work pack") -> None:
    """Create a minimal datapack directory layout if it doesn't exist yet."""

    datapack_dir.mkdir(parents=True, exist_ok=True)
    data_dir = datapack_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    mcmeta = datapack_dir / "pack.mcmeta"
    if mcmeta.is_file():
        return
    payload = {
        "pack": {
            "pack_format": 15,  # Minecraft 1.20.1
            "description": description,
        }
    }
    mcmeta.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class PackStack:
    """Vendor pack(s) (read-only) + work pack overlay (writable)."""

    def __init__(self, *, work_dir: Path, vendors: list[DatapackSource]) -> None:
        self.work_dir = work_dir
        self.work = DatapackSource(work_dir, None)
        self.vendors = list(vendors)
        self.source = PackStackSource([self.work, *self.vendors])

    def ensure_work_pack(self) -> None:
        ensure_datapack_skeleton(self.work_dir)

    def resolve_owner(self, canonical_rel: str) -> DatapackSource | None:
        return self.source.resolve_source(canonical_rel)

    def fork_into_work(self, canonical_rel: str) -> bool:
        """Copy exact bytes from the current owner into the work pack (if needed)."""

        self.ensure_work_pack()
        if self.work.has(canonical_rel):
            return False
        for vendor in self.vendors:
            if vendor.has(canonical_rel):
                self.work.write(canonical_rel, vendor.read(canonical_rel))
                return True
        raise FileNotFoundError(canonical_rel)


def _parse_resource_location(ref: str) -> tuple[str, str]:
    if ":" in ref:
        ns, path = ref.split(":", 1)
        return (ns, path)
    return ("minecraft", ref)


def _canonical_worldgen_json(
    resource_id: str, *, kind_folder: str, allow_minecraft_empty: bool = False
) -> str | None:
    ns, path = _parse_resource_location(resource_id)
    if allow_minecraft_empty and ns == "minecraft" and path == "empty":
        return None
    return f"data/{ns}/worldgen/{kind_folder}/{path}.json"


def canonical_template_pool_json(pool_id: str) -> str | None:
    return _canonical_worldgen_json(pool_id, kind_folder="template_pool", allow_minecraft_empty=True)


def canonical_processor_list_json(proc_id: str) -> str | None:
    return _canonical_worldgen_json(proc_id, kind_folder="processor_list", allow_minecraft_empty=True)


def canonical_worldgen_structure_json(structure_id: str) -> str:
    rel = _canonical_worldgen_json(structure_id, kind_folder="structure")
    assert rel is not None
    return rel


def canonical_structure_template_nbt(location_id: str) -> list[str]:
    ns, path = _parse_resource_location(location_id)
    return [
        f"data/{ns}/structures/{path}.nbt",
        f"data/{ns}/structure/{path}.nbt",
    ]


def _normalize_nonempty_str(value: object, *, default: str) -> str:
    if isinstance(value, str) and value:
        return value
    return default


def _resource_id_from_worldgen_canonical(canonical_rel: str, *, kind_folder: str, suffix: str) -> str | None:
    # Expected: data/<ns>/worldgen/<kind_folder>/<path>.json
    try:
        canon = str(canonical_rel)
    except Exception:
        return None
    parts = canon.split("/", 4)
    if len(parts) < 5 or parts[0] != "data" or parts[2] != "worldgen" or parts[3] != kind_folder:
        return None
    ns = parts[1]
    leaf = parts[4]
    if not leaf.endswith(suffix):
        return None
    path = leaf[: -len(suffix)]
    if not ns or not path:
        return None
    return f"{ns}:{path}"


def _resource_id_from_structure_canonical(canonical_rel: str) -> str | None:
    # Expected: data/<ns>/structures/<path>.nbt or data/<ns>/structure/<path>.nbt
    try:
        canon = str(canonical_rel)
    except Exception:
        return None
    parts = canon.split("/", 3)
    if len(parts) < 4 or parts[0] != "data":
        return None
    ns = parts[1]
    folder = parts[2]
    if folder not in {"structures", "structure"}:
        return None
    leaf = parts[3]
    if not leaf.endswith(".nbt"):
        return None
    path = leaf[: -len(".nbt")]
    if not ns or not path:
        return None
    return f"{ns}:{path}"


def _block_id_bases_from_blocks_json(values: object) -> set[str]:
    out: set[str] = set()
    if not isinstance(values, list):
        return out
    for value in values:
        if isinstance(value, str) and value:
            out.add(_block_id_base(value))
        elif isinstance(value, dict) and "Name" in value:
            name = str(value.get("Name") or "")
            if name:
                out.add(_block_id_base(name))
    return out


def iter_canonical_paths_in_source(src: DatapackSource) -> Iterable[str]:
    if src.zip_file is not None:
        yield from src._zip_name_by_canonical.keys()
        return
    root = src.datapack_path
    data_dir = root / "data"
    if not data_dir.is_dir():
        return
    for ns_dir in sorted((p for p in data_dir.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        worldgen = ns_dir / "worldgen"
        if worldgen.is_dir():
            for p in worldgen.rglob("*.json"):
                if not p.is_file():
                    continue
                try:
                    rel = p.relative_to(root).as_posix()
                except Exception:
                    continue
                yield rel

        for folder in ("structures", "structure"):
            base = ns_dir / folder
            if not base.is_dir():
                continue
            for p in base.rglob("*.nbt"):
                if not p.is_file():
                    continue
                try:
                    rel = p.relative_to(root).as_posix()
                except Exception:
                    continue
                yield rel


def _iter_worldgen_kind_paths_in_source(src: DatapackSource, *, kind_folder: str, suffix: str) -> Iterable[str]:
    if src.zip_file is not None:
        if suffix == ".json":
            yield from src._worldgen_json_by_kind.get(kind_folder, ())
        else:
            needle = f"/worldgen/{kind_folder}/"
            for canon in src._zip_name_by_canonical.keys():
                if canon.endswith(suffix) and needle in canon:
                    yield canon
        return

    root = src.datapack_path
    data_dir = root / "data"
    if not data_dir.is_dir():
        return
    for ns_dir in (p for p in data_dir.iterdir() if p.is_dir()):
        base = ns_dir / "worldgen" / kind_folder
        if not base.is_dir():
            continue
        for p in base.rglob(f"*{suffix}"):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(root).as_posix()
            except Exception:
                continue
            yield rel


def _iter_structure_template_paths_in_source(src: DatapackSource) -> Iterable[str]:
    if src.zip_file is not None:
        for canon in src._zip_name_by_canonical.keys():
            if canon.endswith(".nbt") and ("/structures/" in canon or "/structure/" in canon):
                yield canon
        return

    root = src.datapack_path
    data_dir = root / "data"
    if not data_dir.is_dir():
        return
    for ns_dir in (p for p in data_dir.iterdir() if p.is_dir()):
        for folder in ("structures", "structure"):
            base = ns_dir / folder
            if not base.is_dir():
                continue
            for p in base.rglob("*.nbt"):
                if not p.is_file():
                    continue
                try:
                    rel = p.relative_to(root).as_posix()
                except Exception:
                    continue
                yield rel


def _iter_stack_sources(stack: PackStack) -> Iterable[DatapackSource]:
    yield stack.work
    yield from stack.vendors


def _collect_resource_ids(
    stack: PackStack,
    *,
    canonical_iter: Callable[[DatapackSource], Iterable[str]],
    canonical_to_id: Callable[[str], str | None],
) -> set[str]:
    out: set[str] = set()
    for src in _iter_stack_sources(stack):
        for canon in canonical_iter(src):
            rid = canonical_to_id(canon)
            if rid is not None:
                out.add(rid)
    return out


def list_template_pools(stack: PackStack) -> list[tuple[str, str]]:
    """Return [(pool_id, owner_label)] for all pools in the stack (topmost wins)."""

    pools: dict[str, str] = {}
    for src in _iter_stack_sources(stack):
        owner_label = "work" if src is stack.work else "vendor"
        for canon in _iter_worldgen_kind_paths_in_source(src, kind_folder="template_pool", suffix=".json"):
            pool_id = _resource_id_from_worldgen_canonical(canon, kind_folder="template_pool", suffix=".json")
            if pool_id is None:
                continue
            if pool_id in pools:
                continue
            pools[pool_id] = owner_label
    return sorted(pools.items(), key=lambda kv: kv[0].lower())


def list_processor_lists(stack: PackStack) -> list[str]:
    out = _collect_resource_ids(
        stack,
        canonical_iter=lambda src: _iter_worldgen_kind_paths_in_source(src, kind_folder="processor_list", suffix=".json"),
        canonical_to_id=lambda canon: _resource_id_from_worldgen_canonical(
            canon,
            kind_folder="processor_list",
            suffix=".json",
        ),
    )
    # Always include minecraft:empty for convenience.
    out.add("minecraft:empty")
    return sorted(out, key=lambda s: s.lower())


def list_structure_templates(stack: PackStack) -> list[str]:
    out = _collect_resource_ids(
        stack,
        canonical_iter=_iter_structure_template_paths_in_source,
        canonical_to_id=_resource_id_from_structure_canonical,
    )
    return sorted(out, key=lambda s: s.lower())


def list_worldgen_structures(stack: PackStack) -> list[str]:
    out = _collect_resource_ids(
        stack,
        canonical_iter=lambda src: _iter_worldgen_kind_paths_in_source(src, kind_folder="structure", suffix=".json"),
        canonical_to_id=lambda canon: _resource_id_from_worldgen_canonical(
            canon,
            kind_folder="structure",
            suffix=".json",
        ),
    )
    return sorted(out, key=lambda s: s.lower())


def list_worldgen_jigsaw_structures(stack: PackStack) -> list[str]:
    out: list[str] = []
    for rid in list_worldgen_structures(stack):
        obj = stack.source.read_json(canonical_worldgen_structure_json(rid)) or {}
        if obj.get("type") != "minecraft:jigsaw":
            continue
        start_pool = obj.get("start_pool")
        if not isinstance(start_pool, str) or not start_pool:
            continue
        out.append(rid)
    return out


class JigsawDatapackIndex:
    def __init__(self, source: DatapackSource) -> None:
        self.source = source
        self._pool_cache: dict[str, PoolDefinition] = {}
        self._processor_cache: dict[str, ProcessorPipeline] = {}
        self._template_cache: dict[str, StructureTemplate | None] = {}

    def _pool_json_path(self, pool_id: str) -> str | None:
        return canonical_template_pool_json(pool_id)

    def _structure_nbt_paths(self, location_id: str) -> list[str]:
        return canonical_structure_template_nbt(location_id)

    def _processor_json_path(self, processor_id: str) -> str | None:
        return canonical_processor_list_json(processor_id)

    def load_processor_list(self, processor_id: str) -> ProcessorPipeline:
        cached = self._processor_cache.get(processor_id)
        if cached is not None:
            return cached

        rel = self._processor_json_path(processor_id)
        if rel is None:
            empty = ProcessorPipeline(ignore_base=frozenset(), processors=tuple(), unhandled_processors=tuple())
            self._processor_cache[processor_id] = empty
            return empty

        obj = self.source.read_json(rel) or {}
        ignore_base: set[str] = set()
        specs: list[ProcessorSpec] = []
        unhandled_processors: set[str] = set()

        def parse_rule_processor(rule_obj: object) -> RuleProcessor | None:
            if not isinstance(rule_obj, dict):
                return None
            rules_obj = rule_obj.get("rules")
            if not isinstance(rules_obj, list):
                return None
            rules: list[RuleSpec] = []
            unhandled_predicates: set[str] = set()

            for r in rules_obj:
                if not isinstance(r, dict):
                    continue
                ip = r.get("input_predicate") or {}
                lp = r.get("location_predicate") or {}
                os_obj = r.get("output_state")

                if not isinstance(ip, dict) or not isinstance(lp, dict):
                    continue
                ip_type = ip.get("predicate_type")
                if not isinstance(ip_type, str) or not ip_type:
                    continue

                lp_type = lp.get("predicate_type")
                if not isinstance(lp_type, str) or not lp_type:
                    lp_type = "minecraft:always_true"
                    lp = {"predicate_type": lp_type}

                out_state_id = _block_state_id_from_json_state(os_obj)
                if out_state_id is None:
                    continue

                def clamp_probability(value: object) -> float:
                    try:
                        p = float(value)
                    except (TypeError, ValueError):
                        p = 1.0
                    return max(0.0, min(1.0, p))

                def parse_blocks_base(test_obj: dict) -> set[str]:
                    blocks: set[str] = set()
                    block_obj = test_obj.get("block")
                    if isinstance(block_obj, str) and block_obj:
                        blocks.add(_block_id_base(block_obj))
                    blocks.update(_block_id_bases_from_blocks_json(test_obj.get("blocks")))
                    return blocks

                def parse_block_states(test_obj: dict) -> set[str]:
                    states: set[str] = set()
                    bs_obj = test_obj.get("block_state")
                    state_id = _block_state_id_from_json_state(bs_obj)
                    if state_id is not None:
                        states.add(state_id)
                    bs_list = test_obj.get("block_states")
                    if isinstance(bs_list, list):
                        for item in bs_list:
                            sid = _block_state_id_from_json_state(item)
                            if sid is not None:
                                states.add(sid)
                    return states

                def parse_rule_test(test_obj: dict) -> tuple[str, set[str], set[str], float | None] | None:
                    ptype = test_obj.get("predicate_type")
                    if not isinstance(ptype, str) or not ptype:
                        return None
                    if ptype == "minecraft:always_true":
                        return (ptype, set(), set(), None)
                    if ptype in {"minecraft:block_match", "minecraft:random_block_match"}:
                        blocks = parse_blocks_base(test_obj)
                        if not blocks:
                            return None
                        probability = None
                        if ptype == "minecraft:random_block_match":
                            probability = clamp_probability(test_obj.get("probability", 1.0))
                        return (ptype, blocks, set(), probability)
                    if ptype in {"minecraft:blockstate_match", "minecraft:random_blockstate_match"}:
                        states = parse_block_states(test_obj)
                        if not states:
                            return None
                        probability = None
                        if ptype == "minecraft:random_blockstate_match":
                            probability = clamp_probability(test_obj.get("probability", 1.0))
                        return (ptype, set(), states, probability)
                    unhandled_predicates.add(ptype)
                    return None

                ip_parsed = parse_rule_test(ip)
                if ip_parsed is None:
                    continue
                lp_parsed = parse_rule_test(lp)
                if lp_parsed is None:
                    continue
                ip_type, ip_blocks, ip_states, ip_prob = ip_parsed
                lp_type, lp_blocks, lp_states, lp_prob = lp_parsed

                rules.append(
                    RuleSpec(
                        input_type=ip_type,
                        input_blocks_base=frozenset(ip_blocks),
                        input_block_states=frozenset(ip_states),
                        input_probability=ip_prob,
                        location_type=lp_type,
                        location_blocks_base=frozenset(lp_blocks),
                        location_block_states=frozenset(lp_states),
                        location_probability=lp_prob,
                        output_state_id=out_state_id,
                    )
                )

            if not rules:
                return None
            return RuleProcessor(rules=tuple(rules), unhandled_predicates=tuple(sorted(unhandled_predicates)))

        processors_obj = obj.get("processors")
        if isinstance(processors_obj, list):
            for proc in processors_obj:
                if not isinstance(proc, dict):
                    continue
                ptype = proc.get("processor_type")
                if not isinstance(ptype, str) or not ptype:
                    continue
                if ptype == "minecraft:block_ignore":
                    ignore_base.update(_block_id_bases_from_blocks_json(proc.get("blocks")))
                    continue
                if ptype == "minecraft:rule":
                    rp = parse_rule_processor(proc)
                    if rp is not None:
                        specs.append(rp)
                    continue
                if ptype == "minecraft:capped":
                    rp = parse_rule_processor(proc.get("delegate"))
                    if rp is None:
                        unhandled_processors.add("minecraft:capped(delegate)")
                        continue
                    limit_obj = proc.get("limit", 0)
                    limit = limit_obj if isinstance(limit_obj, int) else 0
                    if limit < 0:
                        limit = 0
                    specs.append(CappedProcessor(limit=limit, delegate=rp))
                    continue
                unhandled_processors.add(ptype)

        pipeline = ProcessorPipeline(
            ignore_base=frozenset(ignore_base),
            processors=tuple(specs),
            unhandled_processors=tuple(sorted(unhandled_processors)),
        )
        self._processor_cache[processor_id] = pipeline
        return pipeline

    def load_pool(self, pool_id: str) -> PoolDefinition:
        cached = self._pool_cache.get(pool_id)
        if cached is not None:
            return cached
        rel = self._pool_json_path(pool_id)
        if rel is None:
            empty = PoolDefinition(elements=tuple(), fallback="minecraft:empty")
            self._pool_cache[pool_id] = empty
            return empty
        obj = self.source.read_json(rel) or {}
        fallback = _normalize_nonempty_str(obj.get("fallback", "minecraft:empty"), default="minecraft:empty")
        elems = obj.get("elements")
        out: list[PoolElement] = []
        if isinstance(elems, list):
            for e in elems:
                if not isinstance(e, dict):
                    continue
                weight = e.get("weight", 1)
                if not isinstance(weight, int) or weight <= 0:
                    weight = 1
                elt = e.get("element")
                if not isinstance(elt, dict):
                    continue
                elt_type = elt.get("element_type", "")
                if elt_type == "minecraft:empty_pool_element":
                    continue
                if not isinstance(elt_type, str) or "single_pool_element" not in elt_type:
                    continue
                loc = elt.get("location")
                if not isinstance(loc, str) or not loc or loc == "minecraft:empty":
                    continue
                processors = _normalize_nonempty_str(elt.get("processors", "minecraft:empty"), default="minecraft:empty")
                projection = _normalize_nonempty_str(elt.get("projection", "rigid"), default="rigid")
                out.append(PoolElement(location_id=loc, weight=weight, processors=processors, projection=projection))
        pool = PoolDefinition(elements=tuple(out), fallback=fallback)
        self._pool_cache[pool_id] = pool
        return pool

    def load_template(self, location_id: str) -> StructureTemplate | None:
        if location_id in self._template_cache:
            return self._template_cache[location_id]
        root: nbtlib.Compound | None = None
        for rel in self._structure_nbt_paths(location_id):
            root = self.source.read_nbt(rel)
            if root is not None:
                break
        if root is None:
            self._template_cache[location_id] = None
            return None
        try:
            tmpl = parse_structure_template(root, template_id=location_id)
        except Exception:
            tmpl = None
        self._template_cache[location_id] = tmpl
        return tmpl
