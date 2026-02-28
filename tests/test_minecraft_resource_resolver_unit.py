from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_minecraft_resource_resolver_resolves_variants_multipart_and_models(
    nbttool: ModuleType, tmp_path: Path
) -> None:
    root = tmp_path / "mc"
    tex_root = root / "assets" / "minecraft"

    # Minimal textures (content not used by resolver; only existence matters here).
    (tex_root / "textures" / "block").mkdir(parents=True, exist_ok=True)
    (tex_root / "textures" / "block" / "stone.png").write_bytes(b"")
    (tex_root / "textures" / "block" / "stone_alt.png").write_bytes(b"")

    cube_all = {
        "textures": {"all": "minecraft:block/stone"},
        "elements": [
            {
                "from": [0, 0, 0],
                "to": [16, 16, 16],
                "faces": {
                    "north": {"texture": "#all", "tintindex": 0},
                    "south": {"texture": "#all"},
                    "west": {"texture": "#all"},
                    "east": {"texture": "#all"},
                    "down": {"texture": "#all"},
                    "up": {"texture": "#all"},
                },
            }
        ],
    }
    _write_json(tex_root / "models" / "block" / "cube_all.json", cube_all)
    _write_json(
        tex_root / "models" / "block" / "stone_alt.json",
        {
            "parent": "minecraft:block/cube_all",
            "textures": {"all": "minecraft:block/stone_alt"},
        },
    )

    # Variants with weights (heaviest wins).
    _write_json(
        tex_root / "blockstates" / "stone.json",
        {
            "variants": {
                "": [
                    {"model": "minecraft:block/cube_all", "weight": 1},
                    {"model": "minecraft:block/stone_alt", "weight": 2},
                ]
            }
        },
    )

    src = nbttool.TextureSource(root)
    try:
        resolver = nbttool.MinecraftResourceResolver(src)

        appearance = resolver.resolve_block_appearance("minecraft:stone")
        assert appearance is not None
        assert appearance.face_texture_png_by_dir["north"].endswith("/textures/block/stone_alt.png")
        assert appearance.face_tintindex_by_dir["north"] == 0

        block_model = resolver.resolve_block_model("minecraft:stone")
        assert block_model is not None
        assert len(block_model.parts) == 1
        assert block_model.parts[0].model_ref == "minecraft:block/stone_alt"

        # Multipart matching (unconditional + conditional + OR).
        _write_json(
            tex_root / "blockstates" / "multi.json",
            {
                "multipart": [
                    {"apply": {"model": "minecraft:block/cube_all"}},
                    {"when": {"powered": "true"}, "apply": {"model": "minecraft:block/stone_alt"}},
                    {"when": {"OR": [{"axis": "x"}, {"axis": "z"}]}, "apply": {"model": "minecraft:block/cube_all"}},
                ]
            },
        )
        bm_true = resolver.resolve_block_model("minecraft:multi[powered=true,axis=x]")
        assert bm_true is not None
        assert len(bm_true.parts) == 3

        bm_false = resolver.resolve_block_model("minecraft:multi[powered=false,axis=y]")
        assert bm_false is not None
        assert len(bm_false.parts) == 1

        # Core mesh building in textured mode should produce texture parts when files exist.
        structure = nbttool.Structure(
            size=(1, 1, 1),
            blocks=(nbttool.BlockInstance(pos=(0, 0, 0), block_id="minecraft:stone", color_key="minecraft:stone"),),
        )
        mesh = nbttool.core_build_mesh_for_structure(structure, source=src, resolver=resolver)
        assert any(part.material_kind == "texture" for part in mesh.meshes)

        # Internal face cull: two stacked boxes share a full face at y=8.
        cull = nbttool._compute_internal_face_cull_for_elements(
            [
                {"from": [0, 0, 0], "to": [16, 8, 16]},
                {"from": [0, 8, 0], "to": [16, 16, 16]},
            ]
        )
        assert cull[0] == frozenset({"up"})
        assert cull[1] == frozenset({"down"})
    finally:
        src.close()


def test_minecraft_resource_resolver_internal_face_cull_is_cached(nbttool: ModuleType, tmp_path: Path) -> None:
    root = tmp_path / "mc"
    root.mkdir(parents=True, exist_ok=True)
    src = nbttool.TextureSource(root)
    try:
        resolver = nbttool.MinecraftResourceResolver(src)
        model = nbttool.ResolvedModel(
            textures={},
            elements=[
                {"from": [0, 0, 0], "to": [16, 8, 16]},
                {"from": [0, 8, 0], "to": [16, 16, 16]},
            ],
        )
        cull_a = resolver.internal_face_cull_for_model("minecraft:block/test", model)
        cull_b = resolver.internal_face_cull_for_model("minecraft:block/test", model)
        assert cull_a is cull_b
        assert cull_a[0] == frozenset({"up"})
    finally:
        src.close()


def test_minecraft_resource_resolver_read_json_handles_missing_and_invalid(nbttool: ModuleType, tmp_path: Path) -> None:
    root = tmp_path / "mc"
    root.mkdir(parents=True, exist_ok=True)
    src = nbttool.TextureSource(root)
    try:
        resolver = nbttool.MinecraftResourceResolver(src)
        assert resolver._read_json("assets/minecraft/models/missing.json") is None

        bad_path = root / "assets" / "minecraft" / "models" / "bad.json"
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_text("{not json", encoding="utf-8")
        assert resolver._read_json("assets/minecraft/models/bad.json") is None
        diags = resolver.diagnostics()
        assert any(msg == "json:assets/minecraft/models/missing.json:missing" for msg in diags)
        assert any(msg == "json:assets/minecraft/models/bad.json:invalid" for msg in diags)
    finally:
        src.close()


def test_minecraft_resource_resolver_resolve_model_handles_non_minecraft_missing_and_cycles(
    nbttool: ModuleType, tmp_path: Path
) -> None:
    root = tmp_path / "mc"
    tex_root = root / "assets" / "minecraft"
    _write_json(tex_root / "models" / "block" / "loop.json", {"parent": "minecraft:block/loop", "textures": {}})

    src = nbttool.TextureSource(root)
    try:
        resolver = nbttool.MinecraftResourceResolver(src)

        assert resolver._parse_ref("stone") == ("minecraft", "stone")
        assert resolver._model_ref_to_jar_rel("acme:block/foo") is None
        assert resolver._texture_ref_to_jar_rel("acme:block/foo") is None

        assert resolver._resolve_model("acme:block/foo") is None
        assert resolver._resolve_model("minecraft:block/missing") is None

        loop = resolver._resolve_model("minecraft:block/loop")
        assert loop is not None
        diags = resolver.diagnostics()
        assert any(msg == "model:acme:block/foo:non_minecraft_namespace" for msg in diags)
        assert any(msg == "model:minecraft:block/missing:json_missing_or_invalid" for msg in diags)
        assert any(msg == "model:minecraft:block/loop:cycle" for msg in diags)
    finally:
        src.close()


def test_minecraft_resource_resolver_appearance_and_model_missing_diagnostics(
    nbttool: ModuleType, tmp_path: Path
) -> None:
    root = tmp_path / "mc"
    tex_root = root / "assets" / "minecraft"
    _write_json(tex_root / "blockstates" / "stone.json", {"variants": {"": {"model": "minecraft:block/missing"}}})

    src = nbttool.TextureSource(root)
    try:
        resolver = nbttool.MinecraftResourceResolver(src)
        assert resolver.resolve_block_appearance("minecraft:stone") is None
        assert resolver.resolve_block_model("minecraft:stone") is None

        diags = resolver.diagnostics()
        assert any(msg == "appearance:minecraft:stone:model_missing_or_no_elements" for msg in diags)
        assert any(msg == "block_model:minecraft:stone:no_resolved_parts" for msg in diags)
    finally:
        src.close()


def test_minecraft_resource_resolver_variant_parsing_and_matching(nbttool: ModuleType, tmp_path: Path) -> None:
    root = tmp_path / "mc"
    root.mkdir(parents=True, exist_ok=True)
    src = nbttool.TextureSource(root)
    try:
        resolver = nbttool.MinecraftResourceResolver(src)

        assert nbttool.MinecraftResourceResolver._parse_variant_key("") == {}
        assert nbttool.MinecraftResourceResolver._parse_variant_key(" facing = north , lit=true ") == {
            "facing": "north",
            "lit": "true",
        }

        blockstate = {
            "variants": {
                "facing=north": {"model": "minecraft:block/a"},
                "facing=north,lit=true": {"model": "minecraft:block/b"},
            }
        }
        chosen = resolver._select_blockstate_variant(blockstate, {"facing": "north", "lit": "true"})
        assert isinstance(chosen, dict)
        assert chosen["model"] == "minecraft:block/b"

        chosen2 = resolver._select_blockstate_variant(blockstate, {"facing": "north", "lit": "false"})
        assert isinstance(chosen2, dict)
        assert chosen2["model"] == "minecraft:block/a"

        props = {"axis": "z", "age": "2"}
        assert nbttool.MinecraftResourceResolver._multipart_when_matches([{"axis": "x"}, {"axis": "z"}], props)
        assert not nbttool.MinecraftResourceResolver._multipart_when_matches("wat", props)
        assert nbttool.MinecraftResourceResolver._multipart_when_matches({"axis": "x|z"}, props)
        assert not nbttool.MinecraftResourceResolver._multipart_when_matches({"axis": ["x", "y"]}, props)
        assert nbttool.MinecraftResourceResolver._multipart_when_matches({"age": 2}, props)
        assert not nbttool.MinecraftResourceResolver._multipart_when_matches({"missing": "x"}, props)
        assert nbttool.MinecraftResourceResolver._multipart_when_matches({1: "x"}, props)
    finally:
        src.close()
