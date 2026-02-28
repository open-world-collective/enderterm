from __future__ import annotations

"""Jar/directory resource helpers (no OpenGL imports)."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Self
import zipfile


class TextureSource:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._is_dir = path.is_dir()
        self._zip: zipfile.ZipFile | None = None
        self._zip_names: set[str] | None = None
        self._read_cache: dict[str, bytes] = {}

        if self._is_dir:
            return
        if path.is_file():
            self._zip = zipfile.ZipFile(path, "r")
            self._zip_names = set(self._zip.namelist())
            return
        raise FileNotFoundError(path)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
        self._zip = None
        self._zip_names = None
        self._read_cache.clear()

    def has(self, relpath: str) -> bool:
        if self._is_dir:
            return (self.path / relpath).is_file()
        if self._zip_names is None:
            return False
        return relpath in self._zip_names

    def read(self, relpath: str) -> bytes:
        if self._is_dir:
            return (self.path / relpath).read_bytes()
        if self._zip is None:
            raise RuntimeError("TextureSource zip not open")
        cached = self._read_cache.pop(relpath, None)
        if cached is not None:
            # Bump LRU order.
            self._read_cache[relpath] = cached
            return cached
        data = self._zip.read(relpath)
        if len(data) <= 256 * 1024:
            self._read_cache[relpath] = data
            if len(self._read_cache) > 512:
                oldest = next(iter(self._read_cache))
                self._read_cache.pop(oldest, None)
        return data


def _spawn_detached(cmd: list[str]) -> None:
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_in_viewer(path: Path) -> None:
    if not path.exists():
        return
    if sys.platform == "darwin":
        if path.is_dir():
            _spawn_detached(["open", str(path)])
            return
        qlmanage = shutil.which("qlmanage")
        if qlmanage is not None:
            _spawn_detached([qlmanage, "-p", str(path)])
            return
        _spawn_detached(["open", str(path)])
        return

    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return

    xdg_open = shutil.which("xdg-open")
    if xdg_open is not None:
        _spawn_detached([xdg_open, str(path)])
        return
    gio = shutil.which("gio")
    if gio is not None:
        _spawn_detached([gio, "open", str(path)])
