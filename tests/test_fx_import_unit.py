from __future__ import annotations

import builtins
import importlib
import math
import sys
import time

import pytest


@pytest.fixture()
def forbid_pyglet_import(monkeypatch: pytest.MonkeyPatch):
    """Fail fast if a module tries to import pyglet at import time.

    The fx/term extraction explicitly requires: no pyglet/OpenGL imports at
    module import time (pyglet/gl objects are passed in by the caller).
    """

    real_import = builtins.__import__

    def guarded_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):  # type: ignore[no-untyped-def]
        if name == "pyglet" or name.startswith("pyglet."):
            raise ImportError("pyglet import forbidden in this test")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    yield


def _fresh_import(module_name: str) -> object:
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


@pytest.mark.parametrize("module_name", ["enderterm.fx", "enderterm.kvalue_window"])
def test_module_imports_without_pyglet(forbid_pyglet_import, module_name: str) -> None:
    _fresh_import(module_name)


def test_effects_pipeline_enabled_defaults_to_true_and_respects_flag() -> None:
    fx_mod = _fresh_import("enderterm.fx")

    class _OwnerMissing:
        pass

    class _OwnerEnabled:
        _effects_enabled = 1

    class _OwnerDisabled:
        _effects_enabled = 0

    assert fx_mod._effects_pipeline_enabled(_OwnerMissing()) is True
    assert fx_mod._effects_pipeline_enabled(_OwnerEnabled()) is True
    assert fx_mod._effects_pipeline_enabled(_OwnerDisabled()) is False


def test_resolve_world_viewport_supports_ratio_and_ratio_fallback() -> None:
    fx_mod = _fresh_import("enderterm.fx")

    class _Owner:
        def __init__(self, ratio: float, sidebar_width: float) -> None:
            self._ratio = float(ratio)
            self.sidebar_width = float(sidebar_width)

        def get_pixel_ratio(self) -> float:
            return float(self._ratio)

    hi_dpi = fx_mod._resolve_world_viewport(_Owner(2.0, 120.0), vp_w=1600, vp_h=900)
    assert hi_dpi.sidebar_px == 240
    assert hi_dpi.view_w_px == 1360
    assert hi_dpi.view_h_px == 900
    assert hi_dpi.view_w_pts == 680.0
    assert hi_dpi.view_h_pts == 450.0

    ratio_fallback = fx_mod._resolve_world_viewport(_Owner(0.0, 120.0), vp_w=1600, vp_h=900)
    assert ratio_fallback.sidebar_px == 0
    assert ratio_fallback.view_w_px == 1600
    assert ratio_fallback.view_h_px == 900
    assert ratio_fallback.view_w_pts == 1600.0
    assert ratio_fallback.view_h_pts == 900.0


def test_compute_world_channel_change_state_disables_activity_when_effects_off() -> None:
    fx_mod = _fresh_import("enderterm.fx")

    class _Owner:
        def __init__(self) -> None:
            self._channel_change_start_t = time.monotonic() - 0.2
            self._viewer_error_text = ""

    class _Store:
        def get(self, key: str) -> float:
            assert key == "fx.channel_change.duration_s"
            return 1.0

    owner = _Owner()
    cc_p, cc_active = fx_mod._compute_world_channel_change_state(
        owner,
        now=time.monotonic(),
        param_store=_Store(),
        effects_enabled=False,
    )
    assert 0.0 < cc_p < 1.0
    assert cc_active is False
    assert owner._channel_change_start_t is not None


def test_compute_world_channel_change_state_caps_hold_and_clears_on_complete() -> None:
    fx_mod = _fresh_import("enderterm.fx")

    class _Store:
        def __init__(self, duration_s: float) -> None:
            self._duration_s = float(duration_s)

        def get(self, key: str) -> float:
            assert key == "fx.channel_change.duration_s"
            return float(self._duration_s)

    class _Owner:
        def __init__(self, *, start_t: float, broken: bool) -> None:
            self._channel_change_start_t = float(start_t)
            self._viewer_error_text = "broken" if broken else ""

    now = time.monotonic()
    hold_owner = _Owner(start_t=now - 999.0, broken=True)
    hold_p, hold_active = fx_mod._compute_world_channel_change_state(
        hold_owner,
        now=now,
        param_store=_Store(0.5),
        effects_enabled=True,
    )
    assert hold_p == fx_mod._BROKEN_CHANNEL_CHANGE_P_CAP
    assert hold_active is True
    assert hold_owner._channel_change_start_t is not None

    complete_owner = _Owner(start_t=now - 5.0, broken=False)
    done_p, done_active = fx_mod._compute_world_channel_change_state(
        complete_owner,
        now=now,
        param_store=_Store(0.5),
        effects_enabled=True,
    )
    assert done_p == 1.0
    assert done_active is False
    assert complete_owner._channel_change_start_t is None


def test_polygon_stipple_style0_uses_precomputed_row_masks() -> None:
    fx_mod = _fresh_import("enderterm.fx")

    lvl = 27
    px = 11
    py = 5
    pattern = fx_mod.polygon_stipple_pattern(lvl, style=0, phase_x=px, phase_y=py)
    row_table = fx_mod._STYLE0_ROW_BITS

    assert len(row_table) == 65
    assert len(row_table[lvl]) == 8
    assert len(row_table[lvl][px & 7]) == 8

    for y in (0, 1, 7, 8, 15, 31):
        base = y * 4
        row_bits = (
            int(pattern[base + 0])
            | (int(pattern[base + 1]) << 8)
            | (int(pattern[base + 2]) << 16)
            | (int(pattern[base + 3]) << 24)
        )
        expected = int(row_table[lvl][px & 7][(y + (py & 7)) & 7])
        assert row_bits == expected


def test_fx_param_helpers_lock_nan_inf_and_invalid_numeric_behavior() -> None:
    fx_mod = _fresh_import("enderterm.fx")

    class _Store:
        def __init__(self, values: dict[str, object]) -> None:
            self._values = dict(values)

        def get(self, key: str) -> object:
            value = self._values[key]
            if isinstance(value, Exception):
                raise value
            return value

        def get_int(self, key: str) -> object:
            value = self._values[key]
            if isinstance(value, Exception):
                raise value
            return value

    store = _Store(
        {
            "finite": "2.5",
            "nan": float("nan"),
            "pos_inf": float("inf"),
            "neg_inf": float("-inf"),
            "neg": "-3.0",
            "bad_float": "bad-float",
            "bad_int": "bad-int",
        }
    )

    assert fx_mod._fx_param_float(store, "finite") == pytest.approx(2.5)
    assert math.isnan(fx_mod._fx_param_float(store, "nan"))
    assert fx_mod._fx_param_float(store, "bad_float", default=7.5) == pytest.approx(7.5)
    assert fx_mod._fx_param_float(store, "missing", default=3.0) == pytest.approx(3.0)

    assert fx_mod._fx_param_int(store, "bad_int", default=9) == 9
    assert fx_mod._fx_param_int(store, "missing_int", default=11) == 11

    assert fx_mod._fx_param_float_nonneg(store, "neg") == 0.0
    assert fx_mod._fx_param_float_nonneg(store, "pos_inf") == float("inf")
    assert fx_mod._fx_param_float_nonneg(store, "nan") == 0.0

    assert fx_mod._fx_param_float_01(store, "pos_inf") == 1.0
    assert fx_mod._fx_param_float_01(store, "neg_inf") == 0.0
    assert fx_mod._fx_param_float_01(store, "nan") == 1.0


def test_fx_param_color_triplet_handles_nan_inf_and_invalid_components() -> None:
    fx_mod = _fresh_import("enderterm.fx")

    class _Store:
        def __init__(self, values: dict[str, object]) -> None:
            self._values = dict(values)

        def get(self, key: str) -> object:
            value = self._values[key]
            if isinstance(value, Exception):
                raise value
            return value

    store = _Store(
        {
            "fx.color.ender.r": float("nan"),
            "fx.color.ender.g": float("inf"),
            "fx.color.ender.b": "oops",
        }
    )

    rgb = fx_mod._fx_param_color_triplet(store, "fx.color.ender", default=(0.25, 0.5, 0.75))
    assert rgb == pytest.approx((1.0, 1.0, 0.75))


def test_compute_bounds_depth_min_returns_expected_nearest_depth() -> None:
    render_world = _fresh_import("enderterm.render_world")

    class _Owner:
        _pivot_center = (0.0, 0.0, 0.0)
        _orbit_target = (0.0, 0.0, 0.0)
        yaw = 0.0
        pitch = 0.0
        distance = 5.0

    depth_min = render_world._compute_bounds_depth_min(_Owner(), bounds_i=(0, 0, 0, 0, 0, 0))
    assert depth_min == pytest.approx(4.0)


def test_compute_bounds_depth_min_returns_none_when_all_corners_are_behind_camera() -> None:
    render_world = _fresh_import("enderterm.render_world")

    class _Owner:
        _pivot_center = (0.0, 0.0, 0.0)
        _orbit_target = (0.0, 0.0, 0.0)
        yaw = 0.0
        pitch = 0.0
        distance = 0.0

    depth_min = render_world._compute_bounds_depth_min(_Owner(), bounds_i=(0, 0, 0, 0, 0, 0))
    assert depth_min is None


def test_resolve_ortho_clip_near_shrinks_from_default_using_bounds_depth() -> None:
    render_world = _fresh_import("enderterm.render_world")

    class _Owner:
        _pivot_center = (0.0, 0.0, 0.0)
        _orbit_target = (0.0, 0.0, 0.0)
        yaw = 0.0
        pitch = 0.0
        distance = 5.0
        _pick_bounds_i = (0, 0, 0, 0, 0, 0)

    clip_near = render_world._resolve_ortho_clip_near(_Owner(), default_near=2.0)
    assert clip_near == pytest.approx(1.0)


def test_resolve_ortho_clip_near_respects_floor_for_tiny_positive_depth() -> None:
    render_world = _fresh_import("enderterm.render_world")

    class _Owner:
        _pivot_center = (0.0, 0.0, 0.0)
        _orbit_target = (0.0, 0.0, 0.0)
        yaw = 0.0
        pitch = 0.0
        distance = 1.000001
        _pick_bounds_i = (0, 0, 0, 0, 0, 0)

    clip_near = render_world._resolve_ortho_clip_near(_Owner(), default_near=0.5)
    assert clip_near == pytest.approx(1.0e-5)
