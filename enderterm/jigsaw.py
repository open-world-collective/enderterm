from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import nbtlib

from enderterm.blockstate import _block_id_base, _parse_block_state_id, _block_state_id
from enderterm.core_types import BlockEntityInstance, BlockInstance, EntityInstance, Structure, Vec3i
from enderterm.structure_nbt import parse_structure

DIR_TO_VEC: dict[str, Vec3i] = {
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "west": (-1, 0, 0),
    "east": (1, 0, 0),
    "down": (0, -1, 0),
    "up": (0, 1, 0),
}


@dataclass(frozen=True, slots=True)
class JigsawConnector:
    pos: Vec3i
    front: Vec3i
    top: Vec3i
    projection: str
    pool: str
    target: str
    name: str
    final_state: str
    joint: str
    source: str


@dataclass(frozen=True, slots=True)
class JigsawExpansionState:
    connectors: tuple[JigsawConnector, ...]
    consumed: frozenset[Vec3i]
    dead_end: frozenset[Vec3i]
    # Bounds for each placed piece (min_x, min_y, min_z, max_x, max_y, max_z), inclusive.
    # Used for vanilla-like collision checks (bounding-box overlap).
    piece_bounds: tuple[tuple[int, int, int, int, int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class StructureTemplate:
    template_id: str
    size: tuple[int, int, int]
    blocks: tuple[BlockInstance, ...]
    connectors: tuple[JigsawConnector, ...]
    block_entities: tuple[BlockEntityInstance, ...] = ()
    entities: tuple[EntityInstance, ...] = ()


@dataclass(frozen=True, slots=True)
class PoolElement:
    location_id: str
    weight: int
    processors: str
    projection: str


@dataclass(frozen=True, slots=True)
class PoolDefinition:
    elements: tuple[PoolElement, ...]
    fallback: str


@dataclass(frozen=True, slots=True)
class RuleSpec:
    input_type: str
    input_blocks_base: frozenset[str]
    input_block_states: frozenset[str]
    input_probability: float | None
    location_type: str
    location_blocks_base: frozenset[str]
    location_block_states: frozenset[str]
    location_probability: float | None
    output_state_id: str


@dataclass(frozen=True, slots=True)
class RuleProcessor:
    rules: tuple[RuleSpec, ...]
    unhandled_predicates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CappedProcessor:
    limit: int
    delegate: RuleProcessor


ProcessorSpec = RuleProcessor | CappedProcessor


@dataclass(frozen=True, slots=True)
class ProcessorPipeline:
    ignore_base: frozenset[str]
    processors: tuple[ProcessorSpec, ...]
    unhandled_processors: tuple[str, ...]


def _parse_jigsaw_orientation(block_state_id: str) -> tuple[Vec3i, Vec3i] | None:
    base, props = _parse_block_state_id(block_state_id)
    if base != "minecraft:jigsaw":
        return None
    ori = props.get("orientation")
    if not ori:
        facing = props.get("facing")
        if not facing:
            return None
        front = DIR_TO_VEC.get(facing)
        if front is None:
            return None
        return (front, DIR_TO_VEC["up"])
    a, _, b = ori.partition("_")
    front = DIR_TO_VEC.get(a)
    top = DIR_TO_VEC.get(b)
    if front is None or top is None:
        return None
    return (front, top)


def extract_jigsaw_connectors(root: nbtlib.Compound, *, template_id: str) -> tuple[JigsawConnector, ...]:
    palette = root.get("palette") or []
    palette_ids = [_block_state_id(entry) for entry in palette]
    blocks_tag = root.get("blocks") or []
    out: list[JigsawConnector] = []

    for b in blocks_tag:
        state_idx = int(b.get("state", -1))
        if state_idx < 0 or state_idx >= len(palette_ids):
            continue
        block_id = palette_ids[state_idx]
        if _block_id_base(block_id) != "minecraft:jigsaw":
            continue
        pos_tag = b.get("pos")
        if not pos_tag or len(pos_tag) != 3:
            continue
        pos = (int(pos_tag[0]), int(pos_tag[1]), int(pos_tag[2]))

        ori = _parse_jigsaw_orientation(block_id)
        if ori is None:
            continue
        front, top = ori

        tag = b.get("nbt") or {}
        pool = str(tag.get("pool", "minecraft:empty"))
        target = str(tag.get("target", "minecraft:empty"))
        name = str(tag.get("name", "minecraft:empty"))
        final_state = str(tag.get("final_state", "minecraft:air"))
        joint = str(tag.get("joint", "aligned"))

        out.append(
            JigsawConnector(
                pos=pos,
                front=front,
                top=top,
                projection="rigid",
                pool=pool,
                target=target,
                name=name,
                final_state=final_state,
                joint=joint,
                source=template_id,
            )
        )

    return tuple(out)


def _block_id_from_jigsaw_final_state(final_state: str) -> str | None:
    """Best-effort parse of jigsaw `final_state` strings into our block_id form.

    Vanilla `final_state` is usually just a blockstate id, but can include NBT.
    We currently ignore any NBT and keep the blockstate portion.
    """

    if not isinstance(final_state, str):
        return None
    text = final_state.strip()
    if not text:
        return None
    # Strip SNBT payload if present: `minecraft:foo{...}`
    if "{" in text:
        text = text.split("{", 1)[0].strip()
    # Some packs include whitespace separators; keep the leading token.
    if not text:
        return None
    parts = text.split()
    if not parts:
        return None
    block_id = parts[0].strip()
    return block_id or None


def apply_jigsaw_final_states_to_blocks(
    blocks_by_pos: dict[Vec3i, BlockInstance],
    block_entities_by_pos: dict[Vec3i, BlockEntityInstance],
    connectors: Iterable[JigsawConnector],
) -> None:
    """Replace any remaining pool connector blocks with their `final_state`."""

    for c in connectors:
        existing = blocks_by_pos.get(c.pos)
        if existing is None or _block_id_base(existing.block_id) != "minecraft:jigsaw":
            continue
        bid = _block_id_from_jigsaw_final_state(c.final_state)
        if not bid:
            continue
        base_bid = _block_id_base(bid)
        if base_bid in {"minecraft:air", "minecraft:cave_air", "minecraft:void_air", "minecraft:structure_void"}:
            blocks_by_pos.pop(c.pos, None)
            block_entities_by_pos.pop(c.pos, None)
            continue
        blocks_by_pos[c.pos] = BlockInstance(pos=c.pos, block_id=bid, color_key=bid)
        block_entities_by_pos.pop(c.pos, None)


def parse_structure_template(root: nbtlib.Compound, *, template_id: str) -> StructureTemplate:
    size_tag = root.get("size")
    if not size_tag or len(size_tag) != 3:
        raise ValueError("Not a structure NBT: missing/invalid 'size'")
    size = (int(size_tag[0]), int(size_tag[1]), int(size_tag[2]))
    structure = parse_structure(root)
    connectors = extract_jigsaw_connectors(root, template_id=template_id)
    return StructureTemplate(
        template_id=template_id,
        size=size,
        blocks=structure.blocks,
        connectors=connectors,
        block_entities=structure.block_entities,
        entities=structure.entities,
    )
