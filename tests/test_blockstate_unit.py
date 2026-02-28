from __future__ import annotations

import nbtlib

from enderterm.blockstate import _block_state_id_from_json_state, _build_place_block_id_for_face, _parse_block_state_id
from enderterm.blockstate import _block_state_id


def test_block_state_id_from_json_state_sorts_properties_and_coerces_values() -> None:
    state = _block_state_id_from_json_state(
        {
            "name": "minecraft:stone",
            "properties": {
                "b": 2,
                "a": "1",
            },
        }
    )
    assert state == "minecraft:stone[a=1,b=2]"


def test_parse_block_state_id_splits_base_and_properties() -> None:
    base, props = _parse_block_state_id("minecraft:oak_log[axis=y,powered=true]")
    assert base == "minecraft:oak_log"
    assert props == {"axis": "y", "powered": "true"}


def test_block_state_id_sorts_nbt_properties() -> None:
    entry = nbtlib.Compound(
        {
            "Name": nbtlib.String("minecraft:stone"),
            "Properties": nbtlib.Compound(
                {
                    "b": nbtlib.String("2"),
                    "a": nbtlib.String("1"),
                }
            ),
        }
    )
    assert _block_state_id(entry) == "minecraft:stone[a=1,b=2]"


def test_block_state_id_from_json_state_handles_invalid_inputs() -> None:
    assert _block_state_id_from_json_state(123) is None
    assert _block_state_id_from_json_state({"properties": {"a": "1"}}) is None
    assert _block_state_id_from_json_state({"name": "minecraft:stone"}) == "minecraft:stone"
    assert _block_state_id_from_json_state({"name": "minecraft:stone", "properties": {1: "x", "a": 1}}) == "minecraft:stone[a=1]"


def test_block_state_id_from_json_state_keeps_empty_suffix_when_all_props_keys_are_non_strings() -> None:
    assert _block_state_id_from_json_state({"name": "minecraft:stone", "properties": {1: "x"}}) == "minecraft:stone[]"


def test_block_state_id_from_json_state_prefers_canonical_name_and_properties_keys() -> None:
    state = _block_state_id_from_json_state(
        {
            "Name": "minecraft:stone",
            "name": "minecraft:dirt",
            "Properties": {"b": "2", "a": "1"},
            "properties": {"z": "9"},
        }
    )
    assert state == "minecraft:stone[a=1,b=2]"


def test_parse_block_state_id_parser_keeps_latest_duplicate_and_empty_values() -> None:
    base, props = _parse_block_state_id("minecraft:oak_log[a=1,b,a=3,]")
    assert base == "minecraft:oak_log"
    assert props == {"a": "3", "b": ""}


def test_build_place_block_id_for_face_maps_torches_to_wall_torches() -> None:
    assert _build_place_block_id_for_face("minecraft:torch", (1, 0, 0)) == "minecraft:wall_torch[facing=east]"
    assert _build_place_block_id_for_face("minecraft:torch", (-1, 0, 0)) == "minecraft:wall_torch[facing=west]"
    assert _build_place_block_id_for_face("minecraft:torch", (0, 0, -1)) == "minecraft:wall_torch[facing=north]"
    assert _build_place_block_id_for_face("minecraft:torch", (0, 0, 1)) == "minecraft:wall_torch[facing=south]"


def test_build_place_block_id_for_face_keeps_floor_torch() -> None:
    assert _build_place_block_id_for_face("minecraft:torch", (0, 1, 0)) == "minecraft:torch"
    # Unknown/unsupported faces keep the original id.
    assert _build_place_block_id_for_face("minecraft:torch", (0, -1, 0)) == "minecraft:torch"
