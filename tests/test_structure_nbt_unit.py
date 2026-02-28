from __future__ import annotations

import gzip
import io
from pathlib import Path
import pytest
import nbtlib

from enderterm.blockstate import _block_state_id_from_json_state
from enderterm.core_types import BlockInstance, Structure
from enderterm.core_types import BlockEntityInstance
from enderterm.structure_nbt import (
    _nbt_to_plain,
    apply_render_mode,
    filter_surface_blocks,
    load_nbt,
    load_nbt_bytes,
    parse_structure,
    structure_to_nbt_root,
)


def _solid_cube_blocks(
    size: int,
    *,
    overrides: dict[tuple[int, int, int], str] | None = None,
) -> tuple[BlockInstance, ...]:
    blocks: list[BlockInstance] = []
    override_map = overrides or {}
    for x in range(size):
        for y in range(size):
            for z in range(size):
                block_id = override_map.get((x, y, z), "minecraft:stone")
                blocks.append(BlockInstance(pos=(x, y, z), block_id=block_id, color_key=block_id))
    return tuple(blocks)


def test_structure_nbt_roundtrip_smoke() -> None:
    structure = Structure(
        size=(2, 2, 2),
        blocks=(
            BlockInstance(pos=(0, 0, 0), block_id="minecraft:stone", color_key="minecraft:stone"),
            BlockInstance(pos=(1, 1, 1), block_id="minecraft:oak_planks", color_key="minecraft:oak_planks"),
        ),
    )

    root, _offset = structure_to_nbt_root(structure)
    reparsed = parse_structure(root)

    assert reparsed.size == (2, 2, 2)
    assert {(b.pos, b.block_id) for b in reparsed.blocks} == {
        ((0, 0, 0), "minecraft:stone"),
        ((1, 1, 1), "minecraft:oak_planks"),
    }


def test_filter_surface_blocks_keeps_block_entities() -> None:
    blocks = _solid_cube_blocks(3)

    internal = (1, 1, 1)
    structure = Structure(size=(3, 3, 3), blocks=blocks)
    filtered = filter_surface_blocks(structure)
    assert internal not in {b.pos for b in filtered.blocks}

    with_entity = Structure(
        size=(3, 3, 3),
        blocks=blocks,
        block_entities=(BlockEntityInstance(pos=internal, nbt={"id": "minecraft:chest"}),),
    )
    filtered2 = filter_surface_blocks(with_entity)
    assert internal in {b.pos for b in filtered2.blocks}
    assert filtered2.block_entities[0].pos == internal


def test_filter_surface_blocks_treats_jigsaw_as_air_for_surface_detection() -> None:
    blocks = _solid_cube_blocks(5, overrides={(2, 2, 2): "minecraft:jigsaw"})

    structure = Structure(size=(5, 5, 5), blocks=blocks)
    filtered = filter_surface_blocks(structure)
    kept = {b.pos for b in filtered.blocks}

    # This block is internal to the solid cube, but becomes "surface" because its +Z neighbor is a jigsaw.
    assert (2, 2, 1) in kept
    # This block is internal and not adjacent to the jigsaw, so it stays culled.
    assert (1, 1, 1) not in kept


def test_filter_surface_blocks_sparse_fallback_matches_dense_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import enderterm.structure_nbt as sn

    blocks = []
    for x in range(3):
        for y in range(3):
            for z in range(3):
                blocks.append(BlockInstance(pos=(x, y, z), block_id="minecraft:stone", color_key="minecraft:stone"))

    structure = Structure(size=(3, 3, 3), blocks=tuple(blocks))
    dense_positions = {b.pos for b in sn.filter_surface_blocks(structure).blocks}
    assert (1, 1, 1) not in dense_positions

    # Force sparse fallback and ensure behavior remains consistent with dense path.
    monkeypatch.setattr(sn, "_MAX_SURFACE_OCCUPANCY_VOLUME", 1)
    sparse_positions = {b.pos for b in sn.filter_surface_blocks(structure).blocks}

    assert sparse_positions == dense_positions


def test_structure_to_nbt_root_returns_offset_and_normalizes_block_positions() -> None:
    structure = Structure(
        size=(1, 1, 1),
        blocks=(
            BlockInstance(pos=(5, 5, 5), block_id="minecraft:stone", color_key="minecraft:stone"),
            BlockInstance(pos=(6, 5, 5), block_id="minecraft:oak_planks", color_key="minecraft:oak_planks"),
        ),
    )

    root, offset = structure_to_nbt_root(structure)
    assert offset == (5, 5, 5)
    assert [int(v) for v in root["size"]] == [2, 1, 1]

    block_positions = {tuple(int(v) for v in b["pos"]) for b in root["blocks"]}
    assert block_positions == {(0, 0, 0), (1, 0, 0)}


def test_apply_render_mode_auto_switches_to_surface_over_threshold() -> None:
    internal = (1, 1, 1)
    structure = Structure(size=(3, 3, 3), blocks=_solid_cube_blocks(3))

    auto_filtered = apply_render_mode(structure, "auto", auto_threshold=1)
    assert internal not in {b.pos for b in auto_filtered.blocks}

    auto_full = apply_render_mode(structure, "auto", auto_threshold=1000)
    assert internal in {b.pos for b in auto_full.blocks}


def test_apply_render_mode_rejects_unknown_modes() -> None:
    structure = Structure(size=(1, 1, 1), blocks=tuple())
    with pytest.raises(ValueError, match="Unknown render mode"):
        apply_render_mode(structure, "nope")


def test_parse_structure_sets_jigsaw_color_key_from_pool_and_skips_block_entity() -> None:
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([2, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound(
                        {
                            "Name": nbtlib.String("minecraft:jigsaw"),
                            "Properties": nbtlib.Compound({"orientation": nbtlib.String("east_up")}),
                        }
                    ),
                    nbtlib.Compound({"Name": nbtlib.String("minecraft:chest")}),
                ]
            ),
            "blocks": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound(
                        {
                            "pos": nbtlib.List[nbtlib.Int]([0, 0, 0]),
                            "state": nbtlib.Int(0),
                            "nbt": nbtlib.Compound({"pool": nbtlib.String("minecraft:test_pool")}),
                        }
                    ),
                    nbtlib.Compound(
                        {
                            "pos": nbtlib.List[nbtlib.Int]([1, 0, 0]),
                            "state": nbtlib.Int(1),
                            "nbt": nbtlib.Compound({"id": nbtlib.String("minecraft:chest")}),
                        }
                    ),
                ]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )

    structure = parse_structure(root)
    blocks = {(b.pos, b.block_id, b.color_key) for b in structure.blocks}
    assert ((0, 0, 0), "minecraft:jigsaw[orientation=east_up]", "minecraft:jigsaw|pool=minecraft:test_pool") in blocks
    assert ((1, 0, 0), "minecraft:chest", "minecraft:chest") in blocks
    assert [be.pos for be in structure.block_entities] == [(1, 0, 0)]


def test_parse_structure_ignores_air_and_structure_void() -> None:
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 3]),
            "palette": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound({"Name": nbtlib.String("minecraft:air")}),
                    nbtlib.Compound({"Name": nbtlib.String("minecraft:structure_void")}),
                    nbtlib.Compound({"Name": nbtlib.String("minecraft:stone")}),
                ]
            ),
            "blocks": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(0)}),
                    nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 1]), "state": nbtlib.Int(1)}),
                    nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 2]), "state": nbtlib.Int(2)}),
                ]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )

    structure = parse_structure(root)
    assert {(b.pos, b.block_id) for b in structure.blocks} == {((0, 0, 2), "minecraft:stone")}


def test_block_state_id_from_json_state_prefers_canonical_keys() -> None:
    state = _block_state_id_from_json_state(
        {
            "Name": "minecraft:stone",
            "name": "minecraft:dirt",
            "Properties": {"b": "2", "a": "1"},
            "properties": {"z": "9"},
        }
    )
    assert state == "minecraft:stone[a=1,b=2]"


def test_nbt_to_plain_converts_nbtlib_containers_to_python_types() -> None:
    raw = nbtlib.Compound(
        {
            "a": nbtlib.Int(1),
            "b": nbtlib.List[nbtlib.String]([nbtlib.String("x"), nbtlib.String("y")]),
            "c": nbtlib.Compound({"d": nbtlib.String("z")}),
        }
    )
    assert _nbt_to_plain(raw) == {"a": 1, "b": ["x", "y"], "c": {"d": "z"}}


def test_load_nbt_bytes_accepts_gzip_compressed_payloads() -> None:
    buf = io.BytesIO()
    nbtlib.File(nbtlib.Compound({"foo": nbtlib.Int(1)})).write(buf)
    gz = gzip.compress(buf.getvalue())

    root = load_nbt_bytes(gz)
    assert int(root.get("foo", 0)) == 1


def test_load_nbt_bytes_accepts_raw_payloads() -> None:
    buf = io.BytesIO()
    nbtlib.File(nbtlib.Compound({"foo": nbtlib.Int(7)})).write(buf)

    root = load_nbt_bytes(buf.getvalue())
    assert int(root.get("foo", 0)) == 7


def test_load_nbt_accepts_raw_and_gzip_files(tmp_path: Path) -> None:
    buf = io.BytesIO()
    nbtlib.File(nbtlib.Compound({"foo": nbtlib.Int(9)})).write(buf)
    raw_payload = buf.getvalue()

    raw_path = tmp_path / "structure_raw.nbt"
    gz_path = tmp_path / "structure_gzip.nbt"
    raw_path.write_bytes(raw_payload)
    gz_path.write_bytes(gzip.compress(raw_payload))

    assert int(load_nbt(raw_path).get("foo", 0)) == 9
    assert int(load_nbt(gz_path).get("foo", 0)) == 9


def test_structure_to_nbt_root_writes_air_palette_for_empty_structures() -> None:
    structure = Structure(size=(1, 2, 3), blocks=tuple())
    root, offset = structure_to_nbt_root(structure)
    assert offset == (0, 0, 0)
    assert [int(v) for v in root["size"]] == [1, 2, 3]
    assert str(root["palette"][0]["Name"]) == "minecraft:air"
    assert list(root["blocks"]) == []


def test_nbt_to_plain_handles_bytes_like_and_non_iterables() -> None:
    assert _nbt_to_plain(None) is None
    assert _nbt_to_plain(bytearray(b"hi")) == b"hi"
    assert isinstance(_nbt_to_plain(object()), str)


def test_parse_structure_rejects_missing_size() -> None:
    with pytest.raises(ValueError, match="missing/invalid 'size'"):
        parse_structure(nbtlib.Compound({}))


def test_parse_structure_rejects_non_integer_size_values() -> None:
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.String]([nbtlib.String("x"), nbtlib.String("1"), nbtlib.String("2")]),
            "palette": nbtlib.List[nbtlib.Compound]([]),
            "blocks": nbtlib.List[nbtlib.Compound]([]),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )
    with pytest.raises(ValueError, match="missing/invalid 'size'"):
        parse_structure(root)


def test_parse_structure_size_error_includes_observed_value() -> None:
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 2]),
            "palette": nbtlib.List[nbtlib.Compound]([]),
            "blocks": nbtlib.List[nbtlib.Compound]([]),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )
    with pytest.raises(ValueError, match=r"got="):
        parse_structure(root)


def test_parse_structure_skips_blocks_when_palette_is_empty() -> None:
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound]([]),
            "blocks": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(0)}),
                ]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )
    parsed = parse_structure(root)
    assert parsed.size == (1, 1, 1)
    assert parsed.blocks == ()
    assert parsed.block_entities == ()


def test_parse_structure_skips_invalid_blocks_and_handles_entities_variants() -> None:
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound({"Name": nbtlib.String("minecraft:oak_log"), "Properties": nbtlib.Compound({"axis": nbtlib.String("y")})}),
                ]
            ),
            "blocks": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(999)}),  # bad state idx
                    nbtlib.Compound({"state": nbtlib.Int(0)}),  # missing pos
                ]
            ),
            # Store as a plain Python list to ensure we can include non-Compound items.
            "entities": [
                123,  # not a Compound
                nbtlib.Compound({"pos": "nope"}),  # invalid pos type
                nbtlib.Compound({"pos": nbtlib.List[nbtlib.String]([nbtlib.String("x"), nbtlib.String("y"), nbtlib.String("z")])}),
                nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([1, 2, 3]), "nbt": "not a dict"}),
            ],
        }
    )

    parsed = parse_structure(root)
    assert parsed.blocks == ()
    assert len(parsed.entities) == 1
    assert parsed.entities[0].pos == (1.0, 2.0, 3.0)
    assert parsed.entities[0].nbt == {}


def test_parse_structure_skips_non_compound_and_non_numeric_block_positions() -> None:
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound]([nbtlib.Compound({"Name": nbtlib.String("minecraft:stone")})]),
            "blocks": [
                123,
                nbtlib.Compound({"pos": "abc", "state": nbtlib.Int(0)}),
                nbtlib.Compound(
                    {
                        "pos": nbtlib.List[nbtlib.String](
                            [nbtlib.String("x"), nbtlib.String("y"), nbtlib.String("z")]
                        ),
                        "state": nbtlib.Int(0),
                    }
                ),
                nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(0)}),
            ],
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )

    parsed = parse_structure(root)
    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].pos == (0, 0, 0)
    assert parsed.blocks[0].block_id == "minecraft:stone"


def test_filter_surface_blocks_returns_original_when_no_solids() -> None:
    empty = Structure(size=(1, 1, 1), blocks=tuple())
    assert filter_surface_blocks(empty) is empty


def test_apply_render_mode_surface_filters() -> None:
    structure = Structure(
        size=(2, 1, 1),
        blocks=(
            BlockInstance(pos=(0, 0, 0), block_id="minecraft:stone", color_key="minecraft:stone"),
            BlockInstance(pos=(1, 0, 0), block_id="minecraft:stone", color_key="minecraft:stone"),
        ),
    )
    out = apply_render_mode(structure, "surface")
    assert out.blocks


def test_structure_to_nbt_root_includes_palette_properties_for_blockstates() -> None:
    structure = Structure(
        size=(1, 1, 1),
        blocks=(BlockInstance(pos=(0, 0, 0), block_id="minecraft:oak_log[axis=y]", color_key="minecraft:oak_log"),),
    )
    root, _offset = structure_to_nbt_root(structure)
    assert "Properties" in root["palette"][0]


def test_parse_structure_entities_handles_nbt_to_plain_non_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    import enderterm.structure_nbt as sn

    monkeypatch.setattr(sn, "_nbt_to_plain", lambda _obj: "not a dict")
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound]([]),
            "blocks": nbtlib.List[nbtlib.Compound]([]),
            "entities": [nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "nbt": nbtlib.Compound({"id": nbtlib.String("minecraft:pig")})})],
        }
    )
    out = sn.parse_structure(root)
    assert out.entities and out.entities[0].nbt == {}
