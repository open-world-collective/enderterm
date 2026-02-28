from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import ModuleType

import nbtlib
import pytest


def _write_min_structure_nbt(path: Path, *, block_name: str = "minecraft:stone") -> None:
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
    nbtlib.File(root).save(path)  # type: ignore[arg-type]


def test_convert_datapack_structures_to_usdz_converts_directory(nbttool: ModuleType, tmp_path: Path) -> None:
    datapack_dir = tmp_path / "pack"
    nbt_path = datapack_dir / "data" / "minecraft" / "structures" / "foo.nbt"
    nbt_path.parent.mkdir(parents=True, exist_ok=True)
    _write_min_structure_nbt(nbt_path)

    output_dir = tmp_path / "out"
    ok, fail = nbttool.convert_datapack_structures_to_usdz(
        datapack_dir,
        output_dir,
        mode="full",
        auto_threshold=999_999,
        textured=False,
        minecraft_jar=None,
    )
    assert (ok, fail) == (1, 0)

    usdz_path = output_dir / "minecraft" / "foo.usdz"
    assert usdz_path.is_file()
    with zipfile.ZipFile(usdz_path, "r") as zf:
        assert set(zf.namelist()) == {"scene.usda"}
        assert zf.read("scene.usda").startswith(b"#usda")


def test_convert_datapack_structures_to_usdz_converts_zip_with_wrapper_folder(nbttool: ModuleType, tmp_path: Path) -> None:
    zip_path = tmp_path / "pack.zip"

    root = nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound]([nbtlib.Compound({"Name": nbtlib.String("minecraft:stone")})]),
            "blocks": nbtlib.List[nbtlib.Compound](
                [nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(0)})]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )
    buf = io.BytesIO()
    nbtlib.File(root).write(buf)

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("wrapper/data/minecraft/structures/foo.nbt", buf.getvalue())

    output_dir = tmp_path / "out"
    ok, fail = nbttool.convert_datapack_structures_to_usdz(
        zip_path,
        output_dir,
        mode="full",
        auto_threshold=999_999,
        textured=False,
        minecraft_jar=None,
    )
    assert (ok, fail) == (1, 0)

    usdz_path = output_dir / "minecraft" / "foo.usdz"
    assert usdz_path.is_file()


def test_open_texture_source_for_conversion_handles_textured_and_untextured(monkeypatch: pytest.MonkeyPatch) -> None:
    from enderterm import usdz as usdz_mod

    assert usdz_mod._open_texture_source_for_conversion(textured=False, minecraft_jar=None) is None

    monkeypatch.setattr(usdz_mod, "find_minecraft_client_jar", lambda: None)
    with pytest.raises(SystemExit, match="textured mode requires a Minecraft client jar"):
        usdz_mod._open_texture_source_for_conversion(textured=True, minecraft_jar=None)


def test_open_texture_source_for_conversion_uses_explicit_jar_without_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from enderterm import usdz as usdz_mod

    jar_path = tmp_path / "client.jar"
    jar_path.write_bytes(b"jar")
    calls: list[Path] = []

    class _FakeTextureSource:
        def __init__(self, path: Path) -> None:
            calls.append(path)

    monkeypatch.setattr(usdz_mod, "TextureSource", _FakeTextureSource, raising=False)

    def _unexpected_discovery() -> Path:
        raise AssertionError("find_minecraft_client_jar should not be called when minecraft_jar is provided")

    monkeypatch.setattr(usdz_mod, "find_minecraft_client_jar", _unexpected_discovery)

    source = usdz_mod._open_texture_source_for_conversion(textured=True, minecraft_jar=jar_path)
    assert isinstance(source, _FakeTextureSource)
    assert calls == [jar_path]


def test_convert_datapack_structures_to_usdz_counts_failed_entries(nbttool: ModuleType, tmp_path: Path) -> None:
    datapack_dir = tmp_path / "pack"
    good_nbt = datapack_dir / "data" / "minecraft" / "structures" / "good.nbt"
    bad_nbt = datapack_dir / "data" / "minecraft" / "structures" / "bad.nbt"
    good_nbt.parent.mkdir(parents=True, exist_ok=True)
    _write_min_structure_nbt(good_nbt)
    bad_nbt.write_bytes(b"not-an-nbt-file")

    output_dir = tmp_path / "out"
    ok, fail = nbttool.convert_datapack_structures_to_usdz(
        datapack_dir,
        output_dir,
        mode="full",
        auto_threshold=999_999,
        textured=False,
        minecraft_jar=None,
    )
    assert (ok, fail) == (1, 1)
    assert (output_dir / "minecraft" / "good.usdz").is_file()
    assert (output_dir / "minecraft" / "bad.usdz").exists() is False
