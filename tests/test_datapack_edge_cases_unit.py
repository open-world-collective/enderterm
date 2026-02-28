from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Callable, TypeVar
import zipfile

import nbtlib
import pytest

from enderterm.datapack import (
    DatapackSource,
    JigsawDatapackIndex,
    PackStack,
    PackStackSource,
    _resource_id_from_structure_canonical,
    _resource_id_from_worldgen_canonical,
    iter_canonical_paths_in_source,
)

T = TypeVar("T")


def _assert_cached_call(loader: Callable[[str], T], resource_id: str) -> T:
    first = loader(resource_id)
    assert first is loader(resource_id)
    return first


def _make_source_and_index(tmp_path: Path) -> tuple[DatapackSource, JigsawDatapackIndex]:
    src = DatapackSource(tmp_path / "pack", None)
    return src, JigsawDatapackIndex(src)


def test_datapacksource_zip_maps_root_and_data_paths_and_caches_nbt(tmp_path: Path) -> None:
    buf = io.BytesIO()
    nbtlib.File(nbtlib.Compound({"foo": nbtlib.Int(1)})).write(buf)

    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("data/", "")  # directory entry (ignored)
        z.writestr("pack.mcmeta", b"{}")  # root-level mapping
        z.writestr("data/minecraft/structures/test.nbt", buf.getvalue())
        z.writestr("data/minecraft/worldgen/structure/a.json", b"{}")

    with zipfile.ZipFile(zip_path, "r") as z:
        src = DatapackSource(zip_path, z)
        assert src.has("pack.mcmeta") is True
        assert src.read("pack.mcmeta") == b"{}"
        assert src.read_json("data/minecraft/worldgen/structure/a.json") is None

        # Missing json caches to None.
        assert src.read_json("data/minecraft/worldgen/structure/missing.json") is None
        assert src.read_json("data/minecraft/worldgen/structure/missing.json") is None

        # read_nbt caches parsed results.
        root1 = src.read_nbt("data/minecraft/structures/test.nbt")
        root2 = src.read_nbt("data/minecraft/structures/test.nbt")
        assert root1 is root2

        with pytest.raises(FileNotFoundError):
            src.read("data/minecraft/structures/does_not_exist.nbt")

        with pytest.raises(RuntimeError):
            src.write("data/minecraft/worldgen/structure/x.json", b"{}")


def test_packstacksource_missing_paths_return_none_or_raise(tmp_path: Path) -> None:
    src = DatapackSource(tmp_path / "pack", None)
    stack = PackStackSource([src])

    ok_rel = "data/minecraft/worldgen/structure/ok.json"
    src.write(ok_rel, b'{"ok": 1}')
    assert stack.read(ok_rel) == b'{"ok": 1}'
    assert stack.read_json(ok_rel) == {"ok": 1}

    buf = io.BytesIO()
    nbtlib.File(nbtlib.Compound({"foo": nbtlib.Int(1)})).write(buf)
    nbt_rel = "data/minecraft/structures/ok.nbt"
    src.write(nbt_rel, buf.getvalue())
    assert stack.read_nbt(nbt_rel) is not None

    assert stack.has("data/minecraft/worldgen/structure/nope.json") is False
    assert stack.resolve_source("data/minecraft/worldgen/structure/nope.json") is None
    assert stack.read_json("data/minecraft/worldgen/structure/nope.json") is None
    assert stack.read_nbt("data/minecraft/structures/nope.nbt") is None
    with pytest.raises(FileNotFoundError):
        stack.read("data/minecraft/worldgen/structure/nope.json")


def test_packstacksource_prefers_work_source_over_stale_vendor_cache(tmp_path: Path) -> None:
    vendor = DatapackSource(tmp_path / "vendor", None)
    work = DatapackSource(tmp_path / "work", None)
    stack = PackStackSource([work, vendor])

    rel = "data/minecraft/worldgen/structure/shared.json"
    vendor.write(rel, b'{"owner":"vendor"}')

    # Seed vendor-cache entry before the work override exists.
    assert stack.resolve_source(rel) is vendor
    assert stack.read_json(rel) == {"owner": "vendor"}

    # Work file should win immediately, even with a prior vendor cache hit.
    work.write(rel, b'{"owner":"work"}')
    assert stack.resolve_source(rel) is work
    assert stack.read_json(rel) == {"owner": "work"}


def test_resource_id_helpers_handle_weird_inputs() -> None:
    class _BadStr:
        def __str__(self) -> str:  # pragma: no cover
            raise RuntimeError("no str")

    assert _resource_id_from_worldgen_canonical(_BadStr(), kind_folder="template_pool", suffix=".json") is None  # type: ignore[arg-type]
    assert _resource_id_from_worldgen_canonical("data/minecraft/worldgen/template_pool/.json", kind_folder="template_pool", suffix=".json") is None

    assert _resource_id_from_structure_canonical(_BadStr()) is None  # type: ignore[arg-type]
    assert _resource_id_from_structure_canonical("data/x") is None
    assert _resource_id_from_structure_canonical("data/minecraft/nope/foo.nbt") is None
    assert _resource_id_from_structure_canonical("data/minecraft/structures/.nbt") is None


def test_iter_canonical_paths_handles_relative_to_exceptions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "pack"
    (root / "data/minecraft/worldgen/structure").mkdir(parents=True, exist_ok=True)
    (root / "data/minecraft/worldgen/structure/a.json").write_text("{}", encoding="utf-8")
    src = DatapackSource(root, None)

    monkeypatch.setattr(Path, "relative_to", lambda _self, _other: (_ for _ in ()).throw(RuntimeError("boom")))
    assert list(iter_canonical_paths_in_source(src)) == []


def test_list_processor_lists_and_template_pools_include_worldgen_ids(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "vendor"
    work_dir = tmp_path / "work"

    (vendor_dir / "data/minecraft/worldgen/processor_list").mkdir(parents=True, exist_ok=True)
    (vendor_dir / "data/minecraft/worldgen/processor_list/p.json").write_text("{}", encoding="utf-8")
    # Non-pool file should be ignored by list_template_pools.
    (vendor_dir / "data/minecraft/worldgen/structure/s.json").parent.mkdir(parents=True, exist_ok=True)
    (vendor_dir / "data/minecraft/worldgen/structure/s.json").write_text("{}", encoding="utf-8")
    (work_dir / "data/minecraft/worldgen/template_pool").mkdir(parents=True, exist_ok=True)
    (work_dir / "data/minecraft/worldgen/template_pool/a.json").write_text("{}", encoding="utf-8")

    vendor = DatapackSource(vendor_dir, None)
    stack = PackStack(work_dir=work_dir, vendors=[vendor])

    from enderterm.datapack import list_processor_lists, list_template_pools

    assert list_processor_lists(stack) == ["minecraft:empty", "minecraft:p"]
    assert ("minecraft:a", "work") in list_template_pools(stack)


def test_jigsawdatapackindex_caches_and_handles_missing_and_bad_templates(tmp_path: Path) -> None:
    src, index = _make_source_and_index(tmp_path)

    # Processor list cache hit.
    proc_rel = "data/minecraft/worldgen/processor_list/p.json"
    src.write(proc_rel, b"{\"processors\": []}")
    _assert_cached_call(index.load_processor_list, "minecraft:p")

    # Pool cache hit.
    pool_rel = "data/minecraft/worldgen/template_pool/t.json"
    src.write(pool_rel, b"{\"fallback\":\"minecraft:empty\",\"elements\":[]}")
    _assert_cached_call(index.load_pool, "minecraft:t")

    # Empty ids short-circuit to cached defaults.
    empty_pipeline = _assert_cached_call(index.load_processor_list, "minecraft:empty")
    assert empty_pipeline.ignore_base == frozenset()
    assert empty_pipeline.processors == tuple()
    assert empty_pipeline.unhandled_processors == tuple()

    empty_pool = _assert_cached_call(index.load_pool, "minecraft:empty")
    assert empty_pool.elements == tuple()
    assert empty_pool.fallback == "minecraft:empty"

    # Missing template caches None.
    assert _assert_cached_call(index.load_template, "minecraft:missing") is None

    # Bad template NBT parses but template extraction fails -> caches None.
    bad_root = nbtlib.Compound({"size": nbtlib.List[nbtlib.Int]([1, 2])})  # invalid size
    buf = io.BytesIO()
    nbtlib.File(bad_root).write(buf)
    src.write("data/minecraft/structures/bad.nbt", buf.getvalue())
    assert _assert_cached_call(index.load_template, "minecraft:bad") is None

    # Successful template caches non-None results.
    good_root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound]([nbtlib.Compound({"Name": nbtlib.String("minecraft:stone")})]),
            "blocks": nbtlib.List[nbtlib.Compound](
                [nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(0)})]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )
    buf2 = io.BytesIO()
    nbtlib.File(good_root).write(buf2)
    src.write("data/minecraft/structures/good.nbt", buf2.getvalue())
    assert _assert_cached_call(index.load_template, "minecraft:good") is not None


def test_processor_and_pool_parsing_edge_cases(tmp_path: Path) -> None:
    src, index = _make_source_and_index(tmp_path)

    proc_rel = "data/minecraft/worldgen/processor_list/edge.json"
    payload = {
        "processors": [
            123,  # not a dict
            {"processor_type": 123},  # invalid type
            {"processor_type": "minecraft:rule", "rules": {}},  # rules not a list
            {
                "processor_type": "minecraft:rule",
                "rules": [
                    123,  # not a dict
                    {"input_predicate": [1], "output_state": "minecraft:stone"},
                    {"input_predicate": {"predicate_type": 123}, "output_state": "minecraft:stone"},
                    {"input_predicate": {"predicate_type": "minecraft:block_match"}, "output_state": None},
                    {"input_predicate": {"predicate_type": "minecraft:block_match", "blocks": []}, "output_state": "minecraft:stone"},
                    {"input_predicate": {"predicate_type": "minecraft:blockstate_match", "block_states": []}, "output_state": "minecraft:stone"},
                    {
                        "input_predicate": {"predicate_type": "minecraft:block_match", "block": "minecraft:stone"},
                        "location_predicate": {"predicate_type": "minecraft:block_match", "blocks": []},
                        "output_state": "minecraft:stone",
                    },
                    {
                        "input_predicate": {
                            "predicate_type": "minecraft:random_block_match",
                            "block": "minecraft:stone",
                            "probability": "oops",
                        },
                        "output_state": "minecraft:dirt",
                    },
                    {
                        "input_predicate": {
                            "predicate_type": "minecraft:blockstate_match",
                            "block_states": ["minecraft:stone", {"name": "minecraft:dirt"}],
                        },
                        "output_state": "minecraft:stone",
                    },
                    {
                        "input_predicate": {
                            "predicate_type": "minecraft:block_match",
                            "blocks": [{"Name": "minecraft:dirt"}],
                        },
                        "output_state": "minecraft:stone",
                    },
                ],
            },
            {
                "processor_type": "minecraft:rule",
                "rules": [
                    {"input_predicate": {"predicate_type": "minecraft:block_match", "blocks": []}, "output_state": "minecraft:stone"},
                ],
            },
        ]
    }
    src.write(proc_rel, json.dumps(payload).encode("utf-8"))
    pipeline = _assert_cached_call(index.load_processor_list, "minecraft:edge")

    pool_rel = "data/minecraft/worldgen/template_pool/edge.json"
    pool_payload = {
        "fallback": "minecraft:empty",
        "elements": [
            123,
            {"weight": 1, "element": "not a dict"},
        ],
    }
    src.write(pool_rel, json.dumps(pool_payload).encode("utf-8"))
    pool = _assert_cached_call(index.load_pool, "minecraft:edge")
    assert pool.elements == tuple()
