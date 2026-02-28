from __future__ import annotations

import time

import pytest
from enderterm import geom
from enderterm import render_world


class _NoOpGL:
    GL_DEPTH_TEST = 0
    GL_LIGHTING = 1
    GL_LIGHT0 = 2
    GL_COLOR_MATERIAL = 3
    GL_FRONT_AND_BACK = 4
    GL_AMBIENT_AND_DIFFUSE = 5
    GL_AMBIENT = 6
    GL_DIFFUSE = 7
    GL_POSITION = 8
    GL_CULL_FACE = 9
    GL_BACK = 10
    GL_CCW = 11
    GL_POLYGON_STIPPLE = 12
    GL_PROJECTION = 13
    GL_MODELVIEW = 14
    GL_POLYGON_OFFSET_FILL = 15
    GL_BLEND = 16
    GL_SRC_ALPHA = 17
    GL_ONE_MINUS_SRC_ALPHA = 18
    GL_ALPHA_TEST = 19
    GL_GREATER = 20
    GL_TRUE = 21

    def __getattr__(self, name: str):
        if name.startswith("GL_"):
            return 0

        def _noop(*_args, **_kwargs):
            return None

        return _noop


class _CaptureProjectionGL(_NoOpGL):
    def __init__(self) -> None:
        self.ortho_calls: list[tuple[float, float, float, float, float, float]] = []

    def glOrtho(self, left: float, right: float, bottom: float, top: float, near: float, far: float) -> None:
        self.ortho_calls.append((float(left), float(right), float(bottom), float(top), float(near), float(far)))


class _CaptureLightingGL(_NoOpGL):
    def __init__(self) -> None:
        self.light_calls: list[tuple[int, int, object]] = []

    def glLightfv(self, light: int, pname: int, params: object) -> None:
        self.light_calls.append((int(light), int(pname), params))


class _Batch:
    def __init__(self) -> None:
        self.draw_calls = 0

    def draw(self) -> None:
        self.draw_calls += 1


class _ParamStore:
    def get_int(self, key: str) -> int:
        if key == "rez.fade.mode":
            return 0
        return 0

    def get(self, key: str) -> float:
        if key == "render.alpha_cutout.threshold":
            return 0.5
        if key == "fx.channel_change.duration_s":
            return 0.35
        return 0.0


class _Viewer:
    def __init__(self, *, effects_enabled: bool) -> None:
        self._ortho_enabled = False
        self.distance = 8.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.pitch = 0.0
        self.yaw = 0.0
        self._orbit_target = (0.0, 0.0, 0.0)
        self._effects_enabled = bool(effects_enabled)
        self._env_patch_fade = {}
        self._env_strip_fade_h = 0
        self._env_batch = _Batch()
        self._env_decor_batch = _Batch()
        self._batch = _Batch()
        self._channel_change_start_t = None
        self._viewer_error_text = ""
        self.delta_overlay_calls = 0
        self.rez_preview_calls = 0
        self.draw_effects_calls = 0
        self.vision_marker_calls = 0
        self.env_patch_stipple_calls = 0
        self.env_strip_stipple_calls = 0
        self._pivot_center = (0.0, 0.0, 0.0)
        self._pick_bounds_i = None

    def _draw_env_patch_stipple_fades(self) -> None:
        self.env_patch_stipple_calls += 1

    def _draw_env_strip_stipple_fade(self) -> None:
        self.env_strip_stipple_calls += 1

    def _draw_structure_delta_fade_overlays(self) -> None:
        self.delta_overlay_calls += 1

    def _draw_rez_live_preview_chunks(self) -> None:
        self.rez_preview_calls += 1

    def _draw_effects(self) -> None:
        self.draw_effects_calls += 1

    def _draw_ender_vision_markers(self) -> None:
        self.vision_marker_calls += 1


def _draw_world_for_test(
    viewer: _Viewer,
    *,
    gl: _NoOpGL | None = None,
    aspect: float = 1.45,
    param_store: _ParamStore | None = None,
) -> tuple[_NoOpGL, list[tuple[float, float, float, float]]]:
    if gl is None:
        gl = _NoOpGL()
    perspective_calls: list[tuple[float, float, float, float]] = []

    def _perspective(fovy: float, aspect_v: float, near: float, far: float) -> None:
        perspective_calls.append((float(fovy), float(aspect_v), float(near), float(far)))

    render_world.draw_world_3d(
        viewer,
        aspect=float(aspect),
        gl=gl,
        param_store=param_store if param_store is not None else _ParamStore(),
        gluPerspective=_perspective,
        pyglet_mod=object(),
        group_cache={},
        no_tex_group=object(),
    )
    return gl, perspective_calls


def _patch_common_fx(
    monkeypatch,
    *,
    apply_channel_change_tint=None,
    draw_model_channel_change_fade=None,
) -> None:
    if apply_channel_change_tint is not None:
        monkeypatch.setattr(render_world.fx_mod, "apply_channel_change_tint", apply_channel_change_tint)
    if draw_model_channel_change_fade is not None:
        monkeypatch.setattr(render_world.fx_mod, "draw_model_channel_change_fade", draw_model_channel_change_fade)
    monkeypatch.setattr(render_world.fx_mod, "draw_env_transparent_blended_pass", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(render_world.fx_mod, "draw_hover_target_box", lambda *_args, **_kwargs: None)


def test_delta_overlay_draws_when_effects_off_and_channel_fx_stays_off(monkeypatch) -> None:
    tint_calls = {"n": 0}
    model_fade_calls = {"n": 0}

    def _tint(*_args, **_kwargs) -> None:
        tint_calls["n"] += 1

    def _model_fade(*_args, **_kwargs) -> None:
        model_fade_calls["n"] += 1

    _patch_common_fx(
        monkeypatch,
        apply_channel_change_tint=_tint,
        draw_model_channel_change_fade=_model_fade,
    )

    viewer = _Viewer(effects_enabled=False)
    viewer._channel_change_start_t = time.monotonic()
    _draw_world_for_test(viewer)

    assert viewer.delta_overlay_calls == 1
    assert viewer.rez_preview_calls == 1
    assert viewer.draw_effects_calls == 0
    assert tint_calls["n"] == 0
    assert model_fade_calls["n"] == 0


def test_delta_overlay_still_draws_when_effects_on(monkeypatch) -> None:
    tint_calls = {"n": 0}

    def _tint(*_args, **_kwargs) -> None:
        tint_calls["n"] += 1

    _patch_common_fx(monkeypatch, apply_channel_change_tint=_tint)

    viewer = _Viewer(effects_enabled=True)
    _draw_world_for_test(viewer)

    assert viewer.delta_overlay_calls == 1
    assert viewer.rez_preview_calls == 1
    assert viewer.draw_effects_calls == 1
    assert tint_calls["n"] == 1


def test_ortho_clip_near_shrinks_for_close_depth_and_exports_planes(monkeypatch) -> None:
    _patch_common_fx(monkeypatch)

    viewer = _Viewer(effects_enabled=False)
    viewer._ortho_enabled = True
    viewer.distance = 1.00002
    viewer._orbit_target = (0.0, 0.0, 0.0)
    viewer._pivot_center = (0.0, 0.0, 0.0)
    viewer._pick_bounds_i = (0, 0, 0, 0, 0, 0)
    gl = _CaptureProjectionGL()

    gl_after, perspective_calls = _draw_world_for_test(viewer, gl=gl)

    assert perspective_calls == []
    assert isinstance(gl_after, _CaptureProjectionGL)
    assert len(gl_after.ortho_calls) == 1
    near = gl_after.ortho_calls[0][4]
    far = gl_after.ortho_calls[0][5]
    assert 1.0e-5 <= near < 0.001
    assert far == 5000.0
    assert abs(float(getattr(viewer, "_clip_near")) - near) < 1e-9
    assert float(getattr(viewer, "_clip_far")) == far


def test_resolve_model_bounds_prefers_current_model_bounds_callable() -> None:
    viewer = _Viewer(effects_enabled=False)
    viewer._pick_bounds_i = (1, 1, 1, 1, 1, 1)

    def _current_model_bounds_i() -> tuple[int, int, int, int, int, int]:
        return (2, 3, 4, 5, 6, 7)

    viewer._current_model_bounds_i = _current_model_bounds_i
    assert render_world._resolve_model_bounds_i(viewer) == (2, 3, 4, 5, 6, 7)


def test_resolve_model_bounds_returns_none_when_current_bounds_callable_raises() -> None:
    viewer = _Viewer(effects_enabled=False)
    viewer._pick_bounds_i = (0, 0, 0, 0, 0, 0)

    def _current_model_bounds_i() -> tuple[int, int, int, int, int, int]:
        raise RuntimeError("boom")

    viewer._current_model_bounds_i = _current_model_bounds_i
    assert render_world._resolve_model_bounds_i(viewer) is None


def test_resolve_ortho_clip_near_uses_default_when_no_positive_corner_depth() -> None:
    viewer = _Viewer(effects_enabled=False)
    viewer.distance = 0.0
    viewer._pick_bounds_i = (0, 0, 0, 0, 0, 0)
    assert render_world._resolve_ortho_clip_near(viewer, default_near=0.001) == 0.001


def test_compute_channel_change_state_completes_and_clears_start() -> None:
    viewer = _Viewer(effects_enabled=True)
    viewer._channel_change_start_t = time.monotonic() - 2.0
    cc_p, cc_active = render_world._compute_channel_change_state(viewer, now=time.monotonic(), param_store=_ParamStore())
    assert cc_p == 1.0
    assert cc_active is False
    assert viewer._channel_change_start_t is None


def test_compute_channel_change_state_broken_hold_clamps_and_stays_inactive() -> None:
    viewer = _Viewer(effects_enabled=True)
    viewer._viewer_error_text = "broken"
    viewer._channel_change_start_t = time.monotonic() + 5.0
    cc_p, cc_active = render_world._compute_channel_change_state(viewer, now=time.monotonic(), param_store=_ParamStore())
    assert cc_p == 0.0
    assert cc_active is False
    assert viewer._channel_change_start_t is not None


def test_apply_default_scene_lighting_reuses_preallocated_light_buffers() -> None:
    gl = _CaptureLightingGL()

    render_world._apply_default_scene_lighting(gl, set_position=True)
    render_world._apply_default_scene_lighting(gl, set_position=True)

    ambient = [params for _, pname, params in gl.light_calls if pname == gl.GL_AMBIENT]
    diffuse = [params for _, pname, params in gl.light_calls if pname == gl.GL_DIFFUSE]
    position = [params for _, pname, params in gl.light_calls if pname == gl.GL_POSITION]

    assert len(ambient) == 2
    assert len(diffuse) == 2
    assert len(position) == 2

    assert ambient[0] is ambient[1]
    assert diffuse[0] is diffuse[1]
    assert position[0] is position[1]

    assert tuple(float(v) for v in ambient[0]) == pytest.approx((0.2, 0.2, 0.2, 1.0))
    assert tuple(float(v) for v in diffuse[0]) == pytest.approx((0.9, 0.9, 0.9, 1.0))
    assert tuple(float(v) for v in position[0]) == pytest.approx((0.35, 0.9, 0.5, 0.0))


def test_geom_mat4_mul_round_trips_identity() -> None:
    translate = geom._mat4_translate(1.25, -2.5, 3.75)
    ident = geom._mat4_identity()
    assert geom._mat4_mul(ident, translate) == translate
    assert geom._mat4_mul(translate, ident) == translate


def test_geom_mat4_from_flat_requires_exact_16_values() -> None:
    with pytest.raises(ValueError, match="mat4 requires exactly 16 values"):
        geom._mat4_from_flat([0.0] * 15)
