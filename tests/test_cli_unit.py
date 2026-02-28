from __future__ import annotations

import importlib.metadata
import zipfile
from pathlib import Path
from types import ModuleType

import nbtlib
import pytest


def _minimal_structure_root() -> nbtlib.Compound:
    return nbtlib.Compound(
        {
            "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
            "palette": nbtlib.List[nbtlib.Compound]([nbtlib.Compound({"Name": nbtlib.String("minecraft:stone")})]),
            "blocks": nbtlib.List[nbtlib.Compound](
                [nbtlib.Compound({"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(0)})]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
        }
    )


def _write_minimal_structure(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nbtlib.File(_minimal_structure_root()).save(path)  # type: ignore[arg-type]


def test_cli_self_test_writes_valid_usdz(nbttool: ModuleType, tmp_path: Path) -> None:
    out_path = tmp_path / "smoke.usdz"
    rc = nbttool.main(["self-test", "--output", str(out_path)])
    assert rc == 0
    assert out_path.is_file()

    with zipfile.ZipFile(out_path, "r") as zf:
        assert "scene.usda" in set(zf.namelist())
        assert zf.read("scene.usda").startswith(b"#usda")


def test_cli_structure_to_usdz_converts_structure_file(nbttool: ModuleType, tmp_path: Path) -> None:
    in_path = tmp_path / "in.nbt"
    _write_minimal_structure(in_path)

    out_path = tmp_path / "out.usdz"
    rc = nbttool.main(["structure-to-usdz", str(in_path), str(out_path), "--mode", "full"])
    assert rc == 0
    assert out_path.is_file()


def test_cli_datapack_structures_to_usdz_converts_pack(nbttool: ModuleType, tmp_path: Path) -> None:
    datapack_dir = tmp_path / "pack"
    in_path = datapack_dir / "data" / "minecraft" / "structures" / "foo.nbt"
    _write_minimal_structure(in_path)

    out_dir = tmp_path / "out"
    rc = nbttool.main(["datapack-structures-to-usdz", str(datapack_dir), str(out_dir), "--mode", "full"])
    assert rc == 0
    assert (out_dir / "minecraft" / "foo.usdz").is_file()


def test_cli_version_flag_prints_and_exits(nbttool: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        nbttool.main(["--version"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.strip().startswith("EnderTerm ")


def test_cli_version_lookup_is_cached(nbttool: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def fake_version(dist_name: str) -> str:
        calls["count"] += 1
        assert dist_name == "enderterm"
        return "test-version"

    cache_clear = getattr(nbttool._enderterm_version, "cache_clear")
    cache_clear()
    monkeypatch.setattr(importlib.metadata, "version", fake_version)

    assert nbttool._enderterm_version() == "test-version"
    assert nbttool._enderterm_version() == "test-version"
    assert calls["count"] == 1

    cache_clear()


def test_cli_parser_build_is_cached(nbttool: ModuleType) -> None:
    cache_clear = getattr(nbttool._build_parser, "cache_clear")
    cache_clear()

    parser_a = nbttool._build_parser()
    parser_b = nbttool._build_parser()

    assert parser_a is parser_b

    cache_clear()


def test_cli_parser_build_does_not_resolve_version(nbttool: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_clear = getattr(nbttool._build_parser, "cache_clear")
    cache_clear()

    def fail_if_called() -> str:
        raise AssertionError("_enderterm_version should not run during parser construction")

    monkeypatch.setattr(nbttool, "_enderterm_version", fail_if_called)

    parser = nbttool._build_parser()
    assert parser is not None

    cache_clear()
