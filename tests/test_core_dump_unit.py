from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import nbtlib
import pytest


def _write_min_structure_nbt(path: Path, *, block_name: str = "minecraft:stone", gzipped: bool = False) -> None:
    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound]([nbtlib.Compound({"Name": nbtlib.String(block_name)})]),
            "blocks": nbtlib.List[nbtlib.Compound](
                [nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(0)})]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )
    nbtlib.File(root).save(path, gzipped=gzipped)  # type: ignore[arg-type]


def test_dump_structure_core_json_writes_core_dump(nbttool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nbttool, "DEFAULT_PARAM_PATH", tmp_path / "params.json")

    nbt_path = tmp_path / "one.nbt"
    _write_min_structure_nbt(nbt_path, gzipped=True)

    out_path = tmp_path / "core.json"
    nbttool.dump_structure_core_json(
        nbt_path,
        mode="full",
        auto_threshold=999_999,
        textured=False,
        minecraft_jar=None,
        out_path=out_path,
    )

    obj = json.loads(out_path.read_text(encoding="utf-8"))
    assert obj["schema"] == "enderterm.core_dump"
    assert obj["schema_version"] == 1
    assert obj["scene_id"] == str(nbt_path)
    assert obj["textured"] is False
    assert isinstance(obj["mesh"]["parts"], list)
    assert obj["blocks"] == [{"pos": [0, 0, 0], "id": "minecraft:stone", "color_key": "minecraft:stone"}]


def test_dump_datapack_core_json_selects_by_label_substring(
    nbttool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbttool, "DEFAULT_PARAM_PATH", tmp_path / "params.json")

    datapack_dir = tmp_path / "pack"
    a_path = datapack_dir / "data" / "minecraft" / "structures" / "a.nbt"
    b_path = datapack_dir / "data" / "minecraft" / "structures" / "b.nbt"
    a_path.parent.mkdir(parents=True, exist_ok=True)
    b_path.parent.mkdir(parents=True, exist_ok=True)
    _write_min_structure_nbt(a_path, block_name="minecraft:stone")
    _write_min_structure_nbt(b_path, block_name="minecraft:diamond_block")

    out_path = tmp_path / "core.json"
    nbttool.dump_datapack_core_json(
        datapack_dir,
        mode="full",
        auto_threshold=999_999,
        textured=False,
        minecraft_jar=None,
        select="b",
        out_path=out_path,
    )

    obj = json.loads(out_path.read_text(encoding="utf-8"))
    assert obj["scene_id"] == "minecraft/b"
    assert obj["blocks"][0]["id"] == "minecraft:diamond_block"


def test_core_scene_json_text_matches_dump_core_json_output(tmp_path: Path) -> None:
    from enderterm.core_dump import CoreFxData, CoreMeshBuild, CoreSceneData, _core_scene_json_text, dump_core_json

    scene = CoreSceneData(
        schema_version=1,
        scene_id="scene",
        textured=False,
        minecraft_jar=None,
        mesh=CoreMeshBuild(
            pivot_world=(0.0, 0.0, 0.0),
            bounds_min=(0.0, 0.0, 0.0),
            bounds_max=(1.0, 1.0, 1.0),
            initial_distance=2.0,
            meshes=(),
        ),
        fx=CoreFxData(defs=(), values=(("z", 1), ("a", 2.5))),
        blocks=(((0, 0, 0), "minecraft:stone", "minecraft:stone"),),
    )

    out_path = tmp_path / "core.json"
    dump_core_json(scene, out_path)

    text = out_path.read_text(encoding="utf-8")
    assert text == _core_scene_json_text(scene)
    assert text.endswith("\n")


def test_select_datapack_item_index_case_insensitive_and_default() -> None:
    from enderterm.core_dump import _select_datapack_item_index

    labels = ["minecraft/a", "minecraft/Beta", "demo/gamma"]
    assert _select_datapack_item_index(labels, None) == 0
    assert _select_datapack_item_index(labels, "beT") == 1
    assert _select_datapack_item_index(labels, "missing") == 0


def test_select_datapack_item_uses_label_matching() -> None:
    from enderterm.core_dump import _select_datapack_item

    items = [
        ("minecraft/a.usdz", "a.nbt"),
        ("minecraft/Beta.usdz", "b.nbt"),
        ("demo/gamma.usdz", "c.nbt"),
    ]
    assert _select_datapack_item(items, None) == items[0]
    assert _select_datapack_item(items, "beT") == items[1]
    assert _select_datapack_item(items, "missing") == items[0]
