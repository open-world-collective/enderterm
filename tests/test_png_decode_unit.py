from __future__ import annotations

import zlib
from pathlib import Path
from types import ModuleType

import pytest


def _fake_png(*, width: int, height: int, color_type: int, raw_rows: bytes) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(typ: bytes, data: bytes) -> bytes:
        return len(data).to_bytes(4, "big") + typ + data + b"\x00\x00\x00\x00"

    ihdr = (
        int(width).to_bytes(4, "big")
        + int(height).to_bytes(4, "big")
        + bytes([8, color_type, 0, 0, 0])
    )
    idat = zlib.compress(raw_rows)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def test_decode_png_rgba8_decodes_rgba_and_rgb_and_rejects_unknown_filter(nbttool: ModuleType) -> None:
    rgba_png = _fake_png(width=1, height=1, color_type=6, raw_rows=b"\x00" + b"\xFF\x00\x00\x80")
    w, h, rgba = nbttool._decode_png_rgba8(rgba_png)
    assert (w, h) == (1, 1)
    assert rgba == b"\xFF\x00\x00\x80"

    rgb_png = _fake_png(width=1, height=1, color_type=2, raw_rows=b"\x00" + b"\x00\xFF\x00")
    w2, h2, rgba2 = nbttool._decode_png_rgba8(rgb_png)
    assert (w2, h2) == (1, 1)
    assert rgba2 == b"\x00\xFF\x00\xFF"

    bad_filter_png = _fake_png(width=1, height=1, color_type=6, raw_rows=b"\x09" + b"\x00\x00\x00\x00")
    with pytest.raises(ValueError, match="unsupported png filter"):
        nbttool._decode_png_rgba8(bad_filter_png)


def test_sample_colormap_reads_and_caches_png(nbttool: ModuleType, tmp_path: Path) -> None:
    jar_rel = "assets/minecraft/textures/colormap/test.png"

    rgba = (
        b"\xFF\x00\x00\xFF"  # red
        + b"\x00\xFF\x00\xFF"  # green
        + b"\x00\x00\xFF\xFF"  # blue
        + b"\xFF\xFF\xFF\xFF"  # white
    )
    png = _fake_png(width=2, height=2, color_type=6, raw_rows=b"\x00" + rgba[:8] + b"\x00" + rgba[8:])
    tex_root = tmp_path / "tex"
    path = tex_root / jar_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)

    nbttool._COLORMAP_CACHE.clear()
    source = nbttool.TextureSource(tex_root)
    try:
        assert nbttool._sample_colormap(source, jar_rel, temperature=1.0, humidity=1.0) == (255, 0, 0)
        assert nbttool._sample_colormap(source, jar_rel, temperature=0.0, humidity=0.0) == (255, 255, 255)
        assert jar_rel in nbttool._COLORMAP_CACHE
    finally:
        source.close()
