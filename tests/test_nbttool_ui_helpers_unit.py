from __future__ import annotations

from types import ModuleType


def test_ui_color_math_helpers(nbttool: ModuleType) -> None:
    assert nbttool._clamp01(-1.0) == 0.0
    assert nbttool._clamp01(0.0) == 0.0
    assert nbttool._clamp01(0.5) == 0.5
    assert nbttool._clamp01(2.0) == 1.0

    assert nbttool._u8_from01(-1.0) == 0
    assert nbttool._u8_from01(0.0) == 0
    assert nbttool._u8_from01(1.0) == 255
    assert nbttool._u8_from01(2.0) == 255

    assert nbttool._mix_u8(0, 255, -1.0) == 0
    assert nbttool._mix_u8(0, 255, 0.0) == 0
    assert nbttool._mix_u8(0, 255, 0.5) == 128
    assert nbttool._mix_u8(0, 255, 1.0) == 255
    assert nbttool._mix_u8(0, 255, 2.0) == 255

    c0 = (0, 0, 0, 10)
    c1 = (255, 255, 255, 200)
    assert nbttool._mix_rgba(c0, c1, 0.0) == (0, 0, 0, 10)
    assert nbttool._mix_rgba(c0, c1, 1.0) == (255, 255, 255, 10)
    assert nbttool._mix_rgba(c0, c1, 0.5, alpha=123) == (128, 128, 128, 123)

    assert nbttool._luma01_rgba((0, 0, 0, 255)) == 0.0
    assert abs(nbttool._luma01_rgba((255, 255, 255, 255)) - 1.0) < 1e-9


def test_termui_theme_from_store_defaults_and_contrast(nbttool: ModuleType) -> None:
    class BrokenStore:
        def get(self, _key: str) -> float:
            raise KeyError("boom")

    theme = nbttool._termui_theme_from_store(BrokenStore())
    for rgba in (
        theme.bg,
        theme.fg,
        theme.muted,
        theme.box_fg,
        theme.sel_bg,
        theme.sel_fg,
        theme.accent,
    ):
        r, g, b, a = rgba
        assert 0 <= r <= 255
        assert 0 <= g <= 255
        assert 0 <= b <= 255
        assert 0 <= a <= 255

    # Selection text must choose whichever (fg/bg) contrasts more with sel_bg.
    l_sel = nbttool._luma01_rgba(theme.sel_bg)
    l_fg = nbttool._luma01_rgba(theme.fg)
    l_bg = nbttool._luma01_rgba(theme.bg)
    choose_fg = abs(l_sel - l_fg) >= abs(l_sel - l_bg)
    assert theme.sel_fg == (theme.fg if choose_fg else theme.bg)


def test_tween_value_and_done(nbttool: ModuleType) -> None:
    Tween = nbttool.Tween

    assert nbttool.ease_linear(-1.0) == 0.0
    assert nbttool.ease_linear(2.0) == 1.0
    assert nbttool.ease_smoothstep(0.0) == 0.0
    assert nbttool.ease_smoothstep(1.0) == 1.0

    t = Tween(start_t=10.0, duration_s=0.0, start=2.0, end=5.0, ease=nbttool.ease_linear)
    assert t.value(10.0) == 5.0
    assert t.done(10.0) is True

    t2 = Tween(start_t=0.0, duration_s=10.0, start=0.0, end=1.0, ease=nbttool.ease_smoothstep)
    assert t2.done(9.99) is False
    assert t2.done(10.0) is True
    assert 0.49 < t2.value(5.0) < 0.51
