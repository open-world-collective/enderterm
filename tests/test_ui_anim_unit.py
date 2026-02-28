from __future__ import annotations

from enderterm.ui_anim import _clamp01
from enderterm.ui_anim import _mix_rgba
from enderterm.ui_anim import _mix_u8
from enderterm.ui_anim import _termui_theme_from_store
from enderterm.ui_anim import _u8_from01


class _Store:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, key: str) -> object | None:
        return self._values.get(key)


def _theme_for_selection_mix(mix: float):
    store = _Store(
        {
            "ui.term.bg.luma": 1.0,
            "ui.term.fg.luma": 0.0,
            "ui.term.selection.mix": mix,
        }
    )
    return _termui_theme_from_store(store)


def test_clamp_and_u8_helpers_bound_inputs() -> None:
    assert _clamp01(-1.0) == 0.0
    assert _clamp01(2.0) == 1.0
    assert _clamp01(0.25) == 0.25

    assert _u8_from01(-1.0) == 0
    assert _u8_from01(2.0) == 255
    assert _u8_from01(0.5) == 128


def test_mix_helpers_clamp_t_and_alpha() -> None:
    assert _mix_u8(0, 255, -1.0) == 0
    assert _mix_u8(0, 255, 2.0) == 255
    assert _mix_u8(0, 255, 0.5) == 128

    c0 = (10, 20, 30, 40)
    c1 = (110, 120, 130, 200)
    assert _mix_rgba(c0, c1, 0.5) == (60, 70, 80, 40)
    assert _mix_rgba(c0, c1, 0.5, alpha=999) == (60, 70, 80, 255)


def test_theme_selection_contrast_picks_best_text_color() -> None:
    theme_high = _theme_for_selection_mix(1.0)
    assert theme_high.sel_bg == theme_high.fg
    assert theme_high.sel_fg == theme_high.bg

    theme_low = _theme_for_selection_mix(0.0)
    assert theme_low.sel_bg == theme_low.bg
    assert theme_low.sel_fg == theme_low.fg


def test_theme_store_values_clamp_and_fall_back_for_invalid_entries() -> None:
    theme = _termui_theme_from_store(
        _Store(
            {
                "ui.term.bg.luma": -5.0,  # clamp to 0
                "ui.term.fg.luma": 2.0,  # clamp to 1
                "ui.term.selection.mix": "bad",  # fallback to default 0.12
                "ui.term.accent.mix": 2.5,  # clamp to 1.0
            }
        )
    )

    assert theme.bg == (0, 0, 0, 255)
    assert theme.fg == (255, 255, 255, 255)
    assert theme.sel_bg == (31, 31, 31, 255)
    assert theme.sel_fg == theme.fg
    assert theme.accent == (184, 82, 224, 240)
