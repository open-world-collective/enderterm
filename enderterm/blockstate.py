from __future__ import annotations

from collections.abc import Mapping

import nbtlib


def _block_id_base(block_id: str) -> str:
    return block_id.split("[", 1)[0]


def _parse_property_assignments(parts: list[str]) -> dict[str, str]:
    props: dict[str, str] = {}
    for part in parts:
        key, _, value = part.partition("=")
        if key:
            # Keep current parser semantics: missing "=" yields empty value,
            # duplicate keys are resolved by later entries.
            props[key] = value
    return props


def _parse_block_state_props(props_part: str) -> dict[str, str]:
    """Parse the `k=v` property segment from a block-state id."""
    if not props_part:
        return {}
    props_part = props_part.rstrip("]")
    if not props_part:
        return {}
    return _parse_property_assignments(props_part.split(","))


def _parse_block_state_id(block_state_id: str) -> tuple[str, dict[str, str]]:
    base, _, props_part = block_state_id.partition("[")
    return (base, _parse_block_state_props(props_part))


def _block_state_id(palette_entry: nbtlib.Compound) -> str:
    name = str(palette_entry.get("Name", ""))
    props = palette_entry.get("Properties")
    return _compose_block_state_id(name, props, string_keys_only=False)


def _sorted_property_items(props: Mapping[object, object], *, string_keys_only: bool) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in props.items():
        if string_keys_only and not isinstance(key, str):
            continue
        items.append((str(key), str(value)))
    items.sort()
    return items


def _format_block_state_id(name: str, props: Mapping[object, object], *, string_keys_only: bool) -> str:
    items = _sorted_property_items(props, string_keys_only=string_keys_only)
    props_snbt = ",".join(f"{key}={value}" for key, value in items)
    return f"{name}[{props_snbt}]"


def _compose_block_state_id(name: str, props: Mapping[object, object] | None, *, string_keys_only: bool) -> str:
    if not props:
        return name
    return _format_block_state_id(name, props, string_keys_only=string_keys_only)


def _select_state_field(state_obj: dict[object, object], canonical: str, fallback: str) -> object:
    if canonical in state_obj:
        return state_obj.get(canonical)
    return state_obj.get(fallback)


def _block_state_id_from_json_state(obj: object) -> str | None:
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, dict):
        return None
    name = _select_state_field(obj, "Name", "name")
    if not isinstance(name, str) or not name:
        return None
    props = _select_state_field(obj, "Properties", "properties")
    if not isinstance(props, dict):
        return name
    return _compose_block_state_id(name, props, string_keys_only=True)


_HORIZ_NORMAL_TO_FACING: dict[tuple[int, int, int], str] = {
    (0, 0, -1): "north",
    (0, 0, 1): "south",
    (1, 0, 0): "east",
    (-1, 0, 0): "west",
}
_TORCH_FACE_AWARE_BLOCK_IDS: frozenset[str] = frozenset({"minecraft:torch", "minecraft:wall_torch"})


def _torch_placement_block_id(face_n: tuple[int, int, int] | None) -> str | None:
    if face_n is None:
        return None
    facing = _HORIZ_NORMAL_TO_FACING.get(face_n)
    if facing is not None:
        return f"minecraft:wall_torch[facing={facing}]"
    if face_n == (0, 1, 0):
        return "minecraft:torch"
    return None


def _build_place_block_id_for_face(block_id: str, face_n: tuple[int, int, int] | None) -> str:
    """Adjust a block id for interactive (Minecraft-style) face placement.

    v0 scope: torches only.
    - Vertical faces => wall torch with facing.
    - Floor placement => standing torch.
    """

    if not isinstance(block_id, str) or not block_id:
        return block_id

    base = _block_id_base(block_id)
    if base not in _TORCH_FACE_AWARE_BLOCK_IDS or face_n is None:
        return block_id

    placed = _torch_placement_block_id(face_n)
    if placed is not None:
        return placed
    return block_id
