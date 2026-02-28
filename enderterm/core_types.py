from __future__ import annotations

from dataclasses import dataclass

Vec3i = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class BlockInstance:
    pos: Vec3i
    block_id: str
    color_key: str


@dataclass(frozen=True, slots=True)
class BlockEntityInstance:
    pos: Vec3i
    nbt: dict[str, object]


@dataclass(frozen=True, slots=True)
class EntityInstance:
    pos: tuple[float, float, float]
    nbt: dict[str, object]


@dataclass(frozen=True, slots=True)
class Structure:
    size: Vec3i
    blocks: tuple[BlockInstance, ...]
    block_entities: tuple[BlockEntityInstance, ...] = ()
    entities: tuple[EntityInstance, ...] = ()

