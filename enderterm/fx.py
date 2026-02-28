from __future__ import annotations

"""
Viewer effects helpers.

This module intentionally avoids importing pyglet/OpenGL at import time.
Callers pass `gl` (pyglet.gl) and other runtime objects explicitly.
"""

import ctypes
import math
import random
import sys
import time
import secrets
from dataclasses import dataclass
from typing import Any, Callable, Iterable


_BAYER8: tuple[tuple[int, ...], ...] = (
    (0, 48, 12, 60, 3, 51, 15, 63),
    (32, 16, 44, 28, 35, 19, 47, 31),
    (8, 56, 4, 52, 11, 59, 7, 55),
    (40, 24, 36, 20, 43, 27, 39, 23),
    (2, 50, 14, 62, 1, 49, 13, 61),
    (34, 18, 46, 30, 33, 17, 45, 29),
    (10, 58, 6, 54, 9, 57, 5, 53),
    (42, 26, 38, 22, 41, 25, 37, 21),
)


def _build_style0_row_bits() -> tuple[tuple[tuple[int, ...], ...], ...]:
    """Precompute style-0 row masks by [level][phase_x][bayer_row]."""
    by_level: list[tuple[tuple[int, ...], ...]] = []
    for lvl in range(65):
        by_phase: list[tuple[int, ...]] = []
        for px in range(8):
            by_bayer_row: list[int] = []
            for by in range(8):
                row_bits = 0
                row = _BAYER8[by]
                for x in range(32):
                    if row[(x + px) & 7] < lvl:
                        row_bits |= 1 << x
                by_bayer_row.append(int(row_bits))
            by_phase.append(tuple(by_bayer_row))
        by_level.append(tuple(by_phase))
    return tuple(by_level)


_STYLE0_ROW_BITS: tuple[tuple[tuple[int, ...], ...], ...] = _build_style0_row_bits()


def _write_stipple_row(data: ctypes.Array[ctypes.c_ubyte], *, y: int, row_bits: int) -> None:
    base = int(y) * 4
    data[base + 0] = row_bits & 0xFF
    data[base + 1] = (row_bits >> 8) & 0xFF
    data[base + 2] = (row_bits >> 16) & 0xFF
    data[base + 3] = (row_bits >> 24) & 0xFF


def _hash32_u32(value: int) -> int:
    hashed = int(value) & 0xFFFFFFFF
    hashed ^= hashed >> 16
    hashed = (hashed * 0x7FEB352D) & 0xFFFFFFFF
    hashed ^= hashed >> 15
    hashed = (hashed * 0x846CA68B) & 0xFFFFFFFF
    hashed ^= hashed >> 16
    return hashed & 0xFFFFFFFF


def _deterministic_lcg_u32(seed: int) -> int:
    return (int(seed) * 1664525 + 1013904223) & 0xFFFFFFFF


def _deterministic_rand01(seed: int) -> tuple[float, int]:
    next_seed = _deterministic_lcg_u32(seed)
    return (float(next_seed) / 4294967296.0), next_seed


def _clamp_float(value: float, *, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), float(value)))


def _fx_param_float(param_store: Any, key: str, *, default: float = 0.0) -> float:
    try:
        return float(param_store.get(key))
    except Exception:
        return float(default)


def _fx_param_int(param_store: Any, key: str, *, default: int = 0) -> int:
    try:
        return int(param_store.get_int(key))
    except Exception:
        return int(default)


def _fx_param_float_nonneg(param_store: Any, key: str, *, default: float = 0.0) -> float:
    return max(0.0, _fx_param_float(param_store, key, default=default))


def _fx_param_float_01(param_store: Any, key: str, *, default: float = 0.0) -> float:
    return _clamp_float(_fx_param_float(param_store, key, default=default), minimum=0.0, maximum=1.0)


def _fx_param_color_triplet(
    param_store: Any,
    key_prefix: str,
    *,
    default: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[float, float, float]:
    default_r, default_g, default_b = default
    return (
        _fx_param_float_01(param_store, f"{key_prefix}.r", default=default_r),
        _fx_param_float_01(param_store, f"{key_prefix}.g", default=default_g),
        _fx_param_float_01(param_store, f"{key_prefix}.b", default=default_b),
    )


def polygon_stipple_pattern(
    level: int,
    *,
    phase_x: int = 0,
    phase_y: int = 0,
    seed: int = 0,
    style: int = 0,
    cell: int = 1,
    square_exp: float = 1.0,
    square_jitter: int = 0,
) -> ctypes.Array[ctypes.c_ubyte]:
    lvl = int(level)
    if lvl < 0:
        lvl = 0
    if lvl > 64:
        lvl = 64
    mode = int(style)
    cell_px = int(cell)
    if cell_px < 1:
        cell_px = 1
    if cell_px > 8:
        cell_px = 8
    sq_exp = float(square_exp) if isinstance(square_exp, (int, float)) else 1.0
    if not math.isfinite(sq_exp) or sq_exp <= 0.0:
        sq_exp = 1.0
    if sq_exp > 16.0:
        sq_exp = 16.0
    sq_jit = int(square_jitter) if isinstance(square_jitter, (int, float)) else 0
    if sq_jit < 0:
        sq_jit = 0
    if sq_jit > 32:
        sq_jit = 32

    if mode == 0:
        # Ordered dither (Bayer 8x8), shifted by an 8x8 phase.
        phase_x &= 7
        phase_y &= 7
        data = (ctypes.c_ubyte * 128)()
        by_row = _STYLE0_ROW_BITS[lvl][phase_x]
        for y in range(32):
            _write_stipple_row(data, y=y, row_bits=by_row[(y + phase_y) & 7])
        return data

    data = (ctypes.c_ubyte * 128)()

    if mode == 2:
        # Single-square mask: a lone block of visible pixels whose side length
        # shrinks as `lvl` decreases. The square position jitters/flickers.
        t = float(lvl) / 64.0 if lvl > 0 else 0.0
        if t <= 0.0:
            return data
        if t >= 1.0:
            for i in range(128):
                data[i] = 0xFF
            return data

        side = int(round(32.0 * (t**sq_exp)))
        if side < 1:
            return data
        if side > 32:
            side = 32
        if side == 32:
            for i in range(128):
                data[i] = 0xFF
            return data

        s = int(seed) & 0xFFFFFFFF
        px = int(phase_x) & 0xFFFFFFFF
        py = int(phase_y) & 0xFFFFFFFF
        # For big squares (lvl near 64), the “room to move” inside a 32×32 tile
        # becomes tiny. To avoid the square looking “centered”, choose a full
        # 0..31 offset and allow wrap-around when the square crosses the tile edge.
        # Important: make the square placement depend on `lvl` (coverage). This lets
        # small “alpha/level” differences naturally de-correlate the square’s (x0,y0)
        # placement without requiring per-range stipple uploads.
        mix_x = (s ^ (px * 0x9E3779B1) ^ (lvl * 0xD1B54A35) ^ 0xA1B2C3D4) & 0xFFFFFFFF
        mix_y = (s ^ (py * 0x85EBCA6B) ^ (lvl * 0x94D049BB) ^ 0x31415926) & 0xFFFFFFFF

        hash32 = _hash32_u32
        x0 = int(hash32(mix_x) & 31)
        y0 = int(hash32(mix_y) & 31)
        jit = int(sq_jit)
        if jit > 0:
            if jit > 32:
                jit = 32
            jitter_x = int(hash32(mix_x ^ 0xDEADBEEF) % (2 * jit + 1)) - jit
            jitter_y = int(hash32(mix_y ^ 0xBADC0FFE) % (2 * jit + 1)) - jit
            x0 = int((int(x0) + int(jitter_x)) & 31)
            y0 = int((int(y0) + int(jitter_y)) & 31)

        # Compute the horizontal run mask once. This preserves wrap-around
        # behavior while avoiding per-bit loops on a hot path.
        side_i = int(side)
        x0_i = int(x0) & 31
        base_mask = (1 << side_i) - 1
        if (x0_i + side_i) <= 32:
            row_bits = base_mask << x0_i
        else:
            row_bits = ((base_mask << x0_i) | (base_mask >> (32 - x0_i))) & 0xFFFFFFFF

        b0 = row_bits & 0xFF
        b1 = (row_bits >> 8) & 0xFF
        b2 = (row_bits >> 16) & 0xFF
        b3 = (row_bits >> 24) & 0xFF

        # Fill exactly `side` contiguous rows, wrapping in 32x32 space.
        y0_i = int(y0) & 31
        base = int(y0_i) * 4
        for _ in range(side_i):
            data[base + 0] = b0
            data[base + 1] = b1
            data[base + 2] = b2
            data[base + 3] = b3
            base += 4
            if base >= 128:
                base = 0
        return data

    # "Static" stipple: square pixel noise. Each `cell_px`×`cell_px` region shares
    # a threshold, producing chunky blocks that flicker with phase.
    #
    # Note: this is intentionally not a perfect blue-noise; it just needs to feel
    # like retro screen-door static.
    s = int(seed) & 0xFFFFFFFF
    px = int(phase_x) & 0xFFFFFFFF
    py = int(phase_y) & 0xFFFFFFFF
    mix_base = (s ^ (px * 0x9E3779B1) ^ (py * 0x85EBCA6B)) & 0xFFFFFFFF

    hash32 = _hash32_u32
    for y in range(32):
        row_bits = 0
        cy = (y // cell_px) & 0xFFFFFFFF
        x0 = 0
        while x0 < 32:
            cx = (x0 // cell_px) & 0xFFFFFFFF
            hash_value = hash32(mix_base ^ (cx * 0xD1B54A35) ^ (cy * 0x94D049BB))
            threshold = int(hash_value & 63)
            if threshold < lvl:
                x1 = min(32, x0 + cell_px)
                width = int(x1 - x0)
                if width >= 32:
                    row_bits |= 0xFFFFFFFF
                else:
                    row_bits |= ((1 << width) - 1) << x0
            x0 += cell_px
        _write_stipple_row(data, y=y, row_bits=row_bits)
    return data


def _stipple_runtime_params(param_store: Any, *, now: float) -> tuple[int, int, int, float, int]:
    # Shared stipple runtime knobs used by multiple fade paths.
    try:
        hz = float(param_store.get("rez.fade.stipple.flicker_hz"))
    except Exception:
        hz = 24.0
    if hz < 0.0:
        hz = 0.0
    if hz > 240.0:
        hz = 240.0
    phase_tick = int(float(now) * hz)
    try:
        style = int(param_store.get_int("rez.fade.stipple.style"))
    except Exception:
        style = 0
    try:
        cell = int(param_store.get_int("rez.fade.stipple.cell"))
    except Exception:
        cell = 1
    try:
        sq_exp = float(param_store.get("rez.fade.stipple.square.exp"))
    except Exception:
        sq_exp = 1.0
    try:
        sq_jit = int(param_store.get_int("rez.fade.stipple.square.jitter_px"))
    except Exception:
        sq_jit = 0
    return phase_tick, style, cell, sq_exp, sq_jit


@dataclass(slots=True)
class FlashBox:
    min_corner: tuple[float, float, float]
    max_corner: tuple[float, float, float]
    pop: Any
    fade: Any
    color: tuple[float, float, float] = (205.0 / 255.0, 140.0 / 255.0, 255.0 / 255.0)


@dataclass(slots=True)
class StructureDeltaFade:
    start_t: float
    duration_s: float
    pivot_center: tuple[float, float, float]
    base_batch: object
    final_batch: object
    added_batch: object | None
    removed_batch: object | None


@dataclass(slots=True)
class RezLiveFadeChunk:
    start_t: float
    duration_s: float
    phase_seed: int
    batch: object
    vlists: list[object]


def spawn_flash_box(
    self: Any,
    min_corner: tuple[float, float, float],
    max_corner: tuple[float, float, float],
    *,
    duration_s: float = 1.0,
    color: tuple[float, float, float] | None = None,
    param_store: Any,
    Tween: Any,
    ease_smoothstep: Any,
    ease_linear: Any,
) -> None:
    if not self._effects_enabled:
        return
    if duration_s <= 0.0:
        return
    start = time.monotonic()
    pop = Tween(start_t=start, duration_s=0.08, start=0.0, end=1.0, ease=ease_smoothstep)
    fade = Tween(start_t=start, duration_s=duration_s, start=1.0, end=0.0, ease=ease_linear)
    if color is None:
        try:
            color = (
                max(0.0, min(1.0, float(param_store.get("fx.color.ender.r")))),
                max(0.0, min(1.0, float(param_store.get("fx.color.ender.g")))),
                max(0.0, min(1.0, float(param_store.get("fx.color.ender.b")))),
            )
        except Exception:
            color = None
    if color is None:
        box = FlashBox(min_corner, max_corner, pop, fade)
    else:
        box = FlashBox(min_corner, max_corner, pop, fade, color=color)
    self._effects.append(box)
    if len(self._effects) > 2400:
        self._effects = self._effects[-2400:]


def tick_effects(self: Any) -> None:
    if not self._effects_enabled:
        return
    if not self._effects:
        return
    now = time.monotonic()
    self._effects = [e for e in self._effects if not e.fade.done(now)]


def draw_effects(self: Any, *, gl: Any) -> None:
    if not self._effects_enabled:
        return
    if not self._effects:
        return
    now = time.monotonic()

    pushed = False
    pushed_client = False
    try:
        gl.glPushAttrib(gl.GL_ALL_ATTRIB_BITS)
        pushed = True
        try:
            gl.glPushClientAttrib(gl.GL_CLIENT_ALL_ATTRIB_BITS)
            pushed_client = True
        except Exception:
            pushed_client = False

        gl.glDisable(gl.GL_TEXTURE_2D)
        gl.glDisable(gl.GL_LIGHTING)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glDepthMask(gl.GL_FALSE)
        try:
            gl.glLineWidth(2.0)
        except Exception:
            pass

        any_faces = False
        gl.glBegin(gl.GL_TRIANGLES)
        for fx in self._effects:
            if fx.fade.done(now):
                continue
            pop = fx.pop.value(now)
            fade = fx.fade.value(now)
            a_faces = 0.12 * pop * fade
            if a_faces <= 0.001:
                continue
            any_faces = True

            x0, y0, z0 = fx.min_corner
            x1, y1, z1 = fx.max_corner
            cx = (x0 + x1) * 0.5
            cy = (y0 + y1) * 0.5
            cz = (z0 + z1) * 0.5
            hx = (x1 - x0) * 0.5
            hy = (y1 - y0) * 0.5
            hz = (z1 - z0) * 0.5
            grow = 0.22 * (1.0 - fade)
            scale = (0.92 + 0.08 * pop) * (1.0 + grow)
            hx *= scale
            hy *= scale
            hz *= scale
            x0, x1 = (cx - hx), (cx + hx)
            y0, y1 = (cy - hy), (cy + hy)
            z0, z1 = (cz - hz), (cz + hz)

            c000 = (x0, y0, z0)
            c100 = (x1, y0, z0)
            c110 = (x1, y1, z0)
            c010 = (x0, y1, z0)
            c001 = (x0, y0, z1)
            c101 = (x1, y0, z1)
            c111 = (x1, y1, z1)
            c011 = (x0, y1, z1)

            quads = (
                (c000, c100, c110, c010),  # -Z
                (c001, c101, c111, c011),  # +Z
                (c000, c001, c011, c010),  # -X
                (c100, c101, c111, c110),  # +X
                (c000, c100, c101, c001),  # -Y
                (c010, c110, c111, c011),  # +Y
            )
            r, g, b = fx.color
            gl.glColor4f(r, g, b, a_faces)
            # Fix winding for faces so backface culling works.
            for idx_face, (p0, p1, p2, p3) in enumerate(quads):
                flip = idx_face in {0, 3}  # -Z, +X
                if flip:
                    gl.glVertex3f(*p0)
                    gl.glVertex3f(*p2)
                    gl.glVertex3f(*p1)
                    gl.glVertex3f(*p0)
                    gl.glVertex3f(*p3)
                    gl.glVertex3f(*p2)
                else:
                    gl.glVertex3f(*p0)
                    gl.glVertex3f(*p1)
                    gl.glVertex3f(*p2)
                    gl.glVertex3f(*p0)
                    gl.glVertex3f(*p2)
                    gl.glVertex3f(*p3)
        gl.glEnd()
        if not any_faces:
            # Keep state clean even if nothing was drawn.
            pass

        any_edges = False
        gl.glBegin(gl.GL_LINES)
        for fx in self._effects:
            if fx.fade.done(now):
                continue
            pop = fx.pop.value(now)
            fade = fx.fade.value(now)
            a_edges = 0.65 * pop * (fade**0.55)
            if a_edges <= 0.001:
                continue
            any_edges = True

            x0, y0, z0 = fx.min_corner
            x1, y1, z1 = fx.max_corner
            cx = (x0 + x1) * 0.5
            cy = (y0 + y1) * 0.5
            cz = (z0 + z1) * 0.5
            hx = (x1 - x0) * 0.5
            hy = (y1 - y0) * 0.5
            hz = (z1 - z0) * 0.5
            grow = 0.22 * (1.0 - fade)
            scale = (0.92 + 0.08 * pop) * (1.0 + grow)
            hx *= scale
            hy *= scale
            hz *= scale
            x0, x1 = (cx - hx), (cx + hx)
            y0, y1 = (cy - hy), (cy + hy)
            z0, z1 = (cz - hz), (cz + hz)

            c000 = (x0, y0, z0)
            c100 = (x1, y0, z0)
            c110 = (x1, y1, z0)
            c010 = (x0, y1, z0)
            c001 = (x0, y0, z1)
            c101 = (x1, y0, z1)
            c111 = (x1, y1, z1)
            c011 = (x0, y1, z1)

            edges = (
                (c000, c100),
                (c100, c110),
                (c110, c010),
                (c010, c000),
                (c001, c101),
                (c101, c111),
                (c111, c011),
                (c011, c001),
                (c000, c001),
                (c100, c101),
                (c110, c111),
                (c010, c011),
            )
            r, g, b = fx.color
            gl.glColor4f(r, g, b, a_edges)
            for p, q in edges:
                gl.glVertex3f(*p)
                gl.glVertex3f(*q)
        gl.glEnd()
        if not any_edges:
            pass
    finally:
        try:
            if pushed_client:
                gl.glPopClientAttrib()
        except Exception:
            pass
        try:
            if pushed:
                gl.glPopAttrib()
        except Exception:
            pass


def _tick_ui_selection_fx(self: Any, *, now: float) -> None:
    if self.selection_bg.visible:
        sel_pulse = 0.5 + 0.5 * math.sin(now * 3.1 + float(self.selected) * 0.13)
        self.selection_bg.opacity = int(120 + 70 * sel_pulse)
        self.selection_glow.opacity = int(34 + 48 * (1.0 - sel_pulse))
        flicker = 0.5 + 0.5 * math.sin(now * 11.0 + float(self.selected) * 0.71)
        spark = 0.5 + 0.5 * math.sin(now * 47.0 + float(self.selected) * 1.9)
        shine_amt = 0.55 * (1.0 - sel_pulse) + 0.30 * flicker + 0.15 * spark
        self.selection_shine.opacity = int(16 + 70 * shine_amt)
        self.selection_glow.x = max(-4, int(self.selection_bg.x) - 2)
        self.selection_glow.y = max(0, int(self.selection_bg.y) - 2)
        self.selection_glow.width = int(self.selection_bg.width) + 4
        self.selection_glow.height = int(self.selection_bg.height) + 4
        self.selection_shine.x = int(self.selection_bg.x)
        self.selection_shine.y = int(self.selection_bg.y)
        self.selection_shine.width = max(1, int(self.selection_bg.width))
        self.selection_shine.height = int(self.selection_bg.height)
        self.selection_shine.visible = True
        self.selection_glow.visible = True
    else:
        self.selection_glow.visible = False
        self.selection_shine.visible = False


def _tick_ui_cancel_fx(
    self: Any,
    *,
    now: float,
    cancel_seed: int,
    bg: object,
    label_o: object,
    label_x: object,
    glows: list[object] | None,
    active: bool,
    seed_tag: int,
) -> None:
    try:
        bg_vis = bool(getattr(bg, "visible"))
    except Exception:
        bg_vis = False
    if not bg_vis:
        return

    bx = float(getattr(bg, "x", 0.0))
    by = float(getattr(bg, "y", 0.0))
    bw = float(getattr(bg, "width", 0.0))
    bh = float(getattr(bg, "height", 0.0))
    hot = bool(active)

    def _as_layers(obj: object) -> list[tuple[object, int, float]]:
        if isinstance(obj, list):
            out: list[tuple[object, int, float]] = []
            for item in obj:
                try:
                    lbl, dx_unit, a_mul = item
                    out.append((lbl, int(dx_unit), float(a_mul)))
                except Exception:
                    continue
            return out
        return [(obj, 0, 1.0)]

    bx_i = int(bx + bw / 2.0)
    by_i = int(by + bh / 2.0)
    spread_px = max(1, int(round(min(bw, bh) * 0.10)))
    o_layers = _as_layers(label_o)
    x_layers = _as_layers(label_x)
    for lbl, dx_unit, _ in o_layers:
        try:
            setattr(lbl, "x", bx_i + dx_unit * spread_px)
            setattr(lbl, "y", by_i)
        except Exception:
            pass
    for lbl, dx_unit, _ in x_layers:
        try:
            setattr(lbl, "x", bx_i + dx_unit * spread_px)
            setattr(lbl, "y", by_i)
        except Exception:
            pass

    flick = 0.5 + 0.5 * math.sin(now * (10.0 if hot else 6.0) + float(seed_tag & 0xFF) * 0.12)
    glow_pulse = flick
    try:
        o_a = int((160 if hot else 120) + (70 if hot else 55) * glow_pulse)
        x_a = int((235 if hot else 205) + (20 if hot else 25) * glow_pulse)
        o_a = max(0, min(255, o_a))
        x_a = max(0, min(255, x_a))
        for lbl, _, a_mul in o_layers:
            a = int(float(o_a) * a_mul)
            a = max(0, min(255, a))
            setattr(lbl, "color", (*self._ui_purple_hi, a))
        for lbl, _, a_mul in x_layers:
            a = int(float(x_a) * a_mul)
            a = max(0, min(255, a))
            setattr(lbl, "color", (*self._ui_purple_hot, a))
    except Exception:
        pass

    bg_base = 120 if hot else 85
    bg_amp = 45 if hot else 30
    try:
        bg_col = self._ui_cancel_bg_hot if hot else self._ui_cancel_bg
        setattr(bg, "color", bg_col)
        setattr(bg, "opacity", int(bg_base + bg_amp * glow_pulse))
    except Exception:
        pass

    if not glows:
        return
    if not hot:
        for r in glows:
            try:
                setattr(r, "opacity", 0)
            except Exception:
                pass
        return
    # "Rez bar" style glitch: a few bright, terminal-colored blocks
    # that flash around the button edges. (Additive blended.)
    outer_pad = 7 if hot else 6
    region_x0 = bx - float(outer_pad)
    region_y0 = by - float(outer_pad)
    region_w = bw + float(outer_pad) * 2.0
    region_h = bh + float(outer_pad) * 2.0
    center_shift = 4 if hot else 2
    for i, glow in enumerate(glows):
        try:
            glow_vis = bool(getattr(glow, "visible"))
        except Exception:
            glow_vis = True
        if not glow_vis:
            continue

        s = (cancel_seed ^ seed_tag ^ ((i + 1) * 0x85EBCA6B)) & 0xFFFFFFFF
        show, s = _deterministic_rand01(s)
        show_p = 0.72 if hot else 0.42
        if show > show_p:
            try:
                setattr(glow, "opacity", 0)
            except Exception:
                pass
            continue

        shape_r, s = _deterministic_rand01(s)
        rc, s = _deterministic_rand01(s)
        ra, s = _deterministic_rand01(s)

        # Small jitter for where the block appears (keeps it buzzy).
        r, s = _deterministic_rand01(s)
        gdx = int(round((r * 2.0 - 1.0) * float(center_shift)))
        r, s = _deterministic_rand01(s)
        gdy = int(round((r * 2.0 - 1.0) * float(center_shift)))

        # Choose a block shape (mostly skinny stripes, like the rez bar).
        if shape_r < 0.55:
            # Horizontal band on top/bottom.
            r, s = _deterministic_rand01(s)
            band_h = 1 + int(r * (3 if hot else 2))
            r, s = _deterministic_rand01(s)
            band_w = max(4, int((0.35 + 0.65 * r) * region_w))
            band_w = min(int(region_w), band_w)
            r, s = _deterministic_rand01(s)
            x0 = region_x0 + float(gdx) + float(int(r * max(1, int(region_w) - band_w)))
            top = (rc < 0.5)
            y0 = region_y0 + float(gdy) + (region_h - float(band_h) if top else 0.0)
            w = band_w
            h = band_h
        elif shape_r < 0.82:
            # Vertical band on left/right.
            r, s = _deterministic_rand01(s)
            band_w = 1 + int(r * (3 if hot else 2))
            r, s = _deterministic_rand01(s)
            band_h = max(4, int((0.35 + 0.65 * r) * region_h))
            band_h = min(int(region_h), band_h)
            r, s = _deterministic_rand01(s)
            y0 = region_y0 + float(gdy) + float(int(r * max(1, int(region_h) - band_h)))
            left = (rc < 0.5)
            x0 = region_x0 + float(gdx) + (0.0 if left else (region_w - float(band_w)))
            w = band_w
            h = band_h
        else:
            # Square-ish block along an edge.
            size_max = 8 if hot else 6
            r, s = _deterministic_rand01(s)
            size = 2 + int(r * float(size_max))
            r, s = _deterministic_rand01(s)
            side = int(r * 4.0) % 4
            if side == 0:  # left
                x0 = region_x0 + float(gdx)
                r, s = _deterministic_rand01(s)
                y0 = region_y0 + float(gdy) + float(int(r * max(1, int(region_h) - size)))
            elif side == 1:  # right
                x0 = region_x0 + float(gdx) + (region_w - float(size))
                r, s = _deterministic_rand01(s)
                y0 = region_y0 + float(gdy) + float(int(r * max(1, int(region_h) - size)))
            elif side == 2:  # bottom
                y0 = region_y0 + float(gdy)
                r, s = _deterministic_rand01(s)
                x0 = region_x0 + float(gdx) + float(int(r * max(1, int(region_w) - size)))
            else:  # top
                y0 = region_y0 + float(gdy) + (region_h - float(size))
                r, s = _deterministic_rand01(s)
                x0 = region_x0 + float(gdx) + float(int(r * max(1, int(region_w) - size)))
            w = size
            h = size

        # Color palette similar to the rez bar static.
        if rc < 0.70:
            col = self._ui_purple
        elif rc < 0.86:
            col = self._ui_amber
        elif rc < 0.95:
            col = self._ui_green
        else:
            col = self._ui_purple_hi  # hot highlight

        base_a = 10 if hot else 6
        amp_a = 40 if hot else 28
        glow_a = int((base_a + amp_a * ra) * (0.35 + 0.55 * glow_pulse))
        glow_a = max(0, min(120, glow_a))

        try:
            setattr(glow, "x", int(x0))
            setattr(glow, "y", int(y0))
            setattr(glow, "width", max(1, int(w)))
            setattr(glow, "height", max(1, int(h)))
            setattr(glow, "color", col)
            setattr(glow, "opacity", glow_a)
        except Exception:
            pass


def _tick_ui_search_fx(self: Any, *, now: float, cancel_seed: int) -> None:
    if self._search_ui_visible():
        # Keep search UI "alive" so you don't forget you're filtered.
        self._update_search_ui()
        if self._search_active:
            sp = 0.5 + 0.5 * math.sin(now * 4.6)
            self.search_glow.opacity = int(70 + 110 * sp)
            self.search_bg.opacity = int(210 + 20 * sp)
            self.search_cancel_bg.opacity = int(165 + 55 * sp)
        else:
            self.search_glow.opacity = 42
            self.search_bg.opacity = 225
            self.search_cancel_bg.opacity = 175
        self.search_glow.visible = True
        _tick_ui_cancel_fx(
            self,
            now=now,
            cancel_seed=cancel_seed,
            bg=self.search_cancel_bg,
            label_o=self.search_cancel_label_o_layers,
            label_x=self.search_cancel_label_x_layers,
            glows=self.search_cancel_glows,
            active=self._search_active,
            seed_tag=0x51E4C4,
        )
    else:
        self.search_glow.opacity = 0
        self.search_glow.visible = False
        self.search_cancel_bg.opacity = 0
        for lbl, _, _ in self.search_cancel_label_o_layers:
            lbl.color = (*self._ui_purple_hi, 0)
        for lbl, _, _ in self.search_cancel_label_x_layers:
            lbl.color = (*self._ui_purple_hot, 0)
        for r in self.search_cancel_glows:
            r.opacity = 0


def _tick_ui_rez_cancel_fx(self: Any, *, now: float, cancel_seed: int) -> None:
    if self._rez_active:
        _tick_ui_cancel_fx(
            self,
            now=now,
            cancel_seed=cancel_seed,
            bg=self.rez_cancel_bg,
            label_o=self.rez_cancel_label_o_layers,
            label_x=self.rez_cancel_label_x_layers,
            glows=self.rez_cancel_glows,
            active=True,
            seed_tag=0x52E2C5,
        )


def tick_ui_fx(self: Any) -> None:
    now = time.monotonic()
    pulse = 0.5 + 0.5 * math.sin(now * 2.2)
    self.scroll_thumb.opacity = int(130 + 35 * pulse)
    self.scroll_thumb_glow.opacity = int(28 + 18 * (1.0 - pulse))
    self.scroll_thumb_shine.opacity = int(22 + 18 * (1.0 - pulse))

    _tick_ui_selection_fx(self, now=now)

    cancel_seed = (self._fx_seed ^ (self._fx_frame * 0x9E3779B1) ^ 0xC0DEC0DE) & 0xFFFFFFFF
    _tick_ui_search_fx(self, now=now, cancel_seed=cancel_seed)
    _tick_ui_rez_cancel_fx(self, now=now, cancel_seed=cancel_seed)


def iter_text_glitch_labels(self: Any) -> Iterable[object]:
    # Sidebar.
    for lbl in (self.title, self.subtitle, self.log_title, self.log_toggle_label):
        yield lbl
    for lbl in (self.search_label, self.search_count_label):
        yield lbl
    for lbl in self.status_labels:
        yield lbl
    for lbl in self.line_labels:
        yield lbl
    for lbl in self.log_labels:
        yield lbl
    # Overlays.
    for lbl in (
        self.rez_label,
        self.search_cancel_label_o,
        self.search_cancel_label_x,
        self.rez_cancel_label_o,
        self.rez_cancel_label_x,
        self.help_label,
        self.ender_vision_label,
        self.brand_label,
        self.palette_title,
        self.palette_search_label,
        self.palette_hint_label,
    ):
        yield lbl
    for lbl in self.hotbar_slot_labels:
        yield lbl
    for lbl in self.hotbar_slot_numbers:
        yield lbl


def apply_text_glitch_for_draw(self: Any, *, param_store: Any) -> Callable[[], None]:
    # Render-time swap: no timers, no scheduled state.
    rate_key = "fx.glitch.text.rate_hz.rez" if self._rez_active else "fx.glitch.text.rate_hz.normal"
    rate_hz = float(param_store.get(rate_key))
    intensity = max(1.0, float(param_store.get_int("fx.glitch.text.max_chars")))
    rate_hz *= intensity
    if rate_hz <= 1e-6:
        return lambda: None

    # Requires the mixed font (Ender variants are in the PUA).
    if self.ui_font_name != "terminal Mixed":
        return lambda: None

    seed_tag = 0x52E2C5 if self._rez_active else 0x51E4C4
    rng = random.Random((self._fx_seed ^ (self._fx_frame * 0x9E3779B1) ^ seed_tag) & 0xFFFFFFFF)
    dt = max(1e-4, float(self._fx_last_dt))
    mean = max(0.0, rate_hz * dt)
    if mean <= 1e-6:
        return lambda: None

    def _poisson(lmbda: float) -> int:
        if lmbda <= 1e-8:
            return 0
        if lmbda < 30.0:
            L = math.exp(-lmbda)
            k = 0
            p = 1.0
            while p > L:
                k += 1
                p *= rng.random()
            return max(0, k - 1)
        # Large lambda: normal approximation.
        return max(0, int(rng.gauss(lmbda, math.sqrt(lmbda)) + 0.5))

    def _swap_to_pua(ch: str) -> str | None:
        if ch in {" ", "\n", "\r", "\t"} or ch == "\u00a0":
            return None
        o = ord(ch)
        if o < 0 or o >= 128:
            return None
        return chr(0xE000 + o)

    k = _poisson(mean)
    if k <= 0:
        return lambda: None

    labels: list[object] = []
    candidates_by_label: list[list[int]] = []
    cumulative: list[int] = []
    total = 0

    for lbl in iter_text_glitch_labels(self):
        if getattr(lbl, "visible", True) is False:
            continue
        text = getattr(lbl, "text", "")
        if not isinstance(text, str) or not text:
            continue
        idxs = [i for i, ch in enumerate(text) if _swap_to_pua(ch) is not None]
        if not idxs:
            continue
        labels.append(lbl)
        candidates_by_label.append(idxs)
        total += len(idxs)
        cumulative.append(total)

    if total <= 0 or not labels:
        return lambda: None

    import bisect

    k = min(k, total)
    if k <= 0:
        return lambda: None

    picks: list[int]
    if k >= total:
        picks = list(range(total))
    else:
        used: set[int] = set()
        tries = 0
        while len(used) < k and tries < k * 20:
            tries += 1
            used.add(int(rng.randrange(total)))
        picks = list(used)

    originals: dict[object, str] = {}
    mutated: dict[object, list[str]] = {}

    for r in picks:
        li = int(bisect.bisect_left(cumulative, int(r) + 1))
        if li < 0 or li >= len(labels):
            continue
        start = int(cumulative[li - 1]) if li > 0 else 0
        cand_idx = int(r) - start
        idxs = candidates_by_label[li]
        if cand_idx < 0 or cand_idx >= len(idxs):
            continue
        idx = int(idxs[cand_idx])
        lbl = labels[li]
        orig = originals.get(lbl)
        if orig is None:
            orig = str(getattr(lbl, "text", ""))
            originals[lbl] = orig
        repl = _swap_to_pua(orig[idx]) if 0 <= idx < len(orig) else None
        if repl is None:
            continue
        chars = mutated.get(lbl)
        if chars is None:
            chars = list(orig)
            mutated[lbl] = chars
        if 0 <= idx < len(chars):
            chars[idx] = repl

    if not mutated:
        return lambda: None

    for lbl, chars in mutated.items():
        try:
            setattr(lbl, "text", "".join(chars))
        except Exception:
            pass

    def _restore() -> None:
        for lbl, text in originals.items():
            try:
                setattr(lbl, "text", text)
            except Exception:
                pass

    return _restore


def draw_post_fx_overlay(self: Any, *, gl: Any, param_store: Any) -> None:
    if not bool(getattr(self, "_effects_enabled", True)):
        return
    w = float(self.width)
    h = float(self.height)
    if w <= 0.0 or h <= 0.0:
        return

    ratio = float(self.get_pixel_ratio())
    now = time.monotonic()
    tick_hz = max(1, _fx_param_int(param_store, "fx.glitch.postfx.tick_hz", default=1))
    frame = int(now * float(tick_hz))
    sidebar_px = int(max(0.0, float(self.sidebar_width)) * ratio)
    view_w_px = int(max(0.0, w - float(self.sidebar_width)) * ratio)
    view_h_px = int(h * ratio)

    # Keep it subtle: scanlines + faint purple side noise.
    base_r, base_g, base_b = _fx_param_color_triplet(param_store, "fx.color.ender")
    amber_r, amber_g, amber_b = _fx_param_color_triplet(param_store, "fx.color.accent.amber")
    green_r, green_g, green_b = _fx_param_color_triplet(param_store, "fx.color.accent.green")

    gl.glDisable(gl.GL_TEXTURE_2D)
    gl.glDisable(gl.GL_DEPTH_TEST)
    gl.glDepthMask(gl.GL_FALSE)
    gl.glEnable(gl.GL_BLEND)
    gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
    try:
        gl.glLineWidth(1.0)
    except Exception:
        pass

    _rand01 = _deterministic_rand01

    def _draw_scanline_subpass() -> None:
        # Scanlines (in pixel-ish spacing so they feel CRT-ish on Retina too).
        scanline_strength = _fx_param_float_nonneg(param_store, "fx.glitch.scanline.strength")
        scanline_thick_mult = _fx_param_float_nonneg(param_store, "fx.glitch.scanline.thickness.mult")
        scanline_spacing_px = _fx_param_float_nonneg(param_store, "fx.glitch.scanline.spacing.px")
        scanline_drift_pt_s = _fx_param_float_nonneg(param_store, "fx.glitch.scanline.drift.pt_s")
        if scanline_strength <= 1e-6 or scanline_thick_mult <= 1e-6 or scanline_spacing_px <= 1e-6:
            return
        line_h = 1.0 * scanline_thick_mult
        spacing = max(line_h, scanline_spacing_px / max(1.0, ratio))
        drift = (now * scanline_drift_pt_s) % spacing
        y = -drift
        gl.glBegin(gl.GL_QUADS)
        while y <= h:
            wobble = 0.75 + 0.25 * math.sin((y * 0.55) + (now * 2.1))
            a_dark = min(0.35, 0.05 * wobble * scanline_strength)
            a_purple = min(0.22, 0.022 * wobble * scanline_strength)

            # Dark line to read on bright areas.
            gl.glColor4f(0.0, 0.0, 0.0, a_dark)
            gl.glVertex2f(0.0, y)
            gl.glVertex2f(w, y)
            gl.glVertex2f(w, y + line_h)
            gl.glVertex2f(0.0, y + line_h)

            # Purple tint to make it "ours" even on dark backgrounds.
            gl.glColor4f(base_r, base_g, base_b, a_purple)
            gl.glVertex2f(0.0, y)
            gl.glVertex2f(w, y)
            gl.glVertex2f(w, y + line_h)
            gl.glVertex2f(0.0, y + line_h)

            y += spacing
        gl.glEnd()

    def _draw_edge_noise_subpass() -> None:
        # Side noise: small Ender static blocks along the edges.
        edge_w_min = _fx_param_float_nonneg(param_store, "fx.glitch.edge_noise.width.min_px")
        edge_w_max = max(edge_w_min, _fx_param_float(param_store, "fx.glitch.edge_noise.width.max_px"))
        edge_w_frac = _fx_param_float_nonneg(param_store, "fx.glitch.edge_noise.width.frac")
        band_w = max(edge_w_min, min(edge_w_max, w * edge_w_frac))
        band_w = min(band_w, max(0.0, w))
        count = max(0, _fx_param_int(param_store, "fx.glitch.edge_noise.count"))
        edge_alpha = _fx_param_float_nonneg(param_store, "fx.glitch.edge_noise.alpha")
        edge_size_mult = _fx_param_float_nonneg(param_store, "fx.glitch.edge_noise.size.mult")
        if band_w <= 1e-6 or count <= 0 or edge_alpha <= 1e-6 or edge_size_mult <= 1e-6:
            return
        seed = (self._fx_seed ^ (frame * 0x9E3779B1)) & 0xFFFFFFFF
        gl.glBegin(gl.GL_QUADS)
        for side_x in (0.0, w - band_w):
            s = (seed ^ (0xA341316C if side_x < 1.0 else 0xC8013EA4)) & 0xFFFFFFFF
            for _ in range(count):
                ry, s = _rand01(s)
                rh, s = _rand01(s)
                rx, s = _rand01(s)
                rw, s = _rand01(s)
                ra, s = _rand01(s)

                y0 = ry * h
                hh = (2.0 + rh * 18.0) * edge_size_mult
                x0 = side_x + rx * max(1.0, band_w - 2.0)
                ww = (1.0 + rw * 8.0) * edge_size_mult
                a = edge_alpha * (0.35 + 0.65 * ra)
                gl.glColor4f(base_r, base_g, base_b, a)
                gl.glVertex2f(x0, y0)
                gl.glVertex2f(x0 + ww, y0)
                gl.glVertex2f(x0 + ww, y0 + hh)
                gl.glVertex2f(x0, y0 + hh)
        gl.glEnd()

    def _draw_tear_subpass(noise_seed: int, *, pixel: float) -> int:
        # Occasional VHS tear band; more frequent while rezzing (but keep it
        # calmer during rez so the build visuals read cleanly).
        tear_chance = max(0.0, float(param_store.get("fx.glitch.tear.chance.base")))
        if self._rez_active:
            tear_chance *= max(0.0, float(param_store.get("fx.glitch.tear.chance.rez.mult")))
        r_tear, noise_seed = _rand01(noise_seed)
        if r_tear >= tear_chance or view_w_px <= 0 or view_h_px <= 0:
            return noise_seed

        gl.glEnable(gl.GL_SCISSOR_TEST)
        gl.glScissor(sidebar_px, 0, max(1, view_w_px), max(1, view_h_px))
        ry, noise_seed = _rand01(noise_seed)
        rh, noise_seed = _rand01(noise_seed)
        rx, noise_seed = _rand01(noise_seed)
        r_big, noise_seed = _rand01(noise_seed)
        r_scale, noise_seed = _rand01(noise_seed)
        y0 = ry * h
        hh = (10.0 + rh * 60.0) * pixel * ratio
        if r_big < (0.16 if self._rez_active else 0.07):
            hh *= 3.0 + 6.0 * r_scale
        hh *= max(0.0, float(param_store.get("fx.glitch.tear.height.mult")))
        hh = min(hh, h * 0.55)
        shift_amp_px = max(0.0, float(param_store.get("fx.glitch.tear.shift.px")))
        if self._rez_active:
            shift_amp_px *= max(0.0, float(param_store.get("fx.glitch.tear.shift.rez.mult")))
        shift = (rx - 0.5) * shift_amp_px * pixel
        a = max(0.0, float(param_store.get("fx.glitch.tear.alpha.base")))
        if self._rez_active:
            a *= max(0.0, float(param_store.get("fx.glitch.tear.alpha.rez.mult")))
        gl.glBegin(gl.GL_QUADS)
        gl.glColor4f(base_r, base_g, base_b, a)
        gl.glVertex2f(shift, y0)
        gl.glVertex2f(w + shift, y0)
        gl.glVertex2f(w + shift, y0 + hh)
        gl.glVertex2f(shift, y0 + hh)
        gl.glEnd()
        gl.glDisable(gl.GL_SCISSOR_TEST)
        return noise_seed

    def _draw_band_subpass(noise_seed: int, *, pixel: float) -> None:
        # Glitch effect #1: rolling interference bands (more during rez).
        band_seed = (noise_seed ^ 0xD1B54A35) & 0xFFFFFFFF
        band_chance = max(0.0, float(param_store.get("fx.glitch.band.chance")))
        if self._rez_active:
            band_chance *= max(0.0, float(param_store.get("fx.glitch.band.chance.rez.mult")))
        r_band, band_seed = _rand01(band_seed)
        if r_band >= band_chance or view_w_px <= 0 or view_h_px <= 0:
            return

        gl.glEnable(gl.GL_SCISSOR_TEST)
        gl.glScissor(sidebar_px, 0, max(1, view_w_px), max(1, view_h_px))
        band_count = max(0, param_store.get_int("fx.glitch.band.count"))
        if self._rez_active:
            band_count += max(0, param_store.get_int("fx.glitch.band.count.rez.extra"))
        band_count = max(0, min(256, band_count))
        band_alpha_scale = max(0.0, float(param_store.get("fx.glitch.band.alpha.base_mult")))
        if self._rez_active:
            band_alpha_scale *= max(0.0, float(param_store.get("fx.glitch.band.alpha.rez.mult")))
        band_height_mult = max(0.0, float(param_store.get("fx.glitch.band.height.mult")))
        band_shift_amp = max(0.0, float(param_store.get("fx.glitch.band.shift.base")))
        if self._rez_active:
            band_shift_amp *= max(0.0, float(param_store.get("fx.glitch.band.shift.rez.mult")))
        gl.glBegin(gl.GL_QUADS)
        s = band_seed
        for i in range(band_count):
            ry, s = _rand01(s)
            rh, s = _rand01(s)
            rc, s = _rand01(s)
            ra, s = _rand01(s)
            r_big, s = _rand01(s)
            r_scale, s = _rand01(s)
            y0 = ry * h
            hh = (8.0 + rh * 44.0) * pixel * ratio
            if r_big < (0.28 if self._rez_active else 0.13):
                hh *= 2.2 + 4.6 * r_scale
            hh *= band_height_mult
            hh = min(hh, h * 0.45)
            wob = math.sin((now * (1.2 + 0.35 * i)) + (ry * 9.0))
            shift = wob * band_shift_amp * pixel * ratio
            a = (0.016 + 0.03 * ra) * band_alpha_scale
            if rc < 0.72:
                r, g, b = base_r, base_g, base_b
            elif rc < 0.9:
                r, g, b = (amber_r, amber_g, amber_b)
            else:
                r, g, b = (green_r, green_g, green_b)
            gl.glColor4f(r, g, b, min(0.22, a))
            gl.glVertex2f(-24.0 + shift, y0)
            gl.glVertex2f(w + 24.0 + shift, y0)
            gl.glVertex2f(w + 24.0 + shift, y0 + hh)
            gl.glVertex2f(-24.0 + shift, y0 + hh)
        gl.glEnd()
        gl.glDisable(gl.GL_SCISSOR_TEST)

    def _draw_grain_subpass(*, noise_seed: int, pixel: float, noise_count: int, grain: float, grain_size_mult: float) -> None:
        # Full-screen noise: subtle always-on, but more glitchy during rez.
        if noise_count <= 0 or grain <= 1e-9 or grain_size_mult <= 1e-9:
            return
        gl.glBegin(gl.GL_QUADS)
        s = noise_seed
        for _ in range(noise_count):
            rx, s = _rand01(s)
            ry, s = _rand01(s)
            rw, s = _rand01(s)
            rh, s = _rand01(s)
            rc, s = _rand01(s)
            ra, s = _rand01(s)

            x0 = rx * w
            y0 = ry * h
            ww = (1.0 + rw * 8.0) * pixel * ratio * grain_size_mult
            hh = (1.0 + rh * 6.0) * pixel * ratio * grain_size_mult

            if rc < 0.82:
                r, g, b = base_r, base_g, base_b
            elif rc < 0.92:
                r, g, b = (amber_r, amber_g, amber_b)
            else:
                r, g, b = (green_r, green_g, green_b)

            a = grain * (0.25 + 0.75 * ra)
            gl.glColor4f(r, g, b, a)
            gl.glVertex2f(x0, y0)
            gl.glVertex2f(x0 + ww, y0)
            gl.glVertex2f(x0 + ww, y0 + hh)
            gl.glVertex2f(x0, y0 + hh)
        gl.glEnd()

    def _draw_macroblock_subpass(*, noise_seed: int, pixel: float) -> None:
        # Glitch effect #2: macroblock dropouts (compression-y squares).
        macro_seed = (noise_seed ^ 0x9E3779B9) & 0xFFFFFFFF
        macro_count = max(0, param_store.get_int("fx.glitch.macroblock.count"))
        if self._rez_active:
            macro_count += max(0, param_store.get_int("fx.glitch.macroblock.count.rez.extra"))
        macro_count = max(0, min(20000, macro_count))
        macro_alpha_scale = max(0.0, float(param_store.get("fx.glitch.macroblock.alpha.base_mult")))
        if self._rez_active:
            macro_alpha_scale *= max(0.0, float(param_store.get("fx.glitch.macroblock.alpha.rez.mult")))
        macro_size_mult = max(0.0, float(param_store.get("fx.glitch.macroblock.size.mult")))
        if macro_count <= 0 or macro_alpha_scale <= 1e-9 or macro_size_mult <= 1e-9:
            return

        gl.glBegin(gl.GL_QUADS)
        s = macro_seed
        for _ in range(macro_count):
            rx, s = _rand01(s)
            ry, s = _rand01(s)
            rw, s = _rand01(s)
            rh, s = _rand01(s)
            ra, s = _rand01(s)
            x0 = rx * w
            y0 = ry * h
            ww = (18.0 + rw * 86.0) * pixel * ratio * macro_size_mult
            hh = (10.0 + rh * 54.0) * pixel * ratio * macro_size_mult
            a = (0.008 + 0.018 * ra) * macro_alpha_scale
            gl.glColor4f(0.0, 0.0, 0.0, min(0.10, a))
            gl.glVertex2f(x0, y0)
            gl.glVertex2f(x0 + ww, y0)
            gl.glVertex2f(x0 + ww, y0 + hh)
            gl.glVertex2f(x0, y0 + hh)
        gl.glEnd()

    def _draw_vignette_fallback_subpass() -> None:
        # Vignette is now applied as a post-process (Ender tint) after
        # the rest of the render pass so it behaves like a "coating" on
        # the model view. Keep the old vignette as a fallback when GLSL
        # isn't available.
        strength = float(param_store.get("fx.glitch.vignette.strength"))
        if strength <= 1e-6 or int(self._ender_vignette_prog.value) or view_w_px <= 0 or view_h_px <= 0:
            return

        gl.glEnable(gl.GL_SCISSOR_TEST)
        gl.glScissor(sidebar_px, 0, max(1, view_w_px), max(1, view_h_px))
        vx0 = float(self.sidebar_width)
        vx1 = w
        vy0 = 0.0
        vy1 = h
        view_w = max(1.0, vx1 - vx0)
        thick = min(
            float(param_store.get("fx.glitch.vignette.thickness.max_px")),
            max(
                float(param_store.get("fx.glitch.vignette.thickness.min_px")),
                min(view_w, h) * float(param_store.get("fx.glitch.vignette.thickness.frac")),
            ),
        )
        # Dark purple tint (not pure black) so the viewer's "meta"
        # UI aesthetic bleeds into the edges.
        tint = 0.55
        r_v, g_v, b_v = (base_r * tint, base_g * tint, base_b * tint)
        a_edge = 0.10 * max(0.0, min(1.0, strength))
        gl.glBegin(gl.GL_QUADS)
        # Left
        gl.glColor4f(r_v, g_v, b_v, a_edge)
        gl.glVertex2f(vx0, vy0)
        gl.glColor4f(r_v, g_v, b_v, 0.0)
        gl.glVertex2f(vx0 + thick, vy0)
        gl.glVertex2f(vx0 + thick, vy1)
        gl.glColor4f(r_v, g_v, b_v, a_edge)
        gl.glVertex2f(vx0, vy1)
        # Right
        gl.glColor4f(r_v, g_v, b_v, 0.0)
        gl.glVertex2f(vx1 - thick, vy0)
        gl.glColor4f(r_v, g_v, b_v, a_edge)
        gl.glVertex2f(vx1, vy0)
        gl.glVertex2f(vx1, vy1)
        gl.glColor4f(r_v, g_v, b_v, 0.0)
        gl.glVertex2f(vx1 - thick, vy1)
        # Bottom
        gl.glColor4f(r_v, g_v, b_v, a_edge)
        gl.glVertex2f(vx0, vy0)
        gl.glVertex2f(vx1, vy0)
        gl.glColor4f(r_v, g_v, b_v, 0.0)
        gl.glVertex2f(vx1, vy0 + thick)
        gl.glVertex2f(vx0, vy0 + thick)
        # Top
        gl.glColor4f(r_v, g_v, b_v, 0.0)
        gl.glVertex2f(vx0, vy1 - thick)
        gl.glVertex2f(vx1, vy1 - thick)
        gl.glColor4f(r_v, g_v, b_v, a_edge)
        gl.glVertex2f(vx1, vy1)
        gl.glVertex2f(vx0, vy1)
        gl.glEnd()
        gl.glDisable(gl.GL_SCISSOR_TEST)

    def _draw_beam_and_spark_subpass() -> None:
        # Glitch beam + sparkles (model viewport only) — always on.
        if view_w_px <= 0 or view_h_px <= 0 or self.sidebar_width >= self.width:
            return

        vx0 = float(self.sidebar_width)
        vx1 = w
        cy = 0.5 * h

        gl.glEnable(gl.GL_SCISSOR_TEST)
        gl.glScissor(sidebar_px, 0, max(1, view_w_px), max(1, view_h_px))
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE)

        flicker_seed = (self._fx_seed ^ (frame * 0x9E3779B1) ^ 0xBEEFBEEF) & 0xFFFFFFFF
        rf, flicker_seed = _rand01(flicker_seed)
        flicker = 0.86 + 0.14 * math.sin((now * 24.0) + (rf * 10.0))
        flicker = max(0.45, min(1.35, flicker))

        beam_alpha = max(0.0, float(param_store.get("fx.glitch.beam.alpha")))
        if self._rez_active:
            beam_alpha *= max(0.0, float(param_store.get("fx.glitch.beam.rez.mult")))

        if beam_alpha > 1e-6:
            beam_thick = max(0.0, float(param_store.get("fx.glitch.beam.thick.px")))
            core_thick = max(1.0, beam_thick * 0.40)
            glow_thick = max(core_thick, beam_thick)
            a_glow = float(param_store.get("fx.glitch.beam.alpha.glow")) * beam_alpha * flicker
            a_core = float(param_store.get("fx.glitch.beam.alpha.core")) * beam_alpha * flicker
            y_core0 = max(0.0, cy - core_thick * 0.5)
            y_core1 = min(h, cy + core_thick * 0.5)
            y_glow0 = max(0.0, cy - glow_thick * 0.5)
            y_glow1 = min(h, cy + glow_thick * 0.5)

            gl.glBegin(gl.GL_QUADS)
            gl.glColor4f(base_r, base_g, base_b, a_glow)
            gl.glVertex2f(vx0, y_glow0)
            gl.glVertex2f(vx1, y_glow0)
            gl.glVertex2f(vx1, y_glow1)
            gl.glVertex2f(vx0, y_glow1)
            gl.glColor4f(min(1.0, base_r * 0.25 + 0.92), min(1.0, base_g * 0.25 + 0.92), 1.0, a_core)
            gl.glVertex2f(vx0, y_core0)
            gl.glVertex2f(vx1, y_core0)
            gl.glVertex2f(vx1, y_core1)
            gl.glVertex2f(vx0, y_core1)
            gl.glEnd()

        count_base = max(0, param_store.get_int("fx.glitch.spark.count"))
        count_mult = 1.0
        if self._rez_active:
            count_mult *= max(0.0, float(param_store.get("fx.glitch.spark.rez.mult")))
        spark_count = int(round(float(count_base) * count_mult))
        spark_count = max(0, min(20000, spark_count))
        if spark_count > 0 and beam_alpha > 1e-6:
            max_dy = max(2.0, float(param_store.get("fx.glitch.spark.spread.frac")) * h)
            density_exp = float(param_store.get("fx.glitch.spark.density.exp"))
            if density_exp < 0.01:
                density_exp = 0.01
            gl.glBegin(gl.GL_QUADS)
            s = flicker_seed ^ 0xC001D00D
            for _ in range(spark_count):
                rx, s = _rand01(s)
                rr, s = _rand01(s)
                ry, s = _rand01(s)
                rs, s = _rand01(s)
                ra, s = _rand01(s)
                x = vx0 + rx * max(1.0, vx1 - vx0)
                mag = abs(ry - 0.5) * 2.0
                mag = mag**density_exp
                dy = (-1.0 if ry < 0.5 else 1.0) * mag * max_dy
                y = cy + dy
                size = float(param_store.get("fx.glitch.spark.size.base")) + rs * float(
                    param_store.get("fx.glitch.spark.size.extra")
                )
                a = (
                    float(param_store.get("fx.glitch.spark.alpha.base"))
                    + float(param_store.get("fx.glitch.spark.alpha.extra")) * ra
                ) * beam_alpha
                if rr < 0.78:
                    r, g, b = base_r, base_g, base_b
                elif rr < 0.92:
                    r, g, b = (amber_r, amber_g, amber_b)
                else:
                    r, g, b = (green_r, green_g, green_b)
                gl.glColor4f(r, g, b, a)
                gl.glVertex2f(x, y)
                gl.glVertex2f(x + size, y)
                gl.glVertex2f(x + size, y + size)
                gl.glVertex2f(x, y + size)
            gl.glEnd()

        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glDisable(gl.GL_SCISSOR_TEST)

    _draw_scanline_subpass()
    _draw_edge_noise_subpass()

    # Full-screen noise: subtle always-on, but more glitchy during rez.
    noise_seed = (self._fx_seed ^ (frame * 0x27D4EB2D) ^ (0xBADC0FFE if self._rez_active else 0)) & 0xFFFFFFFF
    pixel = 1.0 / max(1.0, ratio)

    grain_count_base = max(0, param_store.get_int("fx.glitch.grain.count"))
    grain_count_mult = 1.0
    if self._rez_active:
        grain_count_mult = max(0.0, float(param_store.get("fx.glitch.grain.rez.mult")))
    noise_count = int(round(float(grain_count_base) * grain_count_mult))
    noise_count = max(0, min(20000, noise_count))

    grain = max(0.0, float(param_store.get("fx.glitch.grain.alpha.base")))
    if self._rez_active:
        grain *= max(0.0, float(param_store.get("fx.glitch.grain.alpha.rez.mult")))

    grain_size_mult = max(0.0, float(param_store.get("fx.glitch.grain.size.mult")))

    noise_seed = _draw_tear_subpass(noise_seed, pixel=pixel)
    _draw_band_subpass(noise_seed, pixel=pixel)
    _draw_grain_subpass(
        noise_seed=noise_seed,
        pixel=pixel,
        noise_count=noise_count,
        grain=grain,
        grain_size_mult=grain_size_mult,
    )
    _draw_macroblock_subpass(noise_seed=noise_seed, pixel=pixel)
    _draw_vignette_fallback_subpass()
    _draw_beam_and_spark_subpass()

    gl.glDepthMask(gl.GL_TRUE)
    gl.glEnable(gl.GL_TEXTURE_2D)



def apply_ender_vignette(self: Any, vp_w: int, vp_h: int, *, gl: Any, param_store: Any) -> None:
    # Post-process the *current* model viewport (after all other fx)
    # so this behaves like a screen "coating".
    if not self._effects_enabled:
        return
    if not int(self._ender_vignette_prog.value):
        return

    strength = float(param_store.get("fx.glitch.vignette.strength"))
    if strength <= 1e-6:
        return

    ratio = float(self.get_pixel_ratio())
    sidebar_px = int(max(0.0, float(self.sidebar_width)) * ratio)
    view_w_px = int(vp_w) - sidebar_px
    view_h_px = int(vp_h)
    if view_w_px <= 4 or view_h_px <= 4:
        return

    if not self._ensure_ender_vignette_tex(view_w_px, view_h_px):
        return

    try:
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._ender_vignette_tex)
        gl.glCopyTexSubImage2D(
            gl.GL_TEXTURE_2D,
            0,
            0,
            0,
            int(sidebar_px),
            0,
            int(view_w_px),
            int(view_h_px),
        )
    except Exception:
        try:
            gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        except Exception:
            pass
        return

    gl.glDisable(gl.GL_DEPTH_TEST)
    gl.glDisable(gl.GL_LIGHTING)
    gl.glDepthMask(gl.GL_FALSE)
    gl.glDisable(gl.GL_BLEND)

    gl.glViewport(0, 0, max(1, int(vp_w)), max(1, int(vp_h)))
    gl.glMatrixMode(gl.GL_PROJECTION)
    gl.glLoadIdentity()
    gl.glOrtho(0.0, float(self.width), 0.0, float(self.height), -1.0, 1.0)
    gl.glMatrixMode(gl.GL_MODELVIEW)
    gl.glLoadIdentity()

    gl.glEnable(gl.GL_SCISSOR_TEST)
    gl.glScissor(int(sidebar_px), 0, max(1, int(view_w_px)), max(1, int(view_h_px)))

    try:
        gl.glUseProgram(self._ender_vignette_prog)  # type: ignore[attr-defined]
    except Exception:
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glDisable(gl.GL_SCISSOR_TEST)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glDepthMask(gl.GL_TRUE)
        return

    try:
        try:
            gl.glUniform1i(self._ender_vignette_u_tex, 0)  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            gl.glUniform2f(
                self._ender_vignette_u_view_px,
                float(view_w_px),
                float(view_h_px),
            )  # type: ignore[attr-defined]
        except Exception:
            pass

        er, eg, eb = _fx_param_color_triplet(param_store, "fx.color.ender")
        try:
            gl.glUniform3f(self._ender_vignette_u_ender_rgb, er, eg, eb)  # type: ignore[attr-defined]
        except Exception:
            pass

        thick_frac = max(0.0, float(param_store.get("fx.glitch.vignette.thickness.frac")))
        thick_min = max(0.0, float(param_store.get("fx.glitch.vignette.thickness.min_px")))
        thick_max = max(thick_min, float(param_store.get("fx.glitch.vignette.thickness.max_px")))
        thick = max(thick_min, min(thick_max, min(float(view_w_px), float(view_h_px)) * thick_frac))

        falloff = float(param_store.get("fx.glitch.vignette.falloff.exp"))
        if falloff < 0.01:
            falloff = 0.01

        try:
            gl.glUniform1f(self._ender_vignette_u_strength, max(0.0, strength))  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            gl.glUniform1f(self._ender_vignette_u_thick_px, float(thick))  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            gl.glUniform1f(self._ender_vignette_u_falloff_exp, float(falloff))  # type: ignore[attr-defined]
        except Exception:
            pass

        gl.glEnable(gl.GL_TEXTURE_2D)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._ender_vignette_tex)
        gl.glColor4f(1.0, 1.0, 1.0, 1.0)
        x0 = float(self.sidebar_width)
        x1 = float(self.width)
        y0 = 0.0
        y1 = float(self.height)
        gl.glBegin(gl.GL_QUADS)
        gl.glTexCoord2f(0.0, 0.0)
        gl.glVertex2f(x0, y0)
        gl.glTexCoord2f(1.0, 0.0)
        gl.glVertex2f(x1, y0)
        gl.glTexCoord2f(1.0, 1.0)
        gl.glVertex2f(x1, y1)
        gl.glTexCoord2f(0.0, 1.0)
        gl.glVertex2f(x0, y1)
        gl.glEnd()
    finally:
        try:
            gl.glUseProgram(0)  # type: ignore[attr-defined]
        except Exception:
            pass
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glDisable(gl.GL_SCISSOR_TEST)
        gl.glDepthMask(gl.GL_TRUE)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)


def delete_ender_vignette(self: Any, *, gl: Any) -> None:
    try:
        if int(self._ender_vignette_tex.value):
            gl.glDeleteTextures(1, ctypes.byref(self._ender_vignette_tex))
    except Exception:
        pass
    self._ender_vignette_tex = gl.GLuint(0)
    self._ender_vignette_tex_w = 0
    self._ender_vignette_tex_h = 0

    try:
        if int(self._ender_vignette_prog.value):
            gl.glDeleteProgram(self._ender_vignette_prog)  # type: ignore[attr-defined]
    except Exception:
        pass
    self._ender_vignette_prog = gl.GLuint(0)
    self._ender_vignette_u_tex = -1
    self._ender_vignette_u_view_px = -1
    self._ender_vignette_u_ender_rgb = -1
    self._ender_vignette_u_strength = -1
    self._ender_vignette_u_thick_px = -1
    self._ender_vignette_u_falloff_exp = -1


def init_ender_vignette(self: Any, *, gl: Any) -> None:
    delete_ender_vignette(self, gl=gl)

    try:
        gl_create_shader = gl.glCreateShader  # type: ignore[attr-defined]
        gl_create_program = gl.glCreateProgram  # type: ignore[attr-defined]
        gl_shader_source = gl.glShaderSource  # type: ignore[attr-defined]
        gl_compile_shader = gl.glCompileShader  # type: ignore[attr-defined]
        gl_get_shader_iv = gl.glGetShaderiv  # type: ignore[attr-defined]
        gl_get_shader_info_log = gl.glGetShaderInfoLog  # type: ignore[attr-defined]
        gl_attach_shader = gl.glAttachShader  # type: ignore[attr-defined]
        gl_link_program = gl.glLinkProgram  # type: ignore[attr-defined]
        gl_get_program_iv = gl.glGetProgramiv  # type: ignore[attr-defined]
        gl_get_program_info_log = gl.glGetProgramInfoLog  # type: ignore[attr-defined]
        gl_delete_shader = gl.glDeleteShader  # type: ignore[attr-defined]
        gl_get_uniform_loc = gl.glGetUniformLocation  # type: ignore[attr-defined]
    except Exception:
        return

    def _compile(shader_type: int, src: str) -> int | None:
        shader = int(gl_create_shader(shader_type))
        if not shader:
            return None
        # pyglet's glShaderSource binding wants a `char**` (not
        # `char_p*`), so build an array of `POINTER(c_char)`.
        src_b = src.encode("utf-8")
        src_buf = ctypes.create_string_buffer(src_b)
        src_ptr = ctypes.cast(src_buf, ctypes.POINTER(ctypes.c_char))
        srcs = (ctypes.POINTER(ctypes.c_char) * 1)(src_ptr)
        lengths = (ctypes.c_int * 1)(len(src_b))
        gl_shader_source(shader, 1, srcs, lengths)
        gl_compile_shader(shader)
        ok = ctypes.c_int(0)
        gl_get_shader_iv(shader, gl.GL_COMPILE_STATUS, ctypes.byref(ok))
        if ok.value:
            return shader
        try:
            log_len = ctypes.c_int(0)
            gl_get_shader_iv(shader, gl.GL_INFO_LOG_LENGTH, ctypes.byref(log_len))
            buf = ctypes.create_string_buffer(max(1, int(log_len.value)))
            out_len = ctypes.c_int(0)
            gl_get_shader_info_log(shader, len(buf), ctypes.byref(out_len), buf)
            sys.stderr.write(buf.value.decode("utf-8", errors="replace") + "\n")
        except Exception:
            pass
        try:
            gl_delete_shader(shader)
        except Exception:
            pass
        return None

    vs_src = """#version 120
    varying vec2 v_uv;
    void main() {
      v_uv = gl_MultiTexCoord0.st;
      gl_Position = gl_ModelViewProjectionMatrix * gl_Vertex;
    }"""

    fs_src = """#version 120
    uniform sampler2D u_tex;
    uniform vec2 u_view_px;
    uniform vec3 u_ender_rgb;
    uniform float u_strength;
    uniform float u_thick_px;
    uniform float u_falloff_exp;
    varying vec2 v_uv;
    void main() {
      vec4 s = texture2D(u_tex, v_uv);
      vec3 orig = s.rgb;

      // Approx spectral multiply: grayscale(A) * enderpurple.
      float luma = dot(orig, vec3(0.299, 0.587, 0.114));
      vec3 tinted = luma * u_ender_rgb;

      float dx = min(v_uv.x, 1.0 - v_uv.x) * u_view_px.x;
      float dy = min(v_uv.y, 1.0 - v_uv.y) * u_view_px.y;
      // Rounded-corner distance: behaves like min(dx,dy) on edges,
      // but strengthens the effect near corners.
      float denom = max(1e-6, sqrt(dx*dx + dy*dy));
      float d = (dx * dy) / denom;
      float thick = max(1e-6, u_thick_px);
      float t = clamp(d / thick, 0.0, 1.0);
      float expv = max(0.01, u_falloff_exp);
      float mask = pow(1.0 - t, expv);
      float amt = clamp(u_strength * mask, 0.0, 1.0);

      vec3 out_rgb = mix(orig, tinted, amt);
      gl_FragColor = vec4(out_rgb, 1.0);
    }"""

    vs = _compile(gl.GL_VERTEX_SHADER, vs_src)
    fs = _compile(gl.GL_FRAGMENT_SHADER, fs_src)
    if vs is None or fs is None:
        try:
            if vs is not None:
                gl_delete_shader(vs)
        except Exception:
            pass
        try:
            if fs is not None:
                gl_delete_shader(fs)
        except Exception:
            pass
        return

    prog = int(gl_create_program())
    if not prog:
        try:
            gl_delete_shader(vs)
            gl_delete_shader(fs)
        except Exception:
            pass
        return

    gl_attach_shader(prog, vs)
    gl_attach_shader(prog, fs)
    gl_link_program(prog)
    ok = ctypes.c_int(0)
    gl_get_program_iv(prog, gl.GL_LINK_STATUS, ctypes.byref(ok))

    try:
        gl_delete_shader(vs)
        gl_delete_shader(fs)
    except Exception:
        pass

    if not ok.value:
        try:
            log_len = ctypes.c_int(0)
            gl_get_program_iv(prog, gl.GL_INFO_LOG_LENGTH, ctypes.byref(log_len))
            buf = ctypes.create_string_buffer(max(1, int(log_len.value)))
            out_len = ctypes.c_int(0)
            gl_get_program_info_log(prog, len(buf), ctypes.byref(out_len), buf)
            sys.stderr.write(buf.value.decode("utf-8", errors="replace") + "\n")
        except Exception:
            pass
        try:
            gl.glDeleteProgram(prog)  # type: ignore[attr-defined]
        except Exception:
            pass
        return

    self._ender_vignette_prog = gl.GLuint(prog)
    self._ender_vignette_u_tex = int(gl_get_uniform_loc(self._ender_vignette_prog, b"u_tex"))
    self._ender_vignette_u_view_px = int(gl_get_uniform_loc(self._ender_vignette_prog, b"u_view_px"))
    self._ender_vignette_u_ender_rgb = int(gl_get_uniform_loc(self._ender_vignette_prog, b"u_ender_rgb"))
    self._ender_vignette_u_strength = int(gl_get_uniform_loc(self._ender_vignette_prog, b"u_strength"))
    self._ender_vignette_u_thick_px = int(gl_get_uniform_loc(self._ender_vignette_prog, b"u_thick_px"))
    self._ender_vignette_u_falloff_exp = int(gl_get_uniform_loc(self._ender_vignette_prog, b"u_falloff_exp"))


def ensure_ender_vignette_tex(self: Any, w: int, h: int, *, gl: Any) -> bool:
    w = max(1, int(w))
    h = max(1, int(h))
    if int(self._ender_vignette_tex.value) and w == self._ender_vignette_tex_w and h == self._ender_vignette_tex_h:
        return True

    try:
        if int(self._ender_vignette_tex.value):
            gl.glDeleteTextures(1, ctypes.byref(self._ender_vignette_tex))
    except Exception:
        pass

    self._ender_vignette_tex = gl.GLuint(0)
    self._ender_vignette_tex_w = 0
    self._ender_vignette_tex_h = 0

    try:
        gl.glGenTextures(1, ctypes.byref(self._ender_vignette_tex))
        if not int(self._ender_vignette_tex.value):
            return False
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._ender_vignette_tex)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        gl.glTexImage2D(
            gl.GL_TEXTURE_2D,
            0,
            gl.GL_RGBA,
            w,
            h,
            0,
            gl.GL_RGBA,
            gl.GL_UNSIGNED_BYTE,
            None,
        )
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        self._ender_vignette_tex_w = w
        self._ender_vignette_tex_h = h
        return True
    except Exception:
        try:
            gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        except Exception:
            pass
        return False


def delete_ssao(self: Any, *, gl: Any) -> None:
    try:
        if int(self._ssao_prog.value):
            gl.glDeleteProgram(self._ssao_prog)  # type: ignore[attr-defined]
    except Exception:
        pass
    self._ssao_prog = gl.GLuint(0)
    self._ssao_u_color = -1
    self._ssao_u_depth = -1
    self._ssao_u_view_px = -1
    self._ssao_u_strength = -1
    self._ssao_u_radius_px = -1
    self._ssao_u_bias = -1
    self._ssao_u_brightness = -1
    self._ssao_u_near = -1
    self._ssao_u_far = -1
    self._ssao_u_is_ortho = -1


def init_ssao(self: Any, *, gl: Any) -> None:
    delete_ssao(self, gl=gl)

    try:
        gl_create_shader = gl.glCreateShader  # type: ignore[attr-defined]
        gl_create_program = gl.glCreateProgram  # type: ignore[attr-defined]
        gl_shader_source = gl.glShaderSource  # type: ignore[attr-defined]
        gl_compile_shader = gl.glCompileShader  # type: ignore[attr-defined]
        gl_get_shader_iv = gl.glGetShaderiv  # type: ignore[attr-defined]
        gl_get_shader_info_log = gl.glGetShaderInfoLog  # type: ignore[attr-defined]
        gl_attach_shader = gl.glAttachShader  # type: ignore[attr-defined]
        gl_link_program = gl.glLinkProgram  # type: ignore[attr-defined]
        gl_get_program_iv = gl.glGetProgramiv  # type: ignore[attr-defined]
        gl_get_program_info_log = gl.glGetProgramInfoLog  # type: ignore[attr-defined]
        gl_delete_shader = gl.glDeleteShader  # type: ignore[attr-defined]
        gl_get_uniform_loc = gl.glGetUniformLocation  # type: ignore[attr-defined]
    except Exception:
        return

    def _compile(shader_type: int, src: str) -> int | None:
        shader = int(gl_create_shader(shader_type))
        if not shader:
            return None
        src_b = src.encode("utf-8")
        src_buf = ctypes.create_string_buffer(src_b)
        src_ptr = ctypes.cast(src_buf, ctypes.POINTER(ctypes.c_char))
        srcs = (ctypes.POINTER(ctypes.c_char) * 1)(src_ptr)
        lengths = (ctypes.c_int * 1)(len(src_b))
        gl_shader_source(shader, 1, srcs, lengths)
        gl_compile_shader(shader)
        ok = ctypes.c_int(0)
        gl_get_shader_iv(shader, gl.GL_COMPILE_STATUS, ctypes.byref(ok))
        if ok.value:
            return shader
        try:
            log_len = ctypes.c_int(0)
            gl_get_shader_iv(shader, gl.GL_INFO_LOG_LENGTH, ctypes.byref(log_len))
            buf = ctypes.create_string_buffer(max(1, int(log_len.value)))
            out_len = ctypes.c_int(0)
            gl_get_shader_info_log(shader, len(buf), ctypes.byref(out_len), buf)
            sys.stderr.write(buf.value.decode("utf-8", errors="replace") + "\n")
        except Exception:
            pass
        try:
            gl_delete_shader(shader)
        except Exception:
            pass
        return None

    vs_src = """#version 120
    varying vec2 v_uv;
    void main() {
      v_uv = gl_MultiTexCoord0.st;
      gl_Position = gl_ModelViewProjectionMatrix * gl_Vertex;
    }"""

    fs_src = """#version 120
    uniform sampler2D u_color;
    uniform sampler2D u_depth;
    uniform vec2 u_view_px;
    uniform float u_strength;
    uniform float u_radius_px;
    uniform float u_bias;
    uniform float u_brightness;
    uniform float u_near;
    uniform float u_far;
    uniform float u_is_ortho;
    varying vec2 v_uv;

    float linear_depth(float depth01) {
      if (u_is_ortho > 0.5) {
        return u_near + depth01 * (u_far - u_near);
      }
      float z = depth01 * 2.0 - 1.0;
      return (2.0 * u_near * u_far) / (u_far + u_near - z * (u_far - u_near));
    }

    void main() {
      vec4 c = texture2D(u_color, v_uv);
      float d0 = texture2D(u_depth, v_uv).r;
      if (d0 >= 0.99999) {
        gl_FragColor = c;
        return;
      }
      float z0 = linear_depth(d0);
      vec2 inv_px = vec2(1.0) / max(u_view_px, vec2(1.0, 1.0));
      float r = max(0.0, u_radius_px);
      vec2 step = inv_px * r;
      float occ = 0.0;
      float n = 0.0;

      vec2 o;
      float dz;

      o = vec2( 1.0,  0.0) * step;
      dz = z0 - linear_depth(texture2D(u_depth, v_uv + o).r);
      occ += clamp((dz - u_bias) * 0.5, 0.0, 1.0); n += 1.0;

      o = vec2(-1.0,  0.0) * step;
      dz = z0 - linear_depth(texture2D(u_depth, v_uv + o).r);
      occ += clamp((dz - u_bias) * 0.5, 0.0, 1.0); n += 1.0;

      o = vec2( 0.0,  1.0) * step;
      dz = z0 - linear_depth(texture2D(u_depth, v_uv + o).r);
      occ += clamp((dz - u_bias) * 0.5, 0.0, 1.0); n += 1.0;

      o = vec2( 0.0, -1.0) * step;
      dz = z0 - linear_depth(texture2D(u_depth, v_uv + o).r);
      occ += clamp((dz - u_bias) * 0.5, 0.0, 1.0); n += 1.0;

      o = vec2( 1.0,  1.0) * step;
      dz = z0 - linear_depth(texture2D(u_depth, v_uv + o).r);
      occ += clamp((dz - u_bias) * 0.35, 0.0, 1.0); n += 1.0;

      o = vec2(-1.0,  1.0) * step;
      dz = z0 - linear_depth(texture2D(u_depth, v_uv + o).r);
      occ += clamp((dz - u_bias) * 0.35, 0.0, 1.0); n += 1.0;

      o = vec2( 1.0, -1.0) * step;
      dz = z0 - linear_depth(texture2D(u_depth, v_uv + o).r);
      occ += clamp((dz - u_bias) * 0.35, 0.0, 1.0); n += 1.0;

      o = vec2(-1.0, -1.0) * step;
      dz = z0 - linear_depth(texture2D(u_depth, v_uv + o).r);
      occ += clamp((dz - u_bias) * 0.35, 0.0, 1.0); n += 1.0;

      float ao = 1.0;
      if (n > 0.0) {
        ao = 1.0 - u_strength * (occ / n);
      }
      ao = clamp(ao, 0.0, 1.0);
      vec3 rgb = c.rgb * ao;
      rgb *= max(0.0, u_brightness);
      gl_FragColor = vec4(clamp(rgb, 0.0, 1.0), c.a);
    }"""

    vs = _compile(gl.GL_VERTEX_SHADER, vs_src)
    fs = _compile(gl.GL_FRAGMENT_SHADER, fs_src)
    if vs is None or fs is None:
        try:
            if vs is not None:
                gl_delete_shader(vs)
        except Exception:
            pass
        try:
            if fs is not None:
                gl_delete_shader(fs)
        except Exception:
            pass
        return

    prog = int(gl_create_program())
    if not prog:
        try:
            gl_delete_shader(vs)
            gl_delete_shader(fs)
        except Exception:
            pass
        return

    gl_attach_shader(prog, vs)
    gl_attach_shader(prog, fs)
    gl_link_program(prog)
    ok = ctypes.c_int(0)
    gl_get_program_iv(prog, gl.GL_LINK_STATUS, ctypes.byref(ok))

    try:
        gl_delete_shader(vs)
        gl_delete_shader(fs)
    except Exception:
        pass

    if not ok.value:
        try:
            log_len = ctypes.c_int(0)
            gl_get_program_iv(prog, gl.GL_INFO_LOG_LENGTH, ctypes.byref(log_len))
            buf = ctypes.create_string_buffer(max(1, int(log_len.value)))
            out_len = ctypes.c_int(0)
            gl_get_program_info_log(prog, len(buf), ctypes.byref(out_len), buf)
            sys.stderr.write(buf.value.decode("utf-8", errors="replace") + "\n")
        except Exception:
            pass
        try:
            gl.glDeleteProgram(prog)  # type: ignore[attr-defined]
        except Exception:
            pass
        return

    self._ssao_prog = gl.GLuint(prog)
    self._ssao_u_color = int(gl_get_uniform_loc(self._ssao_prog, b"u_color"))
    self._ssao_u_depth = int(gl_get_uniform_loc(self._ssao_prog, b"u_depth"))
    self._ssao_u_view_px = int(gl_get_uniform_loc(self._ssao_prog, b"u_view_px"))
    self._ssao_u_strength = int(gl_get_uniform_loc(self._ssao_prog, b"u_strength"))
    self._ssao_u_radius_px = int(gl_get_uniform_loc(self._ssao_prog, b"u_radius_px"))
    self._ssao_u_bias = int(gl_get_uniform_loc(self._ssao_prog, b"u_bias"))
    self._ssao_u_brightness = int(gl_get_uniform_loc(self._ssao_prog, b"u_brightness"))
    self._ssao_u_near = int(gl_get_uniform_loc(self._ssao_prog, b"u_near"))
    self._ssao_u_far = int(gl_get_uniform_loc(self._ssao_prog, b"u_far"))
    self._ssao_u_is_ortho = int(gl_get_uniform_loc(self._ssao_prog, b"u_is_ortho"))



def draw_camera_safety_overlay(
    self: Any,
    *,
    sidebar_px: int,
    view_w_px: int,
    view_h_px: int,
    gl: Any,
    param_store: Any,
) -> None:
    inside_p, low_p = self._camera_safety_strengths()
    strength = max(float(inside_p), float(low_p))
    if strength <= 1e-6:
        return

    base_r, base_g, base_b = _fx_param_color_triplet(param_store, "fx.color.ender")

    x0 = float(self.sidebar_width)
    x1 = float(self.width)
    y0 = 0.0
    y1 = float(self.height)
    if x1 - x0 <= 2.0 or y1 - y0 <= 2.0:
        return

    now = time.monotonic()
    tick_hz = max(1, _fx_param_int(param_store, "fx.glitch.void_wash.tick_hz", default=1))
    frame = int(now * float(tick_hz))
    seed = (self._fx_seed ^ (frame * 0x9E3779B1) ^ 0xA11CE5AF) & 0xFFFFFFFF
    rng = random.Random(seed)

    gl.glDisable(gl.GL_TEXTURE_2D)
    gl.glDisable(gl.GL_DEPTH_TEST)
    gl.glDepthMask(gl.GL_FALSE)
    gl.glEnable(gl.GL_BLEND)
    gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
    gl.glEnable(gl.GL_SCISSOR_TEST)
    gl.glScissor(int(sidebar_px), 0, max(1, int(view_w_px)), max(1, int(view_h_px)))

    try:
        wash = max(0.0, min(1.0, strength))
        wash_exp = float(param_store.get("fx.glitch.void_wash.exp"))
        if wash_exp < 1e-3:
            wash_exp = 1e-3
        wash = wash**wash_exp
        wash_opacity = max(0.0, min(1.0, float(param_store.get("fx.glitch.void_wash.opacity"))))
        wash_tint = max(0.0, float(param_store.get("fx.glitch.void_wash.tint.mult")))
        gl.glColor4f(base_r * wash_tint, base_g * wash_tint, base_b * wash_tint, wash_opacity * wash)
        gl.glBegin(gl.GL_QUADS)
        gl.glVertex2f(x0, y0)
        gl.glVertex2f(x1, y0)
        gl.glVertex2f(x1, y1)
        gl.glVertex2f(x0, y1)
        gl.glEnd()

        if low_p > 1e-6:
            strips = max(0, param_store.get_int("fx.glitch.void_wash.strips.count"))
            strip_alpha = max(0.0, float(param_store.get("fx.glitch.void_wash.strips.alpha")))
            gl.glBegin(gl.GL_QUADS)
            for i in range(strips):
                t0 = float(i) / float(strips)
                t1 = float(i + 1) / float(strips)
                p0 = (1.0 - t0) ** 2
                p1 = (1.0 - t1) ** 2
                a0 = strip_alpha * low_p * p0
                a1 = strip_alpha * low_p * p1
                yy0 = y0 + t0 * (y1 - y0)
                yy1 = y0 + t1 * (y1 - y0)
                gl.glColor4f(base_r * 0.22, base_g * 0.22, base_b * 0.22, a0)
                gl.glVertex2f(x0, yy0)
                gl.glVertex2f(x1, yy0)
                gl.glColor4f(base_r * 0.22, base_g * 0.22, base_b * 0.22, a1)
                gl.glVertex2f(x1, yy1)
                gl.glVertex2f(x0, yy1)
            gl.glEnd()

        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE)
        tile_px = max(0.0, float(param_store.get("fx.glitch.void_wash.sparks.tile.px")))
        tile = tile_px / max(1.0, float(self.get_pixel_ratio()))
        n = int(round(float(60 + 240 * strength + 260 * inside_p) * float(param_store.get("fx.glitch.void_wash.sparks.count.mult"))))
        n = max(0, min(20000, n))
        size_mult = max(0.0, float(param_store.get("fx.glitch.void_wash.sparks.size.mult")))
        alpha_mult = max(0.0, float(param_store.get("fx.glitch.void_wash.sparks.alpha.mult")))
        gl.glBegin(gl.GL_QUADS)
        for _ in range(n):
            rx = rng.random()
            ry = rng.random()
            rs = rng.random()
            ra = rng.random()
            size = (tile + rs * tile * 8.0) * (0.65 + 0.85 * strength) * size_mult
            xx = x0 + rx * (x1 - x0)
            yy = y0 + ry * (y1 - y0)
            a = (0.016 + 0.095 * ra) * (0.35 + 0.65 * strength) * (0.70 + 0.30 * inside_p) * alpha_mult
            gl.glColor4f(base_r, base_g, base_b, a)
            gl.glVertex2f(xx, yy)
            gl.glVertex2f(xx + size, yy)
            gl.glVertex2f(xx + size, yy + size)
            gl.glVertex2f(xx, yy + size)
        gl.glEnd()
    finally:
        gl.glDisable(gl.GL_SCISSOR_TEST)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
    gl.glDepthMask(gl.GL_TRUE)



def draw_structure_delta_fade_overlays(self: Any, *, gl: Any, param_store: Any, stable_seed: Any) -> None:
    fades = getattr(self, "_structure_delta_fades", None)
    if fades is None:
        fade_single = getattr(self, "_structure_delta_fade", None)
        fades = [fade_single] if fade_single is not None else []
    if not fades:
        return

    def _draw_one(fade: StructureDeltaFade) -> None:
        if fade.duration_s <= 0.0:
            return
        now = time.monotonic()
        if now < float(fade.start_t):
            return
        p = (now - float(fade.start_t)) / float(fade.duration_s)
        if p <= 0.0:
            p = 0.0
        if p >= 1.0:
            p = 1.0

        want_added = fade.added_batch is not None and p > 0.0
        want_removed = fade.removed_batch is not None and p < 1.0
        if not want_added and not want_removed:
            return
        try:
            use_stipple = bool(int(param_store.get_int("rez.fade.mode")))
        except Exception:
            use_stipple = True
        self._dbg_last_struct_delta_use_stipple = bool(use_stipple)

        if use_stipple:
            # Stipple fade: avoids blending/sorting and keeps depth test crisp.
            phase_tick, style, cell, sq_exp, sq_jit = _stipple_runtime_params(param_store, now=now)
            seed_base = int(stable_seed("rez-delta", int(fade.start_t * 1000.0), int(fade.start_t * 1e6))) & 0xFFFFFFFF
            try:
                gl.glEnable(gl.GL_POLYGON_STIPPLE)
                try:
                    gl.glEnable(gl.GL_POLYGON_OFFSET_FILL)
                except Exception:
                    pass

                if want_removed and fade.removed_batch is not None:
                    try:
                        gl.glPolygonOffset(1.0, 1.0)
                    except Exception:
                        pass
                    lvl = int(round((1.0 - p) * 64.0))
                    if lvl > 0:
                        px = int(phase_tick) + int(seed_base & 0xFFFF)
                        py = int(phase_tick) + int((seed_base >> 16) & 0xFFFF)
                        gl.glPolygonStipple(
                            polygon_stipple_pattern(
                                lvl,
                                phase_x=int(px) ^ 3,
                                phase_y=int(py) ^ 5,
                                seed=int(seed_base) ^ 0xA53A9E37,
                                style=int(style),
                                cell=int(cell),
                                square_exp=float(sq_exp),
                                square_jitter=int(sq_jit),
                            )
                        )
                        fade.removed_batch.draw()  # type: ignore[attr-defined]

                if want_added and fade.added_batch is not None:
                    try:
                        gl.glPolygonOffset(-1.0, -1.0)
                    except Exception:
                        pass
                    lvl = int(round(p * 64.0))
                    if lvl > 0:
                        px = int(phase_tick) + int((seed_base ^ 0x5C2D1B9A) & 0xFFFF)
                        py = int(phase_tick) + int(((seed_base ^ 0xA53A9E37) >> 16) & 0xFFFF)
                        gl.glPolygonStipple(
                            polygon_stipple_pattern(
                                lvl,
                                phase_x=int(px),
                                phase_y=int(py) ^ 2,
                                seed=int(seed_base) ^ 0x5C2D1B9A,
                                style=int(style),
                                cell=int(cell),
                                square_exp=float(sq_exp),
                                square_jitter=int(sq_jit),
                            )
                        )
                        fade.added_batch.draw()  # type: ignore[attr-defined]
            finally:
                try:
                    gl.glDisable(gl.GL_POLYGON_OFFSET_FILL)
                except Exception:
                    pass
                try:
                    gl.glDisable(gl.GL_POLYGON_STIPPLE)
                except Exception:
                    pass
            return

        # Alpha fade: smooth transparency (ghosty), no stipple.
        polygon_offset = False
        alpha_test_prev = False
        try:
            # Be defensive: if polygon stipple leaked from some other pass,
            # alpha fades will still look "screen-door". Ensure it's off.
            try:
                gl.glDisable(gl.GL_POLYGON_STIPPLE)
            except Exception:
                pass
            try:
                alpha_test_prev = bool(gl.glIsEnabled(gl.GL_ALPHA_TEST))
            except Exception:
                alpha_test_prev = False
            if alpha_test_prev:
                try:
                    gl.glDisable(gl.GL_ALPHA_TEST)
                except Exception:
                    pass
            try:
                gl.glEnable(gl.GL_POLYGON_OFFSET_FILL)
                gl.glPolygonOffset(1.0, 1.0)
                polygon_offset = True
            except Exception:
                polygon_offset = False
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            gl.glDepthMask(gl.GL_FALSE)

            if want_removed and fade.removed_batch is not None:
                a = max(0.0, min(1.0, 1.0 - float(p)))
                gl.glColor4f(1.0, 1.0, 1.0, float(a))
                fade.removed_batch.draw()  # type: ignore[attr-defined]

            if want_added and fade.added_batch is not None:
                a = max(0.0, min(1.0, float(p)))
                gl.glColor4f(1.0, 1.0, 1.0, float(a))
                fade.added_batch.draw()  # type: ignore[attr-defined]
        finally:
            gl.glDepthMask(gl.GL_TRUE)
            gl.glColor4f(1.0, 1.0, 1.0, 1.0)
            try:
                gl.glDisable(gl.GL_BLEND)
            except Exception:
                pass
            if alpha_test_prev:
                try:
                    gl.glEnable(gl.GL_ALPHA_TEST)
                except Exception:
                    pass
            if polygon_offset:
                try:
                    gl.glDisable(gl.GL_POLYGON_OFFSET_FILL)
                except Exception:
                    pass

    # Draw oldest -> newest so newer overlays can sit "on top" visually.
    for fade in list(fades):
        if fade is None:
            continue
        _draw_one(fade)


def draw_rez_live_preview_chunks(self: Any, *, gl: Any, param_store: Any) -> None:
    if not self._rez_active:
        return
    chunks = getattr(self, "_rez_live_chunks", None)
    if not chunks:
        return
    now = time.monotonic()
    try:
        use_stipple = bool(int(param_store.get_int("rez.fade.mode")))
    except Exception:
        use_stipple = True
    self._dbg_last_rez_live_use_stipple = bool(use_stipple)

    if not use_stipple:
        # Alpha fade live preview.
        alpha_test_prev = False
        # Be defensive: if polygon stipple leaked from some other pass,
        # alpha fades will still look "screen-door". Ensure it's off.
        try:
            gl.glDisable(gl.GL_POLYGON_STIPPLE)
        except Exception:
            pass
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glDepthMask(gl.GL_FALSE)
        try:
            try:
                alpha_test_prev = bool(gl.glIsEnabled(gl.GL_ALPHA_TEST))
            except Exception:
                alpha_test_prev = False
            if alpha_test_prev:
                try:
                    gl.glDisable(gl.GL_ALPHA_TEST)
                except Exception:
                    pass
            for ch in list(chunks):
                try:
                    dur = float(getattr(ch, "duration_s", 0.0))
                    start_t = float(getattr(ch, "start_t", 0.0))
                except Exception:
                    dur = 0.0
                    start_t = 0.0
                if dur <= 0.0:
                    continue
                p = (now - start_t) / dur
                if p <= 0.0:
                    continue
                if p >= 1.0:
                    p = 1.0
                gl.glColor4f(1.0, 1.0, 1.0, float(p))
                try:
                    ch.batch.draw()  # type: ignore[attr-defined]
                except Exception:
                    pass
        finally:
            gl.glDepthMask(gl.GL_TRUE)
            gl.glColor4f(1.0, 1.0, 1.0, 1.0)
            try:
                gl.glDisable(gl.GL_BLEND)
            except Exception:
                pass
            if alpha_test_prev:
                try:
                    gl.glEnable(gl.GL_ALPHA_TEST)
                except Exception:
                    pass
        return

    phase_tick, style, cell, sq_exp, sq_jit = _stipple_runtime_params(param_store, now=now)
    try:
        gl.glEnable(gl.GL_POLYGON_STIPPLE)
    except Exception:
        # No stipple support; draw normally.
        for ch in list(chunks):
            try:
                ch.batch.draw()  # type: ignore[attr-defined]
            except Exception:
                pass
        return

    try:
        done: list[RezLiveFadeChunk] = []
        fading: list[tuple[RezLiveFadeChunk, int, int, int, int]] = []

        for ch in list(chunks):
            try:
                dur = float(getattr(ch, "duration_s", 0.0))
                start_t = float(getattr(ch, "start_t", 0.0))
                seed = int(getattr(ch, "phase_seed", 0))
            except Exception:
                dur = 0.0
                start_t = 0.0
                seed = 0
            if dur <= 0.0:
                done.append(ch)
                continue
            p = (now - start_t) / dur
            if p >= 1.0:
                done.append(ch)
                continue
            if p <= 0.0:
                continue
            lvl = int(round(p * 64.0))
            if lvl <= 0:
                continue
            if int(style) == 2:
                px = int(phase_tick) + int(seed & 0xFFFF)
                py = int(phase_tick) + int((seed >> 16) & 0xFFFF)
            else:
                px = int(phase_tick) + int(seed & 7)
                py = int(phase_tick >> 8) + int((seed >> 3) & 7)
            fading.append((ch, int(lvl), int(px), int(py), int(seed)))

        # Draw fully-visible chunks without stipple.
        if done:
            try:
                gl.glDisable(gl.GL_POLYGON_STIPPLE)
            except Exception:
                pass
            for ch in done:
                try:
                    ch.batch.draw()  # type: ignore[attr-defined]
                except Exception:
                    pass
            try:
                gl.glEnable(gl.GL_POLYGON_STIPPLE)
            except Exception:
                pass

        # Draw fading chunks with per-chunk stipple.
        for ch, lvl, px, py, seed in fading:
            try:
                gl.glPolygonStipple(
                    polygon_stipple_pattern(
                        int(lvl),
                        phase_x=int(px),
                        phase_y=int(py),
                        seed=int(seed),
                        style=int(style),
                        cell=int(cell),
                        square_exp=float(sq_exp),
                        square_jitter=int(sq_jit),
                    )
                )
            except Exception:
                pass
            try:
                ch.batch.draw()  # type: ignore[attr-defined]
            except Exception:
                pass
    finally:
        try:
            gl.glDisable(gl.GL_POLYGON_STIPPLE)
        except Exception:
            pass



def draw_env_patch_stipple_fades(
    self: Any,
    *,
    gl: Any,
    param_store: Any,
    stable_seed: Any,
    pyglet_mod: Any,
    group_cache: dict[str, Any],
    no_tex_group: Any,
) -> None:
    """Draw terrain patches that are currently fade-in, using stipple fade."""
    try:
        if not bool(int(param_store.get_int("rez.fade.mode"))):
            return
    except Exception:
        return
    fades = self._env_patch_fade
    if not fades:
        return
    try:
        fade_s = float(param_store.get("rez.fade_s"))
    except Exception:
        fade_s = 5.0
    if fade_s < 0.05:
        fade_s = 0.05
    now = time.monotonic()
    phase_tick, style, cell, sq_exp, sq_jit = _stipple_runtime_params(param_store, now=now)
    try:
        lvl_jit = int(param_store.get_int("rez.fade.stipple.lvl_jitter"))
    except Exception:
        lvl_jit = 0
    if lvl_jit < 0:
        lvl_jit = 0
    if lvl_jit > 16:
        lvl_jit = 16
    try:
        gl.glEnable(gl.GL_POLYGON_STIPPLE)
    except Exception:
        return
    polygon_offset = False
    current_group: Any = None
    try:
        try:
            gl.glDisable(gl.GL_BLEND)
        except Exception:
            pass
        try:
            gl.glDisable(gl.GL_ALPHA_TEST)
        except Exception:
            pass
        gl.glDepthMask(gl.GL_TRUE)
        try:
            gl.glEnable(gl.GL_POLYGON_OFFSET_FILL)
            gl.glPolygonOffset(1.0, 1.0)
            polygon_offset = True
        except Exception:
            polygon_offset = False

        vtx_sub_cls = pyglet_mod.graphics.vertexdomain.VertexList
        for key in sorted(fades.keys()):
            entry = fades.get(key)
            if entry is None:
                continue
            start_t, ranges, _last_a = entry
            p = (now - float(start_t)) / float(fade_s)
            if p <= 0.0 or p >= 1.0:
                continue
            seed = int(stable_seed("env-stipple", int(key[0]), int(key[1]), int(self._env_height_seed)))
            px_base = int(phase_tick) + int(seed & 7)
            if int(style) == 2:
                py_base = int(phase_tick) + int((seed >> 3) & 7)
            else:
                py_base = int(phase_tick >> 8) + int((seed >> 3) & 7)

            for kind, jar_rel, start, count, target_a in list(ranges):
                if int(count) <= 0:
                    continue
                lvl = int(round((p * float(int(target_a)) / 255.0) * 64.0))
                if lvl <= 0:
                    continue
                if lvl > 64:
                    lvl = 64
                seed2 = int(seed)
                if int(style) == 2:
                    seed2 = (
                        int(seed)
                        ^ (int(start) * 0x9E3779B1)
                        ^ (int(target_a) * 0x85EBCA6B)
                        ^ (0xA53A9E37 if kind == "tex" else 0x5C2D1B9A)
                    ) & 0xFFFFFFFF
                px = int(px_base)
                py = int(py_base)
                if int(style) == 2:
                    # De-sync the square’s motion per-range so multiple
                    # fades don’t “line up” into see-through tunnels.
                    px = int(px_base) + int(seed2 & 0xFFFF)
                    py = int(py_base) + int((seed2 >> 16) & 0xFFFF)
                try:
                    gl.glPolygonStipple(
                        polygon_stipple_pattern(
                            int(lvl),
                            phase_x=int(px),
                            phase_y=int(py),
                            seed=int(seed2),
                            style=int(style),
                            cell=int(cell),
                            square_exp=float(sq_exp),
                            square_jitter=int(sq_jit),
                        )
                    )
                except Exception:
                    pass

                if kind == "tex":
                    vl_full = self._env_tex_vlists.get(str(jar_rel))
                    group = group_cache.get(str(jar_rel))
                else:
                    vl_full = self._env_colored_vlist
                    group = no_tex_group
                if vl_full is None or group is None:
                    continue
                if current_group is not group:
                    if current_group is not None:
                        try:
                            current_group.unset_state()
                        except Exception:
                            pass
                    try:
                        group.set_state()
                    except Exception:
                        pass
                    current_group = group
                start_abs = int(vl_full.start) + int(start)
                sub_vl = vtx_sub_cls(vl_full.domain, start_abs, int(count))
                sub_vl.draw(gl.GL_TRIANGLES)
    finally:
        if current_group is not None:
            try:
                current_group.unset_state()
            except Exception:
                pass
        if polygon_offset:
            try:
                gl.glDisable(gl.GL_POLYGON_OFFSET_FILL)
            except Exception:
                pass
        try:
            gl.glDisable(gl.GL_POLYGON_STIPPLE)
        except Exception:
            pass


def draw_env_strip_stipple_fade(
    self: Any,
    *,
    gl: Any,
    param_store: Any,
    stable_seed: Any,
    pyglet_mod: Any,
    group_cache: dict[str, Any],
    no_tex_group: Any,
) -> None:
    """Draw terrain strip-fade (alpha<1.0) using stipple fade (no blending).

    This replaces the old blended transparent-terrain pass when
    `rez.fade.mode=1`.
    """
    try:
        if not bool(int(param_store.get_int("rez.fade.mode"))):
            return
    except Exception:
        return
    patch_has_t = self._env_patch_has_transparency
    if not patch_has_t:
        return
    fades = self._env_patch_fade
    patch_ranges = self._env_patch_ranges
    if not patch_ranges:
        return
    now = time.monotonic()
    phase_tick, style, cell, sq_exp, sq_jit = _stipple_runtime_params(param_store, now=now)
    try:
        lvl_jit = int(param_store.get_int("rez.fade.stipple.lvl_jitter"))
    except Exception:
        lvl_jit = 0
    if lvl_jit < 0:
        lvl_jit = 0
    if lvl_jit > 16:
        lvl_jit = 16
    try:
        gl.glEnable(gl.GL_POLYGON_STIPPLE)
    except Exception:
        return
    polygon_offset = False
    current_group: Any = None
    try:
        try:
            gl.glDisable(gl.GL_BLEND)
        except Exception:
            pass
        try:
            gl.glDisable(gl.GL_ALPHA_TEST)
        except Exception:
            pass
        gl.glDepthMask(gl.GL_TRUE)
        try:
            gl.glEnable(gl.GL_POLYGON_OFFSET_FILL)
            gl.glPolygonOffset(1.0, 1.0)
            polygon_offset = True
        except Exception:
            polygon_offset = False

        # Performance: avoid changing the stipple mask for every tiny
        # sub-range. Instead, bucket by stipple "lvl" and draw all
        # geometry for that lvl together.
        #
        # We also split into a tiny set of spatial groups (2×2 parity)
        # so holes don't line up perfectly across the whole terrain.
        bucket_tex: dict[int, dict[int, dict[str, list[tuple[int, int]]]]] = {}
        bucket_col: dict[int, dict[int, list[tuple[int, int]]]] = {}

        def _hash32(v: int) -> int:
            x = int(v) & 0xFFFFFFFF
            x ^= x >> 16
            x = (x * 0x7FEB352D) & 0xFFFFFFFF
            x ^= x >> 15
            x = (x * 0x846CA68B) & 0xFFFFFFFF
            x ^= x >> 16
            return x & 0xFFFFFFFF

        for key in patch_has_t.keys():
            if not patch_has_t.get(key, False):
                continue
            if key in fades:
                continue
            ranges = patch_ranges.get(key)
            if not ranges:
                continue
            grp = ((int(key[0]) & 1) << 1) | (int(key[1]) & 1)
            patch_seed = (
                (int(key[0]) * 0x9E3779B1) ^ (int(key[1]) * 0x85EBCA6B) ^ int(self._env_height_seed)
            ) & 0xFFFFFFFF
            for kind, jar_rel, start, count, target_a in ranges:
                if int(count) <= 0:
                    continue
                ta = int(target_a)
                if ta <= 0 or ta >= 255:
                    continue
                lvl = int(round((float(ta) / 255.0) * 64.0))
                if lvl <= 0:
                    continue
                if lvl > 64:
                    lvl = 64
                if lvl_jit:
                    kind_tag = 0xA53A9E37 if kind == "tex" else 0x5C2D1B9A
                    h = _hash32(int(patch_seed) ^ (int(start) * 0xD1B54A35) ^ (int(ta) * 0x94D049BB) ^ int(kind_tag))
                    j = int(h % (2 * int(lvl_jit) + 1)) - int(lvl_jit)
                    lvl = int(lvl) + int(j)
                    if lvl < 1:
                        lvl = 1
                    if lvl > 64:
                        lvl = 64
                if kind == "tex":
                    j = str(jar_rel)
                    bucket_tex.setdefault(int(lvl), {}).setdefault(int(grp), {}).setdefault(j, []).append(
                        (int(start), int(count))
                    )
                else:
                    bucket_col.setdefault(int(lvl), {}).setdefault(int(grp), []).append((int(start), int(count)))

        def _merge_ranges(rngs: list[tuple[int, int]]) -> list[tuple[int, int]]:
            if not rngs:
                return []
            rngs2 = sorted((int(s), int(c)) for (s, c) in rngs if int(c) > 0)
            if not rngs2:
                return []
            out: list[tuple[int, int]] = []
            cur_s = int(rngs2[0][0])
            cur_e = cur_s + int(rngs2[0][1])
            for s, c in rngs2[1:]:
                s = int(s)
                e = s + int(c)
                if s <= cur_e:
                    if e > cur_e:
                        cur_e = e
                    continue
                out.append((cur_s, int(cur_e - cur_s)))
                cur_s = s
                cur_e = e
            out.append((cur_s, int(cur_e - cur_s)))
            return out

        vtx_sub_cls = pyglet_mod.graphics.vertexdomain.VertexList
        vl_col = self._env_colored_vlist
        for lvl in sorted(set(bucket_tex.keys()) | set(bucket_col.keys())):
            for grp in range(4):
                has_any = (lvl in bucket_col and grp in bucket_col[lvl]) or (lvl in bucket_tex and grp in bucket_tex[lvl])
                if not has_any:
                    continue
                seed = int(stable_seed("env-strip", int(lvl), int(grp), int(self._env_height_seed))) & 0xFFFFFFFF
                px = int(phase_tick) + int(seed & 0xFFFF)
                py = int(phase_tick) + int((seed >> 16) & 0xFFFF)
                try:
                    gl.glPolygonStipple(
                        polygon_stipple_pattern(
                            int(lvl),
                            phase_x=int(px),
                            phase_y=int(py),
                            seed=int(seed),
                            style=int(style),
                            cell=int(cell),
                            square_exp=float(sq_exp),
                            square_jitter=int(sq_jit),
                        )
                    )
                except Exception:
                    pass

                if vl_col is not None and lvl in bucket_col and grp in bucket_col[lvl]:
                    if current_group is not no_tex_group:
                        if current_group is not None:
                            try:
                                current_group.unset_state()
                            except Exception:
                                pass
                        try:
                            no_tex_group.set_state()
                        except Exception:
                            pass
                        current_group = no_tex_group
                    for start, count in _merge_ranges(bucket_col[lvl][grp]):
                        start_abs = int(vl_col.start) + int(start)
                        sub_vl = vtx_sub_cls(vl_col.domain, int(start_abs), int(count))
                        sub_vl.draw(gl.GL_TRIANGLES)

                if lvl in bucket_tex and grp in bucket_tex[lvl]:
                    for jar_rel in sorted(bucket_tex[lvl][grp].keys()):
                        vl_full = self._env_tex_vlists.get(str(jar_rel))
                        group = group_cache.get(str(jar_rel))
                        if vl_full is None or group is None:
                            continue
                        if current_group is not group:
                            if current_group is not None:
                                try:
                                    current_group.unset_state()
                                except Exception:
                                    pass
                            try:
                                group.set_state()
                            except Exception:
                                pass
                            current_group = group
                        for start, count in _merge_ranges(bucket_tex[lvl][grp][jar_rel]):
                            start_abs = int(vl_full.start) + int(start)
                            sub_vl = vtx_sub_cls(vl_full.domain, int(start_abs), int(count))
                            sub_vl.draw(gl.GL_TRIANGLES)
    finally:
        if current_group is not None:
            try:
                current_group.unset_state()
            except Exception:
                pass
        if polygon_offset:
            try:
                gl.glDisable(gl.GL_POLYGON_OFFSET_FILL)
            except Exception:
                pass
        try:
            gl.glDisable(gl.GL_POLYGON_STIPPLE)
        except Exception:
            pass


def draw_env_transparent_blended_pass(
    self: Any,
    *,
    gl: Any,
    param_store: Any,
    pyglet_mod: Any,
    group_cache: dict[str, Any],
    no_tex_group: Any,
) -> None:
    # Transparent terrain (alpha < 1.0): blended pass. Draw
    # patches back-to-front so blending isn't order-random.
    polygon_offset = False
    env_alpha_test = False
    alpha_buckets: dict[int, list[tuple[float, tuple[int, int]]]] = {}
    try:
        try:
            gl.glEnable(gl.GL_POLYGON_OFFSET_FILL)
            gl.glPolygonOffset(1.0, 1.0)
            polygon_offset = True
        except Exception:
            polygon_offset = False
        try:
            gl.glEnable(gl.GL_ALPHA_TEST)
            gl.glAlphaFunc(gl.GL_LESS, 0.999)
            env_alpha_test = True
        except Exception:
            env_alpha_test = False
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

        # No depth writes for transparent terrain. Depth test
        # against the already-rendered opaque scene is enough.
        gl.glDepthMask(gl.GL_FALSE)

        yaw_r = math.radians(float(self.yaw))
        pitch_r = math.radians(float(self.pitch))
        cyaw = math.cos(yaw_r)
        syaw = math.sin(yaw_r)
        cpitch = math.cos(pitch_r)
        spitch = math.sin(pitch_r)

        # Camera forward vector in world space.
        fwd_x = cpitch * syaw
        fwd_y = -spitch
        fwd_z = -cpitch * cyaw

        # Camera position in world space (matches modelview
        # transform order above).
        vx = -float(self.pan_x)
        vy = -float(self.pan_y)
        vz = float(self.distance)
        # Rotate by -pitch around X.
        y1 = vy * cpitch + vz * spitch
        z1 = -vy * spitch + vz * cpitch
        x1 = vx
        # Rotate by -yaw around Y.
        x2 = x1 * cyaw - z1 * syaw
        z2 = x1 * syaw + z1 * cyaw
        cam_x = float(self._orbit_target[0]) + x2
        cam_y = float(self._orbit_target[1]) + y1
        cam_z = float(self._orbit_target[2]) + z2

        step = int(self._env_patch_step)
        if step <= 0:
            step = 1
        bucket_size = float(step)
        if bucket_size < 1.0:
            bucket_size = 1.0
        min_b: int | None = None
        max_b: int | None = None

        # Bucket patches by view-depth for an O(n) back-to-front
        # ordering (the terrain lives on an XZ lattice).
        cx, cy, cz = self._pivot_center
        base_y = float(self._env_base_y or 0.0)
        fades = self._env_patch_fade
        patch_ranges = self._env_patch_ranges
        patch_spans = self._env_patch_spans
        use_stipple_fade = False
        try:
            use_stipple_fade = bool(int(param_store.get_int("rez.fade.mode")))
        except Exception:
            use_stipple_fade = False
        patch_keys = set(fades.keys()) | set(self._env_patch_has_transparency.keys()) | set(patch_spans.keys())
        for (px, pz) in patch_keys:
            if use_stipple_fade:
                # In stipple mode, we don't treat "patch fade-in" as
                # blended transparency. Only draw true transparent
                # terrain (strip fade, etc.) here.
                if not self._env_patch_has_transparency.get((px, pz), False):
                    continue
            else:
                if (px, pz) not in fades and not self._env_patch_has_transparency.get((px, pz), False):
                    continue
            x0 = int(px) * step
            z0 = int(pz) * step
            mid_x = int(x0 + step // 2)
            mid_z = int(z0 + step // 2)
            top_y = self._env_top_y_by_xz.get((mid_x, mid_z))
            if top_y is None:
                top_y = base_y
            x_c = float(x0) + float(step) * 0.5 - float(cx)
            y_c = float(top_y) + 0.5 - float(cy)
            z_c = float(z0) + float(step) * 0.5 - float(cz)
            dx = x_c - cam_x
            dy = y_c - cam_y
            dz = z_c - cam_z
            depth = dx * fwd_x + dy * fwd_y + dz * fwd_z
            b = int(math.floor(depth / bucket_size))
            alpha_buckets.setdefault(b, []).append((float(depth), (int(px), int(pz))))
            if min_b is None or b < min_b:
                min_b = b
            if max_b is None or b > max_b:
                max_b = b

        # Draw buckets far-to-near; small in-bucket sort keeps
        # ordering stable without needing an O(n log n) global sort.
        if min_b is not None and max_b is not None:
            vtx_sub_cls = pyglet_mod.graphics.vertexdomain.VertexList
            for b in range(int(max_b), int(min_b) - 1, -1):
                items = alpha_buckets.get(b)
                if not items:
                    continue
                items.sort(key=lambda it: it[0], reverse=True)
                for _depth, key in items:
                    entry = fades.get(key)
                    spans = patch_spans.get(key)
                    if spans is None:
                        ranges = None
                        if entry is not None:
                            _start_t, ranges, _last_a = entry
                        else:
                            ranges = patch_ranges.get(key)
                        if ranges is not None:
                            by_key: dict[tuple[str, str], tuple[int, int]] = {}
                            for kind, jar_rel, start, count, _target_a in list(ranges):
                                if int(count) <= 0:
                                    continue
                                k2 = (str(kind), str(jar_rel))
                                s0 = int(start)
                                e0 = int(start) + int(count)
                                prev = by_key.get(k2)
                                if prev is None:
                                    by_key[k2] = (int(s0), int(e0))
                                else:
                                    ps, pe = prev
                                    if s0 < ps:
                                        ps = int(s0)
                                    if e0 > pe:
                                        pe = int(e0)
                                    by_key[k2] = (int(ps), int(pe))
                            spans = [
                                (k2[0], k2[1], s0, int(e0 - s0))
                                for (k2, (s0, e0)) in by_key.items()
                                if int(e0) > int(s0)
                            ]
                    if not spans:
                        continue
                    current_group: Any = None
                    try:
                        for kind, jar_rel, start, count in spans:
                            if int(count) <= 0:
                                continue
                            if kind == "tex":
                                vl_full = self._env_tex_vlists.get(jar_rel)
                                group = group_cache.get(jar_rel)
                            else:
                                vl_full = self._env_colored_vlist
                                group = no_tex_group
                            if vl_full is None or group is None:
                                continue
                            if current_group is not group:
                                if current_group is not None:
                                    try:
                                        current_group.unset_state()
                                    except Exception:
                                        pass
                                try:
                                    group.set_state()
                                except Exception:
                                    pass
                                current_group = group
                            start_abs = int(vl_full.start) + int(start)
                            sub_vl = vtx_sub_cls(vl_full.domain, start_abs, int(count))
                            sub_vl.draw(gl.GL_TRIANGLES)
                    finally:
                        if current_group is not None:
                            try:
                                current_group.unset_state()
                            except Exception:
                                pass
    finally:
        gl.glDepthMask(gl.GL_TRUE)
        if env_alpha_test:
            try:
                gl.glDisable(gl.GL_ALPHA_TEST)
            except Exception:
                pass
        try:
            gl.glDisable(gl.GL_BLEND)
        except Exception:
            pass
        if polygon_offset:
            try:
                gl.glDisable(gl.GL_POLYGON_OFFSET_FILL)
            except Exception:
                pass


def draw_ender_vision_markers(
    self: Any,
    *,
    gl: Any,
    param_store: Any,
    face_dirs: Iterable[Any],
    cube_face_quad_points: Callable[..., list[tuple[float, float, float]]],
) -> None:
    if not self._ender_vision_active:
        return
    state = self._jigsaw_state
    if state is None or not state.connectors:
        return

    open_conns = self._ender_vision_open
    used_conns = self._ender_vision_used
    dead_conns = self._ender_vision_dead
    hover = self._ender_vision_hover
    if not open_conns and not used_conns and not dead_conns and hover is None:
        return

    def _clamp01(x: float) -> float:
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        return x

    pr = _clamp01(float(param_store.get("fx.color.ender.r")))
    pg = _clamp01(float(param_store.get("fx.color.ender.g")))
    pb = _clamp01(float(param_store.get("fx.color.ender.b")))
    yr = _clamp01(float(param_store.get("fx.color.ender.yellow.r")))
    yg = _clamp01(float(param_store.get("fx.color.ender.yellow.g")))
    yb = _clamp01(float(param_store.get("fx.color.ender.yellow.b")))
    ar = _clamp01(float(param_store.get("fx.color.ender.pink.r")))
    ag = _clamp01(float(param_store.get("fx.color.ender.pink.g")))
    ab = _clamp01(float(param_store.get("fx.color.ender.pink.b")))

    cx, cy, cz = self._pivot_center

    def _center(pos: Any) -> tuple[float, float, float]:
        x, y, z = pos
        return (float(x) + 0.5 - float(cx), float(y) + 0.5 - float(cy), float(z) + 0.5 - float(cz))

    pushed = False
    try:
        gl.glPushAttrib(gl.GL_ENABLE_BIT | gl.GL_LINE_BIT | gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        pushed = True
    except Exception:
        pushed = False

    try:
        gl.glDisable(gl.GL_LIGHTING)
        gl.glDisable(gl.GL_TEXTURE_2D)
        try:
            gl.glDisable(gl.GL_DEPTH_TEST)
        except Exception:
            pass
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glDepthMask(gl.GL_FALSE)
        axis_len = 0.60

        def _cube_at(pos: Any, *, size: float, r: float, g: float, b: float, a: float) -> None:
            x, y, z = pos
            x0 = float(x) - float(cx)
            y0 = float(y) - float(cy)
            z0 = float(z) - float(cz)
            x1 = x0 + 1.0
            y1 = y0 + 1.0
            z1 = z0 + 1.0
            if abs(float(size) - 1.0) > 1e-6:
                mx = (x0 + x1) * 0.5
                my = (y0 + y1) * 0.5
                mz = (z0 + z1) * 0.5
                half = 0.5 * float(size)
                x0 = mx - half
                x1 = mx + half
                y0 = my - half
                y1 = my + half
                z0 = mz - half
                z1 = mz + half
            gl.glColor4f(r, g, b, a)
            gl.glBegin(gl.GL_QUADS)
            for face in face_dirs:
                quad = cube_face_quad_points(face, xmin=x0, xmax=x1, ymin=y0, ymax=y1, zmin=z0, zmax=z1)
                for px, py, pz in quad:
                    gl.glVertex3f(px, py, pz)
            gl.glEnd()

        def _axis(
            ox: float,
            oy: float,
            oz: float,
            vx: int,
            vy: int,
            vz: int,
            *,
            r: float,
            g: float,
            b: float,
            a: float,
        ) -> None:
            a0 = a * 0.25
            gl.glColor4f(r, g, b, a0)
            gl.glVertex3f(ox, oy, oz)
            gl.glColor4f(r, g, b, a)
            gl.glVertex3f(ox + float(vx) * axis_len, oy + float(vy) * axis_len, oz + float(vz) * axis_len)

        try:
            gl.glLineWidth(1.8)
        except Exception:
            pass

        # Solid cubes so ghost sockets remain visible even after
        # jigsaw blocks are replaced by their final_state.
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        for c in used_conns:
            _cube_at(c.pos, size=1.0, r=yr, g=yg, b=yb, a=0.14)
        for c in dead_conns:
            _cube_at(c.pos, size=1.0, r=ar, g=ag, b=ab, a=0.11)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE)
        for c in open_conns:
            _cube_at(c.pos, size=1.0, r=pr, g=pg, b=pb, a=0.24)
        if hover is not None:
            _cube_at(hover.pos, size=1.12, r=ar, g=ag, b=ab, a=0.70)

        gl.glBegin(gl.GL_LINES)
        for c in used_conns:
            ox, oy, oz = _center(c.pos)
            fx, fy, fz = c.front
            tx, ty, tz = c.top
            _axis(ox, oy, oz, fx, fy, fz, r=pr, g=pg, b=pb, a=0.26)
            _axis(ox, oy, oz, tx, ty, tz, r=yr, g=yg, b=yb, a=0.20)
        for c in dead_conns:
            ox, oy, oz = _center(c.pos)
            fx, fy, fz = c.front
            tx, ty, tz = c.top
            _axis(ox, oy, oz, fx, fy, fz, r=ar, g=ag, b=ab, a=0.18)
            _axis(ox, oy, oz, tx, ty, tz, r=ar, g=ag, b=ab, a=0.14)
        gl.glEnd()

        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE)
        try:
            gl.glLineWidth(2.6)
        except Exception:
            pass
        gl.glBegin(gl.GL_LINES)
        for c in open_conns:
            ox, oy, oz = _center(c.pos)
            fx, fy, fz = c.front
            tx, ty, tz = c.top
            _axis(ox, oy, oz, fx, fy, fz, r=pr, g=pg, b=pb, a=0.55)
            _axis(ox, oy, oz, tx, ty, tz, r=yr, g=yg, b=yb, a=0.46)
        gl.glEnd()

        if hover is not None:
            ox, oy, oz = _center(hover.pos)
            fx, fy, fz = hover.front
            tx, ty, tz = hover.top
            try:
                gl.glLineWidth(3.8)
            except Exception:
                pass
            gl.glBegin(gl.GL_LINES)
            _axis(ox, oy, oz, fx, fy, fz, r=ar, g=ag, b=ab, a=0.95)
            _axis(ox, oy, oz, tx, ty, tz, r=yr, g=yg, b=yb, a=0.95)
            gl.glEnd()
    finally:
        gl.glDepthMask(gl.GL_TRUE)
        try:
            gl.glLineWidth(1.0)
        except Exception:
            pass
        if pushed:
            try:
                gl.glPopAttrib()
            except Exception:
                pass


def draw_hover_target_box(self: Any, *, gl: Any, param_store: Any) -> None:
    hover = self._hover_block
    if not self._build_enabled or hover is None:
        return
    hr, hg, hb = _fx_param_color_triplet(param_store, "fx.color.ender")
    x, y, z = hover
    cx, cy, cz = self._pivot_center
    x0 = float(x) - float(cx)
    y0 = float(y) - float(cy)
    z0 = float(z) - float(cz)
    x1 = float(x + 1) - float(cx)
    y1 = float(y + 1) - float(cy)
    z1 = float(z + 1) - float(cz)
    try:
        try:
            # The hover target should respect depth so it doesn't render through
            # solid geometry.
            gl.glEnable(gl.GL_DEPTH_TEST)
        except Exception:
            pass
        gl.glDisable(gl.GL_LIGHTING)
        gl.glDisable(gl.GL_TEXTURE_2D)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glDepthMask(gl.GL_FALSE)
        border_t = float(param_store.get("ui.selection.border.frac") or 0.0)
        if not math.isfinite(border_t) or border_t < 0.0:
            border_t = 0.0

        def _emit_box_quads(
            bx0: float,
            by0: float,
            bz0: float,
            bx1: float,
            by1: float,
            bz1: float,
        ) -> None:
            # Bottom.
            gl.glVertex3f(bx0, by0, bz0)
            gl.glVertex3f(bx1, by0, bz0)
            gl.glVertex3f(bx1, by0, bz1)
            gl.glVertex3f(bx0, by0, bz1)
            # Top.
            gl.glVertex3f(bx0, by1, bz0)
            gl.glVertex3f(bx0, by1, bz1)
            gl.glVertex3f(bx1, by1, bz1)
            gl.glVertex3f(bx1, by1, bz0)
            # North (z0).
            gl.glVertex3f(bx0, by0, bz0)
            gl.glVertex3f(bx0, by1, bz0)
            gl.glVertex3f(bx1, by1, bz0)
            gl.glVertex3f(bx1, by0, bz0)
            # South (z1).
            gl.glVertex3f(bx0, by0, bz1)
            gl.glVertex3f(bx1, by0, bz1)
            gl.glVertex3f(bx1, by1, bz1)
            gl.glVertex3f(bx0, by1, bz1)
            # West (x0).
            gl.glVertex3f(bx0, by0, bz0)
            gl.glVertex3f(bx0, by0, bz1)
            gl.glVertex3f(bx0, by1, bz1)
            gl.glVertex3f(bx0, by1, bz0)
            # East (x1).
            gl.glVertex3f(bx1, by0, bz0)
            gl.glVertex3f(bx1, by1, bz0)
            gl.glVertex3f(bx1, by1, bz1)
            gl.glVertex3f(bx1, by0, bz1)

        def _draw_target_frame(
            ax0: float,
            ay0: float,
            az0: float,
            ax1: float,
            ay1: float,
            az1: float,
            *,
            thick: float,
        ) -> None:
            t = float(thick)
            if t <= 1e-6:
                return

            gl.glBegin(gl.GL_QUADS)

            # Edge bars (12) — thickness is entirely outside the block.
            # X edges.
            _emit_box_quads(ax0, ay0 - t, az0 - t, ax1, ay0, az0)
            _emit_box_quads(ax0, ay0 - t, az1, ax1, ay0, az1 + t)
            _emit_box_quads(ax0, ay1, az0 - t, ax1, ay1 + t, az0)
            _emit_box_quads(ax0, ay1, az1, ax1, ay1 + t, az1 + t)
            # Y edges.
            _emit_box_quads(ax0 - t, ay0, az0 - t, ax0, ay1, az0)
            _emit_box_quads(ax0 - t, ay0, az1, ax0, ay1, az1 + t)
            _emit_box_quads(ax1, ay0, az0 - t, ax1 + t, ay1, az0)
            _emit_box_quads(ax1, ay0, az1, ax1 + t, ay1, az1 + t)
            # Z edges.
            _emit_box_quads(ax0 - t, ay0 - t, az0, ax0, ay0, az1)
            _emit_box_quads(ax1, ay0 - t, az0, ax1 + t, ay0, az1)
            _emit_box_quads(ax0 - t, ay1, az0, ax0, ay1 + t, az1)
            _emit_box_quads(ax1, ay1, az0, ax1 + t, ay1 + t, az1)

            # Corner cubes (8) — makes the thick outline read continuous like a 2D border.
            _emit_box_quads(ax0 - t, ay0 - t, az0 - t, ax0, ay0, az0)
            _emit_box_quads(ax0 - t, ay0 - t, az1, ax0, ay0, az1 + t)
            _emit_box_quads(ax0 - t, ay1, az0 - t, ax0, ay1 + t, az0)
            _emit_box_quads(ax0 - t, ay1, az1, ax0, ay1 + t, az1 + t)
            _emit_box_quads(ax1, ay0 - t, az0 - t, ax1 + t, ay0, az0)
            _emit_box_quads(ax1, ay0 - t, az1, ax1 + t, ay0, az1 + t)
            _emit_box_quads(ax1, ay1, az0 - t, ax1 + t, ay1 + t, az0)
            _emit_box_quads(ax1, ay1, az1, ax1 + t, ay1 + t, az1 + t)

            gl.glEnd()

        gl.glColor4f(float(hr), float(hg), float(hb), 0.92)
        _draw_target_frame(x0, y0, z0, x1, y1, z1, thick=border_t)

        now = time.monotonic()
        frame = int(now * 24.0)
        seed = (
            self._fx_seed ^ (frame * 0x9E3779B1) ^ (x * 0x632BE5AB) ^ (y * 0x85157AF5) ^ (z * 0x9E3779B9)
        ) & 0xFFFFFFFF
        rng = random.Random(seed)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE)
        glow_t = border_t * 0.75
        for i in range(2):
            pulse = 0.5 + 0.5 * math.sin(now * (12.0 + i * 3.1) + float((seed >> 8) & 0xFF) * 0.07)
            jitter = (0.006 + 0.010 * rng.random()) * (0.35 + 0.65 * pulse)
            jx = (rng.random() - 0.5) * jitter
            jy = (rng.random() - 0.5) * jitter
            jz = (rng.random() - 0.5) * jitter
            a = 0.16 + 0.22 * rng.random()
            gl.glColor4f(float(hr), float(hg), float(hb), float(a))
            _draw_target_frame(x0 + jx, y0 + jy, z0 + jz, x1 + jx, y1 + jy, z1 + jz, thick=glow_t)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
    finally:
        gl.glDepthMask(gl.GL_TRUE)
        try:
            gl.glLineWidth(1.0)
        except Exception:
            pass
        try:
            gl.glDisable(gl.GL_BLEND)
        except Exception:
            pass
        try:
            gl.glEnable(gl.GL_DEPTH_TEST)
        except Exception:
            pass
        gl.glEnable(gl.GL_TEXTURE_2D)
        gl.glEnable(gl.GL_LIGHTING)
        gl.glColor3f(1.0, 1.0, 1.0)


def draw_model_rt_to_screen(
    self: Any,
    vp_w: int,
    vp_h: int,
    *,
    sidebar_px: int,
    view_w_px: int,
    view_h_px: int,
    now: float,
    cc_active: bool,
    gl: Any,
    param_store: Any,
) -> None:
    # Composite the model render target back to the default framebuffer,
    # with optional SSAO and always-on warp.
    #
    # This is the post-3D part of the "tube": the channel-change reveal is
    # drawn into the RT earlier so it warps together with the model.

    if cc_active:
        try:
            r, g, b = self._env_clear_rgb()
        except Exception:
            r, g, b = (0.0, 0.0, 0.0)
        gl.glClearColor(float(r), float(g), float(b), 1.0)

    gl.glViewport(0, 0, max(1, int(vp_w)), max(1, int(vp_h)))
    gl.glDisable(gl.GL_LIGHTING)
    gl.glDisable(gl.GL_DEPTH_TEST)
    gl.glDepthMask(gl.GL_FALSE)
    gl.glDisable(gl.GL_BLEND)
    gl.glEnable(gl.GL_TEXTURE_2D)
    gl.glColor4f(1.0, 1.0, 1.0, 1.0)
    gl.glMatrixMode(gl.GL_PROJECTION)
    gl.glLoadIdentity()
    gl.glOrtho(0.0, float(self.width), 0.0, float(self.height), -1.0, 1.0)
    gl.glMatrixMode(gl.GL_MODELVIEW)
    gl.glLoadIdentity()

    ssao_radius_blocks = float(param_store.get("fx.glitch.ssao.radius.blocks"))
    if ssao_radius_blocks < 0.0:
        ssao_radius_blocks = 0.0
    # Convert an SSAO radius in world units ("blocks") into a
    # viewport pixel radius so the apparent occlusion scale
    # stays consistent as the user zooms.
    ssao_radius_px = 0.0
    if ssao_radius_blocks > 1e-6:
        tan_y = math.tan(math.radians(55.0) / 2.0)
        world_h = 2.0 * max(0.001, float(self.distance)) * tan_y
        px_per_unit = float(view_h_px) / max(1e-6, float(world_h))
        ssao_radius_px = float(ssao_radius_blocks) * float(px_per_unit)
        if ssao_radius_px > 240.0:
            ssao_radius_px = 240.0

    effects_enabled = _effects_pipeline_enabled(self)
    use_ssao = bool(
        effects_enabled
        and int(self._ssao_prog.value)
        and int(self._model_rt.depth_tex.value)
        and float(param_store.get("fx.glitch.ssao.strength")) > 1e-6
        and ssao_radius_px > 1e-6
    )
    if use_ssao:
        try:
            gl.glActiveTexture(gl.GL_TEXTURE0)
            gl.glBindTexture(gl.GL_TEXTURE_2D, self._model_rt.color_tex)
            gl.glActiveTexture(gl.GL_TEXTURE1)
            gl.glBindTexture(gl.GL_TEXTURE_2D, self._model_rt.depth_tex)
            gl.glActiveTexture(gl.GL_TEXTURE0)
            gl.glUseProgram(self._ssao_prog)  # type: ignore[attr-defined]

            try:
                gl.glUniform1i(self._ssao_u_color, 0)  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                gl.glUniform1i(self._ssao_u_depth, 1)  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                gl.glUniform2f(
                    self._ssao_u_view_px,
                    float(view_w_px),
                    float(view_h_px),
                )  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                gl.glUniform1f(
                    self._ssao_u_strength,
                    max(0.0, float(param_store.get("fx.glitch.ssao.strength"))),
                )  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                gl.glUniform1f(
                    self._ssao_u_radius_px,
                    max(0.0, float(ssao_radius_px)),
                )  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                gl.glUniform1f(
                    self._ssao_u_bias,
                    max(0.0, float(param_store.get("fx.glitch.ssao.bias"))),
                )  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                gl.glUniform1f(
                    self._ssao_u_brightness,
                    max(0.0, float(param_store.get("fx.glitch.ssao.brightness"))),
                )  # type: ignore[attr-defined]
            except Exception:
                pass

            try:
                default_near = 0.001 if self._ortho_enabled else 0.05
                near = float(getattr(self, "_clip_near", default_near))
                if not math.isfinite(near):
                    near = float(default_near)
                gl.glUniform1f(self._ssao_u_near, float(near))  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                far = float(getattr(self, "_clip_far", 5000.0))
                if not math.isfinite(far):
                    far = 5000.0
                gl.glUniform1f(self._ssao_u_far, float(far))  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                gl.glUniform1f(self._ssao_u_is_ortho, 1.0 if self._ortho_enabled else 0.0)  # type: ignore[attr-defined]
            except Exception:
                pass
        except Exception:
            use_ssao = False
            try:
                gl.glUseProgram(0)  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                gl.glActiveTexture(gl.GL_TEXTURE1)
                gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
            except Exception:
                pass
            try:
                gl.glActiveTexture(gl.GL_TEXTURE0)
                gl.glBindTexture(gl.GL_TEXTURE_2D, self._model_rt.color_tex)
            except Exception:
                pass
    if not use_ssao:
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._model_rt.color_tex)
    x0 = float(self.sidebar_width)
    x1 = float(self.width)
    y0 = 0.0
    y1 = float(self.height)
    if not effects_enabled:
        gl.glBegin(gl.GL_QUADS)
        gl.glTexCoord2f(0.0, 0.0)
        gl.glVertex2f(x0, y0)
        gl.glTexCoord2f(1.0, 0.0)
        gl.glVertex2f(x1, y0)
        gl.glTexCoord2f(1.0, 1.0)
        gl.glVertex2f(x1, y1)
        gl.glTexCoord2f(0.0, 1.0)
        gl.glVertex2f(x0, y1)
        gl.glEnd()
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glDepthMask(gl.GL_TRUE)
        return

    # Channel-change warp: barrel-distort the model viewport by
    # drawing the render target through a subdivided mesh.
    k_base = float(param_store.get("fx.glitch.warp.barrel.base"))
    k_extra = float(param_store.get("fx.channel_change.warp.barrel.extra"))
    decay_exp = float(param_store.get("fx.channel_change.warp.decay.exp"))
    if decay_exp < 0.01:
        decay_exp = 0.01
    wobble_amp = float(param_store.get("fx.glitch.warp.wobble.amp"))
    wobble_hz = float(param_store.get("fx.glitch.warp.wobble.hz"))
    if wobble_amp < 0.0:
        wobble_amp = 0.0
    if wobble_hz < 0.0:
        wobble_hz = 0.0
    channel_mult = float(param_store.get("fx.glitch.warp.energy.channel.mult"))
    if channel_mult < 0.0:
        channel_mult = 0.0

    extra_scale = 0.0
    if self._channel_change_start_t is not None:
        dur = max(0.02, float(param_store.get("fx.channel_change.duration_s")))
        p = max(0.0, (now - self._channel_change_start_t) / dur) if dur > 1e-6 else 1.0
        if _channel_change_hold_active(self):
            p = min(float(p), float(_BROKEN_CHANNEL_CHANGE_P_CAP))
        p = max(0.0, min(1.0, float(p)))
        p = p * p * (3.0 - 2.0 * p)
        extra_scale = (1.0 - p) ** decay_exp

    k = k_base + k_extra * extra_scale
    energy = 1.0
    if extra_scale > 1e-6 and channel_mult > 1e-6:
        energy *= 1.0 + extra_scale * channel_mult

    if abs(k) >= 1e-6 and wobble_amp > 1e-6 and wobble_hz > 1e-6:
        phase = float((self._fx_seed ^ self._channel_change_seed) & 0xFFFF) / 65535.0
        phase = phase * (2.0 * math.pi)
        wob = math.sin((now * wobble_hz * 2.0 * math.pi) + phase)
        wobble = min(0.95, wobble_amp * max(0.0, energy))
        k *= 1.0 + wobble * wob

    if abs(k) < 1e-6:
        gl.glBegin(gl.GL_QUADS)
        gl.glTexCoord2f(0.0, 0.0)
        gl.glVertex2f(x0, y0)
        gl.glTexCoord2f(1.0, 0.0)
        gl.glVertex2f(x1, y0)
        gl.glTexCoord2f(1.0, 1.0)
        gl.glVertex2f(x1, y1)
        gl.glTexCoord2f(0.0, 1.0)
        gl.glVertex2f(x0, y1)
        gl.glEnd()
    else:
        gl.glEnable(gl.GL_SCISSOR_TEST)
        gl.glScissor(sidebar_px, 0, max(1, view_w_px), max(1, view_h_px))
        cx = 0.5 * (x0 + x1)
        cy = 0.5 * (y0 + y1)
        hw = 0.5 * (x1 - x0)
        hh = 0.5 * (y1 - y0)
        seg = max(4, min(240, param_store.get_int("fx.glitch.warp.grid")))
        seg_x = seg
        seg_y = int(round(float(seg) * (y1 - y0) / max(1e-6, (x1 - x0))))
        seg_y = max(4, min(240, seg_y))

        def _warp(nx: float, ny: float) -> tuple[float, float]:
            r2 = nx * nx + ny * ny
            s = 1.0 + k * r2
            return (cx + nx * s * hw, cy + ny * s * hh)

        gl.glBegin(gl.GL_QUADS)
        for iy in range(seg_y):
            v0 = float(iy) / float(seg_y)
            v1 = float(iy + 1) / float(seg_y)
            ny0 = v0 * 2.0 - 1.0
            ny1 = v1 * 2.0 - 1.0
            for ix in range(seg_x):
                u0 = float(ix) / float(seg_x)
                u1 = float(ix + 1) / float(seg_x)
                nx0 = u0 * 2.0 - 1.0
                nx1 = u1 * 2.0 - 1.0

                x00, y00 = _warp(nx0, ny0)
                x10, y10 = _warp(nx1, ny0)
                x11, y11 = _warp(nx1, ny1)
                x01, y01 = _warp(nx0, ny1)

                gl.glTexCoord2f(u0, v0)
                gl.glVertex2f(x00, y00)
                gl.glTexCoord2f(u1, v0)
                gl.glVertex2f(x10, y10)
                gl.glTexCoord2f(u1, v1)
                gl.glVertex2f(x11, y11)
                gl.glTexCoord2f(u0, v1)
                gl.glVertex2f(x01, y01)
        gl.glEnd()
        gl.glDisable(gl.GL_SCISSOR_TEST)
    if use_ssao:
        try:
            gl.glUseProgram(0)  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            gl.glActiveTexture(gl.GL_TEXTURE1)
            gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
            gl.glActiveTexture(gl.GL_TEXTURE0)
        except Exception:
            pass
    gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
    if effects_enabled:
        self._draw_camera_safety_overlay(sidebar_px=sidebar_px, view_w_px=view_w_px, view_h_px=view_h_px)
    gl.glDepthMask(gl.GL_TRUE)



def apply_copy_glitch(self: Any, vp_w: int, vp_h: int, *, gl: Any, param_store: Any) -> None:
    if not _effects_pipeline_enabled(self):
        return
    # Cheap "screen displacement" glitch: copy a horizontal band from
    # the 3D viewport and paste it back a few pixels offset.
    #
    # Render-time version of the effect: we sample from the model
    # render target (when available) so it never touches the sidebar.
    now = time.monotonic()
    frame = int(now * 24.0)
    seed = (self._fx_seed ^ (frame * 0x9E3779B1) ^ 0xC0FFEE) & 0xFFFFFFFF

    _rand01 = _deterministic_rand01

    ratio = float(self.get_pixel_ratio())
    sidebar_px = int(max(0.0, float(self.sidebar_width)) * ratio)
    view_w_px = int(vp_w) - sidebar_px
    view_h_px = int(vp_h)
    if view_w_px <= 8 or view_h_px <= 8:
        return

    # Keep it subtle unless we're actively rezzing.
    n_ops = 0
    r, seed = _rand01(seed)
    if self._rez_active:
        if r < 0.002:
            n_ops = 2
        elif r < 0.008:
            n_ops = 1
    else:
        if r < 0.0005:
            n_ops = 2
        elif r < 0.002:
            n_ops = 1

    if n_ops <= 0:
        return

    max_shift = 4 if self._rez_active else 2
    ops: list[tuple[int, int, int, int, int]] = []
    for _ in range(n_ops):
        rh, seed = _rand01(seed)
        r_big, seed = _rand01(seed)
        r_scale, seed = _rand01(seed)
        band_h = int(6.0 + rh * min(view_h_px * (0.22 if not self._rez_active else 0.28), 180.0))
        if r_big < (0.18 if self._rez_active else 0.08):
            band_h = int(band_h * (2.2 + 4.0 * r_scale))
        band_h = max(2, min(int(view_h_px) - 2, band_h))
        if band_h < 2:
            continue

        ry, seed = _rand01(seed)
        src_y = int(ry * float(max(1, int(view_h_px) - band_h)))

        rdx, seed = _rand01(seed)
        dx = int(round((rdx - 0.5) * 2.0 * max_shift))
        if dx == 0:
            dx = 1 if rdx >= 0.5 else -1
        dx = max(-(view_w_px - 2), min(view_w_px - 2, dx))

        band_w = int(view_w_px) - abs(int(dx))
        if band_w < 2:
            continue

        if dx > 0:
            src_x_view = 0
            dst_x_view = dx
        else:
            src_x_view = -dx
            dst_x_view = 0

        ops.append((int(src_x_view), int(src_y), int(band_w), int(band_h), int(dst_x_view)))

    if not ops:
        return

    # Ensure pixel operations aren't clipped or blended.
    gl.glDisable(gl.GL_DEPTH_TEST)
    gl.glDisable(gl.GL_LIGHTING)
    gl.glDepthMask(gl.GL_FALSE)
    gl.glDisable(gl.GL_BLEND)
    gl.glColorMask(gl.GL_TRUE, gl.GL_TRUE, gl.GL_TRUE, gl.GL_TRUE)

    if self._model_rt.ok and int(self._model_rt.color_tex.value):
        gl.glViewport(0, 0, max(1, int(vp_w)), max(1, int(vp_h)))
        gl.glMatrixMode(gl.GL_PROJECTION)
        gl.glLoadIdentity()
        gl.glOrtho(0.0, float(self.width), 0.0, float(self.height), -1.0, 1.0)
        gl.glMatrixMode(gl.GL_MODELVIEW)
        gl.glLoadIdentity()

        gl.glEnable(gl.GL_SCISSOR_TEST)
        gl.glScissor(sidebar_px, 0, max(1, view_w_px), max(1, view_h_px))
        gl.glEnable(gl.GL_TEXTURE_2D)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self._model_rt.color_tex)
        gl.glColor4f(1.0, 1.0, 1.0, 1.0)
        inv_ratio = 1.0 / max(1e-6, ratio)
        gl.glBegin(gl.GL_QUADS)
        for src_x_view, src_y, band_w, band_h, dst_x_view in ops:
            x0 = float(self.sidebar_width) + float(dst_x_view) * inv_ratio
            x1 = x0 + float(band_w) * inv_ratio
            y0 = float(src_y) * inv_ratio
            y1 = y0 + float(band_h) * inv_ratio

            u0 = float(src_x_view) / float(view_w_px)
            u1 = float(src_x_view + band_w) / float(view_w_px)
            v0 = float(src_y) / float(view_h_px)
            v1 = float(src_y + band_h) / float(view_h_px)

            gl.glTexCoord2f(u0, v0)
            gl.glVertex2f(x0, y0)
            gl.glTexCoord2f(u1, v0)
            gl.glVertex2f(x1, y0)
            gl.glTexCoord2f(u1, v1)
            gl.glVertex2f(x1, y1)
            gl.glTexCoord2f(u0, v1)
            gl.glVertex2f(x0, y1)
        gl.glEnd()
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glDisable(gl.GL_SCISSOR_TEST)
    else:
        # Fallback: copy pixels from the default framebuffer.
        gl.glDisable(gl.GL_SCISSOR_TEST)
        gl.glDisable(gl.GL_TEXTURE_2D)
        try:
            gl.glPixelZoom(1.0, 1.0)
        except Exception:
            pass

        for src_x_view, src_y, band_w, band_h, dst_x_view in ops:
            try:
                src_x = int(sidebar_px + src_x_view)
                dst_x = int(sidebar_px + dst_x_view)
                gl.glWindowPos2i(int(dst_x), int(src_y))
                gl.glCopyPixels(int(src_x), int(src_y), int(band_w), int(band_h), gl.GL_COLOR)
            except Exception:
                break

    # Restore baseline state expected by the next frame.
    gl.glDepthMask(gl.GL_TRUE)
    gl.glEnable(gl.GL_TEXTURE_2D)
    gl.glEnable(gl.GL_BLEND)
    gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)



# Freeze the channel-change FX fairly early so it stays loud/obvious.
_BROKEN_CHANNEL_CHANGE_P_CAP = 0.15


def _channel_change_hold_active(self: Any) -> bool:
    # When the currently-selected item fails to load/render, treat the
    # channel-change FX as "stuck on" until selection changes again.
    try:
        return bool(getattr(self, "_viewer_error_text", ""))
    except Exception:
        return False


def trigger_channel_change_fx(self: Any) -> None:
    self._channel_change_start_t = time.monotonic()
    self._channel_change_seed = secrets.randbits(32)


def apply_channel_change_tint(self: Any, *, now: float, gl: Any, param_store: Any) -> None:
    # Channel change: start the model tinted purple and drift back to
    # normal as the transition completes.
    tint_r, tint_g, tint_b = _fx_param_color_triplet(param_store, "fx.color.ender")
    tint_p = 0.0
    if self._channel_change_start_t is not None:
        dur = max(0.10, float(param_store.get("fx.channel_change.duration_s")))
        p = (float(now) - float(self._channel_change_start_t)) / dur if dur > 1e-6 else 1.0
        if p < 0.0:
            p = 0.0
        if p > 1.0:
            p = 1.0
        if _channel_change_hold_active(self) and p > _BROKEN_CHANNEL_CHANGE_P_CAP:
            p = float(_BROKEN_CHANNEL_CHANGE_P_CAP)
        hold = float(param_store.get("fx.channel_change.tint.hold.frac"))
        if hold < 0.0:
            hold = 0.0
        if hold > 0.98:
            hold = 0.98
        fade_span = max(1e-6, 1.0 - hold)

        if p <= hold:
            tint_p = 1.0
        else:
            t = (p - hold) / fade_span
            t = max(0.0, min(1.0, t))
            t = t * t * (3.0 - 2.0 * t)
            fade_exp = float(param_store.get("fx.channel_change.tint.fade.exp"))
            if fade_exp < 0.01:
                fade_exp = 0.01
            t = t**fade_exp
            tint_p = 1.0 - t

        strength = float(param_store.get("fx.channel_change.tint.strength"))
        if strength < 0.0:
            strength = 0.0
        if strength > 2.0:
            strength = 2.0
        tint_p *= strength

    def _clamp01(x: float) -> float:
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        return x

    gl.glColor3f(
        _clamp01(1.0 + (tint_r - 1.0) * tint_p),
        _clamp01(1.0 + (tint_g - 1.0) * tint_p),
        _clamp01(1.0 + (tint_b - 1.0) * tint_p),
    )


def draw_model_channel_change_fade(
    self: Any,
    *,
    cc_p: float,
    now: float,
    alpha_test: bool,
    cutout_thr: float,
    gl: Any,
    param_store: Any,
) -> None:
    # Channel change: fade in the new model (old model already swapped out).
    try:
        use_stipple = bool(int(param_store.get_int("rez.fade.mode")))
    except Exception:
        use_stipple = False
    self._dbg_last_channel_change_use_stipple = bool(use_stipple)

    if use_stipple:
        lvl = int(round(float(cc_p) * 64.0))
        if lvl > 0:
            phase_tick, style, cell, sq_exp, sq_jit = _stipple_runtime_params(param_store, now=now)
            seed_base = int(self._channel_change_seed) & 0xFFFFFFFF
            try:
                gl.glEnable(gl.GL_POLYGON_STIPPLE)
                gl.glPolygonStipple(
                    polygon_stipple_pattern(
                        lvl,
                        phase_x=int(phase_tick) + int(seed_base & 0xFFFF),
                        phase_y=int(phase_tick) + int((seed_base >> 16) & 0xFFFF),
                        seed=int(seed_base) ^ 0xC001D00D,
                        style=int(style),
                        cell=int(cell),
                        square_exp=float(sq_exp),
                        square_jitter=int(sq_jit),
                    )
                )
                self._batch.draw()
            finally:
                try:
                    gl.glDisable(gl.GL_POLYGON_STIPPLE)
                except Exception:
                    pass
    else:
        a = max(0.0, min(1.0, float(cc_p)))
        if a > 0.0:
            # Be defensive: if polygon stipple is enabled, a
            # smooth alpha fade will still appear pixelated.
            try:
                gl.glDisable(gl.GL_POLYGON_STIPPLE)
            except Exception:
                pass
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            # For smooth (alpha) channel fade, keep depth writes
            # enabled so the model doesn't "pop" on the final
            # frame when depth testing resumes. If alpha-test is
            # available, scale the cutout threshold by the
            # global fade alpha so cutouts remain stable while
            # still allowing the fade to start at low alpha.
            restore_alpha_func = False
            if alpha_test:
                try:
                    gl.glAlphaFunc(gl.GL_GREATER, max(0.0, min(1.0, float(cutout_thr) * float(a))))
                    restore_alpha_func = True
                except Exception:
                    restore_alpha_func = False
            if alpha_test:
                gl.glDepthMask(gl.GL_TRUE)
            else:
                gl.glDepthMask(gl.GL_FALSE)
            gl.glColor4f(1.0, 1.0, 1.0, float(a))
            try:
                self._batch.draw()
            finally:
                gl.glColor4f(1.0, 1.0, 1.0, 1.0)
                gl.glDepthMask(gl.GL_TRUE)
                if restore_alpha_func:
                    try:
                        gl.glAlphaFunc(gl.GL_GREATER, float(cutout_thr))
                    except Exception:
                        pass
                try:
                    gl.glDisable(gl.GL_BLEND)
                except Exception:
                    pass



def draw_channel_change_in_model_view(self: Any, *, view_w: float, view_h: float, gl: Any, param_store: Any) -> None:
    # Draw the channel-change reveal *inside the tube* (i.e. into
    # the model viewport / render-target) so it gets warped along
    # with the 3D view. This is intentionally independent of
    # `rez.fade.mode` (which controls rez/selection fades).
    if self._channel_change_start_t is None:
        return
    w = float(view_w)
    h = float(view_h)
    if w <= 0.0 or h <= 0.0:
        return

    now = time.monotonic()
    dur = max(0.10, float(param_store.get("fx.channel_change.duration_s")))
    cc_elapsed = max(0.0, now - self._channel_change_start_t)
    p = cc_elapsed / dur if dur > 1e-6 else 1.0
    if _channel_change_hold_active(self) and p > _BROKEN_CHANNEL_CHANGE_P_CAP:
        p = float(_BROKEN_CHANNEL_CHANGE_P_CAP)
    if p >= 1.0:
        return

    base_r, base_g, base_b = _fx_param_color_triplet(param_store, "fx.color.ender")
    amber_r, amber_g, amber_b = _fx_param_color_triplet(param_store, "fx.color.accent.amber")
    green_r, green_g, green_b = _fx_param_color_triplet(param_store, "fx.color.accent.green")

    gl.glDisable(gl.GL_TEXTURE_2D)
    gl.glDisable(gl.GL_DEPTH_TEST)
    gl.glDisable(gl.GL_LIGHTING)
    gl.glDepthMask(gl.GL_FALSE)
    gl.glEnable(gl.GL_BLEND)
    gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

    # Ensure we are drawing in viewport coordinates.
    gl.glMatrixMode(gl.GL_PROJECTION)
    gl.glLoadIdentity()
    gl.glOrtho(0.0, w, 0.0, h, -1.0, 1.0)
    gl.glMatrixMode(gl.GL_MODELVIEW)
    gl.glLoadIdentity()

    def _smoothstep(x: float) -> float:
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        return x * x * (3.0 - 2.0 * x)

    _rand01 = _deterministic_rand01

    open_p = _smoothstep(min(1.0, p / 0.75))
    fade_p = _smoothstep(min(1.0, p))
    beam_alpha = min(1.0, (1.0 - fade_p) ** 1.20) * (0.22 + 0.78 * (1.0 - open_p))

    vx0 = 0.0
    vx1 = w
    cy = 0.5 * h
    feather = max(18.0, min(120.0, h * float(param_store.get("fx.channel_change.feather.frac"))))
    band_half = max(
        0.0,
        (open_p ** float(param_store.get("fx.channel_change.band.exp"))) * ((0.5 * h) + feather),
    )
    cover_scale = (1.0 - fade_p) ** float(param_store.get("fx.channel_change.cover.exp"))

    # Tile cover mask (pixelated reveal).
    if cover_scale > 0.002:
        tile = max(1.0, float(param_store.get("fx.channel_change.tile.size")))
        mask_seed = (
            self._channel_change_seed
            ^ int(now * 120.0) * 0x9E3779B1
            ^ int(cc_elapsed * 1000.0) * 0x85EBCA6B
            ^ 0xB16B00B5
        ) & 0xFFFFFFFF

        rx0, mask_seed = _rand01(mask_seed)
        ry0, mask_seed = _rand01(mask_seed)
        off_x = rx0 * tile
        off_y = ry0 * tile

        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glColor4f(0.0, 0.0, 0.0, 1.0)
        gl.glBegin(gl.GL_QUADS)
        y = -off_y
        while y < h:
            dist = abs((y + (tile * 0.5)) - cy)
            t = (band_half - dist) / feather if feather > 1e-6 else 1.0
            if t <= 0.0:
                reveal = 0.0
            elif t >= 1.0:
                reveal = 1.0
            else:
                reveal = t * t * (3.0 - 2.0 * t)
            cover_prob = (1.0 - reveal) * cover_scale
            if cover_prob > 0.0005:
                x = vx0 - off_x
                while x < vx1:
                    rr, mask_seed = _rand01(mask_seed)
                    if rr < cover_prob:
                        gl.glVertex2f(x, y)
                        gl.glVertex2f(x + tile, y)
                        gl.glVertex2f(x + tile, y + tile)
                        gl.glVertex2f(x, y + tile)
                    x += tile
            y += tile
        gl.glEnd()

    # Beam + sparkles.
    gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE)
    flicker_seed = (
        self._channel_change_seed
        ^ int(now * 120.0) * 0x9E3779B1
        ^ int(cc_elapsed * 1000.0) * 0x85EBCA6B
        ^ 0xF00DBAAD
    ) & 0xFFFFFFFF
    rf, flicker_seed = _rand01(flicker_seed)
    flicker = 0.78 + 0.22 * math.sin((now * 38.0) + (rf * 12.0))
    flicker = max(0.30, min(1.25, flicker))

    beam_thick = float(param_store.get("fx.channel_change.beam.thick.base")) + (1.0 - open_p) * float(
        param_store.get("fx.channel_change.beam.thick.extra")
    )
    core_thick = max(1.0, beam_thick * 0.22)
    glow_thick = beam_thick
    a_glow = float(param_store.get("fx.channel_change.beam.alpha.glow")) * beam_alpha * flicker
    a_core = float(param_store.get("fx.channel_change.beam.alpha.core")) * beam_alpha * flicker
    y_core0 = max(0.0, cy - core_thick * 0.5)
    y_core1 = min(h, cy + core_thick * 0.5)
    y_glow0 = max(0.0, cy - glow_thick * 0.5)
    y_glow1 = min(h, cy + glow_thick * 0.5)

    gl.glBegin(gl.GL_QUADS)
    gl.glColor4f(base_r, base_g, base_b, a_glow)
    gl.glVertex2f(vx0, y_glow0)
    gl.glVertex2f(vx1, y_glow0)
    gl.glVertex2f(vx1, y_glow1)
    gl.glVertex2f(vx0, y_glow1)
    gl.glColor4f(min(1.0, base_r * 0.25 + 0.92), min(1.0, base_g * 0.25 + 0.92), 1.0, a_core)
    gl.glVertex2f(vx0, y_core0)
    gl.glVertex2f(vx1, y_core0)
    gl.glVertex2f(vx1, y_core1)
    gl.glVertex2f(vx0, y_core1)
    gl.glEnd()

    spark_count = param_store.get_int("fx.channel_change.spark.count.base") + int(
        round((1.0 - open_p) * float(param_store.get_int("fx.channel_change.spark.count.extra")))
    )
    max_dy = max(
        2.0,
        float(param_store.get("fx.channel_change.spark.spread.frac")) * h,
    )
    density_exp = float(param_store.get("fx.channel_change.spark.density.exp"))
    if density_exp < 0.01:
        density_exp = 0.01

    gl.glBegin(gl.GL_QUADS)
    s = flicker_seed ^ 0xC001D00D
    for _ in range(int(spark_count)):
        rx, s = _rand01(s)
        rr, s = _rand01(s)
        ry, s = _rand01(s)
        rs, s = _rand01(s)
        ra, s = _rand01(s)
        x = vx0 + rx * max(1.0, vx1 - vx0)
        mag = abs(ry - 0.5) * 2.0
        mag = mag**density_exp
        dy = (-1.0 if ry < 0.5 else 1.0) * mag * max_dy
        y = cy + dy
        size = float(param_store.get("fx.channel_change.spark.size.base")) + rs * float(
            param_store.get("fx.channel_change.spark.size.extra")
        )
        a = (
            float(param_store.get("fx.channel_change.spark.alpha.base"))
            + float(param_store.get("fx.channel_change.spark.alpha.extra")) * ra
        ) * beam_alpha
        if rr < 0.78:
            r, g, b = base_r, base_g, base_b
        elif rr < 0.92:
            r, g, b = (amber_r, amber_g, amber_b)
        else:
            r, g, b = (green_r, green_g, green_b)
        gl.glColor4f(r, g, b, a)
        gl.glVertex2f(x, y)
        gl.glVertex2f(x + size, y)
        gl.glVertex2f(x + size, y + size)
        gl.glVertex2f(x, y + size)
    gl.glEnd()

    gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
    gl.glDepthMask(gl.GL_TRUE)



def draw_channel_change_under_ui(self: Any, *, vp_w: int, vp_h: int, gl: Any, param_store: Any) -> None:
    if self._channel_change_start_t is None:
        return
    w = float(self.width)
    h = float(self.height)
    if w <= 0.0 or h <= 0.0:
        return
    if self.sidebar_width >= self.width:
        return

    ratio = float(self.get_pixel_ratio())
    now = time.monotonic()
    sidebar_px = int(max(0.0, float(self.sidebar_width)) * ratio)
    view_w_px = max(0, int(vp_w) - int(sidebar_px))
    view_h_px = max(0, int(vp_h))
    if view_w_px <= 0 or view_h_px <= 0:
        return

    dur = max(0.10, float(param_store.get("fx.channel_change.duration_s")))
    cc_elapsed = max(0.0, now - self._channel_change_start_t)
    p = cc_elapsed / dur if dur > 1e-6 else 1.0
    if _channel_change_hold_active(self) and p > _BROKEN_CHANNEL_CHANGE_P_CAP:
        p = float(_BROKEN_CHANNEL_CHANGE_P_CAP)
    if p >= 1.0:
        # Keep the state machine from getting stuck if the 3D pass
        # is suppressed during channel change.
        self._channel_change_start_t = None
        return

    # Model viewport only: keep floating UI panels unaffected by the reveal.
    base_r, base_g, base_b = _fx_param_color_triplet(param_store, "fx.color.ender")
    amber_r, amber_g, amber_b = _fx_param_color_triplet(param_store, "fx.color.accent.amber")
    green_r, green_g, green_b = _fx_param_color_triplet(param_store, "fx.color.accent.green")

    gl.glDisable(gl.GL_TEXTURE_2D)
    gl.glDisable(gl.GL_DEPTH_TEST)
    gl.glDepthMask(gl.GL_FALSE)
    gl.glEnable(gl.GL_BLEND)
    gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

    # Ensure we are drawing in window coordinates.
    gl.glMatrixMode(gl.GL_PROJECTION)
    gl.glLoadIdentity()
    gl.glOrtho(0.0, float(self.width), 0.0, float(self.height), -1.0, 1.0)
    gl.glMatrixMode(gl.GL_MODELVIEW)
    gl.glLoadIdentity()

    def _smoothstep(x: float) -> float:
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        return x * x * (3.0 - 2.0 * x)

    _rand01 = _deterministic_rand01

    open_p = _smoothstep(min(1.0, p / 0.75))
    fade_p = _smoothstep(min(1.0, p))
    beam_alpha = min(1.0, (1.0 - fade_p) ** 1.20) * (0.22 + 0.78 * (1.0 - open_p))

    vx0 = float(self.sidebar_width)
    vx1 = w
    cy = 0.5 * h
    feather = max(18.0, min(120.0, h * float(param_store.get("fx.channel_change.feather.frac"))))
    band_half = max(
        0.0,
        (open_p ** float(param_store.get("fx.channel_change.band.exp"))) * ((0.5 * h) + feather),
    )
    cover_scale = (1.0 - fade_p) ** float(param_store.get("fx.channel_change.cover.exp"))

    if cover_scale > 0.002:
        tile = max(1.0, float(param_store.get("fx.channel_change.tile.size")))
        mask_seed = (
            self._channel_change_seed
            ^ int(now * 120.0) * 0x9E3779B1
            ^ int(cc_elapsed * 1000.0) * 0x85EBCA6B
            ^ 0xB16B00B5
        ) & 0xFFFFFFFF

        rx0, mask_seed = _rand01(mask_seed)
        ry0, mask_seed = _rand01(mask_seed)
        off_x = rx0 * tile
        off_y = ry0 * tile

        gl.glEnable(gl.GL_SCISSOR_TEST)
        gl.glScissor(sidebar_px, 0, max(1, view_w_px), max(1, view_h_px))
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glColor4f(0.0, 0.0, 0.0, 1.0)
        gl.glBegin(gl.GL_QUADS)
        y = -off_y
        while y < h:
            dist = abs((y + (tile * 0.5)) - cy)
            t = (band_half - dist) / feather if feather > 1e-6 else 1.0
            if t <= 0.0:
                reveal = 0.0
            elif t >= 1.0:
                reveal = 1.0
            else:
                reveal = t * t * (3.0 - 2.0 * t)
            cover_prob = (1.0 - reveal) * cover_scale
            if cover_prob > 0.0005:
                x = vx0 - off_x
                while x < vx1:
                    rr, mask_seed = _rand01(mask_seed)
                    if rr < cover_prob:
                        gl.glVertex2f(x, y)
                        gl.glVertex2f(x + tile, y)
                        gl.glVertex2f(x + tile, y + tile)
                        gl.glVertex2f(x, y + tile)
                    x += tile
            y += tile
        gl.glEnd()
        gl.glDisable(gl.GL_SCISSOR_TEST)

    # Beam + sparkles (under UI; model viewport only).
    gl.glEnable(gl.GL_SCISSOR_TEST)
    gl.glScissor(sidebar_px, 0, max(1, view_w_px), max(1, view_h_px))
    try:
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE)
        flicker_seed = (
            self._channel_change_seed
            ^ int(now * 120.0) * 0x9E3779B1
            ^ int(cc_elapsed * 1000.0) * 0x85EBCA6B
            ^ 0xF00DBAAD
        ) & 0xFFFFFFFF
        rf, flicker_seed = _rand01(flicker_seed)
        flicker = 0.78 + 0.22 * math.sin((now * 38.0) + (rf * 12.0))
        flicker = max(0.30, min(1.25, flicker))

        beam_thick = float(param_store.get("fx.channel_change.beam.thick.base")) + (1.0 - open_p) * float(
            param_store.get("fx.channel_change.beam.thick.extra")
        )
        core_thick = max(1.0, beam_thick * 0.22)
        glow_thick = beam_thick
        a_glow = float(param_store.get("fx.channel_change.beam.alpha.glow")) * beam_alpha * flicker
        a_core = float(param_store.get("fx.channel_change.beam.alpha.core")) * beam_alpha * flicker
        y_core0 = max(0.0, cy - core_thick * 0.5)
        y_core1 = min(h, cy + core_thick * 0.5)
        y_glow0 = max(0.0, cy - glow_thick * 0.5)
        y_glow1 = min(h, cy + glow_thick * 0.5)

        gl.glBegin(gl.GL_QUADS)
        gl.glColor4f(base_r, base_g, base_b, a_glow)
        gl.glVertex2f(vx0, y_glow0)
        gl.glVertex2f(vx1, y_glow0)
        gl.glVertex2f(vx1, y_glow1)
        gl.glVertex2f(vx0, y_glow1)
        gl.glColor4f(min(1.0, base_r * 0.25 + 0.92), min(1.0, base_g * 0.25 + 0.92), 1.0, a_core)
        gl.glVertex2f(vx0, y_core0)
        gl.glVertex2f(vx1, y_core0)
        gl.glVertex2f(vx1, y_core1)
        gl.glVertex2f(vx0, y_core1)
        gl.glEnd()

        spark_count = param_store.get_int("fx.channel_change.spark.count.base") + int(
            round((1.0 - open_p) * float(param_store.get_int("fx.channel_change.spark.count.extra")))
        )
        max_dy = max(
            2.0,
            float(param_store.get("fx.channel_change.spark.spread.frac")) * h,
        )
        density_exp = float(param_store.get("fx.channel_change.spark.density.exp"))
        if density_exp < 0.01:
            density_exp = 0.01

        gl.glBegin(gl.GL_QUADS)
        s = flicker_seed ^ 0xC001D00D
        for _ in range(int(spark_count)):
            rx, s = _rand01(s)
            rr, s = _rand01(s)
            ry, s = _rand01(s)
            rs, s = _rand01(s)
            ra, s = _rand01(s)
            x = vx0 + rx * max(1.0, vx1 - vx0)
            mag = abs(ry - 0.5) * 2.0
            mag = mag**density_exp
            dy = (-1.0 if ry < 0.5 else 1.0) * mag * max_dy
            y = cy + dy
            size = float(param_store.get("fx.channel_change.spark.size.base")) + rs * float(
                param_store.get("fx.channel_change.spark.size.extra")
            )
            a = (
                float(param_store.get("fx.channel_change.spark.alpha.base"))
                + float(param_store.get("fx.channel_change.spark.alpha.extra")) * ra
            ) * beam_alpha
            if rr < 0.78:
                r, g, b = base_r, base_g, base_b
            elif rr < 0.92:
                r, g, b = (amber_r, amber_g, amber_b)
            else:
                r, g, b = (green_r, green_g, green_b)
            gl.glColor4f(r, g, b, a)
            gl.glVertex2f(x, y)
            gl.glVertex2f(x + size, y)
            gl.glVertex2f(x + size, y + size)
            gl.glVertex2f(x, y + size)
        gl.glEnd()
    finally:
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glDisable(gl.GL_SCISSOR_TEST)


@dataclass(frozen=True, slots=True)
class _WorldViewport:
    sidebar_px: int
    view_w_px: int
    view_h_px: int
    view_w_pts: float
    view_h_pts: float


def _effects_pipeline_enabled(self: Any) -> bool:
    """Return the master FX toggle used by world/post-effect pipeline stages."""
    return bool(getattr(self, "_effects_enabled", True))


def _resolve_world_viewport(self: Any, *, vp_w: int, vp_h: int) -> _WorldViewport:
    """Resolve viewport dimensions in both device pixels and window points."""
    ratio = float(self.get_pixel_ratio())
    sidebar_px = int(self.sidebar_width * ratio)
    view_w_px = max(1, int(vp_w) - sidebar_px)
    view_h_px = max(1, int(vp_h))
    view_w_pts = float(view_w_px) / ratio if ratio > 1e-6 else float(view_w_px)
    view_h_pts = float(view_h_px) / ratio if ratio > 1e-6 else float(view_h_px)
    return _WorldViewport(
        sidebar_px=int(sidebar_px),
        view_w_px=int(view_w_px),
        view_h_px=int(view_h_px),
        view_w_pts=float(view_w_pts),
        view_h_pts=float(view_h_pts),
    )


def _compute_world_channel_change_state(
    self: Any,
    *,
    now: float,
    param_store: Any,
    effects_enabled: bool,
) -> tuple[float, bool]:
    """Compute channel-change render state for the FX world pipeline."""
    return _compute_channel_change_state_shared(
        self,
        now=now,
        param_store=param_store,
        effects_enabled=effects_enabled,
        broken_hold_draw_active=True,
    )


def _compute_channel_change_state_shared(
    self: Any,
    *,
    now: float,
    param_store: Any,
    effects_enabled: bool,
    broken_hold_draw_active: bool,
) -> tuple[float, bool]:
    """Shared channel-change state machine for fx/render_world callers.

    When `broken_hold_draw_active` is true, broken-hold mode keeps the draw
    transition active (FX world pipeline behavior). When false, broken-hold
    mode keeps the transition clock alive but suppresses draw-active state
    (render_world 3D behavior).
    """
    cc_p = 1.0
    cc_active = False
    if self._channel_change_start_t is not None:
        broken_hold = _channel_change_hold_active(self)
        try:
            dur = max(0.02, float(param_store.get("fx.channel_change.duration_s")))
        except Exception:
            dur = 0.02
        if dur > 1e-6:
            cc_p = (now - float(self._channel_change_start_t)) / dur

        if broken_hold:
            if broken_hold_draw_active:
                if cc_p > _BROKEN_CHANNEL_CHANGE_P_CAP:
                    cc_p = float(_BROKEN_CHANNEL_CHANGE_P_CAP)
                elif cc_p <= 0.0:
                    cc_p = 0.0
                cc_active = True
            else:
                if cc_p < 0.0:
                    cc_p = 0.0
                elif cc_p > 1.0:
                    cc_p = 1.0
                cc_active = False
        elif cc_p >= 1.0:
            cc_p = 1.0
            self._channel_change_start_t = None
        elif cc_p <= 0.0:
            cc_p = 0.0
            cc_active = True
        else:
            cc_active = True
    if not bool(effects_enabled):
        cc_active = False
    return (float(cc_p), bool(cc_active))


def draw_world(self: Any, vp_w: int, vp_h: int, *, gl: Any, param_store: Any) -> None:
    view = _resolve_world_viewport(self, vp_w=int(vp_w), vp_h=int(vp_h))
    use_rt = self._model_rt.ensure(view.view_w_px, view.view_h_px)
    now = time.monotonic()
    effects_enabled = _effects_pipeline_enabled(self)
    _cc_p, cc_active = _compute_world_channel_change_state(
        self,
        now=now,
        param_store=param_store,
        effects_enabled=effects_enabled,
    )

    if use_rt:
        # Render the model view to an offscreen buffer so we can do
        # true screen-space post-fx later (FBO+shader pipeline).
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self._model_rt.fbo)
        gl.glViewport(0, 0, view.view_w_px, view.view_h_px)
        if cc_active:
            gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        else:
            try:
                r, g, b = self._env_clear_rgb()
            except Exception:
                r, g, b = (0.0, 0.0, 0.0)
            gl.glClearColor(float(r), float(g), float(b), 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        self._draw_world_3d(aspect=float(view.view_w_px) / float(max(1, view.view_h_px)))
        if cc_active:
            draw_channel_change_in_model_view(
                self,
                view_w=view.view_w_pts,
                view_h=view.view_h_pts,
                gl=gl,
                param_store=param_store,
            )
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
        draw_model_rt_to_screen(
            self,
            vp_w,
            vp_h,
            sidebar_px=view.sidebar_px,
            view_w_px=view.view_w_px,
            view_h_px=view.view_h_px,
            now=now,
            cc_active=cc_active,
            gl=gl,
            param_store=param_store,
        )
        return

    # Fallback: draw directly to the default framebuffer.
    if cc_active:
        # Channel change: cut the model view to black, then fade in the new model.
        gl.glEnable(gl.GL_SCISSOR_TEST)
        gl.glScissor(int(view.sidebar_px), 0, int(view.view_w_px), int(view.view_h_px))
        gl.glDisable(gl.GL_TEXTURE_2D)
        gl.glDisable(gl.GL_LIGHTING)
        gl.glDisable(gl.GL_BLEND)
        gl.glDisable(gl.GL_DEPTH_TEST)
        gl.glDepthMask(gl.GL_FALSE)
        gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        gl.glDisable(gl.GL_SCISSOR_TEST)
        gl.glDepthMask(gl.GL_TRUE)
    gl.glViewport(view.sidebar_px, 0, view.view_w_px, view.view_h_px)
    self._draw_world_3d(aspect=float(view.view_w_px) / float(max(1, view.view_h_px)))
    if cc_active:
        draw_channel_change_in_model_view(
            self,
            view_w=view.view_w_pts,
            view_h=view.view_h_pts,
            gl=gl,
            param_store=param_store,
        )


def draw_ui(self: Any, vp_w: int, vp_h: int, *, gl: Any, param_store: Any) -> None:
    # UI overlay.
    ratio = self.get_pixel_ratio()
    sidebar_px = int(self.sidebar_width * ratio)
    if sidebar_px > 0:
        # Draw sidebar in device pixels for crisp terminal rendering.
        self._draw_sidebar_termui(vp_w_px=int(vp_w), vp_h_px=int(vp_h), sidebar_px=int(sidebar_px))

    # Draw non-sidebar overlays (progress, hotbar, help, etc.) in
    # window points, so existing widgets remain stable during the
    # migration.
    gl.glViewport(0, 0, max(1, int(vp_w)), max(1, int(vp_h)))
    gl.glDisable(gl.GL_LIGHTING)
    gl.glDisable(gl.GL_DEPTH_TEST)
    gl.glDepthMask(gl.GL_TRUE)
    gl.glEnable(gl.GL_BLEND)
    gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
    gl.glColor4f(1.0, 1.0, 1.0, 1.0)
    gl.glMatrixMode(gl.GL_PROJECTION)
    gl.glLoadIdentity()
    gl.glOrtho(0.0, float(self.width), 0.0, float(self.height), -1.0, 1.0)
    gl.glMatrixMode(gl.GL_MODELVIEW)
    gl.glLoadIdentity()

    restore_text = self._apply_text_glitch_for_draw()
    try:
        gl.glDisable(gl.GL_SCISSOR_TEST)
        # Hard reset: overlays must draw on top of the model.
        gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
        gl.glDisable(gl.GL_DEPTH_TEST)
        gl.glDisable(gl.GL_TEXTURE_2D)
        self.overlay_shape_batch.draw()
        gl.glEnable(gl.GL_TEXTURE_2D)
        self.overlay_text_batch.draw()
        if getattr(self, "_rez_active", False):
            try:
                self._draw_rez_termui(vp_w_px=int(vp_w), vp_h_px=int(vp_h), sidebar_px=int(sidebar_px))
            except Exception:
                pass
    finally:
        restore_text()


def _start_perf_timer(enabled: bool) -> float:
    """Return a perf-counter start value when counters are enabled."""
    return time.perf_counter() if bool(enabled) else 0.0


def _update_perf_counter_ms(self: Any, *, enabled: bool, start_t: float, attr_name: str) -> None:
    """Write elapsed milliseconds to a named perf counter when enabled."""
    if not bool(enabled):
        return
    setattr(self, str(attr_name), (time.perf_counter() - float(start_t)) * 1000.0)


def draw_scene(self: Any, *, gl: Any, param_store: Any) -> None:
    perf_enabled = bool(getattr(self, "_perf_enabled", False))
    draw_t0 = _start_perf_timer(perf_enabled)
    now = time.monotonic()
    self._fps_frames += 1
    span = now - self._fps_last_t
    if span >= 0.5:
        self._fps_value = float(self._fps_frames) / span
        self._fps_frames = 0
        self._fps_last_t = now

    try:
        r, g, b = self._env_clear_rgb()
        gl.glClearColor(float(r), float(g), float(b), 1.0)
    except Exception:
        gl.glClearColor(0.0, 0.0, 0.0, 1.0)
    self.clear()
    gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
    self._update_ender_vision_hover()
    self._update_ender_vision_overlay()
    vp_w, vp_h = self.get_viewport_size()
    world_t0 = _start_perf_timer(perf_enabled)
    draw_world(self, int(vp_w), int(vp_h), gl=gl, param_store=param_store)
    _update_perf_counter_ms(self, enabled=perf_enabled, start_t=world_t0, attr_name="_perf_last_world_ms")
    # Channel-change is handled as a model-view cut-to-black + fade-in
    # in `_draw_world`/`_draw_world_3d` (no separate overlay pass).
    ui_t0 = _start_perf_timer(perf_enabled)
    draw_ui(self, int(vp_w), int(vp_h), gl=gl, param_store=param_store)
    _update_perf_counter_ms(self, enabled=perf_enabled, start_t=ui_t0, attr_name="_perf_last_ui_ms")
    if _effects_pipeline_enabled(self):
        draw_post_fx_overlay(self, gl=gl, param_store=param_store)
        apply_copy_glitch(self, int(vp_w), int(vp_h), gl=gl, param_store=param_store)
        apply_ender_vignette(self, int(vp_w), int(vp_h), gl=gl, param_store=param_store)
    _update_perf_counter_ms(self, enabled=perf_enabled, start_t=draw_t0, attr_name="_perf_last_draw_ms")
