from __future__ import annotations

"""Minecraft texture tint + minimal PNG decode helpers (no OpenGL imports)."""

import zlib

from enderterm.blockstate import _parse_block_state_id
from enderterm.mc_source import TextureSource

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_COLORMAP_CACHE: dict[str, tuple[int, int, bytes]] = {}


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _decode_png_rgba8(png: bytes) -> tuple[int, int, bytes]:
    if not png.startswith(_PNG_SIG):
        raise ValueError("not a png")
    pos = 8
    width = 0
    height = 0
    bit_depth = 0
    color_type = 0
    interlace = 0
    have_ihdr = False
    idat = bytearray()

    while pos + 8 <= len(png):
        length = int.from_bytes(png[pos : pos + 4], "big")
        pos += 4
        chunk_type = png[pos : pos + 4]
        pos += 4
        if pos + length + 4 > len(png):
            break
        chunk = png[pos : pos + length]
        pos += length
        pos += 4  # CRC

        if chunk_type == b"IHDR":
            if length != 13:
                raise ValueError("bad IHDR")
            width = int.from_bytes(chunk[0:4], "big")
            height = int.from_bytes(chunk[4:8], "big")
            bit_depth = int(chunk[8])
            color_type = int(chunk[9])
            interlace = int(chunk[12])
            have_ihdr = True
        elif chunk_type == b"IDAT":
            idat.extend(chunk)
        elif chunk_type == b"IEND":
            break

    if not have_ihdr:
        raise ValueError("missing IHDR")
    if bit_depth != 8:
        raise ValueError(f"unsupported png bit depth {bit_depth}")
    if interlace != 0:
        raise ValueError("interlaced png unsupported")
    if color_type not in (2, 6):
        raise ValueError(f"unsupported png color type {color_type}")

    raw = zlib.decompress(bytes(idat))
    bpp = 3 if color_type == 2 else 4
    stride = int(width) * bpp
    expected = int(height) * (1 + stride)
    if len(raw) < expected:
        raise ValueError("truncated png")

    out = bytearray(int(width) * int(height) * 4)
    prev = bytearray(stride)
    in_pos = 0
    out_pos = 0
    for _y in range(int(height)):
        filter_type = int(raw[in_pos])
        in_pos += 1
        row = bytearray(raw[in_pos : in_pos + stride])
        in_pos += stride

        if filter_type == 0:
            pass
        elif filter_type == 1:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + left) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                up = prev[i]
                row[i] = (row[i] + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                up = prev[i]
                up_left = prev[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + _paeth_predictor(left, up, up_left)) & 0xFF
        else:
            raise ValueError(f"unsupported png filter {filter_type}")

        if color_type == 6:
            out[out_pos : out_pos + stride] = row
            out_pos += stride
        else:
            # RGB -> RGBA
            for x in range(int(width)):
                s = x * 3
                d = out_pos + x * 4
                out[d] = row[s]
                out[d + 1] = row[s + 1]
                out[d + 2] = row[s + 2]
                out[d + 3] = 255
            out_pos += int(width) * 4

        prev = row

    return (int(width), int(height), bytes(out))


def _sample_colormap(
    source: TextureSource | None, jar_rel: str, *, temperature: float, humidity: float
) -> tuple[int, int, int]:
    if source is None or not source.has(jar_rel):
        return (255, 255, 255)
    cached = _COLORMAP_CACHE.get(jar_rel)
    if cached is None:
        data = source.read(jar_rel)
        w, h, rgba = _decode_png_rgba8(data)
        cached = (w, h, rgba)
        _COLORMAP_CACHE[jar_rel] = cached
    w, h, rgba = cached
    x = max(0, min(w - 1, int((1.0 - temperature) * float(w - 1))))
    y = max(0, min(h - 1, int((1.0 - humidity) * float(h - 1))))
    idx = (y * w + x) * 4
    return (rgba[idx], rgba[idx + 1], rgba[idx + 2])


def _tint_rgb(source: TextureSource | None, block_state_id: str, tintindex: int) -> tuple[int, int, int]:
    if tintindex < 0:
        return (255, 255, 255)
    base, props = _parse_block_state_id(block_state_id)
    if base == "minecraft:redstone_wire":
        try:
            power = int(props.get("power", "0"))
        except ValueError:
            power = 0
        power = max(0, min(15, power))
        if power == 0:
            return (77, 0, 0)
        f = power / 15.0
        r = 0.4 + 0.6 * f
        g = max(0.0, f * f * 0.7 - 0.5)
        b = max(0.0, f * f * 0.6 - 0.7)
        return (int(r * 255), int(g * 255), int(b * 255))

    if (
        base == "minecraft:grass_block"
        or base.endswith("_grass")
        or base in {"minecraft:grass", "minecraft:short_grass", "minecraft:tall_grass", "minecraft:fern", "minecraft:large_fern"}
    ):
        return _sample_colormap(
            source,
            "assets/minecraft/textures/colormap/grass.png",
            temperature=0.8,
            humidity=0.4,
        )
    if base.endswith("_leaves") or base in {"minecraft:vine", "minecraft:bamboo"} or base.endswith("_stem") or base.startswith("minecraft:attached_"):
        return _sample_colormap(
            source,
            "assets/minecraft/textures/colormap/foliage.png",
            temperature=0.8,
            humidity=0.4,
        )
    if "cauldron" in base:
        return (63, 118, 228)
    return (255, 255, 255)
