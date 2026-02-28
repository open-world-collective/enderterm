from __future__ import annotations

import zipfile
from pathlib import Path
from types import ModuleType

import pytest


def test_write_usdz_writes_zip_with_extra_files_when_usdzip_missing(
    nbttool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbttool.shutil, "which", lambda _name: None)

    out_path = tmp_path / "out.usdz"
    nbttool.write_usdz(out_path, "#usda 1.0\n", extra_files={"prototypes.usda": b"proto"})

    with zipfile.ZipFile(out_path, "r") as zf:
        assert set(zf.namelist()) == {"scene.usda", "prototypes.usda"}
        assert zf.read("scene.usda").startswith(b"#usda")
        assert zf.read("prototypes.usda") == b"proto"


def test_write_usdz_writes_minimal_zip_without_extra_files_when_converters_unavailable(
    nbttool: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbttool, "_try_usdzip", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(nbttool, "_try_usdzconvert", lambda *_args, **_kwargs: False)

    out_path = tmp_path / "out.usdz"
    nbttool.write_usdz(out_path, "#usda 1.0\n")

    with zipfile.ZipFile(out_path, "r") as zf:
        assert set(zf.namelist()) == {"scene.usda"}
        assert zf.read("scene.usda").startswith(b"#usda")
