from __future__ import annotations

"""
Core 3D world rendering helpers.

This module intentionally avoids importing pyglet/OpenGL at import time.
Callers pass `gl` (pyglet.gl), `gluPerspective`, and other runtime objects.
"""

import math
import time
from ctypes import c_float
from typing import Any

from enderterm import fx as fx_mod
from enderterm.clip_defaults import (
    CLIP_FAR_DEFAULT,
    ORTHO_CLIP_NEAR_DEFAULT,
    ORTHO_CLIP_NEAR_FLOOR,
    PERSPECTIVE_CLIP_NEAR_DEFAULT,
)

_LIGHT0_AMBIENT = (c_float * 4)(0.2, 0.2, 0.2, 1.0)
_LIGHT0_DIFFUSE = (c_float * 4)(0.9, 0.9, 0.9, 1.0)
_LIGHT0_POSITION = (c_float * 4)(0.35, 0.9, 0.5, 0.0)
_PERSPECTIVE_CLIP_NEAR_FLOOR = 1.0e-3
_PERSPECTIVE_CLIP_FAR_CEIL = 20000.0
_PERSPECTIVE_CLIP_MAX_RATIO = 20000.0


def _apply_default_scene_lighting(gl: Any, *, set_position: bool) -> None:
    """Ensure baseline scene lighting is present in the active GL context."""
    gl.glEnable(gl.GL_LIGHTING)
    try:
        gl.glEnable(gl.GL_LIGHT0)
    except Exception:
        pass
    try:
        gl.glEnable(gl.GL_COLOR_MATERIAL)
        gl.glColorMaterial(gl.GL_FRONT_AND_BACK, gl.GL_AMBIENT_AND_DIFFUSE)
    except Exception:
        pass
    try:
        gl.glLightfv(gl.GL_LIGHT0, gl.GL_AMBIENT, _LIGHT0_AMBIENT)
        gl.glLightfv(gl.GL_LIGHT0, gl.GL_DIFFUSE, _LIGHT0_DIFFUSE)
    except Exception:
        pass
    if set_position:
        try:
            gl.glLightfv(gl.GL_LIGHT0, gl.GL_POSITION, _LIGHT0_POSITION)
        except Exception:
            pass


def _compute_channel_change_state(
    self: Any,
    *,
    now: float,
    param_store: Any,
) -> tuple[float, bool]:
    return fx_mod._compute_channel_change_state_shared(
        self,
        now=now,
        param_store=param_store,
        effects_enabled=bool(getattr(self, "_effects_enabled", True)),
        broken_hold_draw_active=False,
    )


def _configure_cutout_alpha_test(gl: Any, *, param_store: Any) -> tuple[bool, float]:
    alpha_test = False
    cutout_thr = 0.5
    try:
        thr = float(param_store.get("render.alpha_cutout.threshold"))
        thr = max(0.0, min(1.0, thr))
        cutout_thr = float(thr)
        gl.glEnable(gl.GL_ALPHA_TEST)
        gl.glAlphaFunc(gl.GL_GREATER, thr)
        alpha_test = True
    except Exception:
        alpha_test = False
    return (bool(alpha_test), float(cutout_thr))


def _resolve_stipple_fade_enabled(*, param_store: Any) -> bool:
    try:
        return bool(int(param_store.get_int("rez.fade.mode")))
    except Exception:
        return False


def _resolve_env_transparent_state(self: Any) -> bool:
    return bool(self._env_patch_fade) or int(self._env_strip_fade_h) > 0


def _draw_env_terrain_base_pass(self: Any, *, gl: Any, env_transparent: bool) -> bool:
    """Draw opaque/base terrain with guarded GL state restoration."""
    env_two_pass = False
    polygon_offset = False
    env_alpha_test = False
    try:
        try:
            gl.glEnable(gl.GL_POLYGON_OFFSET_FILL)
            gl.glPolygonOffset(1.0, 1.0)
            polygon_offset = True
        except Exception:
            polygon_offset = False

        if not env_transparent:
            try:
                gl.glDisable(gl.GL_BLEND)
            except Exception:
                pass
            gl.glDepthMask(gl.GL_TRUE)
            self._env_batch.draw()
        else:
            # Prefer a two-pass split when alpha-test is available so
            # fully-opaque terrain writes depth (prevents “tops vanish”
            # artifacts when blending is enabled).
            try:
                gl.glEnable(gl.GL_ALPHA_TEST)
                env_alpha_test = True
            except Exception:
                env_alpha_test = False

            if env_alpha_test:
                env_two_pass = True
                # Opaque terrain (alpha==1.0): depth-write, no blending.
                try:
                    gl.glAlphaFunc(gl.GL_GREATER, 0.999)
                except Exception:
                    pass
                try:
                    gl.glDisable(gl.GL_BLEND)
                except Exception:
                    pass
                gl.glDepthMask(gl.GL_TRUE)
                self._env_batch.draw()
            else:
                env_two_pass = False
                # No alpha-test: fall back to the legacy blended draw.
                gl.glEnable(gl.GL_BLEND)
                gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
                gl.glDepthMask(gl.GL_FALSE)
                self._env_batch.draw()
    finally:
        gl.glDepthMask(gl.GL_TRUE)
        if env_alpha_test:
            try:
                gl.glDisable(gl.GL_ALPHA_TEST)
            except Exception:
                pass
        if env_transparent and not env_two_pass:
            try:
                gl.glDisable(gl.GL_BLEND)
            except Exception:
                pass
        if polygon_offset:
            try:
                gl.glDisable(gl.GL_POLYGON_OFFSET_FILL)
            except Exception:
                pass
    return bool(env_two_pass)


def _draw_env_stipple_overlay_pass(
    self: Any,
    *,
    env_transparent: bool,
    env_two_pass: bool,
    use_stipple_fade: bool,
) -> None:
    if not (env_transparent and env_two_pass and use_stipple_fade):
        return
    # Terrain patch fade-in: draw new patches with stipple fade (screen-door)
    # instead of smooth transparency.
    self._draw_env_patch_stipple_fades()
    # In stipple mode, draw strip-fade terrain without blending.
    self._draw_env_strip_stipple_fade()


def _draw_world_model_pass(self: Any, *, now: float, gl: Any, param_store: Any) -> None:
    """Draw decor/model layers with cutout alpha-test guard."""
    # Cutout textures (like ladders) should not write depth for fully
    # transparent pixels. Use alpha-test where available.
    alpha_test, cutout_thr = _configure_cutout_alpha_test(gl, param_store=param_store)
    try:
        self._env_decor_batch.draw()
        cc_p, cc_active = _compute_channel_change_state(self, now=now, param_store=param_store)

        if not cc_active:
            self._batch.draw()
            # Build/destroy delta overlays are core edit feedback, not optional
            # glitch/post effects; keep them active when effects are OFF.
            self._draw_structure_delta_fade_overlays()
            self._draw_rez_live_preview_chunks()
        else:
            if self._effects_enabled:
                fx_mod.draw_model_channel_change_fade(
                    self,
                    cc_p=cc_p,
                    now=now,
                    alpha_test=alpha_test,
                    cutout_thr=cutout_thr,
                    gl=gl,
                    param_store=param_store,
                )
            else:
                self._batch.draw()
                self._draw_rez_live_preview_chunks()
    finally:
        if alpha_test:
            try:
                gl.glDisable(gl.GL_ALPHA_TEST)
            except Exception:
                pass


def _draw_env_transparent_blended_pass_if_needed(
    self: Any,
    *,
    env_transparent: bool,
    env_two_pass: bool,
    use_stipple_fade: bool,
    gl: Any,
    param_store: Any,
    pyglet_mod: Any,
    group_cache: Any,
    no_tex_group: Any,
) -> None:
    if not (env_transparent and env_two_pass and not use_stipple_fade):
        return
    fx_mod.draw_env_transparent_blended_pass(
        self,
        gl=gl,
        param_store=param_store,
        pyglet_mod=pyglet_mod,
        group_cache=group_cache,
        no_tex_group=no_tex_group,
    )


def _draw_world_post_effect_passes(self: Any, *, gl: Any, param_store: Any) -> None:
    gl.glColor3f(1.0, 1.0, 1.0)
    if self._effects_enabled:
        self._draw_effects()
    self._draw_ender_vision_markers()
    fx_mod.draw_hover_target_box(self, gl=gl, param_store=param_store)


def _resolve_model_bounds_i(self: Any) -> tuple[float, float, float, float, float, float] | None:
    """Resolve model bounds from the current-model hook or pick-bounds fallback."""
    try:
        bounds_fn = getattr(self, "_current_model_bounds_i", None)
        if callable(bounds_fn):
            bounds_i = bounds_fn()
        else:
            bounds_i = getattr(self, "_pick_bounds_i", None)
    except Exception:
        return None
    if bounds_i is None:
        return None
    try:
        min_x, min_y, min_z, max_x, max_y, max_z = bounds_i
    except Exception:
        return None
    return (min_x, min_y, min_z, max_x, max_y, max_z)


def _compute_ortho_half_extents(*, distance: float, aspect: float, fovy_deg: float) -> tuple[float, float]:
    """Compute ortho half-extents from camera distance and fov/aspect."""
    tan_half_y = math.tan(math.radians(float(fovy_deg)) / 2.0)
    half_y = float(distance) * tan_half_y
    half_x = half_y * float(aspect)
    return (float(half_x), float(half_y))


def _compute_bounds_depth_min(
    self: Any,
    *,
    bounds_i: tuple[float, float, float, float, float, float],
) -> float | None:
    """Return nearest positive camera-space depth for model bounds corners."""
    depth_range = _compute_bounds_depth_range(self, bounds_i=bounds_i)
    if depth_range is None:
        return None
    return float(depth_range[0])


def _compute_bounds_depth_range(
    self: Any,
    *,
    bounds_i: tuple[float, float, float, float, float, float],
) -> tuple[float, float] | None:
    """Return nearest/farthest positive camera-space depth for model bounds corners."""
    min_x, min_y, min_z, max_x, max_y, max_z = bounds_i
    cx, cy, cz = getattr(self, "_pivot_center", (0.0, 0.0, 0.0))
    min_geom_x = float(min_x) - float(cx)
    max_geom_x = float(max_x + 1) - float(cx)
    min_geom_y = float(min_y) - float(cy)
    max_geom_y = float(max_y + 1) - float(cy)
    min_geom_z = float(min_z) - float(cz)
    max_geom_z = float(max_z + 1) - float(cz)

    ox, oy, oz = getattr(self, "_orbit_target", (0.0, 0.0, 0.0))
    yaw = float(getattr(self, "yaw", 0.0))
    pitch = float(getattr(self, "pitch", 0.0))
    dist = float(self.distance)

    yaw_rad = math.radians(yaw)
    yaw_cos = math.cos(yaw_rad)
    yaw_sin = math.sin(yaw_rad)
    pitch_rad = math.radians(pitch)
    pitch_cos = math.cos(pitch_rad)
    pitch_sin = math.sin(pitch_rad)

    depth_min: float | None = None
    depth_max: float | None = None
    for x in (min_geom_x, max_geom_x):
        for y in (min_geom_y, max_geom_y):
            for z in (min_geom_z, max_geom_z):
                vx = float(x) - float(ox)
                vy = float(y) - float(oy)
                vz = float(z) - float(oz)
                # Rotate into camera space (matches the draw transform).
                x2 = vx * yaw_cos + vz * yaw_sin
                z2 = -vx * yaw_sin + vz * yaw_cos
                z3 = vy * pitch_sin + z2 * pitch_cos

                # z_eye = z3 - dist  ->  depth = -z_eye = dist - z3
                depth = float(dist) - float(z3)
                if depth <= 0.0:
                    continue
                if depth_min is None or depth < depth_min:
                    depth_min = depth
                if depth_max is None or depth > depth_max:
                    depth_max = depth
    if depth_min is None or depth_max is None:
        return None
    return (float(depth_min), float(depth_max))


def _resolve_ortho_clip_near(self: Any, *, default_near: float) -> float:
    """Return ortho clip-near, shrinking when bounds approach camera."""
    clip_near = float(default_near)
    try:
        bounds_i = _resolve_model_bounds_i(self)
        if bounds_i is None:
            return clip_near
        depth_min = _compute_bounds_depth_min(self, bounds_i=bounds_i)
        if depth_min is None or not math.isfinite(depth_min):
            return clip_near
        near_floor = float(ORTHO_CLIP_NEAR_FLOOR)
        return min(float(clip_near), max(float(near_floor), float(depth_min) * 0.25))
    except Exception:
        return clip_near


def _resolve_perspective_clip_planes(
    self: Any,
    *,
    default_near: float,
    default_far: float,
) -> tuple[float, float]:
    """Resolve perspective near/far planes using scene depth and precision guards."""
    clip_near = float(default_near)
    clip_far = float(default_far)
    try:
        bounds_i = _resolve_model_bounds_i(self)
        if bounds_i is None:
            return (clip_near, clip_far)
        depth_range = _compute_bounds_depth_range(self, bounds_i=bounds_i)
        if depth_range is None:
            return (clip_near, clip_far)
        depth_min, depth_max = depth_range
        if not math.isfinite(depth_min) or not math.isfinite(depth_max):
            return (clip_near, clip_far)

        near_floor = float(_PERSPECTIVE_CLIP_NEAR_FLOOR)
        clip_near = min(float(clip_near), max(float(near_floor), float(depth_min) * 0.25))
        margin = max(64.0, float(depth_max) * 0.20)
        env_extent = max(0.0, float(getattr(self, "_env_ground_radius", 0.0)) * 4.0)
        desired_far = max(float(depth_max) + float(margin), float(getattr(self, "distance", 0.0)) + env_extent + 64.0)
        if desired_far < float(default_far):
            clip_far = max(float(clip_near) + 8.0, float(desired_far))
        else:
            clip_far = min(float(_PERSPECTIVE_CLIP_FAR_CEIL), max(float(clip_near) + 8.0, float(desired_far)))

        max_ratio = float(_PERSPECTIVE_CLIP_MAX_RATIO)
        if clip_far / max(1e-9, float(clip_near)) > max_ratio:
            clip_near = max(float(clip_near), float(clip_far) / max_ratio)
        clip_near = max(float(near_floor), float(clip_near))
        clip_far = max(float(clip_near) + 8.0, float(clip_far))
        return (float(clip_near), float(clip_far))
    except Exception:
        return (float(default_near), float(default_far))


def draw_world_3d(
    self: Any,
    *,
    aspect: float,
    gl: Any,
    param_store: Any,
    gluPerspective: Any,
    pyglet_mod: Any,
    group_cache: Any,
    no_tex_group: Any,
) -> None:
    gl.glEnable(gl.GL_DEPTH_TEST)
    _apply_default_scene_lighting(gl, set_position=False)
    gl.glEnable(gl.GL_CULL_FACE)
    gl.glCullFace(gl.GL_BACK)
    gl.glFrontFace(gl.GL_CCW)
    # Be defensive about GL state: polygon stipple is a global toggle
    # and can visually “override” alpha fades if it leaks across
    # passes. We enable it explicitly only when needed.
    try:
        gl.glDisable(gl.GL_POLYGON_STIPPLE)
    except Exception:
        pass

    gl.glMatrixMode(gl.GL_PROJECTION)
    gl.glLoadIdentity()
    fovy = 55.0
    clip_far = float(CLIP_FAR_DEFAULT)
    clip_near = float(PERSPECTIVE_CLIP_NEAR_DEFAULT)
    if self._ortho_enabled:
        # In ortho mode the camera can get extremely close to the scene, and a
        # fixed near-plane can start clipping/popping geometry. Prefer a near
        # plane that shrinks as the closest visible bounds approach the camera
        # (while keeping a tiny floor to avoid degeneracy).
        half_x, half_y = _compute_ortho_half_extents(distance=float(self.distance), aspect=float(aspect), fovy_deg=fovy)
        clip_near = _resolve_ortho_clip_near(self, default_near=float(ORTHO_CLIP_NEAR_DEFAULT))

        gl.glOrtho(-half_x, half_x, -half_y, half_y, float(clip_near), float(clip_far))
    else:
        clip_near, clip_far = _resolve_perspective_clip_planes(
            self,
            default_near=float(PERSPECTIVE_CLIP_NEAR_DEFAULT),
            default_far=float(CLIP_FAR_DEFAULT),
        )
        gluPerspective(fovy, max(1e-6, float(aspect)), float(clip_near), float(clip_far))

    # Share the effective clip planes with post-fx (e.g. SSAO depth linearization).
    try:
        setattr(self, "_clip_near", float(clip_near))
        setattr(self, "_clip_far", float(clip_far))
    except Exception:
        pass

    gl.glMatrixMode(gl.GL_MODELVIEW)
    gl.glLoadIdentity()
    _apply_default_scene_lighting(gl, set_position=True)
    gl.glTranslatef(self.pan_x, self.pan_y, -self.distance)
    gl.glRotatef(self.pitch, 1.0, 0.0, 0.0)
    gl.glRotatef(self.yaw, 0.0, 1.0, 0.0)
    gl.glTranslatef(-self._orbit_target[0], -self._orbit_target[1], -self._orbit_target[2])
    now = time.monotonic()
    if self._effects_enabled:
        fx_mod.apply_channel_change_tint(self, now=now, gl=gl, param_store=param_store)

    # Render pass 1: terrain base.
    use_stipple_fade = _resolve_stipple_fade_enabled(param_store=param_store)
    env_transparent = _resolve_env_transparent_state(self)
    env_two_pass = _draw_env_terrain_base_pass(self, gl=gl, env_transparent=env_transparent)

    # Render pass 2: stippled terrain overlay (when enabled).
    _draw_env_stipple_overlay_pass(
        self,
        env_transparent=env_transparent,
        env_two_pass=env_two_pass,
        use_stipple_fade=use_stipple_fade,
    )

    # Render pass 3: model/decor + channel-change transition.
    _draw_world_model_pass(self, now=now, gl=gl, param_store=param_store)

    # Render pass 4: sorted transparent terrain (non-stipple mode).
    _draw_env_transparent_blended_pass_if_needed(
        self,
        env_transparent=env_transparent,
        env_two_pass=env_two_pass,
        use_stipple_fade=use_stipple_fade,
        gl=gl,
        param_store=param_store,
        pyglet_mod=pyglet_mod,
        group_cache=group_cache,
        no_tex_group=no_tex_group,
    )

    # Render pass 5: overlays and hover targeting.
    _draw_world_post_effect_passes(self, gl=gl, param_store=param_store)
