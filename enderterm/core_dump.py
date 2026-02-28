from __future__ import annotations

"""Portable scene/mesh export ("core dump") helpers (no OpenGL/pyglet imports)."""

import json
import math
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal
import zipfile

from enderterm.blockstate import _block_id_base, _parse_block_state_id
from enderterm.core_types import Structure
from enderterm.geom import _apply_element_rotation, _tri_normal
from enderterm.mc_geometry import (
    FACE_DIRS,
    FACE_NEIGHBOR_DELTA,
    FACE_NORMALS,
    _UNIT_CUBE_FACE_QUADS,
    _UNIT_CUBE_FACE_UV_TRI,
    _default_uv_rect_for_face,
    _element_face_points,
    _uv_tri_for_face_rect,
)
from enderterm.mc_models import MinecraftResourceResolver, _block_model_is_full_cube
from enderterm.mc_source import TextureSource
from enderterm.mc_tint import _tint_rgb
from enderterm.params import ParamDef, ParamStore, load_default_param_store
from enderterm.structure_nbt import apply_render_mode, load_nbt, load_nbt_bytes, parse_structure
from enderterm.usdz import (
    _iter_structure_entries_in_datapack_zip,
    _iter_structure_paths_in_datapack_dir,
    _stable_rgb,
    find_minecraft_client_jar,
)


def _rot_xy(v: tuple[float, float, float], *, rx_deg: int, ry_deg: int) -> tuple[float, float, float]:
    if rx_deg == 0 and ry_deg == 0:
        return v
    x, y, z = v
    rx = math.radians(-rx_deg)
    ry = math.radians(-ry_deg)
    # Rotate around X.
    cy = math.cos(rx)
    sy = math.sin(rx)
    y2 = y * cy - z * sy
    z2 = y * sy + z * cy
    y, z = y2, z2
    # Rotate around Y.
    cx = math.cos(ry)
    sx_ = math.sin(ry)
    x2 = x * cx + z * sx_
    z2 = -x * sx_ + z * cx
    return (x2, y, z2)


_BED_FACING_TO_RY_DEG: dict[str, int] = {
    # Canonical bed geometry is authored "facing south" (+Z). Rotation uses the
    # same convention as block-model applies: +ry means rotate by -ry.
    "south": 0,
    "west": 90,
    "north": 180,
    "east": 270,
}
def _bed_color_from_base_id(base_id: str) -> str | None:
    # minecraft:white_bed -> white
    if not base_id.startswith("minecraft:") or not base_id.endswith("_bed"):
        return None
    color = base_id.removeprefix("minecraft:").removesuffix("_bed")
    return color if color else None


def _append_textured_cuboid(
    verts_by_tex: dict[str, list[float]],
    norms_by_tex: dict[str, list[float]],
    uvs_by_tex: dict[str, list[float]],
    cols_by_tex: dict[str, list[int]],
    *,
    jar_rel: str,
    tx: float,
    ty: float,
    tz: float,
    ry_deg: int,
    bounds_16: tuple[float, float, float, float, float, float],
    skip_faces: frozenset[str] = frozenset(),
) -> None:
    fx, fy, fz, txe, tye, tze = bounds_16
    xmin = min(fx, txe) / 16.0 - 0.5
    xmax = max(fx, txe) / 16.0 - 0.5
    ymin = min(fy, tye) / 16.0 - 0.5
    ymax = max(fy, tye) / 16.0 - 0.5
    zmin = min(fz, tze) / 16.0 - 0.5
    zmax = max(fz, tze) / 16.0 - 0.5

    for face in FACE_DIRS:
        if face in skip_faces:
            continue

        quad = _element_face_points(face, xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax, zmin=zmin, zmax=zmax)
        tri_uv = _uv_tri_for_face_rect(face, (0.0, 0.0, 16.0, 16.0), quad_points=quad)

        quad_r = [_rot_xy(p, rx_deg=0, ry_deg=ry_deg) for p in quad]
        quad_w = [(px + tx, py + ty, pz + tz) for (px, py, pz) in quad_r]
        p0, p1, p2, p3 = quad_w
        tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]

        normal = FACE_NORMALS[face]
        normal_r = _rot_xy(normal, rx_deg=0, ry_deg=ry_deg)

        verts_by_tex.setdefault(jar_rel, []).extend(tri_verts)
        norms_by_tex.setdefault(jar_rel, []).extend(
            [*normal_r, *normal_r, *normal_r, *normal_r, *normal_r, *normal_r]
        )
        uvs_by_tex.setdefault(jar_rel, []).extend(tri_uv)
        cols_by_tex.setdefault(jar_rel, []).extend([255, 255, 255] * 6)


def _append_bed_geometry(
    block_state_id: str,
    *,
    source: TextureSource,
    tx: float,
    ty: float,
    tz: float,
    verts_by_tex: dict[str, list[float]],
    norms_by_tex: dict[str, list[float]],
    uvs_by_tex: dict[str, list[float]],
    cols_by_tex: dict[str, list[int]],
) -> bool:
    base_id, props = _parse_block_state_id(block_state_id)
    color = _bed_color_from_base_id(base_id)
    if color is None:
        return False

    part = props.get("part", "foot")
    facing = props.get("facing", "south")
    ry_deg = int(_BED_FACING_TO_RY_DEG.get(facing, 0))

    # IMPORTANT: Face cull is defined in the bed's *canonical* space, not in the
    # post-rotation world space. The geometry is authored facing south (+Z), and
    # then rotated by `ry_deg`. The internal seam between foot/head is therefore
    # always +Z for the foot and -Z for the head (pre-rotation).
    connect_face: str | None = None
    if part == "foot":
        connect_face = "south"
    elif part == "head":
        connect_face = "north"

    def _skip_faces_for(bounds_16: tuple[float, float, float, float, float, float]) -> frozenset[str]:
        skip = {"down"}
        if connect_face == "south":
            zmax = max(float(bounds_16[2]), float(bounds_16[5]))
            if abs(zmax - 16.0) <= 1e-6:
                skip.add("south")
        elif connect_face == "north":
            zmin = min(float(bounds_16[2]), float(bounds_16[5]))
            if abs(zmin - 0.0) <= 1e-6:
                skip.add("north")
        return frozenset(skip)

    wool = f"assets/minecraft/textures/block/{color}_wool.png"
    if not source.has(wool):
        wool = "assets/minecraft/textures/block/white_wool.png"
    planks = "assets/minecraft/textures/block/oak_planks.png"

    # Simple "full mesh" bed: legs + mattress; head adds a pillow.
    # All geometry is authored in block-model units (0..16) in canonical +Z
    # "south-facing" space, then rotated by `ry_deg` to match `facing`.
    #
    # This is intentionally approximate (bunch of cuboids) rather than a full
    # vanilla BedModel port.
    leg_h = 3.0
    leg_w = 3.0
    mattress_y0 = leg_h
    mattress_y1 = 9.0

    legs = (
        (0.0, 0.0, 0.0, leg_w, leg_h, leg_w),
        (0.0, 0.0, 16.0 - leg_w, leg_w, leg_h, 16.0),
        (16.0 - leg_w, 0.0, 0.0, 16.0, leg_h, leg_w),
        (16.0 - leg_w, 0.0, 16.0 - leg_w, 16.0, leg_h, 16.0),
    )
    for b in legs:
        _append_textured_cuboid(
            verts_by_tex,
            norms_by_tex,
            uvs_by_tex,
            cols_by_tex,
            jar_rel=planks,
            tx=tx,
            ty=ty,
            tz=tz,
            ry_deg=ry_deg,
            bounds_16=b,
            skip_faces=_skip_faces_for(b),
        )

    mattress = (0.0, mattress_y0, 0.0, 16.0, mattress_y1, 16.0)
    _append_textured_cuboid(
        verts_by_tex,
        norms_by_tex,
        uvs_by_tex,
        cols_by_tex,
        jar_rel=wool,
        tx=tx,
        ty=ty,
        tz=tz,
        ry_deg=ry_deg,
        bounds_16=mattress,
        skip_faces=_skip_faces_for(mattress),
    )

    if part == "head":
        pillow_tex = "assets/minecraft/textures/block/white_wool.png"
        if not source.has(pillow_tex):
            pillow_tex = wool
        pillow = (0.0, mattress_y1, 11.0, 16.0, 11.0, 16.0)
        _append_textured_cuboid(
            verts_by_tex,
            norms_by_tex,
            uvs_by_tex,
            cols_by_tex,
            jar_rel=pillow_tex,
            tx=tx,
            ty=ty,
            tz=tz,
            ry_deg=ry_deg,
            bounds_16=pillow,
            skip_faces=_skip_faces_for(pillow),
        )

    return True


@dataclass(frozen=True, slots=True)
class CoreMeshPart:
    layer: str
    material_kind: Literal["texture", "vertex_color"]
    material_key: str
    vertices: tuple[float, ...]
    normals: tuple[float, ...]
    uvs: tuple[float, ...] | None
    colors_u8: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CoreMeshBuild:
    pivot_world: tuple[float, float, float]
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    initial_distance: float
    meshes: tuple[CoreMeshPart, ...]


@dataclass(frozen=True, slots=True)
class CoreFxData:
    defs: tuple[ParamDef, ...]
    values: tuple[tuple[str, float | int], ...]


@dataclass(frozen=True, slots=True)
class _CoreTextureContext:
    minecraft_jar: Path | None
    source: TextureSource | None
    resolver: MinecraftResourceResolver | None


@dataclass(frozen=True, slots=True)
class CoreSceneData:
    schema_version: int
    scene_id: str
    textured: bool
    minecraft_jar: str | None
    mesh: CoreMeshBuild
    fx: CoreFxData
    blocks: tuple[tuple[tuple[int, int, int], str, str], ...]


CORE_DUMP_SCHEMA_VERSION = 1


def _scene_fx_values(param_store: ParamStore, defs: tuple[ParamDef, ...]) -> tuple[tuple[str, float | int], ...]:
    values: list[tuple[str, float | int]] = []
    for d in defs:
        value: float | int = param_store.get_int(d.key) if d.is_int else float(param_store.get(d.key))
        values.append((d.key, value))
    values.sort(key=lambda kv: kv[0])
    return tuple(values)


def _scene_block_rows(
    structure: Structure, *, include_blocks: bool
) -> tuple[tuple[tuple[int, int, int], str, str], ...]:
    if not include_blocks:
        return ()
    return tuple(sorted(((b.pos, b.block_id, b.color_key) for b in structure.blocks), key=lambda x: x[0]))


def core_build_mesh_for_structure(
    structure: Structure,
    *,
    source: TextureSource | None,
    resolver: MinecraftResourceResolver | None,
    center_override: tuple[float, float, float] | None = None,
) -> CoreMeshBuild:
    blocks = list(structure.blocks)
    blocks.sort(key=lambda b: (b.pos[0], b.pos[1], b.pos[2], b.block_id))

    solids: set[tuple[int, int, int]] = set()
    for b in blocks:
        base_id = _block_id_base(b.block_id)
        if base_id in {"minecraft:jigsaw", "minecraft:structure_void"}:
            continue
        if _bed_color_from_base_id(base_id) is not None:
            # Beds are rendered as a non-full-cube special case (and should not
            # cause adjacent block faces to be culled).
            continue
        if resolver is None:
            solids.add(b.pos)
            continue
        bm = resolver.resolve_block_model(b.block_id)
        if bm is None or _block_model_is_full_cube(bm):
            solids.add(b.pos)

    if blocks:
        xs = [b.pos[0] for b in blocks]
        ys = [b.pos[1] for b in blocks]
        zs = [b.pos[2] for b in blocks]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        min_z, max_z = min(zs), max(zs)
        center_x = (min_x + max_x + 1) / 2.0
        center_y = (min_y + max_y + 1) / 2.0
        center_z = (min_z + max_z + 1) / 2.0
        size_x = max_x - min_x + 1
        size_y = max_y - min_y + 1
        size_z = max_z - min_z + 1
    else:
        sx, sy, sz = structure.size
        center_x = sx / 2.0
        center_y = sy / 2.0
        center_z = sz / 2.0
        size_x, size_y, size_z = sx, sy, sz
        min_x = 0
        min_y = 0
        min_z = 0
        max_x = int(sx) - 1
        max_y = int(sy) - 1
        max_z = int(sz) - 1

    if center_override is not None:
        center_x, center_y, center_z = center_override

    if blocks:
        min_geom_x = float(min_x) - center_x
        max_geom_x = float(max_x + 1) - center_x
        min_geom_y = float(min_y) - center_y
        max_geom_y = float(max_y + 1) - center_y
        min_geom_z = float(min_z) - center_z
        max_geom_z = float(max_z + 1) - center_z
    else:
        min_geom_x = 0.0 - center_x
        max_geom_x = float(size_x) - center_x
        min_geom_y = 0.0 - center_y
        max_geom_y = float(size_y) - center_y
        min_geom_z = 0.0 - center_z
        max_geom_z = float(size_z) - center_z

    far_x = min_geom_x if abs(min_geom_x) >= abs(max_geom_x) else max_geom_x
    far_y = min_geom_y if abs(min_geom_y) >= abs(max_geom_y) else max_geom_y
    far_z = min_geom_z if abs(min_geom_z) >= abs(max_geom_z) else max_geom_z
    radius = math.sqrt(far_x * far_x + far_y * far_y + far_z * far_z)
    initial_distance = max(2.0, radius * 2.5)

    face_normals = FACE_NORMALS
    neighbor_delta = FACE_NEIGHBOR_DELTA
    unit_face_quads = _UNIT_CUBE_FACE_QUADS
    unit_face_uv_tri = _UNIT_CUBE_FACE_UV_TRI

    verts_by_tex: dict[str, list[float]] = {}
    norms_by_tex: dict[str, list[float]] = {}
    uvs_by_tex: dict[str, list[float]] = {}
    cols_by_tex: dict[str, list[int]] = {}
    colored_verts: list[float] = []
    colored_norms: list[float] = []
    colored_cols: list[int] = []

    for block in blocks:
        base_id = _block_id_base(block.block_id)
        x, y, z = block.pos
        tx = x + 0.5 - center_x
        ty = y + 0.5 - center_y
        tz = z + 0.5 - center_z

        if source is not None and resolver is not None:
            if _append_bed_geometry(
                block.block_id,
                source=source,
                tx=float(tx),
                ty=float(ty),
                tz=float(tz),
                verts_by_tex=verts_by_tex,
                norms_by_tex=norms_by_tex,
                uvs_by_tex=uvs_by_tex,
                cols_by_tex=cols_by_tex,
            ):
                continue

        appearance = None
        block_model = None
        if resolver is not None:
            force_color = base_id == "minecraft:jigsaw"
            if not force_color:
                appearance = resolver.resolve_block_appearance(block.block_id)
                block_model = resolver.resolve_block_model(block.block_id)
        rx = appearance.rotate_x_deg if appearance is not None else 0
        ry = appearance.rotate_y_deg if appearance is not None else 0

        if block_model is not None and source is not None and resolver is not None:
            solid_block = _block_model_is_full_cube(block_model)
            for part in block_model.parts:
                rx_p = int(part.rotate_x_deg)
                ry_p = int(part.rotate_y_deg)
                for el in part.model.elements or []:
                    if not isinstance(el, dict):
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
                    xmin_el = min(fx, txe) / 16.0 - 0.5
                    xmax_el = max(fx, txe) / 16.0 - 0.5
                    ymin_el = min(fy, tye) / 16.0 - 0.5
                    ymax_el = max(fy, tye) / 16.0 - 0.5
                    zmin_el = min(fz, tze) / 16.0 - 0.5
                    zmax_el = max(fz, tze) / 16.0 - 0.5

                    faces_obj = el.get("faces")
                    if not isinstance(faces_obj, dict):
                        continue
                    frm_t = (fx, fy, fz)
                    to_t = (txe, tye, tze)
                    rot_el = el.get("rotation")

                    for face in FACE_DIRS:
                        face_def = faces_obj.get(face)
                        if not isinstance(face_def, dict):
                            continue

                        if solid_block:
                            n_rot = _rot_xy(face_normals[face], rx_deg=rx_p, ry_deg=ry_p)
                            nx, ny, nz = n_rot
                            dx = 1 if nx > 0.5 else (-1 if nx < -0.5 else 0)
                            dy = 1 if ny > 0.5 else (-1 if ny < -0.5 else 0)
                            dz = 1 if nz > 0.5 else (-1 if nz < -0.5 else 0)
                            if (x + dx, y + dy, z + dz) in solids:
                                continue

                        raw_tex = face_def.get("texture")
                        tex_ref: str | None = raw_tex if isinstance(raw_tex, str) and raw_tex else None
                        tex_resolved = (
                            resolver._resolve_texture_ref(tex_ref, part.model.textures) if tex_ref is not None else None
                        )
                        jar_rel = resolver._texture_ref_to_jar_rel(tex_resolved) if tex_resolved else None
                        if jar_rel is None or not source.has(jar_rel):
                            jar_rel = None

                        uv_rect: tuple[float, float, float, float] | None = None
                        uv_obj = face_def.get("uv")
                        if isinstance(uv_obj, list) and len(uv_obj) == 4:
                            try:
                                uv_rect = (float(uv_obj[0]), float(uv_obj[1]), float(uv_obj[2]), float(uv_obj[3]))
                            except (TypeError, ValueError):
                                uv_rect = None
                        if uv_rect is None:
                            uv_rect = _default_uv_rect_for_face(face, frm=frm_t, to=to_t)

                        rot_deg = face_def.get("rotation", 0)
                        rot_deg = int(rot_deg) if isinstance(rot_deg, (int, float)) else 0

                        tint = (255, 255, 255)
                        tint_obj = face_def.get("tintindex")
                        if isinstance(tint_obj, (int, float)):
                            tint = _tint_rgb(source, block.block_id, int(tint_obj))

                        quad = _element_face_points(
                            face,
                            xmin=xmin_el,
                            xmax=xmax_el,
                            ymin=ymin_el,
                            ymax=ymax_el,
                            zmin=zmin_el,
                            zmax=zmax_el,
                        )
                        tri_uv = _uv_tri_for_face_rect(face, uv_rect, rotation_deg=rot_deg, quad_points=quad)
                        quad = _apply_element_rotation(quad, rot_el)
                        quad_r = [_rot_xy(p, rx_deg=rx_p, ry_deg=ry_p) for p in quad]
                        quad_w = [(px + tx, py + ty, pz + tz) for (px, py, pz) in quad_r]
                        p0, p1, p2, p3 = quad_w
                        normal = _tri_normal(p0, p1, p2)
                        tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]

                        if jar_rel is not None:
                            verts_by_tex.setdefault(jar_rel, []).extend(tri_verts)
                            norms_by_tex.setdefault(jar_rel, []).extend([*normal, *normal, *normal, *normal, *normal, *normal])
                            uvs_by_tex.setdefault(jar_rel, []).extend(tri_uv)
                            cols_by_tex.setdefault(jar_rel, []).extend([*tint, *tint, *tint, *tint, *tint, *tint])
                            continue

                        r, g, b = _stable_rgb(block.color_key)
                        col = (int(r * 255), int(g * 255), int(b * 255))
                        colored_verts.extend(tri_verts)
                        colored_norms.extend([*normal, *normal, *normal, *normal, *normal, *normal])
                        colored_cols.extend([*col, *col, *col, *col, *col, *col])

            continue

        for face in FACE_DIRS:
            dx, dy, dz = neighbor_delta[face]
            if (x + dx, y + dy, z + dz) in solids:
                continue

            quad = unit_face_quads[face]
            if base_id == "minecraft:jigsaw":
                quad = [(px * 1.5, py * 1.5, pz * 1.5) for (px, py, pz) in quad]
            normal = face_normals[face]
            normal_r = _rot_xy(normal, rx_deg=rx, ry_deg=ry)

            quad_r = [_rot_xy(p, rx_deg=rx, ry_deg=ry) for p in quad]
            quad_w = [(px + tx, py + ty, pz + tz) for (px, py, pz) in quad_r]
            p0, p1, p2, p3 = quad_w
            tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]

            if appearance is not None and source is not None:
                jar_rel = appearance.face_texture_png_by_dir.get(face) or ""
                if jar_rel and source.has(jar_rel):
                    verts_by_tex.setdefault(jar_rel, []).extend(tri_verts)
                    norms_by_tex.setdefault(jar_rel, []).extend([*normal_r, *normal_r, *normal_r, *normal_r, *normal_r, *normal_r])
                    uvs_by_tex.setdefault(jar_rel, []).extend(unit_face_uv_tri[face])
                    cols_by_tex.setdefault(jar_rel, []).extend([255, 255, 255] * 6)
                    continue

            r, g, b = _stable_rgb(block.color_key)
            col = (int(r * 255), int(g * 255), int(b * 255))
            colored_verts.extend(tri_verts)
            colored_norms.extend([*normal_r, *normal_r, *normal_r, *normal_r, *normal_r, *normal_r])
            colored_cols.extend([*col, *col, *col, *col, *col, *col])

    meshes: list[CoreMeshPart] = []
    for jar_rel in sorted(verts_by_tex.keys()):
        verts = tuple(float(v) for v in (verts_by_tex.get(jar_rel) or []))
        norms = tuple(float(v) for v in (norms_by_tex.get(jar_rel) or []))
        uvs = tuple(float(v) for v in (uvs_by_tex.get(jar_rel) or []))
        cols = tuple(int(c) for c in (cols_by_tex.get(jar_rel) or []))
        meshes.append(
            CoreMeshPart(
                layer="model",
                material_kind="texture",
                material_key=str(jar_rel),
                vertices=verts,
                normals=norms,
                uvs=uvs,
                colors_u8=cols,
            )
        )

    if colored_verts:
        meshes.append(
            CoreMeshPart(
                layer="model",
                material_kind="vertex_color",
                material_key="__vertex_color__",
                vertices=tuple(float(v) for v in colored_verts),
                normals=tuple(float(v) for v in colored_norms),
                uvs=None,
                colors_u8=tuple(int(c) for c in colored_cols),
            )
        )

    return CoreMeshBuild(
        pivot_world=(float(center_x), float(center_y), float(center_z)),
        bounds_min=(float(min_geom_x), float(min_geom_y), float(min_geom_z)),
        bounds_max=(float(max_geom_x), float(max_geom_y), float(max_geom_z)),
        initial_distance=float(initial_distance),
        meshes=tuple(meshes),
    )


def core_build_scene(
    structure: Structure,
    *,
    scene_id: str,
    textured: bool,
    minecraft_jar: Path | None,
    source: TextureSource | None,
    resolver: MinecraftResourceResolver | None,
    param_store: ParamStore,
    center_override: tuple[float, float, float] | None = None,
    include_blocks: bool = True,
) -> CoreSceneData:
    mesh = core_build_mesh_for_structure(
        structure,
        source=source,
        resolver=resolver,
        center_override=center_override,
    )
    defs = tuple(param_store.defs())
    values = _scene_fx_values(param_store, defs)
    blocks = _scene_block_rows(structure, include_blocks=include_blocks)

    return CoreSceneData(
        schema_version=CORE_DUMP_SCHEMA_VERSION,
        scene_id=str(scene_id),
        textured=bool(textured),
        minecraft_jar=str(minecraft_jar) if minecraft_jar is not None else None,
        mesh=mesh,
        fx=CoreFxData(defs=defs, values=tuple(values)),
        blocks=blocks,
    )


def _mesh_part_payload(part: CoreMeshPart) -> dict[str, object]:
    return {
        "layer": part.layer,
        "material_kind": part.material_kind,
        "material_key": part.material_key,
        "vertices": list(part.vertices),
        "normals": list(part.normals),
        "uvs": list(part.uvs) if part.uvs is not None else None,
        "colors_u8": list(part.colors_u8),
    }


def _fx_def_payload(param_def: ParamDef) -> dict[str, object]:
    return {
        "key": param_def.key,
        "label": param_def.label,
        "default": float(param_def.default),
        "min": float(param_def.min_value),
        "max": float(param_def.max_value),
        "is_int": bool(param_def.is_int),
        "fmt": param_def.fmt,
    }


def _block_payload(row: tuple[tuple[int, int, int], str, str]) -> dict[str, object]:
    pos, block_id, color_key = row
    return {"pos": [int(pos[0]), int(pos[1]), int(pos[2])], "id": block_id, "color_key": color_key}


def _core_scene_to_dict(scene: CoreSceneData) -> dict[str, object]:
    mesh = scene.mesh
    return {
        "schema": "enderterm.core_dump",
        "schema_version": int(scene.schema_version),
        "scene_id": scene.scene_id,
        "textured": bool(scene.textured),
        "minecraft_jar": scene.minecraft_jar,
        "mesh": {
            "pivot_world": [mesh.pivot_world[0], mesh.pivot_world[1], mesh.pivot_world[2]],
            "bounds_min": [mesh.bounds_min[0], mesh.bounds_min[1], mesh.bounds_min[2]],
            "bounds_max": [mesh.bounds_max[0], mesh.bounds_max[1], mesh.bounds_max[2]],
            "initial_distance": float(mesh.initial_distance),
            "parts": [_mesh_part_payload(p) for p in mesh.meshes],
        },
        "fx": {
            "defs": [_fx_def_payload(d) for d in scene.fx.defs],
            "values": {k: v for (k, v) in scene.fx.values},
        },
        "blocks": [_block_payload(row) for row in scene.blocks],
    }


def _core_scene_json_text(scene: CoreSceneData) -> str:
    payload = _core_scene_to_dict(scene)
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def dump_core_json(scene: CoreSceneData, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_core_scene_json_text(scene), encoding="utf-8")


def _select_datapack_item_index(labels: list[str], select: str | None) -> int:
    if not select:
        return 0
    needle = select.lower()
    for i, label in enumerate(labels):
        if needle in label.lower():
            return i
    return 0


def _select_datapack_item(items: list[tuple[str, str]], select: str | None) -> tuple[str, str]:
    labels = [out_rel.removesuffix(".usdz") for (out_rel, _) in items]
    return items[_select_datapack_item_index(labels, select)]


@contextmanager
def _open_texture_context(*, textured: bool, minecraft_jar: Path | None) -> Iterator[_CoreTextureContext]:
    jar_path: Path | None = None
    texture_source: TextureSource | None = None
    resolver: MinecraftResourceResolver | None = None
    if textured:
        jar_path = minecraft_jar or find_minecraft_client_jar()
        if jar_path is None:
            raise SystemExit(
                "textured mode requires a Minecraft client jar; pass --minecraft-jar or set $MINECRAFT_JAR"
            )
        texture_source = TextureSource(jar_path)
        resolver = MinecraftResourceResolver(texture_source)

    try:
        yield _CoreTextureContext(minecraft_jar=jar_path, source=texture_source, resolver=resolver)
    finally:
        if texture_source is not None:
            texture_source.close()


def dump_structure_core_json(
    nbt_path: Path,
    *,
    mode: str,
    auto_threshold: int,
    textured: bool,
    minecraft_jar: Path | None,
    out_path: Path,
    param_store: ParamStore | None = None,
) -> None:
    root = load_nbt(nbt_path)
    structure = apply_render_mode(parse_structure(root), mode, auto_threshold)
    with _open_texture_context(textured=textured, minecraft_jar=minecraft_jar) as texture_ctx:
        param_store2 = load_default_param_store() if param_store is None else param_store
        scene = core_build_scene(
            structure,
            scene_id=str(nbt_path),
            textured=textured,
            minecraft_jar=texture_ctx.minecraft_jar,
            source=texture_ctx.source,
            resolver=texture_ctx.resolver,
            param_store=param_store2,
        )
        dump_core_json(scene, out_path)


def dump_datapack_core_json(
    datapack_path: Path,
    *,
    mode: str,
    auto_threshold: int,
    textured: bool,
    minecraft_jar: Path | None,
    select: str | None,
    out_path: Path,
    param_store: ParamStore | None = None,
) -> None:
    items: list[tuple[str, str]] = []
    zip_file: zipfile.ZipFile | None = None
    try:
        if datapack_path.is_file() and datapack_path.suffix.lower() in {".zip", ".jar"}:
            zip_file = zipfile.ZipFile(datapack_path, "r")
            items = list(_iter_structure_entries_in_datapack_zip(zip_file))
        elif datapack_path.is_dir():
            items = [(out_rel, str(p)) for (out_rel, p) in _iter_structure_paths_in_datapack_dir(datapack_path)]
        else:
            raise SystemExit("dump-core input must be a datapack .zip/.jar or directory")

        if not items:
            raise SystemExit("No structure .nbt files found in datapack")

        out_rel, src = _select_datapack_item(items, select)
        label = out_rel.removesuffix(".usdz")
        if zip_file is not None:
            root = load_nbt_bytes(zip_file.read(src))
        else:
            root = load_nbt(Path(src))

        structure = apply_render_mode(parse_structure(root), mode, auto_threshold)
        with _open_texture_context(textured=textured, minecraft_jar=minecraft_jar) as texture_ctx:
            param_store2 = load_default_param_store() if param_store is None else param_store
            scene = core_build_scene(
                structure,
                scene_id=str(label),
                textured=textured,
                minecraft_jar=texture_ctx.minecraft_jar,
                source=texture_ctx.source,
                resolver=texture_ctx.resolver,
                param_store=param_store2,
            )
            dump_core_json(scene, out_path)
    finally:
        if zip_file is not None:
            zip_file.close()
