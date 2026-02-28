from __future__ import annotations

"""Small UI helpers: colors, theming, easing, tweens (no OpenGL imports)."""

from dataclasses import dataclass
from typing import Callable

_U8_MIN = 0
_U8_MAX = 255
RGBA = tuple[int, int, int, int]


def _clamp01(v: float) -> float:
    if v <= 0.0:
        return 0.0
    if v >= 1.0:
        return 1.0
    return v


def _clamp_u8(v: object) -> int:
    return max(_U8_MIN, min(_U8_MAX, int(v)))


def _gray_rgba(v: int, alpha: int = _U8_MAX) -> RGBA:
    u = _clamp_u8(v)
    return (u, u, u, _clamp_u8(alpha))


def _u8_from01(v: float) -> int:
    return _clamp_u8(round(_clamp01(float(v)) * float(_U8_MAX)))


def _mix_u8(a: int, b: int, t: float) -> int:
    mix_t = _clamp01(float(t))
    mixed_value = round(float(a) * (1.0 - mix_t) + float(b) * mix_t)
    return _clamp_u8(mixed_value)


def _mix_rgba(c0: RGBA, c1: RGBA, t: float, *, alpha: int | None = None) -> RGBA:
    alpha_value = c0[3] if alpha is None else int(alpha)
    return (
        _mix_u8(c0[0], c1[0], t),
        _mix_u8(c0[1], c1[1], t),
        _mix_u8(c0[2], c1[2], t),
        _clamp_u8(alpha_value),
    )


def _luma01_rgba(color: RGBA) -> float:
    # Relative luminance for sRGB-ish UI colors; good enough for choosing contrast.
    return (0.2126 * float(color[0]) + 0.7152 * float(color[1]) + 0.0722 * float(color[2])) / float(_U8_MAX)


def _pick_luma_contrast(base: RGBA, c0: RGBA, c1: RGBA) -> RGBA:
    base_luma = _luma01_rgba(base)
    c0_luma = _luma01_rgba(c0)
    c1_luma = _luma01_rgba(c1)
    return c0 if abs(base_luma - c0_luma) >= abs(base_luma - c1_luma) else c1


def _store_c01(store: object, key: str, default: float) -> float:
    try:
        raw_value = float(getattr(store, "get")(key))
    except Exception:
        raw_value = float(default)
    return _clamp01(raw_value)


def _store_u8_c01(store: object, key: str, default: float) -> int:
    return _u8_from01(_store_c01(store, key, default))


@dataclass(slots=True)
class TerminalTheme:
    bg: RGBA
    fg: RGBA
    muted: RGBA
    box_fg: RGBA
    sel_bg: RGBA
    sel_fg: RGBA
    accent: RGBA


def _termui_theme_from_store(store: object) -> TerminalTheme:
    # Store must provide `get(key)` -> number-like.
    bg = _gray_rgba(_store_u8_c01(store, "ui.term.bg.luma", 232.0 / float(_U8_MAX)))
    fg = _gray_rgba(_store_u8_c01(store, "ui.term.fg.luma", 18.0 / float(_U8_MAX)))

    # Border/text hierarchy.
    muted = _mix_rgba(fg, bg, 0.65)
    box_fg = _mix_rgba(fg, bg, 0.45)

    sel_mix = _store_c01(store, "ui.term.selection.mix", 0.12)
    sel_bg = _mix_rgba(bg, fg, sel_mix)

    # Pick selection text color that contrasts best with the selection bg.
    sel_fg = _pick_luma_contrast(sel_bg, fg, bg)

    ender = (
        _store_u8_c01(store, "fx.color.ender.r", 0.72),
        _store_u8_c01(store, "fx.color.ender.g", 0.32),
        _store_u8_c01(store, "fx.color.ender.b", 0.88),
        255,
    )
    accent_mix = _store_c01(store, "ui.term.accent.mix", 0.12)
    accent = _mix_rgba(box_fg, ender, accent_mix, alpha=240)

    return TerminalTheme(bg=bg, fg=fg, muted=muted, box_fg=box_fg, sel_bg=sel_bg, sel_fg=sel_fg, accent=accent)


def ease_linear(t: float) -> float:
    return _clamp01(t)


def ease_smoothstep(t: float) -> float:
    t = _clamp01(t)
    return t * t * (3.0 - 2.0 * t)


@dataclass(slots=True)
class Tween:
    start_t: float
    duration_s: float
    start: float = 0.0
    end: float = 1.0
    ease: Callable[[float], float] = ease_linear

    def value(self, now: float) -> float:
        if self.duration_s <= 0.0:
            return self.end
        t = (now - self.start_t) / self.duration_s
        e = self.ease(t)
        return self.start + (self.end - self.start) * e

    def done(self, now: float) -> bool:
        return now >= (self.start_t + self.duration_s)
