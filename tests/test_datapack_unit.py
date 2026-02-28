from __future__ import annotations

import io
import json
from pathlib import Path
import zipfile

import nbtlib
import pytest

from enderterm.datapack import (
    DatapackSource,
    JigsawDatapackIndex,
    PackStack,
    _resource_id_from_structure_canonical,
    _resource_id_from_worldgen_canonical,
    canonical_processor_list_json,
    canonical_structure_template_nbt,
    canonical_template_pool_json,
    canonical_worldgen_structure_json,
    iter_canonical_paths_in_source,
    list_processor_lists,
    list_template_pools,
    list_structure_templates,
    list_worldgen_structures,
    list_worldgen_jigsaw_structures,
)
from enderterm.jigsaw import CappedProcessor


def _write_json(root: Path, canonical_rel: str, obj: object) -> None:
    path = root / canonical_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _rule_input_probability_from_single_rule_predicate(tmp_path: Path, input_predicate: dict[str, object]) -> float:
    root = tmp_path / "pack"
    src = DatapackSource(root, None)
    index = JigsawDatapackIndex(src)

    canonical = "data/minecraft/worldgen/processor_list/test.json"
    payload = {
        "processors": [
            {
                "processor_type": "minecraft:rule",
                "rules": [{"input_predicate": input_predicate, "output_state": "minecraft:dirt"}],
            }
        ]
    }
    src.write(canonical, json.dumps(payload).encode("utf-8"))

    pipeline = index.load_processor_list("minecraft:test")
    (proc,) = pipeline.processors
    (rule,) = proc.rules
    return rule.input_probability


def test_packstack_overlay_prefers_work_pack(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "vendor"
    canonical = "data/minecraft/worldgen/structure/example.json"
    _write_json(vendor_dir, canonical, {"from": "vendor"})

    vendor = DatapackSource(vendor_dir, None)
    stack = PackStack(work_dir=tmp_path / "work-pack", vendors=[vendor])

    assert stack.source.read_json(canonical) == {"from": "vendor"}

    stack.work.write(canonical, b'{"from":"work"}')
    assert stack.source.read_json(canonical) == {"from": "work"}
    assert stack.resolve_owner(canonical) is stack.work


def test_packstack_fork_into_work_copies_vendor_bytes(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "vendor"
    canonical = "data/minecraft/worldgen/template_pool/example.json"
    payload = b'{"hello":"vendor"}'
    (vendor_dir / canonical).parent.mkdir(parents=True, exist_ok=True)
    (vendor_dir / canonical).write_bytes(payload)

    vendor = DatapackSource(vendor_dir, None)
    stack = PackStack(work_dir=tmp_path / "work-pack", vendors=[vendor])

    assert stack.work.has(canonical) is False
    assert stack.fork_into_work(canonical) is True
    assert stack.work.has(canonical) is True
    assert stack.work.read(canonical) == payload

    # Second fork is a no-op.
    assert stack.fork_into_work(canonical) is False


def test_iter_canonical_paths_in_source_lists_expected_files(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    expected = {
        "data/minecraft/worldgen/template_pool/pool.json",
        "data/minecraft/worldgen/processor_list/proc.json",
        "data/minecraft/worldgen/structure/thing.json",
        "data/minecraft/structures/a.nbt",
        "data/minecraft/structure/b.nbt",
    }
    for rel in expected:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}" if rel.endswith(".json") else b"")

    src = DatapackSource(root, None)
    got = set(iter_canonical_paths_in_source(src))
    assert expected.issubset(got)


def test_template_pool_json_parsing_filters_invalid_elements(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    vendor = DatapackSource(root, None)
    index = JigsawDatapackIndex(vendor)

    canonical = "data/minecraft/worldgen/template_pool/test_pool.json"
    payload = {
        "fallback": "minecraft:empty",
        "elements": [
            {"weight": 0, "element": {"element_type": "minecraft:single_pool_element", "location": "minecraft:foo"}},
            {"weight": 2, "element": {"element_type": "minecraft:empty_pool_element"}},
            {"weight": 3, "element": {"element_type": "minecraft:single_pool_element", "location": "minecraft:empty"}},
            {"weight": 4, "element": {"element_type": "minecraft:feature_pool_element", "location": "minecraft:bar"}},
            {"element": {"element_type": "minecraft:single_pool_element", "location": "minecraft:baz"}},
        ],
    }
    vendor.write(canonical, json.dumps(payload).encode("utf-8"))

    pool = index.load_pool("minecraft:test_pool")
    assert pool.fallback == "minecraft:empty"
    assert [(e.location_id, e.weight) for e in pool.elements] == [("minecraft:foo", 1), ("minecraft:baz", 1)]


def test_processor_list_parsing_rule_ignore_and_unhandled(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    src = DatapackSource(root, None)
    index = JigsawDatapackIndex(src)

    canonical = "data/minecraft/worldgen/processor_list/test.json"
    payload = {
        "processors": [
            {
                "processor_type": "minecraft:block_ignore",
                "blocks": ["minecraft:stone", {"Name": "minecraft:foo[bar=baz]"}],
            },
            {
                "processor_type": "minecraft:rule",
                "rules": [
                    {
                        "input_predicate": {"predicate_type": "minecraft:unknown_predicate"},
                        "output_state": "minecraft:air",
                    },
                    {
                        "input_predicate": {"predicate_type": "minecraft:block_match", "blocks": ["minecraft:stone"]},
                        # omit location_predicate to exercise defaulting to always_true
                        "output_state": "minecraft:dirt",
                    },
                ],
            },
            {
                "processor_type": "minecraft:some_future_processor",
            },
        ]
    }
    src.write(canonical, json.dumps(payload).encode("utf-8"))

    pipeline = index.load_processor_list("minecraft:test")

    assert pipeline.ignore_base == frozenset({"minecraft:stone", "minecraft:foo"})
    assert pipeline.unhandled_processors == ("minecraft:some_future_processor",)
    assert len(pipeline.processors) == 1

    proc = pipeline.processors[0]
    assert proc.unhandled_predicates == ("minecraft:unknown_predicate",)
    assert len(proc.rules) == 1
    assert proc.rules[0].output_state_id == "minecraft:dirt"


def test_datapacksource_invalidate_refreshes_json_cache(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    canonical = "data/minecraft/worldgen/structure/example.json"
    src = DatapackSource(root, None)

    _write_json(root, canonical, {"x": 1})
    assert src.read_json(canonical) == {"x": 1}

    _write_json(root, canonical, {"x": 2})
    assert src.read_json(canonical) == {"x": 1}

    src.invalidate(canonical)
    assert src.read_json(canonical) == {"x": 2}


def test_datapacksource_read_json_avoids_redundant_has_lookup(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    canonical = "data/minecraft/worldgen/structure/example.json"

    class CountingDatapackSource(DatapackSource):
        def __init__(self, datapack_path: Path) -> None:
            super().__init__(datapack_path, None)
            self.has_calls = 0
            self.read_calls = 0

        def has(self, canonical_rel: str) -> bool:
            self.has_calls += 1
            return super().has(canonical_rel)

        def read(self, canonical_rel: str) -> bytes:
            self.read_calls += 1
            return super().read(canonical_rel)

    _write_json(root, canonical, {"x": 1})
    src = CountingDatapackSource(root)

    assert src.read_json(canonical) == {"x": 1}
    assert src.read_calls == 1
    assert src.has_calls == 0

    # Cache hit should also avoid filesystem checks.
    assert src.read_json(canonical) == {"x": 1}
    assert src.read_calls == 1
    assert src.has_calls == 0


def test_datapacksource_zip_maps_canonical_paths(tmp_path: Path) -> None:
    zip_path = tmp_path / "pack.zip"
    canonical = "data/minecraft/worldgen/template_pool/pool.json"
    pool_payload = b'{"fallback":"minecraft:empty","elements":[]}'

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("pack.mcmeta", "{}")
        zf.writestr(f"prefix/{canonical}", pool_payload)

    with zipfile.ZipFile(zip_path, "r") as zf:
        src = DatapackSource(zip_path, zf)
        assert src.has("pack.mcmeta") is True
        assert src.read("pack.mcmeta") == b"{}"
        assert src.has(canonical) is True
        assert src.read(canonical) == pool_payload
        assert canonical in set(iter_canonical_paths_in_source(src))


def test_list_worldgen_jigsaw_structures_filters_non_jigsaw_and_invalid_start_pool(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "vendor"
    vendor = DatapackSource(vendor_dir, None)
    stack = PackStack(work_dir=tmp_path / "work-pack", vendors=[vendor])

    _write_json(
        vendor_dir,
        "data/minecraft/worldgen/structure/not_jigsaw.json",
        {"type": "minecraft:random_spread"},
    )
    _write_json(
        vendor_dir,
        "data/minecraft/worldgen/structure/missing_start_pool.json",
        {"type": "minecraft:jigsaw"},
    )
    _write_json(
        vendor_dir,
        "data/minecraft/worldgen/structure/empty_start_pool.json",
        {"type": "minecraft:jigsaw", "start_pool": ""},
    )
    _write_json(
        vendor_dir,
        "data/minecraft/worldgen/structure/non_string_start_pool.json",
        {"type": "minecraft:jigsaw", "start_pool": ["minecraft:test_pool"]},
    )
    _write_json(
        vendor_dir,
        "data/minecraft/worldgen/structure/ok.json",
        {"type": "minecraft:jigsaw", "start_pool": "minecraft:test_pool"},
    )

    assert list_worldgen_jigsaw_structures(stack) == ["minecraft:ok"]


def test_jigsawdatapackindex_load_template_checks_structure_folder_fallback(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    src = DatapackSource(root, None)

    nbt_root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound({"Name": nbtlib.String("minecraft:stone")}),
                ]
            ),
            "blocks": nbtlib.List[nbtlib.Compound](
                [
                    nbtlib.Compound(
                        {
                            "pos": nbtlib.List[nbtlib.Int]([0, 0, 0]),
                            "state": nbtlib.Int(0),
                        }
                    )
                ]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )
    buf = io.BytesIO()
    nbtlib.File(nbt_root).write(buf)

    # Only write into the singular `structure/` folder to ensure we fall back
    # after missing `structures/`.
    src.write("data/minecraft/structure/test.nbt", buf.getvalue())

    index = JigsawDatapackIndex(src)
    tmpl = index.load_template("minecraft:test")
    assert tmpl is not None
    assert {(b.pos, b.block_id) for b in tmpl.blocks} == {((0, 0, 0), "minecraft:stone")}


def test_processor_list_parsing_capped_limit_is_clamped_to_non_negative(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    src = DatapackSource(root, None)
    index = JigsawDatapackIndex(src)

    canonical = "data/minecraft/worldgen/processor_list/test.json"
    payload = {
        "processors": [
            {
                "processor_type": "minecraft:capped",
                "limit": -3,
                "delegate": {
                    "processor_type": "minecraft:rule",
                    "rules": [
                        {
                            "input_predicate": {"predicate_type": "minecraft:block_match", "blocks": ["minecraft:stone"]},
                            "output_state": "minecraft:dirt",
                        }
                    ],
                },
            }
        ]
    }
    src.write(canonical, json.dumps(payload).encode("utf-8"))

    pipeline = index.load_processor_list("minecraft:test")
    assert pipeline.unhandled_processors == tuple()
    assert len(pipeline.processors) == 1
    assert isinstance(pipeline.processors[0], CappedProcessor)
    assert pipeline.processors[0].limit == 0


def test_processor_list_parsing_capped_without_delegate_is_marked_unhandled(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    src = DatapackSource(root, None)
    index = JigsawDatapackIndex(src)

    canonical = "data/minecraft/worldgen/processor_list/test.json"
    payload = {"processors": [{"processor_type": "minecraft:capped", "limit": 1}]}
    src.write(canonical, json.dumps(payload).encode("utf-8"))

    pipeline = index.load_processor_list("minecraft:test")
    assert pipeline.processors == tuple()
    assert pipeline.unhandled_processors == ("minecraft:capped(delegate)",)


def test_canonical_template_pool_json_handles_empty_and_default_namespace() -> None:
    assert canonical_template_pool_json("minecraft:empty") is None
    assert canonical_template_pool_json("foo") == "data/minecraft/worldgen/template_pool/foo.json"
    assert canonical_template_pool_json("qarl:pool/test") == "data/qarl/worldgen/template_pool/pool/test.json"


def test_canonical_processor_list_json_handles_empty_and_default_namespace() -> None:
    assert canonical_processor_list_json("minecraft:empty") is None
    assert canonical_processor_list_json("foo") == "data/minecraft/worldgen/processor_list/foo.json"
    assert canonical_processor_list_json("qarl:proc/test") == "data/qarl/worldgen/processor_list/proc/test.json"


def test_canonical_worldgen_structure_json_includes_default_namespace() -> None:
    assert canonical_worldgen_structure_json("village/plains") == "data/minecraft/worldgen/structure/village/plains.json"
    assert canonical_worldgen_structure_json("qarl:foo/bar") == "data/qarl/worldgen/structure/foo/bar.json"
    assert canonical_worldgen_structure_json("minecraft:empty") == "data/minecraft/worldgen/structure/empty.json"


def test_canonical_structure_template_nbt_returns_both_structure_paths() -> None:
    assert canonical_structure_template_nbt("minecraft:test") == [
        "data/minecraft/structures/test.nbt",
        "data/minecraft/structure/test.nbt",
    ]
    assert canonical_structure_template_nbt("qarl:foo/bar") == [
        "data/qarl/structures/foo/bar.nbt",
        "data/qarl/structure/foo/bar.nbt",
    ]


def test_list_processor_lists_includes_minecraft_empty_even_without_files(tmp_path: Path) -> None:
    vendor = DatapackSource(tmp_path / "vendor", None)
    stack = PackStack(work_dir=tmp_path / "work-pack", vendors=[vendor])
    assert list_processor_lists(stack) == ["minecraft:empty"]


def test_list_structure_templates_includes_structures_and_structure_folders(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "vendor"
    (vendor_dir / "data/minecraft/structures/foo.nbt").parent.mkdir(parents=True, exist_ok=True)
    (vendor_dir / "data/minecraft/structures/foo.nbt").write_bytes(b"")
    (vendor_dir / "data/qarl/structure/bar/baz.nbt").parent.mkdir(parents=True, exist_ok=True)
    (vendor_dir / "data/qarl/structure/bar/baz.nbt").write_bytes(b"")

    vendor = DatapackSource(vendor_dir, None)
    stack = PackStack(work_dir=tmp_path / "work-pack", vendors=[vendor])

    assert list_structure_templates(stack) == ["minecraft:foo", "qarl:bar/baz"]


def test_list_template_pools_reports_work_owner_when_overridden(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "vendor"
    vendor = DatapackSource(vendor_dir, None)
    stack = PackStack(work_dir=tmp_path / "work-pack", vendors=[vendor])

    # Vendor-only pool.
    stack.vendors[0].write("data/minecraft/worldgen/template_pool/bar.json", b"{}")

    # Both vendor and work define this pool; work should win.
    stack.vendors[0].write("data/minecraft/worldgen/template_pool/foo.json", b"{}")
    stack.work.write("data/minecraft/worldgen/template_pool/foo.json", b"{}")

    assert list_template_pools(stack) == [("minecraft:bar", "vendor"), ("minecraft:foo", "work")]


def test_packstack_ensure_work_pack_creates_pack_mcmeta(tmp_path: Path) -> None:
    vendor = DatapackSource(tmp_path / "vendor", None)
    stack = PackStack(work_dir=tmp_path / "work-pack", vendors=[vendor])

    mcmeta = stack.work_dir / "pack.mcmeta"
    assert mcmeta.exists() is False

    stack.ensure_work_pack()
    assert mcmeta.is_file()

    obj = json.loads(mcmeta.read_text(encoding="utf-8"))
    assert obj["pack"]["pack_format"] == 15
    assert obj["pack"]["description"] == "EnderTerm work pack"


def test_resource_id_from_worldgen_canonical_parses_pool_ids() -> None:
    got = _resource_id_from_worldgen_canonical(
        "data/minecraft/worldgen/template_pool/foo/bar.json",
        kind_folder="template_pool",
        suffix=".json",
    )
    assert got == "minecraft:foo/bar"

    assert (
        _resource_id_from_worldgen_canonical(
            "data/minecraft/worldgen/structure/foo.json",
            kind_folder="template_pool",
            suffix=".json",
        )
        is None
    )


def test_resource_id_from_structure_canonical_parses_both_folders() -> None:
    assert _resource_id_from_structure_canonical("data/minecraft/structures/foo.nbt") == "minecraft:foo"
    assert _resource_id_from_structure_canonical("data/minecraft/structure/foo/bar.nbt") == "minecraft:foo/bar"
    assert _resource_id_from_structure_canonical("data/minecraft/structures/foo.json") is None


def test_datapacksource_invalidate_without_path_clears_all_caches(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    src = DatapackSource(root, None)

    a = "data/minecraft/worldgen/structure/a.json"
    b = "data/minecraft/worldgen/structure/b.json"

    _write_json(root, a, {"x": 1})
    _write_json(root, b, {"y": 1})
    assert src.read_json(a) == {"x": 1}
    assert src.read_json(b) == {"y": 1}

    _write_json(root, a, {"x": 2})
    _write_json(root, b, {"y": 2})
    assert src.read_json(a) == {"x": 1}
    assert src.read_json(b) == {"y": 1}

    src.invalidate()
    assert src.read_json(a) == {"x": 2}
    assert src.read_json(b) == {"y": 2}


def test_datapacksource_write_rejects_zip_sources(tmp_path: Path) -> None:
    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("pack.mcmeta", "{}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        src = DatapackSource(zip_path, zf)
        with pytest.raises(RuntimeError, match="cannot write into a zip"):
            src.write("data/minecraft/worldgen/structure/a.json", b"{}")


def test_jigsawdatapackindex_load_pool_minecraft_empty_is_empty(tmp_path: Path) -> None:
    src = DatapackSource(tmp_path / "pack", None)
    index = JigsawDatapackIndex(src)

    pool = index.load_pool("minecraft:empty")
    assert pool.elements == tuple()
    assert pool.fallback == "minecraft:empty"


def test_jigsawdatapackindex_load_processor_list_minecraft_empty_is_empty(tmp_path: Path) -> None:
    src = DatapackSource(tmp_path / "pack", None)
    index = JigsawDatapackIndex(src)

    pipeline = index.load_processor_list("minecraft:empty")
    assert pipeline.processors == tuple()
    assert pipeline.ignore_base == frozenset()
    assert pipeline.unhandled_processors == tuple()


def test_datapacksource_read_json_caches_invalid_json_until_invalidated(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    canonical = "data/minecraft/worldgen/structure/a.json"
    path = root / canonical
    path.parent.mkdir(parents=True, exist_ok=True)

    src = DatapackSource(root, None)
    path.write_bytes(b"{not json")
    assert src.read_json(canonical) is None

    path.write_text('{"ok": 1}', encoding="utf-8")
    assert src.read_json(canonical) is None

    src.invalidate(canonical)
    assert src.read_json(canonical) == {"ok": 1}


def test_list_worldgen_structures_collects_ids_from_json_files(tmp_path: Path) -> None:
    vendor_dir = tmp_path / "vendor"
    (vendor_dir / "data/minecraft/worldgen/structure/a.json").parent.mkdir(parents=True, exist_ok=True)
    (vendor_dir / "data/minecraft/worldgen/structure/a.json").write_text("{}", encoding="utf-8")
    (vendor_dir / "data/qarl/worldgen/structure/b/c.json").parent.mkdir(parents=True, exist_ok=True)
    (vendor_dir / "data/qarl/worldgen/structure/b/c.json").write_text("{}", encoding="utf-8")
    (vendor_dir / "data/minecraft/worldgen/structure/ignore.txt").parent.mkdir(parents=True, exist_ok=True)
    (vendor_dir / "data/minecraft/worldgen/structure/ignore.txt").write_text("{}", encoding="utf-8")

    vendor = DatapackSource(vendor_dir, None)
    stack = PackStack(work_dir=tmp_path / "work-pack", vendors=[vendor])
    assert list_worldgen_structures(stack) == ["minecraft:a", "qarl:b/c"]


def test_processor_list_parsing_clamps_random_block_match_probability(tmp_path: Path) -> None:
    input_predicate = {
        "predicate_type": "minecraft:random_block_match",
        "blocks": ["minecraft:stone"],
        "probability": 2.5,
    }
    assert _rule_input_probability_from_single_rule_predicate(tmp_path, input_predicate) == 1.0


def test_processor_list_parsing_clamps_random_blockstate_match_probability(tmp_path: Path) -> None:
    input_predicate = {
        "predicate_type": "minecraft:random_blockstate_match",
        "block_state": "minecraft:stone",
        "probability": -1,
    }
    assert _rule_input_probability_from_single_rule_predicate(tmp_path, input_predicate) == 0.0


def test_packstack_fork_into_work_raises_for_missing_vendor_path(tmp_path: Path) -> None:
    vendor = DatapackSource(tmp_path / "vendor", None)
    stack = PackStack(work_dir=tmp_path / "work-pack", vendors=[vendor])

    with pytest.raises(FileNotFoundError):
        stack.fork_into_work("data/minecraft/worldgen/structure/nope.json")


def test_datapacksource_read_nbt_does_not_cache_parse_failures(tmp_path: Path) -> None:
    root = tmp_path / "pack"
    canonical = "data/minecraft/structures/test.nbt"
    path = root / canonical
    path.parent.mkdir(parents=True, exist_ok=True)

    src = DatapackSource(root, None)
    path.write_bytes(b"not nbt")
    assert src.read_nbt(canonical) is None

    buf = io.BytesIO()
    nbtlib.File(nbtlib.Compound({"foo": nbtlib.Int(1)})).write(buf)
    path.write_bytes(buf.getvalue())
    root2 = src.read_nbt(canonical)
    assert root2 is not None
    assert int(root2.get("foo", 0)) == 1


def test_datapacksource_read_nbt_zip_caches_parse_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import enderterm.datapack as dp

    zip_path = tmp_path / "pack.zip"
    canonical = "data/minecraft/structures/test.nbt"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(canonical, b"not nbt")

    calls = {"count": 0}

    def _raise_parse_error(_payload: bytes) -> nbtlib.Compound:
        calls["count"] += 1
        raise ValueError("bad nbt")

    monkeypatch.setattr(dp, "load_nbt_bytes", _raise_parse_error)
    with zipfile.ZipFile(zip_path, "r") as zf:
        src = DatapackSource(zip_path, zf)
        assert src.read_nbt(canonical) is None
        assert src.read_nbt(canonical) is None

    assert calls["count"] == 1
