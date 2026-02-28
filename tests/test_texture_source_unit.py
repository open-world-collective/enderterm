from __future__ import annotations

import zipfile
from pathlib import Path
from types import ModuleType


def test_texture_source_reads_from_zip_and_directory(nbttool: ModuleType, tmp_path: Path) -> None:
    # Directory mode.
    dir_root = tmp_path / "dir"
    (dir_root / "a/b.txt").parent.mkdir(parents=True, exist_ok=True)
    (dir_root / "a/b.txt").write_bytes(b"dir")
    src = nbttool.TextureSource(dir_root)
    try:
        assert src.has("a/b.txt") is True
        assert src.read("a/b.txt") == b"dir"
    finally:
        src.close()

    # Zip mode.
    zip_path = tmp_path / "tex.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a/b.txt", b"zip")

    src2 = nbttool.TextureSource(zip_path)
    try:
        assert src2.has("a/b.txt") is True
        assert src2.read("a/b.txt") == b"zip"
    finally:
        src2.close()

