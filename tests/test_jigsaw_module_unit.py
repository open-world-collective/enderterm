from __future__ import annotations

import nbtlib
import pytest

from enderterm.core_types import BlockEntityInstance, BlockInstance
from enderterm.jigsaw import JigsawConnector, apply_jigsaw_final_states_to_blocks
from enderterm.jigsaw import _block_id_from_jigsaw_final_state, _parse_jigsaw_orientation, extract_jigsaw_connectors
from enderterm.jigsaw import parse_structure_template


def test_extract_jigsaw_connectors_parses_orientation_and_tag_fields() -> None:
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound(
                        {
                            "Name": nbtlib.String("minecraft:jigsaw"),
                            "Properties": nbtlib.Compound({"orientation": nbtlib.String("east_up")}),
                        }
                    )
                ]
            ),
            "blocks": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound(
                        {
                            "pos": nbtlib.List[nbtlib.Int]([0, 0, 0]),
                            "state": nbtlib.Int(0),
                            "nbt": nbtlib.Compound(
                                {
                                    "pool": nbtlib.String("minecraft:test_pool"),
                                    "target": nbtlib.String("socket"),
                                    "name": nbtlib.String("parent"),
                                    "final_state": nbtlib.String("minecraft:stone"),
                                    "joint": nbtlib.String("aligned"),
                                }
                            ),
                        }
                    )
                ]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )

    (conn,) = extract_jigsaw_connectors(root, template_id="tmpl")
    assert conn.pos == (0, 0, 0)
    assert conn.front == (1, 0, 0)
    assert conn.top == (0, 1, 0)
    assert conn.pool == "minecraft:test_pool"
    assert conn.target == "socket"
    assert conn.name == "parent"
    assert conn.final_state == "minecraft:stone"
    assert conn.source == "tmpl"


def test_apply_jigsaw_final_states_replaces_jigsaw_block_and_clears_block_entity() -> None:
    pos = (0, 0, 0)
    blocks_by_pos = {pos: BlockInstance(pos=pos, block_id="minecraft:jigsaw", color_key="minecraft:jigsaw")}
    block_entities_by_pos = {pos: BlockEntityInstance(pos=pos, nbt={"id": "minecraft:chest"})}
    connectors = [
        JigsawConnector(
            pos=pos,
            front=(1, 0, 0),
            top=(0, 1, 0),
            projection="rigid",
            pool="minecraft:empty",
            target="minecraft:empty",
            name="socket",
            final_state="minecraft:stone",
            joint="aligned",
            source="tmpl",
        )
    ]

    apply_jigsaw_final_states_to_blocks(blocks_by_pos, block_entities_by_pos, connectors)
    assert blocks_by_pos[pos].block_id == "minecraft:stone"
    assert pos not in block_entities_by_pos


def test_apply_jigsaw_final_states_removes_block_for_air_final_state() -> None:
    pos = (0, 0, 0)
    blocks_by_pos = {pos: BlockInstance(pos=pos, block_id="minecraft:jigsaw", color_key="minecraft:jigsaw")}
    block_entities_by_pos = {pos: BlockEntityInstance(pos=pos, nbt={"id": "minecraft:chest"})}
    connectors = [
        JigsawConnector(
            pos=pos,
            front=(1, 0, 0),
            top=(0, 1, 0),
            projection="rigid",
            pool="minecraft:empty",
            target="minecraft:empty",
            name="socket",
            final_state="minecraft:air",
            joint="aligned",
            source="tmpl",
        )
    ]

    apply_jigsaw_final_states_to_blocks(blocks_by_pos, block_entities_by_pos, connectors)
    assert pos not in blocks_by_pos
    assert pos not in block_entities_by_pos


def test_parse_jigsaw_orientation_uses_facing_when_orientation_missing() -> None:
    assert _parse_jigsaw_orientation("minecraft:jigsaw[facing=north]") == ((0, 0, -1), (0, 1, 0))


def test_parse_jigsaw_orientation_rejects_non_jigsaw_and_missing_props() -> None:
    assert _parse_jigsaw_orientation("minecraft:stone") is None
    assert _parse_jigsaw_orientation("minecraft:jigsaw") is None


def test_parse_jigsaw_orientation_rejects_invalid_values() -> None:
    assert _parse_jigsaw_orientation("minecraft:jigsaw[facing=sideways]") is None
    assert _parse_jigsaw_orientation("minecraft:jigsaw[orientation=north_nope]") is None


def test_extract_jigsaw_connectors_skips_invalid_state_and_pos() -> None:
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound(
                        {
                            "Name": nbtlib.String("minecraft:jigsaw"),
                            "Properties": nbtlib.Compound({"orientation": nbtlib.String("east_up")}),
                        }
                    )
                ]
            ),
            "blocks": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(999)}),
                    nbtlib.Compound({"state": nbtlib.Int(0)}),
                ]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )
    assert extract_jigsaw_connectors(root, template_id="tmpl") == ()


def test_extract_jigsaw_connectors_skips_invalid_orientation() -> None:
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound(
                        {
                            "Name": nbtlib.String("minecraft:jigsaw"),
                            "Properties": nbtlib.Compound({"facing": nbtlib.String("sideways")}),
                        }
                    )
                ]
            ),
            "blocks": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(0)}),
                ]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )
    assert extract_jigsaw_connectors(root, template_id="tmpl") == ()


def test_block_id_from_jigsaw_final_state_handles_invalid_inputs() -> None:
    assert _block_id_from_jigsaw_final_state(123) is None  # type: ignore[arg-type]
    assert _block_id_from_jigsaw_final_state("") is None
    assert _block_id_from_jigsaw_final_state("   ") is None
    assert _block_id_from_jigsaw_final_state("{foo:1b}") is None


def test_apply_jigsaw_final_states_ignores_empty_final_state() -> None:
    pos = (0, 0, 0)
    blocks_by_pos = {pos: BlockInstance(pos=pos, block_id="minecraft:jigsaw", color_key="minecraft:jigsaw")}
    block_entities_by_pos = {}
    connectors = [
        JigsawConnector(
            pos=pos,
            front=(1, 0, 0),
            top=(0, 1, 0),
            projection="rigid",
            pool="minecraft:empty",
            target="minecraft:empty",
            name="socket",
            final_state="   ",
            joint="aligned",
            source="tmpl",
        )
    ]
    apply_jigsaw_final_states_to_blocks(blocks_by_pos, block_entities_by_pos, connectors)
    assert blocks_by_pos[pos].block_id.startswith("minecraft:jigsaw")


def test_parse_structure_template_rejects_missing_size() -> None:
    with pytest.raises(ValueError, match="missing/invalid 'size'"):
        parse_structure_template(nbtlib.Compound({}), template_id="tmpl")
