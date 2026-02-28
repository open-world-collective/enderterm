from __future__ import annotations

import gzip
import io
from pathlib import Path
from typing import BinaryIO

import nbtlib

from enderterm.blockstate import _block_id_base, _parse_block_state_id, _block_state_id
from enderterm.core_types import BlockEntityInstance, BlockInstance, EntityInstance, Structure, Vec3i

GZIP_MAGIC = b"\x1f\x8b"
AUTO_SURFACE_THRESHOLD = 10_000
_MAX_SURFACE_OCCUPANCY_VOLUME = 4_000_000
_STRUCTURE_SIZE_ERROR = "Not a structure NBT: missing/invalid 'size' (expected 3 integers)"
_NON_RENDER_BLOCK_IDS = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:structure_void"}
NEIGHBORS_6 = (
    (1, 0, 0),
    (-1, 0, 0),
    (0, 1, 0),
    (0, -1, 0),
    (0, 0, 1),
    (0, 0, -1),
)


def _nbt_to_plain(obj: object) -> object:
    # Convert `nbtlib` tags to plain Python types so they're safe to pass through
    # multiprocessing queues (notably `nbtlib.List[Foo]` isn't picklable).
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool, bytes)):
        return obj
    if isinstance(obj, (bytearray, memoryview)):
        return bytes(obj)
    if isinstance(obj, dict):
        out: dict[str, object] = {}
        for k, v in obj.items():
            out[str(k)] = _nbt_to_plain(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_nbt_to_plain(v) for v in obj]
    try:
        return [_nbt_to_plain(v) for v in obj]  # type: ignore[operator]
    except Exception:
        return str(obj)


def _load_nbt_from_fileobj(fileobj: BinaryIO) -> nbtlib.Compound:
    # `nbtlib.File` behaves like the root compound (dict-like).
    return nbtlib.File.parse(fileobj)  # type: ignore[no-any-return]


def _peek_magic(fileobj: BinaryIO) -> bytes:
    try:
        return fileobj.peek(2)[:2]  # type: ignore[attr-defined]
    except Exception:
        magic = fileobj.read(2)
        fileobj.seek(0)
        return magic


def _is_gzip_magic(data: bytes) -> bool:
    return data[:2] == GZIP_MAGIC


def _decode_nbt_bytes(data: bytes) -> bytes:
    if _is_gzip_magic(data):
        return gzip.decompress(data)
    return data


def _structure_size_error(size_tag: object) -> ValueError:
    return ValueError(f"{_STRUCTURE_SIZE_ERROR}; got={size_tag!r}")


def _parse_structure_size(root: nbtlib.Compound) -> Vec3i:
    size_tag = root.get("size")
    if not isinstance(size_tag, (list, tuple)) or len(size_tag) != 3:
        raise _structure_size_error(size_tag)
    try:
        return (int(size_tag[0]), int(size_tag[1]), int(size_tag[2]))  # type: ignore[index]
    except (TypeError, ValueError):
        raise _structure_size_error(size_tag) from None


def _parse_block_pos(pos_tag: object) -> Vec3i | None:
    if not isinstance(pos_tag, (list, tuple)) or len(pos_tag) != 3:
        return None
    try:
        return (int(pos_tag[0]), int(pos_tag[1]), int(pos_tag[2]))  # type: ignore[index]
    except (TypeError, ValueError):
        return None


def _iter_compound_tags(tag_list: object) -> list[nbtlib.Compound]:
    if not isinstance(tag_list, (list, tuple)):
        return []
    return [entry for entry in tag_list if isinstance(entry, nbtlib.Compound)]


def _nbt_plain_dict(nbt_obj: object) -> dict[str, object]:
    if not isinstance(nbt_obj, dict):
        return {}
    nbt_plain = _nbt_to_plain(nbt_obj)
    if isinstance(nbt_plain, dict):
        return nbt_plain
    return {}


def _jigsaw_color_key(block_id: str, block_nbt: object) -> str:
    if not isinstance(block_nbt, dict):
        return block_id
    pool = block_nbt.get("pool")
    if pool:
        return f"minecraft:jigsaw|pool={pool}"
    return block_id


def _bounds_from_positions(positions: list[Vec3i]) -> tuple[int, int, int, int, int, int] | None:
    if not positions:
        return None
    x0, y0, z0 = positions[0]
    min_x = max_x = x0
    min_y = max_y = y0
    min_z = max_z = z0
    for x, y, z in positions[1:]:
        if x < min_x:
            min_x = x
        elif x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        elif y > max_y:
            max_y = y
        if z < min_z:
            min_z = z
        elif z > max_z:
            max_z = z
    return (min_x, max_x, min_y, max_y, min_z, max_z)


def _parse_structure_blocks(
    root: nbtlib.Compound, palette_ids: list[str]
) -> tuple[list[BlockInstance], list[BlockEntityInstance]]:
    blocks_tag = _iter_compound_tags(root.get("blocks") or [])
    blocks: list[BlockInstance] = []
    block_entities: list[BlockEntityInstance] = []
    for block_tag in blocks_tag:
        state_idx = int(block_tag.get("state", -1))
        if state_idx < 0 or state_idx >= len(palette_ids):
            continue

        block_id = palette_ids[state_idx]
        base_name = block_id.split("[", 1)[0]
        if base_name in _NON_RENDER_BLOCK_IDS:
            continue

        pos = _parse_block_pos(block_tag.get("pos"))
        if pos is None:
            continue

        color_key = block_id
        if base_name == "minecraft:jigsaw":
            color_key = _jigsaw_color_key(block_id, block_tag.get("nbt"))
        blocks.append(BlockInstance(pos=pos, block_id=block_id, color_key=color_key))

        if base_name != "minecraft:jigsaw":
            block_entity_nbt = _nbt_plain_dict(block_tag.get("nbt"))
            if block_entity_nbt:
                block_entities.append(BlockEntityInstance(pos=pos, nbt=block_entity_nbt))
    return (blocks, block_entities)


def _parse_structure_entities(root: nbtlib.Compound) -> list[EntityInstance]:
    entities_tag = _iter_compound_tags(root.get("entities") or [])
    entities: list[EntityInstance] = []
    for entity_tag in entities_tag:
        pos_tag = entity_tag.get("pos")
        if not isinstance(pos_tag, (list, tuple)) or len(pos_tag) != 3:
            continue
        try:
            ex, ey, ez = (float(pos_tag[0]), float(pos_tag[1]), float(pos_tag[2]))
        except (TypeError, ValueError):
            continue
        entities.append(EntityInstance(pos=(ex, ey, ez), nbt=_nbt_plain_dict(entity_tag.get("nbt"))))
    return entities


def load_nbt(path: Path) -> nbtlib.Compound:
    with path.open("rb") as f:
        if _is_gzip_magic(_peek_magic(f)):
            with gzip.GzipFile(fileobj=f, mode="rb") as gz:
                return _load_nbt_from_fileobj(gz)
        return _load_nbt_from_fileobj(f)


def load_nbt_bytes(data: bytes) -> nbtlib.Compound:
    return _load_nbt_from_fileobj(io.BytesIO(_decode_nbt_bytes(data)))


def parse_structure(root: nbtlib.Compound) -> Structure:
    size = _parse_structure_size(root)
    palette_ids = [_block_state_id(entry) for entry in (root.get("palette") or [])]
    blocks, block_entities = _parse_structure_blocks(root, palette_ids)
    entities = _parse_structure_entities(root)
    return Structure(size=size, blocks=tuple(blocks), block_entities=tuple(block_entities), entities=tuple(entities))


def filter_surface_blocks(structure: Structure) -> Structure:
    blocks = structure.blocks
    if not blocks:
        return structure

    solid_positions: list[Vec3i] = []
    for b in blocks:
        if _block_id_base(b.block_id) in {"minecraft:jigsaw", "minecraft:structure_void"}:
            continue
        solid_positions.append(b.pos)
    bounds = _bounds_from_positions(solid_positions)
    if bounds is None:
        return structure
    min_x, max_x, min_y, max_y, min_z, max_z = bounds

    sx = max_x - min_x + 1
    sy = max_y - min_y + 1
    sz = max_z - min_z + 1
    volume = sx * sy * sz

    surface_blocks: list[BlockInstance] = []
    if volume <= _MAX_SURFACE_OCCUPANCY_VOLUME:
        # Dense-friendly occupancy grid for fast neighbor checks. The bounds are
        # derived from solid blocks only (jigsaws intentionally don't count as
        # solid for surface detection).
        occ = bytearray(volume)
        stride_z = sx
        stride_y = sx * sz

        for x, y, z in solid_positions:
            dx = x - min_x
            dy = y - min_y
            dz = z - min_z
            occ[(dy * sz + dz) * sx + dx] = 1

        for block in blocks:
            x, y, z = block.pos
            if x < min_x or x > max_x or y < min_y or y > max_y or z < min_z or z > max_z:
                surface_blocks.append(block)
                continue
            dx = x - min_x
            dy = y - min_y
            dz = z - min_z
            idx = (dy * sz + dz) * sx + dx

            if (
                dx == 0
                or occ[idx - 1] == 0
                or dx + 1 == sx
                or occ[idx + 1] == 0
                or dz == 0
                or occ[idx - stride_z] == 0
                or dz + 1 == sz
                or occ[idx + stride_z] == 0
                or dy == 0
                or occ[idx - stride_y] == 0
                or dy + 1 == sy
                or occ[idx + stride_y] == 0
            ):
                surface_blocks.append(block)
    else:
        # Sparse-friendly fallback.
        solids = set(solid_positions)
        for block in blocks:
            x, y, z = block.pos
            if any((x + dx, y + dy, z + dz) not in solids for dx, dy, dz in NEIGHBORS_6):
                surface_blocks.append(block)

    keep_pos = {b.pos for b in surface_blocks}
    keep_pos.update(be.pos for be in structure.block_entities)
    if keep_pos:
        surface_blocks = [b for b in structure.blocks if b.pos in keep_pos]
    return Structure(
        size=structure.size,
        blocks=tuple(surface_blocks),
        block_entities=structure.block_entities,
        entities=structure.entities,
    )


def apply_render_mode(structure: Structure, mode: str, auto_threshold: int = AUTO_SURFACE_THRESHOLD) -> Structure:
    if mode == "full":
        return structure
    if mode == "surface":
        return filter_surface_blocks(structure)
    if mode == "auto":
        if len(structure.blocks) >= auto_threshold:
            return filter_surface_blocks(structure)
        return structure
    raise ValueError(f"Unknown render mode: {mode}")


def structure_to_nbt_root(structure: Structure) -> tuple[nbtlib.Compound, Vec3i]:
    blocks = list(structure.blocks)
    if blocks:
        bounds = _bounds_from_positions([b.pos for b in blocks])
        assert bounds is not None
        min_x, max_x, min_y, max_y, min_z, max_z = bounds
        offset = (min_x, min_y, min_z)
        size = (max_x - min_x + 1, max_y - min_y + 1, max_z - min_z + 1)
    else:
        offset = (0, 0, 0)
        size = structure.size

    palette: list[nbtlib.Compound] = []
    palette_index: dict[str, int] = {}
    block_entries: list[nbtlib.Compound] = []

    for b in blocks:
        state_id = b.block_id
        idx = palette_index.get(state_id)
        if idx is None:
            idx = len(palette)
            palette_index[state_id] = idx
            name, props = _parse_block_state_id(state_id)
            entry = nbtlib.Compound({"Name": nbtlib.String(name)})
            if props:
                entry["Properties"] = nbtlib.Compound({k: nbtlib.String(v) for k, v in sorted(props.items())})
            palette.append(entry)

        x, y, z = b.pos
        px = x - offset[0]
        py = y - offset[1]
        pz = z - offset[2]
        block_entries.append(
            nbtlib.Compound(
                {
                    "pos": nbtlib.List[nbtlib.Int]([px, py, pz]),
                    "state": nbtlib.Int(idx),
                }
            )
        )

    # Empty structures still need a palette for Minecraft to load them.
    if not palette:
        palette.append(nbtlib.Compound({"Name": nbtlib.String("minecraft:air")}))

    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([size[0], size[1], size[2]]),
            "palette": nbtlib.List[nbtlib.Compound](palette),
            "blocks": nbtlib.List[nbtlib.Compound](block_entries),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )
    return (root, offset)
