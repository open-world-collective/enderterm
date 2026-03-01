from __future__ import annotations

"""Pyglet/OpenGL datapack viewer entrypoint (extracted from legacy nbttool_impl)."""

import functools
from collections.abc import Callable
from importlib import import_module as _import_module

from enderterm.minecraft_jar import (
    load_configured_minecraft_jar_path,
    save_configured_minecraft_jar_path,
    validate_minecraft_client_jar,
)
from enderterm.texture_anim import (
    TextureAnimationSpec,
    build_texture_animation_spec,
    frame_rect_bottom_left,
    frame_sequence_pos_for_elapsed,
    frame_sheet_index,
    uses_subframes,
)
from enderterm.clip_defaults import ORTHO_CLIP_NEAR_DEFAULT, PERSPECTIVE_CLIP_NEAR_DEFAULT
from enderterm.termui import (
    handoff_window_focus,
    window_has_key_focus,
    window_key_focus_diagnostics,
    TermMouseCapture,
    route_term_scrollbar_drag,
    route_term_scrollbar_press,
    route_term_scrollbar_release,
)
from enderterm.viewport_registry import ViewportRegistry, is_window_alive

_impl = _import_module("enderterm.nbttool_impl")
for _k, _v in _impl.__dict__.items():
    if _k in {"__name__", "__loader__", "__package__", "__spec__", "__file__", "__cached__"}:
        continue
    globals().setdefault(_k, _v)


def _close_focus_handoff_child_window(
    *,
    target: object | None,
    fallback: Callable[[], None] | None,
    close_trigger: str,
    allow_native_perform_close: bool = True,
    on_window_close: Callable[[object], None] | None = None,
) -> str:
    """Close a non-viewport tool window using native/request/toggle paths."""
    if target is None:
        return "already_closed"

    request_close = getattr(target, "request_close", None)
    if callable(request_close):
        try:
            return str(request_close(trigger=str(close_trigger)))
        except Exception:
            pass

    if bool(allow_native_perform_close):
        ns_window = getattr(target, "_nswindow", None)
        perform_close = getattr(ns_window, "performClose_", None)
        if callable(perform_close):
            perform_close(None)
            return f"{str(close_trigger)}:native_perform_close"

    close_fn = getattr(target, "close", None)
    if callable(close_fn):
        close_fn()
        if callable(on_window_close):
            try:
                on_window_close(target)
            except Exception:
                pass
        return f"{str(close_trigger)}:window_close"

    if callable(fallback):
        fallback()
        return f"{str(close_trigger)}:toggle_fallback"
    return "missing_close"


def _close_focus_handoff_window(
    *,
    source: str,
    palette_window: object | None,
    debug_window: object | None,
    param_window: object | None = None,
    viewport_window: object | None = None,
    close_trigger: str = "child_close",
    close_palette_fallback: Callable[[], None] | None = None,
    close_debug_fallback: Callable[[], None] | None = None,
    close_param_fallback: Callable[[], None] | None = None,
    close_viewport_fallback: Callable[[], None] | None = None,
    on_param_window_close: Callable[[object], None] | None = None,
) -> str:
    """Close a focus-handoff smoke source and report which path was used."""
    src = str(source or "").strip().lower()
    target: object | None
    fallback: Callable[[], None] | None
    if src == "palette":
        target = palette_window
        fallback = close_palette_fallback
    elif src == "debug":
        target = debug_window
        fallback = close_debug_fallback
    elif src == "param":
        target = param_window
        fallback = close_param_fallback
    elif src == "viewport":
        target = viewport_window
        fallback = close_viewport_fallback
    else:
        return "unknown_source"

    if target is None:
        return "already_closed"

    if src == "viewport":
        if callable(fallback):
            fallback()
            return "toggle"
        close_view = getattr(target, "close", None)
        if callable(close_view):
            close_view()
            return f"{str(close_trigger)}:window_close"
        return "missing_close"

    return _close_focus_handoff_child_window(
        target=target,
        fallback=fallback,
        close_trigger=str(close_trigger),
        allow_native_perform_close=(src != "param"),
        on_window_close=on_param_window_close if src == "param" else None,
    )


def _apply_tool_window_close_focus_handoff(
    *,
    owner: object,
    source: str,
    attr_name: str | None,
    restore_focus: Callable[[str], None],
) -> int:
    """Apply a single immediate focus handoff after a tool-window close."""
    if attr_name:
        try:
            setattr(owner, str(attr_name), None)
        except Exception:
            pass
    src = str(source or "").strip().lower()
    if not src:
        return 0
    try:
        restore_focus(src)
    except Exception:
        return 0
    return 1


def _close_and_clear_window_attr(*, owner: object, attr_name: str) -> bool:
    """Close a tracked window attribute and clear it; return whether one existed."""
    name = str(attr_name or "").strip()
    if not name:
        return False
    try:
        win = getattr(owner, name)
    except Exception:
        return False
    if win is None:
        return False
    try:
        close_fn = getattr(win, "close", None)
        if callable(close_fn):
            close_fn()
    except Exception:
        pass
    try:
        setattr(owner, name, None)
    except Exception:
        pass
    return True


def _instance_dict_get(obj: object, attr_name: str) -> object | None:
    """Return obj.__dict__[attr_name] without triggering __getattr__ delegation."""
    name = str(attr_name or "").strip()
    if not name:
        return None
    try:
        inst_dict = obj.__dict__
    except Exception:
        return None
    return inst_dict.get(name)


def _safe_window_gl_cleanup(
    *,
    make_current: Callable[[], None] | None,
    cleanup: Callable[[], None] | None,
) -> bool:
    """Run GL cleanup only when the target window context can be made current."""
    if not callable(cleanup):
        return False
    if not callable(make_current):
        return False
    try:
        make_current()
    except Exception:
        return False
    try:
        cleanup()
    except Exception:
        pass
    return True


def _focus_probe_arm_timeout_s(source: str) -> float:
    """Deadline for smoke focus-probe arm checks by source."""
    src = str(source or "").strip().lower()
    if src == "viewport":
        return 4.50
    return 3.0


def _render_cap_interval_s(frame_cap_hz: float) -> float:
    """Return render-cap interval in seconds (0 => uncapped/tick-paced)."""
    try:
        hz = float(frame_cap_hz)
    except Exception:
        return 0.0
    if hz <= 0.0 or (not math.isfinite(hz)):
        return 0.0
    return 1.0 / max(1.0, hz)


def _render_cap_schedule_step(
    *,
    now_s: float,
    frame_cap_hz: float,
    next_deadline_s: float,
    startup_until_s: float,
    force_render: bool,
) -> tuple[bool, float]:
    """Decide whether to render this frame and return the next deadline.

    This function is pure/scheduler-only: it does not touch simulation state.
    """
    now = float(now_s)
    interval_s = _render_cap_interval_s(frame_cap_hz)
    if interval_s <= 0.0:
        return (True, now)

    deadline_s = float(next_deadline_s)
    if (not math.isfinite(deadline_s)) or deadline_s <= 0.0:
        deadline_s = now

    if bool(force_render) or now < float(startup_until_s):
        return (True, now + interval_s)

    if now + 1e-9 < deadline_s:
        return (False, deadline_s)

    # Bound catch-up so a large stall does not trigger rapid burst rendering.
    elapsed = max(0.0, now - deadline_s)
    catchup_steps = min(4, int(elapsed / interval_s) + 1)
    next_deadline_s_out = deadline_s + (catchup_steps * interval_s)
    if next_deadline_s_out <= now:
        next_deadline_s_out = now + interval_s
    return (True, next_deadline_s_out)


def _adaptive_update_budget_fps(*, dt_s: float, tick_fps_smooth: float) -> float:
    """Effective FPS signal for update/rez budgets (independent of render FPS)."""
    fps_inst = 1.0 / max(1e-6, float(dt_s))
    fps_smooth = float(tick_fps_smooth) if float(tick_fps_smooth) > 1e-6 else fps_inst
    return min(float(fps_inst), float(fps_smooth))


def _walk_mode_key_action(
    *,
    active: bool,
    symbol: int,
    modifiers: int,
    toggle_symbol: int,
    escape_symbol: int,
    cmd_mod: int,
    scaffold_symbols: set[int],
) -> str:
    """Return walk-mode key-routing action for the current keypress."""
    toggle_pressed = int(symbol) == int(toggle_symbol) and not bool(int(modifiers) & int(cmd_mod))
    if not bool(active):
        if toggle_pressed:
            return "toggle_on"
        return "pass"
    if int(symbol) == int(escape_symbol):
        return "exit_escape"
    if int(symbol) in scaffold_symbols:
        return "consume_scaffold"
    if toggle_pressed:
        return "toggle_off"
    return "pass"


def _walk_mode_move_direction_xz(
    *,
    pressed_symbols: set[int],
    yaw_deg: float,
    forward_xz: tuple[float, float] | None = None,
    key_w: int,
    key_a: int,
    key_s: int,
    key_d: int,
) -> tuple[float, float]:
    """Resolve normalized walk direction in world-space XZ from yaw + pressed keys."""
    pressed = {int(s) for s in set(pressed_symbols)}
    forward_axis = int(int(key_w) in pressed) - int(int(key_s) in pressed)
    strafe_axis = int(int(key_d) in pressed) - int(int(key_a) in pressed)
    if forward_axis == 0 and strafe_axis == 0:
        return (0.0, 0.0)

    fwd_x, fwd_z = _walk_mode_forward_xz(yaw_deg=float(yaw_deg), forward_xz=forward_xz)
    right_x = -float(fwd_z)
    right_z = float(fwd_x)

    move_x = fwd_x * float(forward_axis) + right_x * float(strafe_axis)
    move_z = fwd_z * float(forward_axis) + right_z * float(strafe_axis)
    mag = math.hypot(move_x, move_z)
    if mag <= 1e-9 or (not math.isfinite(mag)):
        return (0.0, 0.0)
    return (move_x / mag, move_z / mag)


def _walk_mode_forward_xz(
    *,
    yaw_deg: float,
    forward_xz: tuple[float, float] | None = None,
    orbit_target: tuple[float, float, float] | None = None,
    camera_world: tuple[float, float, float] | None = None,
) -> tuple[float, float]:
    """Resolve walk forward heading projected to XZ (strictly horizontal)."""
    if forward_xz is not None:
        fx = float(forward_xz[0])
        fz = float(forward_xz[1])
        mag = math.hypot(fx, fz)
        if mag > 1e-9 and math.isfinite(mag):
            return (fx / mag, fz / mag)

    if orbit_target is not None and camera_world is not None:
        fx = float(orbit_target[0]) - float(camera_world[0])
        fz = float(orbit_target[2]) - float(camera_world[2])
        mag = math.hypot(fx, fz)
        if mag > 1e-9 and math.isfinite(mag):
            return (fx / mag, fz / mag)

    yaw_rad = math.radians(-float(yaw_deg))
    # Camera-forward projected to XZ (yaw-only fallback): yaw=0 -> -Z.
    return (-math.sin(yaw_rad), -math.cos(yaw_rad))


def _walk_mode_integrate_xz(
    *,
    pressed_symbols: set[int],
    yaw_deg: float,
    forward_xz: tuple[float, float] | None = None,
    frame_dt_s: float,
    carry_dt_s: float,
    fixed_dt_s: float,
    max_steps: int,
    speed_u_per_s: float,
    key_w: int,
    key_a: int,
    key_s: int,
    key_d: int,
) -> tuple[float, float, float]:
    """Integrate walk movement with fixed steps; returns (dx, dz, carry_dt_s)."""
    step_s = float(fixed_dt_s)
    if (not math.isfinite(step_s)) or step_s <= 0.0:
        return (0.0, 0.0, 0.0)
    step_limit = max(1, int(max_steps))
    dt_s = max(0.0, float(frame_dt_s))
    carry_s = max(0.0, float(carry_dt_s))
    # Bound catch-up work to keep motion stable after a stall.
    carry_s = min(carry_s + dt_s, float(step_limit) * step_s)
    steps = min(step_limit, int(carry_s / step_s))
    if steps <= 0:
        return (0.0, 0.0, carry_s)
    carry_s = max(0.0, carry_s - float(steps) * step_s)

    speed = float(speed_u_per_s)
    if (not math.isfinite(speed)) or speed <= 0.0:
        return (0.0, 0.0, carry_s)

    dir_x, dir_z = _walk_mode_move_direction_xz(
        pressed_symbols=pressed_symbols,
        yaw_deg=float(yaw_deg),
        forward_xz=forward_xz,
        key_w=int(key_w),
        key_a=int(key_a),
        key_s=int(key_s),
        key_d=int(key_d),
    )
    if abs(dir_x) <= 1e-9 and abs(dir_z) <= 1e-9:
        return (0.0, 0.0, carry_s)

    travel = speed * step_s * float(steps)
    return (dir_x * travel, dir_z * travel, carry_s)


def _walk_mode_point_blocked(
    *,
    x_u: float,
    y_u: float,
    z_u: float,
    solid_positions: set[tuple[int, int, int]] | None,
    env_top_y_at_xz: Callable[[int, int], int | None] | None = None,
    env_bottom_y: int | None = None,
) -> bool:
    """Return whether a world-space point is blocked by solid geometry."""
    ix = int(math.floor(float(x_u)))
    iy = int(math.floor(float(y_u)))
    iz = int(math.floor(float(z_u)))
    solids = solid_positions
    if solids and (ix, iy, iz) in solids:
        return True
    if env_top_y_at_xz is None or env_bottom_y is None:
        return False
    top_y: int | None
    try:
        top_raw = env_top_y_at_xz(int(ix), int(iz))
    except Exception:
        top_raw = None
    if top_raw is None:
        top_y = None
    else:
        try:
            top_y = int(top_raw)
        except Exception:
            top_y = None
    if top_y is None:
        return False
    return int(env_bottom_y) <= int(iy) <= int(top_y)


def _walk_mode_apply_collision_xz(
    *,
    start_x_u: float,
    start_y_u: float,
    start_z_u: float,
    move_dx_u: float,
    move_dz_u: float,
    solid_positions: set[tuple[int, int, int]] | None,
    env_top_y_at_xz: Callable[[int, int], int | None] | None = None,
    env_bottom_y: int | None = None,
    max_substep_u: float,
) -> tuple[float, float]:
    """Resolve an XZ movement delta using deterministic substep collision checks."""
    dx = float(move_dx_u)
    dz = float(move_dz_u)
    if (not math.isfinite(dx)) or (not math.isfinite(dz)):
        return (0.0, 0.0)
    travel_u = math.hypot(dx, dz)
    if travel_u <= 1e-9:
        return (0.0, 0.0)
    step_u = float(max_substep_u)
    if (not math.isfinite(step_u)) or step_u <= 1e-6:
        step_u = 0.25
    substeps = max(1, int(math.ceil(travel_u / step_u)))
    step_dx = dx / float(substeps)
    step_dz = dz / float(substeps)
    cur_x = float(start_x_u)
    cur_z = float(start_z_u)
    y_u = float(start_y_u)
    for _ in range(int(substeps)):
        next_x = cur_x + float(step_dx)
        next_z = cur_z + float(step_dz)
        if _walk_mode_point_blocked(
            x_u=next_x,
            y_u=y_u,
            z_u=next_z,
            solid_positions=solid_positions,
            env_top_y_at_xz=env_top_y_at_xz,
            env_bottom_y=env_bottom_y,
        ):
            break
        cur_x = float(next_x)
        cur_z = float(next_z)
    return (float(cur_x - float(start_x_u)), float(cur_z - float(start_z_u)))


def _render_cap_refresh_hz_state(*, current_hz: int, desired_hz: int, force_draw: bool) -> tuple[int, bool]:
    """Apply render-cap Hz refresh and force-draw transition."""
    current = int(current_hz)
    desired = int(desired_hz)
    next_force = bool(force_draw)
    if desired != current:
        current = desired
        next_force = True
    return (int(current), bool(next_force))


def _render_cap_desired_hz(param_store: Any) -> int:
    """Read render-cap Hz from params with a safe integer fallback."""
    try:
        return max(0, int(param_store.get_int("render.frame_cap_hz")))
    except Exception:
        return 0


def _render_cap_refresh_hz(owner: object, *, param_store: Any) -> None:
    """Refresh owner render-cap Hz/force flags from shared param-store state."""
    desired_hz = _render_cap_desired_hz(param_store)
    next_hz, next_force = _render_cap_refresh_hz_state(
        current_hz=int(owner._render_cap_hz),
        desired_hz=int(desired_hz),
        force_draw=bool(owner._render_cap_force_next),
    )
    owner._render_cap_hz = int(next_hz)
    owner._render_cap_force_next = bool(next_force)


def _render_cap_mark_dirty_state(*, force_draw: bool) -> bool:
    """Dirty-mark transition helper: always force one upcoming draw."""
    _ = bool(force_draw)
    return True


def _render_cap_view_changed(
    prev_view_px: tuple[int, int] | None,
    curr_view_px: tuple[int, int] | None,
) -> bool:
    """Return whether viewport changed materially enough to force redraw.

    Ignore +/-1 px oscillations from platform rounding to avoid bypassing low
    frame caps under idle conditions.
    """
    if prev_view_px is None or curr_view_px is None:
        return False
    prev_w, prev_h = int(prev_view_px[0]), int(prev_view_px[1])
    curr_w, curr_h = int(curr_view_px[0]), int(curr_view_px[1])
    if prev_w <= 0 or prev_h <= 0:
        return True
    return bool(abs(curr_w - prev_w) >= 2 or abs(curr_h - prev_h) >= 2)


def _render_cap_ratio_changed(prev_ratio: float, curr_ratio: float) -> bool:
    """Return whether pixel-ratio changed materially enough to force redraw."""
    prev = float(prev_ratio)
    curr = float(curr_ratio)
    if prev <= 0.0 or (not math.isfinite(prev)):
        return True
    if curr <= 0.0 or (not math.isfinite(curr)):
        return False
    # Ignore tiny jitter; preserve immediate redraw on real display ratio shifts.
    return bool(abs(curr - prev) >= 0.05)


def _render_cap_is_uncapped(frame_cap_hz: float) -> bool:
    """Return whether render cap is disabled (draw every frame)."""
    try:
        hz = float(frame_cap_hz)
    except Exception:
        return False
    if not math.isfinite(hz):
        return False
    return hz <= 0.0


def _render_cap_coerce_viewport_px(
    vp_w_px: object,
    vp_h_px: object,
    *,
    clamp_min_one: bool = True,
) -> tuple[int, int] | None:
    """Best-effort viewport coercion for render-cap checks."""
    try:
        vp_w = int(vp_w_px)
        vp_h = int(vp_h_px)
    except Exception:
        return None
    if bool(clamp_min_one):
        vp_w = max(1, int(vp_w))
        vp_h = max(1, int(vp_h))
    return (int(vp_w), int(vp_h))


def _render_cap_read_viewport_px(owner: object) -> tuple[int, int] | None:
    """Read owner viewport size as positive px, or None when unavailable."""
    try:
        vp_w_px, vp_h_px = owner.get_viewport_size()
    except Exception:
        return None
    return _render_cap_coerce_viewport_px(vp_w_px, vp_h_px)


def _render_cap_fallback_viewport_px(owner: object) -> tuple[int, int]:
    """Fallback viewport size from window dimensions."""
    fallback_px = _render_cap_coerce_viewport_px(
        getattr(owner, "width", 1),
        getattr(owner, "height", 1),
    )
    if fallback_px is None:
        return (1, 1)
    return (int(fallback_px[0]), int(fallback_px[1]))


def _render_cap_normalize_pixel_ratio(raw_ratio: object, *, default: float = 1.0) -> float:
    """Normalize pixel-ratio inputs to a finite positive float."""
    ratio_default = float(default)
    if (not math.isfinite(ratio_default)) or ratio_default <= 0.0:
        ratio_default = 1.0
    try:
        ratio = float(raw_ratio)
    except Exception:
        ratio = ratio_default
    if (not math.isfinite(ratio)) or ratio <= 0.0:
        ratio = ratio_default
    if (not math.isfinite(ratio)) or ratio <= 0.0:
        ratio = 1.0
    return float(ratio)


def _render_cap_read_pixel_ratio(owner: object, *, default: float = 1.0) -> float:
    """Read owner pixel-ratio with standardized coercion/fallback."""
    try:
        raw_ratio = owner.get_pixel_ratio()
    except Exception:
        raw_ratio = float(default)
    return float(_render_cap_normalize_pixel_ratio(raw_ratio, default=float(default)))


def _resolve_present_cache_viewport_px(owner: object) -> tuple[int, int]:
    """Resolve present-cache viewport size, preferring cached render-cap dimensions."""
    cached = getattr(owner, "_render_cap_last_view_px", None)
    if isinstance(cached, tuple) and len(cached) == 2:
        cached_px = _render_cap_coerce_viewport_px(cached[0], cached[1], clamp_min_one=False)
        if cached_px is not None and int(cached_px[0]) > 0 and int(cached_px[1]) > 0:
            return (int(cached_px[0]), int(cached_px[1]))

    viewport_px = _render_cap_read_viewport_px(owner)
    if viewport_px is not None:
        return (int(viewport_px[0]), int(viewport_px[1]))
    return _render_cap_fallback_viewport_px(owner)


@functools.lru_cache(maxsize=8192)
def _strip_fade_target_alpha(y: int, bottom_y_base: int, strip_fade_h: int, strip_fade_levels: int) -> int:
    """Compute quantized strip-fade alpha for side faces at a given y."""
    h = int(strip_fade_h)
    if h <= 0:
        return 255

    y_mid = float(y) + 0.5
    t = (y_mid - float(bottom_y_base)) / float(h)
    if t <= 0.0:
        a = 0
    elif t >= 1.0:
        a = 255
    else:
        a = int(round(t * 255.0))
        if a < 0:
            a = 0
        if a > 255:
            a = 255

    levels = int(strip_fade_levels)
    if levels > 1:
        idx = int(round(float(a) * float(levels - 1) / 255.0))
        if idx < 0:
            idx = 0
        if idx > levels - 1:
            idx = levels - 1
        a = int(round(float(idx) * 255.0 / float(levels - 1)))
        if a < 0:
            a = 0
        if a > 255:
            a = 255
    return int(a)


def _strip_fade_side_alpha_cached(
    y: int,
    *,
    bottom_y_base: int,
    strip_fade_h: int,
    strip_fade_levels: int,
    cache: dict[int, int],
) -> int:
    """Return strip-fade alpha for side faces, memoized by y in a local cache."""
    yi = int(y)
    a = cache.get(yi)
    if a is None:
        a = int(
            _strip_fade_target_alpha(
                int(yi),
                int(bottom_y_base),
                int(strip_fade_h),
                int(strip_fade_levels),
            )
        )
        cache[int(yi)] = int(a)
    return int(a)


def _draw_guard_render_cap(
    owner: object,
    *,
    now_s: float,
    on_skip: Callable[[], None] | None = None,
) -> bool:
    """Shared on_draw guard for render-cap frame eligibility."""
    if bool(owner._should_render_frame(now_s=float(now_s))):
        return True
    if callable(on_skip):
        on_skip()
    return False


def _draw_guard_render_retry(
    owner: object,
    *,
    now_s: float,
    on_retry: Callable[[], None] | None = None,
) -> bool:
    """Shared on_draw guard for active render-error retry windows."""
    if str(getattr(owner, "_viewer_error_kind", "")) != "render":
        return True
    if float(now_s) >= float(getattr(owner, "_viewer_error_retry_after_t", 0.0)):
        return True
    if callable(on_retry):
        on_retry()
    return False


def _smoke_signature_from_rgba(raw_rgba: bytes, *, width: int, height: int) -> dict[str, object]:
    """Build a compact image signature from RGBA bytes (lower-left origin)."""
    w = max(1, int(width))
    h = max(1, int(height))
    px_count = int(w * h)
    if px_count <= 0:
        return {"width": int(w), "height": int(h), "dhash64": "0" * 16}

    row_stride = int(w * 4)
    total_r = 0
    total_g = 0
    total_b = 0

    # 9x8 luminance grid for a 64-bit horizontal dHash.
    grid_w = 9
    grid_h = 8
    grid_luma: list[int] = []
    for gy in range(grid_h):
        y = min(h - 1, int(((float(gy) + 0.5) * float(h)) / float(grid_h)))
        row_off = int(y * row_stride)
        for gx in range(grid_w):
            x = min(w - 1, int(((float(gx) + 0.5) * float(w)) / float(grid_w)))
            i = int(row_off + (x * 4))
            try:
                r = int(raw_rgba[i])
                g = int(raw_rgba[i + 1])
                b = int(raw_rgba[i + 2])
            except Exception:
                r = g = b = 0
            luma = int((299 * r + 587 * g + 114 * b) // 1000)
            grid_luma.append(luma)

    for i in range(0, int(len(raw_rgba)), 4):
        try:
            total_r += int(raw_rgba[i])
            total_g += int(raw_rgba[i + 1])
            total_b += int(raw_rgba[i + 2])
        except Exception:
            break

    bits = 0
    bit_idx = 0
    for gy in range(grid_h):
        row_base = int(gy * grid_w)
        for gx in range(8):
            a = int(grid_luma[row_base + gx])
            b = int(grid_luma[row_base + gx + 1])
            if a > b:
                bits |= int(1 << bit_idx)
            bit_idx += 1

    mean_r = float(total_r) / float(px_count)
    mean_g = float(total_g) / float(px_count)
    mean_b = float(total_b) / float(px_count)
    mean_luma = float((299.0 * mean_r + 587.0 * mean_g + 114.0 * mean_b) / 1000.0)

    return {
        "width": int(w),
        "height": int(h),
        "mean_rgb": [float(mean_r), float(mean_g), float(mean_b)],
        "mean_luma": float(mean_luma),
        "dhash64": f"{int(bits) & ((1 << 64) - 1):016x}",
    }


def _smoke_hex_hamming_distance(a: str, b: str) -> int:
    """Return bit-distance between two hex-encoded hashes."""
    try:
        va = int(str(a).strip(), 16)
        vb = int(str(b).strip(), 16)
    except Exception:
        return -1
    return int((va ^ vb).bit_count())


def _make_structure_root_loader(
    *,
    items: list[tuple[str, object]],
    zip_file: zipfile.ZipFile | None,
) -> Callable[[int], nbtlib.Compound]:
    if zip_file is not None:
        zip_ref = zip_file

        def load_root_by_index(idx: int) -> nbtlib.Compound:
            _, entry_name = items[idx]
            data = zip_ref.read(str(entry_name))
            return load_nbt_bytes(data)

        return load_root_by_index

    def load_root_by_index(idx: int) -> nbtlib.Compound:
        _, nbt_path = items[idx]
        return load_nbt(nbt_path)

    return load_root_by_index


def _import_viewer_runtime() -> tuple[object, object, object, object, object, object]:
    """Import runtime-only viewer modules used by the OpenGL entrypoint."""
    try:
        import pyglet
        from pyglet import gl
        from pyglet.gl import GLfloat, gluPerspective  # type: ignore[import-not-found]
    except Exception as e:  # pragma: no cover
        raise SystemExit(f"OpenGL viewer requires pyglet (pip install pyglet). Import error: {e}") from e

    import sys
    import traceback

    return (pyglet, gl, GLfloat, gluPerspective, sys, traceback)


@dataclass(frozen=True, slots=True)
class _ViewerBootstrapFlags:
    perf_s: float
    perf_enabled: bool
    perf_out_path: Path
    smoke_expand_enabled: bool
    smoke_second_viewport_fx_enabled: bool
    smoke_focus_handoff_enabled: bool
    smoke_real_window_click_enabled: bool
    smoke_real_window_build_edits_enabled: bool
    smoke_real_window_keys_enabled: bool
    smoke_build_edits_enabled: bool
    smoke_suite_enabled: bool
    smoke_enabled: bool
    smoke_timeout_s: float
    smoke_out_path: Path


@dataclass(frozen=True, slots=True)
class _ViewerSmokeModeOptions:
    smoke_expand_enabled: bool
    smoke_second_viewport_fx_enabled: bool
    smoke_focus_handoff_enabled: bool
    smoke_real_window_click_enabled: bool
    smoke_real_window_build_edits_enabled: bool
    smoke_real_window_keys_enabled: bool
    smoke_build_edits_enabled: bool
    smoke_suite_enabled: bool
    smoke_enabled: bool
    smoke_timeout_s: float
    smoke_out_path: Path


def _resolve_smoke_mode_options(
    *,
    smoke_expand_once: bool,
    smoke_second_viewport_fx: bool,
    smoke_focus_handoff: bool,
    smoke_real_window_click: bool,
    smoke_real_window_build_edits: bool,
    smoke_real_window_keys: bool,
    smoke_build_edits: bool,
    smoke_suite: bool,
    smoke_timeout: float,
    smoke_out: Path | None,
) -> _ViewerSmokeModeOptions:
    """Resolve and validate smoke-mode options for viewer startup."""
    smoke_expand_enabled = bool(smoke_expand_once)
    smoke_second_viewport_fx_enabled = bool(smoke_second_viewport_fx)
    smoke_focus_handoff_enabled = bool(smoke_focus_handoff)
    smoke_real_window_click_enabled = bool(smoke_real_window_click)
    smoke_real_window_build_edits_enabled = bool(smoke_real_window_build_edits)
    smoke_real_window_keys_enabled = bool(smoke_real_window_keys)
    smoke_build_edits_enabled = bool(smoke_build_edits)
    smoke_suite_enabled = bool(smoke_suite)

    smoke_mode_count = (
        int(smoke_expand_enabled)
        + int(smoke_second_viewport_fx_enabled)
        + int(smoke_focus_handoff_enabled)
        + int(smoke_real_window_click_enabled)
        + int(smoke_real_window_build_edits_enabled)
        + int(smoke_real_window_keys_enabled)
        + int(smoke_build_edits_enabled)
        + int(smoke_suite_enabled)
    )
    if smoke_mode_count > 1:
        raise SystemExit(
            "--smoke-expand-once, --smoke-second-viewport-fx, --smoke-focus-handoff, --smoke-real-window-click, "
            "--smoke-real-window-build-edits, --smoke-real-window-keys, --smoke-build-edits, and --smoke-suite are mutually exclusive"
        )

    smoke_enabled = smoke_mode_count > 0
    smoke_timeout_s = float(smoke_timeout or 0.0)
    if smoke_enabled and smoke_timeout_s <= 0.0:
        smoke_timeout_s = 20.0
    smoke_out_path = Path(smoke_out) if smoke_out is not None else Path("/tmp/enderterm_smoke.json")
    return _ViewerSmokeModeOptions(
        smoke_expand_enabled=bool(smoke_expand_enabled),
        smoke_second_viewport_fx_enabled=bool(smoke_second_viewport_fx_enabled),
        smoke_focus_handoff_enabled=bool(smoke_focus_handoff_enabled),
        smoke_real_window_click_enabled=bool(smoke_real_window_click_enabled),
        smoke_real_window_build_edits_enabled=bool(smoke_real_window_build_edits_enabled),
        smoke_real_window_keys_enabled=bool(smoke_real_window_keys_enabled),
        smoke_build_edits_enabled=bool(smoke_build_edits_enabled),
        smoke_suite_enabled=bool(smoke_suite_enabled),
        smoke_enabled=bool(smoke_enabled),
        smoke_timeout_s=float(smoke_timeout_s),
        smoke_out_path=smoke_out_path,
    )


def _resolve_viewer_bootstrap_flags(
    *,
    perf_seconds: float,
    perf_out: Path | None,
    smoke_expand_once: bool,
    smoke_second_viewport_fx: bool,
    smoke_focus_handoff: bool,
    smoke_real_window_click: bool,
    smoke_real_window_build_edits: bool,
    smoke_real_window_keys: bool,
    smoke_build_edits: bool,
    smoke_suite: bool,
    smoke_timeout: float,
    smoke_out: Path | None,
) -> _ViewerBootstrapFlags:
    """Resolve perf/smoke runtime flags for view_datapack_opengl startup."""
    perf_s = float(perf_seconds or 0.0)
    perf_enabled = perf_s > 0.0
    perf_out_path = Path(perf_out) if perf_out is not None else Path("/tmp/enderterm_perf.json")
    smoke_options = _resolve_smoke_mode_options(
        smoke_expand_once=bool(smoke_expand_once),
        smoke_second_viewport_fx=bool(smoke_second_viewport_fx),
        smoke_focus_handoff=bool(smoke_focus_handoff),
        smoke_real_window_click=bool(smoke_real_window_click),
        smoke_real_window_build_edits=bool(smoke_real_window_build_edits),
        smoke_real_window_keys=bool(smoke_real_window_keys),
        smoke_build_edits=bool(smoke_build_edits),
        smoke_suite=bool(smoke_suite),
        smoke_timeout=float(smoke_timeout),
        smoke_out=smoke_out,
    )

    return _ViewerBootstrapFlags(
        perf_s=float(perf_s),
        perf_enabled=bool(perf_enabled),
        perf_out_path=perf_out_path,
        smoke_expand_enabled=bool(smoke_options.smoke_expand_enabled),
        smoke_second_viewport_fx_enabled=bool(smoke_options.smoke_second_viewport_fx_enabled),
        smoke_focus_handoff_enabled=bool(smoke_options.smoke_focus_handoff_enabled),
        smoke_real_window_click_enabled=bool(smoke_options.smoke_real_window_click_enabled),
        smoke_real_window_build_edits_enabled=bool(smoke_options.smoke_real_window_build_edits_enabled),
        smoke_real_window_keys_enabled=bool(smoke_options.smoke_real_window_keys_enabled),
        smoke_build_edits_enabled=bool(smoke_options.smoke_build_edits_enabled),
        smoke_suite_enabled=bool(smoke_options.smoke_suite_enabled),
        smoke_enabled=bool(smoke_options.smoke_enabled),
        smoke_timeout_s=float(smoke_options.smoke_timeout_s),
        smoke_out_path=smoke_options.smoke_out_path,
    )


def _register_viewer_fonts(*, pyglet: object) -> None:
    """Register preferred viewer fonts from workspace-first candidate paths."""
    # Prefer the shared workspace font dir (../../font) so updates take effect
    # immediately without re-copying assets; fall back to bundled fonts.
    workspace_font_dir = Path(__file__).resolve().parents[2] / "font"
    font_candidates = [
        workspace_font_dir / "term_mixed.ttf",
        workspace_font_dir / "english.ttf",
        workspace_font_dir / "ender.otf",
        workspace_font_dir / "term.ttc",
        Path(__file__).resolve().parent / "assets" / "fonts" / "term_mixed.ttf",
        Path(__file__).resolve().parent / "assets" / "fonts" / "term.ttc",
        Path(__file__).resolve().parent / "assets" / "fonts" / "Glass_TTY_VT220.ttf",
    ]
    for font_path in font_candidates:
        if font_path.is_file():
            try:
                pyglet.font.add_file(str(font_path))
            except Exception:
                pass


def _load_datapack_structure_inputs(
    datapack_path: Path,
) -> tuple[zipfile.ZipFile | None, list[tuple[str, object]], Callable[[int], nbtlib.Compound]]:
    """Load structure entries and a root-loader from a datapack input path."""
    if datapack_path.is_file() and datapack_path.suffix.lower() in {".zip", ".jar"}:
        zip_file: zipfile.ZipFile | None = zipfile.ZipFile(datapack_path, "r")
        items = list(_iter_structure_entries_in_datapack_zip(zip_file))
    elif datapack_path.is_dir():
        items = list(_iter_structure_paths_in_datapack_dir(datapack_path))
        zip_file = None
    else:
        raise SystemExit("datapack-view input must be a datapack .zip/.jar or directory")

    load_root_by_index = _make_structure_root_loader(items=items, zip_file=zip_file)
    if not items:
        if zip_file is not None:
            zip_file.close()
        raise SystemExit("No structure .nbt files found in datapack")
    return (zip_file, items, load_root_by_index)


@dataclass(frozen=True, slots=True)
class _ViewerPackBootstrap:
    work_pack_dir: Path
    dp_source: DatapackSource
    pack_stack: PackStack
    jigsaw_index: JigsawDatapackIndex
    pool_items: list[tuple[str, object]]
    pool_labels: list[str]
    worldgen_labels: list[str]
    env_decor_cfg: dict[str, object]
    jigsaw_seed_base: int | None
    jigsaw_seed_tape: list[int]
    cinematic_start: bool


def _build_viewer_pack_bootstrap(
    *,
    datapack_path: Path,
    zip_file: zipfile.ZipFile | None,
    jigsaw_seed: int | None,
    jigsaw_seeds: list[int] | None,
    cinematic: bool,
) -> _ViewerPackBootstrap:
    """Build datapack + pool context needed for viewer bootstrap."""
    dp_source = DatapackSource(datapack_path, zip_file)
    work_pack_dir = Path(__file__).resolve().parent / "work-pack"
    pack_stack = PackStack(work_dir=work_pack_dir, vendors=[dp_source])
    pack_stack.ensure_work_pack()
    jigsaw_index = JigsawDatapackIndex(pack_stack.source)
    pool_items = list_template_pools(pack_stack)
    pool_labels = [pid for pid, _owner in pool_items]
    worldgen_labels = list_worldgen_jigsaw_structures(pack_stack)
    env_decor_cfg = load_environments_config(pack_stack.source)
    jigsaw_seed_base = int(jigsaw_seed) if isinstance(jigsaw_seed, int) else None
    jigsaw_seed_tape = [int(s) & 0xFFFFFFFF for s in (jigsaw_seeds or [])]
    cinematic_start = bool(cinematic)
    return _ViewerPackBootstrap(
        work_pack_dir=work_pack_dir,
        dp_source=dp_source,
        pack_stack=pack_stack,
        jigsaw_index=jigsaw_index,
        pool_items=pool_items,
        pool_labels=pool_labels,
        worldgen_labels=worldgen_labels,
        env_decor_cfg=env_decor_cfg,
        jigsaw_seed_base=jigsaw_seed_base,
        jigsaw_seed_tape=jigsaw_seed_tape,
        cinematic_start=cinematic_start,
    )


def view_datapack_opengl(  # pragma: no cover
    datapack_path: Path,
    *,
    mode: str,
    auto_threshold: int,
    textured: bool,
    minecraft_jar: Path | None,
    export_dir: Path | None = None,
    select: str | None = None,
    cinematic: bool = False,
    jigsaw_seed: int | None = None,
    jigsaw_seeds: list[int] | None = None,
    perf_seconds: float = 0.0,
    perf_out: Path | None = None,
    smoke_expand_once: bool = False,
    smoke_second_viewport_fx: bool = False,
    smoke_focus_handoff: bool = False,
    smoke_real_window_click: bool = False,
    smoke_real_window_build_edits: bool = False,
    smoke_real_window_keys: bool = False,
    smoke_build_edits: bool = False,
    smoke_suite: bool = False,
    smoke_timeout: float = 0.0,
    smoke_out: Path | None = None,
    test_banner: str | None = None,
) -> None:
    pyglet, gl, GLfloat, gluPerspective, sys, traceback = _import_viewer_runtime()

    bootstrap_flags = _resolve_viewer_bootstrap_flags(
        perf_seconds=float(perf_seconds),
        perf_out=perf_out,
        smoke_expand_once=bool(smoke_expand_once),
        smoke_second_viewport_fx=bool(smoke_second_viewport_fx),
        smoke_focus_handoff=bool(smoke_focus_handoff),
        smoke_real_window_click=bool(smoke_real_window_click),
        smoke_real_window_build_edits=bool(smoke_real_window_build_edits),
        smoke_real_window_keys=bool(smoke_real_window_keys),
        smoke_build_edits=bool(smoke_build_edits),
        smoke_suite=bool(smoke_suite),
        smoke_timeout=float(smoke_timeout),
        smoke_out=smoke_out,
    )
    perf_s = float(bootstrap_flags.perf_s)
    perf_enabled = bool(bootstrap_flags.perf_enabled)
    perf_out_path = bootstrap_flags.perf_out_path
    smoke_expand_enabled = bool(bootstrap_flags.smoke_expand_enabled)
    smoke_second_viewport_fx_enabled = bool(bootstrap_flags.smoke_second_viewport_fx_enabled)
    smoke_focus_handoff_enabled = bool(bootstrap_flags.smoke_focus_handoff_enabled)
    smoke_real_window_click_enabled = bool(bootstrap_flags.smoke_real_window_click_enabled)
    smoke_real_window_build_edits_enabled = bool(bootstrap_flags.smoke_real_window_build_edits_enabled)
    smoke_real_window_keys_enabled = bool(bootstrap_flags.smoke_real_window_keys_enabled)
    smoke_build_edits_enabled = bool(bootstrap_flags.smoke_build_edits_enabled)
    smoke_suite_enabled = bool(bootstrap_flags.smoke_suite_enabled)
    smoke_enabled = bool(bootstrap_flags.smoke_enabled)
    smoke_timeout_s = float(bootstrap_flags.smoke_timeout_s)
    smoke_out_path = bootstrap_flags.smoke_out_path

    _register_viewer_fonts(pyglet=pyglet)
    zip_file, items, load_root_by_index = _load_datapack_structure_inputs(datapack_path)
    pack_bootstrap = _build_viewer_pack_bootstrap(
        datapack_path=datapack_path,
        zip_file=zip_file,
        jigsaw_seed=jigsaw_seed,
        jigsaw_seeds=jigsaw_seeds,
        cinematic=cinematic,
    )
    work_pack_dir = pack_bootstrap.work_pack_dir
    dp_source = pack_bootstrap.dp_source
    pack_stack = pack_bootstrap.pack_stack
    jigsaw_index = pack_bootstrap.jigsaw_index
    pool_items = pack_bootstrap.pool_items
    pool_labels = pack_bootstrap.pool_labels
    worldgen_labels = pack_bootstrap.worldgen_labels
    env_decor_cfg = pack_bootstrap.env_decor_cfg
    jigsaw_seed_base = pack_bootstrap.jigsaw_seed_base
    jigsaw_seed_tape = pack_bootstrap.jigsaw_seed_tape
    cinematic_start = bool(pack_bootstrap.cinematic_start)

    # 3D helpers.
    def _rot_xy(v: tuple[float, float, float], *, rx_deg: int, ry_deg: int) -> tuple[float, float, float]:
        if rx_deg == 0 and ry_deg == 0:
            return v
        x, y, z = v
        rx = math.radians(-rx_deg)
        ry = math.radians(-ry_deg)
        # Rotate around X.
        cy = math.cos(rx)
        sy = math.sin(rx)
        y2 = y * cy - z * sy
        z2 = y * sy + z * cy
        y, z = y2, z2
        # Rotate around Y.
        cx = math.cos(ry)
        sx_ = math.sin(ry)
        x2 = x * cx + z * sx_
        z2 = -x * sx_ + z * cx
        return (x2, y, z2)

    face_normals = FACE_NORMALS
    neighbor_delta = FACE_NEIGHBOR_DELTA
    unit_face_quads = _UNIT_CUBE_FACE_QUADS
    unit_face_uv_tri = _UNIT_CUBE_FACE_UV_TRI

    class NoTextureGroup(pyglet.graphics.Group):
        def set_state(self) -> None:
            gl.glDisable(gl.GL_TEXTURE_2D)

        def unset_state(self) -> None:
            gl.glEnable(gl.GL_TEXTURE_2D)

    no_tex_group = NoTextureGroup()
    tex_cache: dict[str, pyglet.image.Texture] = {}
    group_cache: dict[str, pyglet.graphics.Group] = {}
    ui_anim_spec_cache: dict[str, TextureAnimationSpec] = {}
    ui_anim_region_cache: dict[tuple[str, int], object] = {}
    shared_term_scrollbar_capture = TermMouseCapture()

    def load_tex_from_jar(source: TextureSource, jar_rel: str) -> pyglet.image.Texture | None:
        cached = tex_cache.get(jar_rel)
        if cached is not None:
            return cached
        if not source.has(jar_rel):
            return None
        data = source.read(jar_rel)
        img = pyglet.image.load(jar_rel.rsplit("/", 1)[-1], file=io.BytesIO(data))
        tex = img.get_texture()
        tex.mag_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
        tex.min_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
        tex_cache[jar_rel] = tex
        return tex

    def _load_ui_anim_spec(source: TextureSource, jar_rel: str, tex: object) -> TextureAnimationSpec:
        cached = ui_anim_spec_cache.get(jar_rel)
        if cached is not None:
            return cached
        mcmeta_rel = f"{jar_rel}.mcmeta"
        mcmeta_bytes = source.read(mcmeta_rel) if source.has(mcmeta_rel) else None
        spec = build_texture_animation_spec(
            image_width=max(1, int(getattr(tex, "width", 1))),
            image_height=max(1, int(getattr(tex, "height", 1))),
            mcmeta_bytes=mcmeta_bytes,
        )
        ui_anim_spec_cache[jar_rel] = spec
        return spec

    def _load_ui_icon_tex(source: TextureSource, jar_rel: str, *, now_s: float | None = None) -> tuple[object | None, str]:
        tex = load_tex_from_jar(source, jar_rel)
        if tex is None:
            return (None, "")
        spec = _load_ui_anim_spec(source, jar_rel, tex)
        need_region = bool(uses_subframes(spec))
        seq_pos = 0
        if len(spec.frames) > 1 and spec.total_ticks > 1:
            if now_s is None:
                now_s = time.monotonic()
            seq_pos = frame_sequence_pos_for_elapsed(spec, elapsed_seconds=float(now_s), tick_rate=20.0)
        frame_idx = int(frame_sheet_index(spec, sequence_pos=int(seq_pos)))
        if not need_region:
            return (tex, f"{jar_rel}#{frame_idx}")
        key = (str(jar_rel), int(frame_idx))
        cached_region = ui_anim_region_cache.get(key)
        if cached_region is not None:
            return (cached_region, f"{jar_rel}#{frame_idx}")
        x, y, w, h = frame_rect_bottom_left(spec, sequence_pos=int(seq_pos))
        try:
            region = tex.get_region(x=int(x), y=int(y), width=int(w), height=int(h))
        except TypeError:
            region = tex.get_region(int(x), int(y), int(w), int(h))
        except Exception:
            return (tex, f"{jar_rel}#0")
        ui_anim_region_cache[key] = region
        return (region, f"{jar_rel}#{frame_idx}")

    def build_batch_for_structure(
        structure: Structure,
        *,
        source: TextureSource | None,
        resolver: MinecraftResourceResolver | None,
        center_override: tuple[float, float, float] | None = None,
    ) -> tuple[pyglet.graphics.Batch, float]:
        mesh = core_build_mesh_for_structure(
            structure,
            source=source,
            resolver=resolver,
            center_override=center_override,
        )
        batch = pyglet.graphics.Batch()
        for part in mesh.meshes:
            if part.material_kind == "texture":
                tex = load_tex_from_jar(source, part.material_key) if source is not None else None
                if tex is not None:
                    group = group_cache.get(part.material_key)
                    if group is None:
                        group = pyglet.graphics.TextureGroup(tex)
                        group_cache[part.material_key] = group
                    batch.add(
                        len(part.vertices) // 3,
                        gl.GL_TRIANGLES,
                        group,
                        ("v3f/static", part.vertices),
                        ("n3f/static", part.normals),
                        ("t2f/static", part.uvs or ()),
                        ("c3B/static", part.colors_u8),
                    )
                else:
                    batch.add(
                        len(part.vertices) // 3,
                        gl.GL_TRIANGLES,
                        no_tex_group,
                        ("v3f/static", part.vertices),
                        ("n3f/static", part.normals),
                        ("c3B/static", part.colors_u8),
                    )
            else:
                batch.add(
                    len(part.vertices) // 3,
                    gl.GL_TRIANGLES,
                    no_tex_group,
                    ("v3f/static", part.vertices),
                    ("n3f/static", part.normals),
                    ("c3B/static", part.colors_u8),
                )
        return (batch, float(mesh.initial_distance))

    jar_path: Path | None = None
    texture_source: TextureSource | None = None
    resolver: MinecraftResourceResolver | None = None
    if textured:
        jar_path = minecraft_jar or find_minecraft_client_jar()
        if jar_path is None:
            raise SystemExit(
                "textured view requires a Minecraft client jar; pass --minecraft-jar or set $MINECRAFT_JAR"
            )
        texture_source = TextureSource(jar_path)
        resolver = MinecraftResourceResolver(texture_source)

    cfg_jar_path = load_configured_minecraft_jar_path()
    cfg_jar_error: str | None = None
    if cfg_jar_path is not None:
        if not cfg_jar_path.is_file():
            cfg_jar_error = f"Configured Minecraft jar not found: {cfg_jar_path}"
        else:
            err = validate_minecraft_client_jar(cfg_jar_path)
            if err is not None:
                cfg_jar_error = f"Configured Minecraft jar invalid: {err}"

    startup_jar_banner_text = ""
    startup_jar_banner_kind: Literal["warn", "error"] = "warn"
    if cfg_jar_error:
        startup_jar_banner_kind = "error"
        if jar_path is not None and jar_path.is_file():
            startup_jar_banner_text = (
                f"{cfg_jar_error}\nUsing fallback jar: {jar_path}\n"
                "Drag-drop a valid Minecraft client .jar into this window (or onto the app icon) to fix."
            )
        else:
            startup_jar_banner_text = (
                f"{cfg_jar_error}\nDrag-drop a valid Minecraft client .jar into this window (or onto the app icon) to fix."
            )
    elif texture_source is None:
        startup_jar_banner_kind = "warn"
        startup_jar_banner_text = (
            "Textures are disabled (no Minecraft client .jar configured).\n"
            "Drag-drop a Minecraft client .jar into this window (or onto the app icon) to enable textures."
        )

    if export_dir is None:
        export_dir = Path.home() / "tmp" / "enderterm-exports"

    try:
        labels = [out_rel.removesuffix(".usdz") for (out_rel, _) in items]
        # Sidebar mode (what the left list shows) is independent from what we load initially.
        # Default to pool templates; allow `--select` to load a structure without
        # forcing the sidebar into NBT mode.
        start_browser_mode = "pools"
        start_load_mode: Literal["pools", "structures"] = "pools"
        start_load_idx = 0
        if select:
            needle = select.lower()
            for i, pid in enumerate(pool_labels):
                if needle == pid.lower():
                    start_load_mode = "pools"
                    start_load_idx = i
                    break
            else:
                for i, label in enumerate(labels):
                    if needle == label.lower():
                        start_load_mode = "structures"
                        start_load_idx = i
                        break
                else:
                    for i, pid in enumerate(pool_labels):
                        if needle in pid.lower():
                            start_load_mode = "pools"
                            start_load_idx = i
                            break
                    else:
                        for i, label in enumerate(labels):
                            if needle in label.lower():
                                start_load_mode = "structures"
                                start_load_idx = i
                                break

        ui_group_bg = pyglet.graphics.OrderedGroup(0)
        ui_group_frame = pyglet.graphics.OrderedGroup(1)
        ui_group_sel = pyglet.graphics.OrderedGroup(2)
        ui_group_text = pyglet.graphics.OrderedGroup(3)

        class AdditiveBlendGroup(pyglet.graphics.OrderedGroup):
            def set_state(self) -> None:
                gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE)

            def unset_state(self) -> None:
                gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)

        ui_group_glow = AdditiveBlendGroup(2)
        ui_group_hotbar_bg = pyglet.graphics.OrderedGroup(10)
        ui_group_hotbar_frame = pyglet.graphics.OrderedGroup(11)
        ui_group_hotbar_icon = pyglet.graphics.OrderedGroup(12)
        ui_group_hotbar_text = pyglet.graphics.OrderedGroup(13)
        ui_group_help_bg = pyglet.graphics.OrderedGroup(20)
        ui_group_help_frame = pyglet.graphics.OrderedGroup(21)
        ui_group_help_text = pyglet.graphics.OrderedGroup(22)
        ui_group_palette_bg = pyglet.graphics.OrderedGroup(30)
        ui_group_palette_frame = pyglet.graphics.OrderedGroup(31)
        ui_group_palette_sel = pyglet.graphics.OrderedGroup(32)
        ui_group_palette_text = pyglet.graphics.OrderedGroup(33)
        ui_group_palette_icon = pyglet.graphics.OrderedGroup(34)
        ui_group_error_bg = pyglet.graphics.OrderedGroup(40)
        ui_group_error_frame = pyglet.graphics.OrderedGroup(41)
        ui_group_error_text = pyglet.graphics.OrderedGroup(42)

        @dataclass(frozen=True, slots=True)
        class PaletteEntry:
            block_id: str
            label: str
            jar_rel_tex: str

        class ModelRenderTarget:
            def __init__(self) -> None:
                self.fbo = gl.GLuint(0)
                self.color_tex = gl.GLuint(0)
                self.depth_tex = gl.GLuint(0)
                self.depth_rb = gl.GLuint(0)
                self.w = 0
                self.h = 0
                self.ok = False

            def ensure(self, w: int, h: int) -> bool:
                w = max(1, int(w))
                h = max(1, int(h))
                if self.ok and w == self.w and h == self.h:
                    return True

                self.delete()
                self.w = w
                self.h = h

                try:
                    gl.glGenFramebuffers(1, ctypes.byref(self.fbo))
                    gl.glGenTextures(1, ctypes.byref(self.color_tex))
                    gl.glBindTexture(gl.GL_TEXTURE_2D, self.color_tex)
                    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
                    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
                    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
                    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
                    gl.glTexImage2D(
                        gl.GL_TEXTURE_2D,
                        0,
                        gl.GL_RGBA,
                        self.w,
                        self.h,
                        0,
                        gl.GL_RGBA,
                        gl.GL_UNSIGNED_BYTE,
                        None,
                    )

                    gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.fbo)
                    gl.glFramebufferTexture2D(
                        gl.GL_FRAMEBUFFER,
                        gl.GL_COLOR_ATTACHMENT0,
                        gl.GL_TEXTURE_2D,
                        self.color_tex,
                        0,
                    )

                    depth_ok = False
                    try:
                        gl.glGenTextures(1, ctypes.byref(self.depth_tex))
                        gl.glBindTexture(gl.GL_TEXTURE_2D, self.depth_tex)
                        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
                        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
                        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
                        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
                        try:
                            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_COMPARE_MODE, gl.GL_NONE)
                        except Exception:
                            pass
                        gl.glTexImage2D(
                            gl.GL_TEXTURE_2D,
                            0,
                            gl.GL_DEPTH_COMPONENT24,
                            self.w,
                            self.h,
                            0,
                            gl.GL_DEPTH_COMPONENT,
                            gl.GL_UNSIGNED_INT,
                            None,
                        )
                        gl.glFramebufferTexture2D(
                            gl.GL_FRAMEBUFFER,
                            gl.GL_DEPTH_ATTACHMENT,
                            gl.GL_TEXTURE_2D,
                            self.depth_tex,
                            0,
                        )
                        status = gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER)
                        depth_ok = bool(status == gl.GL_FRAMEBUFFER_COMPLETE)
                    except Exception:
                        depth_ok = False

                    if not depth_ok:
                        try:
                            if int(self.depth_tex.value):
                                gl.glDeleteTextures(1, ctypes.byref(self.depth_tex))
                                self.depth_tex.value = 0
                        except Exception:
                            pass

                        gl.glGenRenderbuffers(1, ctypes.byref(self.depth_rb))
                        gl.glBindRenderbuffer(gl.GL_RENDERBUFFER, self.depth_rb)
                        gl.glRenderbufferStorage(gl.GL_RENDERBUFFER, gl.GL_DEPTH_COMPONENT24, self.w, self.h)
                        gl.glFramebufferRenderbuffer(
                            gl.GL_FRAMEBUFFER,
                            gl.GL_DEPTH_ATTACHMENT,
                            gl.GL_RENDERBUFFER,
                            self.depth_rb,
                        )
                        status = gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER)
                        depth_ok = bool(status == gl.GL_FRAMEBUFFER_COMPLETE)

                    self.ok = bool(depth_ok)
                except Exception:
                    self.ok = False
                finally:
                    try:
                        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
                        gl.glBindRenderbuffer(gl.GL_RENDERBUFFER, 0)
                        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                    except Exception:
                        pass

                if not self.ok:
                    self.delete()
                return self.ok

            def delete(self) -> None:
                try:
                    if int(self.depth_rb.value):
                        gl.glDeleteRenderbuffers(1, ctypes.byref(self.depth_rb))
                        self.depth_rb.value = 0
                    if int(self.depth_tex.value):
                        gl.glDeleteTextures(1, ctypes.byref(self.depth_tex))
                        self.depth_tex.value = 0
                    if int(self.color_tex.value):
                        gl.glDeleteTextures(1, ctypes.byref(self.color_tex))
                        self.color_tex.value = 0
                    if int(self.fbo.value):
                        gl.glDeleteFramebuffers(1, ctypes.byref(self.fbo))
                        self.fbo.value = 0
                except Exception:
                    pass
                self.w = 0
                self.h = 0
                self.ok = False

        # Legacy in-function parameter defaults (unused). Kept here for archeology; not executed.
        """
        channel_param_defs: list[ParamDef] = [
            # Debug-oriented ranges: allow extreme values for fast iteration.
            ParamDef("ui.slider.brightness", "UI slider brightness", 1.309, 0.05, 10.0),
            ParamDef("render.alpha_cutout.threshold", "Alpha cutout threshold", 0.50, 0.00, 1.00),
            ParamDef("render.target_box.line_width", "Target box line width", 5.0, 1.0, 16.0),
            ParamDef(
                "env.ground.patch.size",
                "Env ground patch size (blocks)",
                4,
                3,
                128,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef(
                "env.ground.patches_per_tick",
                "Env patches per tick",
                1,
                1,
                512,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef("env.ground.rez_fade_s", "Env ground rez fade (s)", 5.0, 0.05, 30.0),
            ParamDef(
                "env.ground.radius",
                "Env ground radius (blocks)",
                ENV_GROUND_RADIUS,
                0,
                512,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef(
                "env.ground.bottom",
                "Env ground bottom Y",
                -ENV_GROUND_THICKNESS,
                WORLD_MIN_Y,
                4096,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef(
                "env.terrain.amp",
                "Env terrain amplitude (blocks)",
                18,
                0,
                512,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef("env.terrain.scale", "Env terrain scale (blocks)", 96.0, 4.0, 2048.0),
            ParamDef(
                "env.terrain.octaves",
                "Env terrain octaves",
                5,
                1,
                12,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef("env.terrain.lacunarity", "Env terrain lacunarity", 2.0, 1.0, 8.0),
            ParamDef("env.terrain.h", "Env terrain H", 1.0, 0.0, 2.5),
            ParamDef("env.terrain.ridged.offset", "Env terrain ridged offset", 1.0, 0.0, 3.0),
            ParamDef("env.terrain.ridged.gain", "Env terrain ridged gain", 2.0, 0.0, 6.0),
            ParamDef("rez.throttle.sleep_ms", "Rez throttle sleep (ms)", 1.0, 0.0, 50.0),
            ParamDef(
                "rez.throttle.every",
                "Rez throttle interval (candidates)",
                256,
                64,
                65_536,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef("fx.color.ender.r", "FX Ender purple R", 0.803922, 0.00, 1.50),
            ParamDef("fx.color.ender.g", "FX Ender purple G", 0.0, 0.00, 1.50),
            ParamDef("fx.color.ender.b", "FX Ender purple B", 1.0, 0.00, 1.50),
            ParamDef("fx.color.ender.yellow.r", "FX Ender yellow R", 0.592157, 0.00, 1.50),
            ParamDef("fx.color.ender.yellow.g", "FX Ender yellow G", 0.635294, 0.00, 1.50),
            ParamDef("fx.color.ender.yellow.b", "FX Ender yellow B", 0.447059, 0.00, 1.50),
            ParamDef("fx.color.ender.pink.r", "FX Ender pink R", 0.639216, 0.00, 1.50),
            ParamDef("fx.color.ender.pink.g", "FX Ender pink G", 0.490196, 0.00, 1.50),
            ParamDef("fx.color.ender.pink.b", "FX Ender pink B", 0.607843, 0.00, 1.50),
            ParamDef("fx.glitch.warp.barrel.base", "Warp barrel base", 0.040706, -2.00, 2.00),
            ParamDef("fx.glitch.warp.wobble.amp", "Warp wobble amp", 0.46133, 0.00, 4.00),
            ParamDef("fx.glitch.warp.wobble.hz", "Warp wobble Hz", 0.15142, 0.00, 24.0),
            ParamDef("fx.glitch.warp.energy.rez.mult", "Warp energy rez mult", 2.50, 0.00, 20.0),
            ParamDef("fx.glitch.warp.energy.channel.mult", "Warp energy channel mult", 8.00, 0.00, 40.0),
            ParamDef(
                "fx.glitch.warp.grid",
                "Warp grid segments",
                36,
                4,
                240,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef("fx.glitch.beam.alpha", "Glitch beam alpha", 0.08, 0.00, 1.00),
            ParamDef("fx.glitch.beam.rez.mult", "Glitch beam rez mult", 2.25, 0.00, 20.0),
            ParamDef("fx.glitch.beam.thick.px", "Glitch beam thickness (px)", 1.15, 0.00, 120.0),
            ParamDef("fx.glitch.beam.alpha.glow", "Glitch beam glow alpha", 0.060, 0.000, 1.000),
            ParamDef("fx.glitch.beam.alpha.core", "Glitch beam core alpha", 0.120, 0.000, 1.000),
            ParamDef(
                "fx.glitch.spark.count",
                "Glitch sparkles count",
                22,
                0,
                20000,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef("fx.glitch.spark.rez.mult", "Glitch sparkles rez mult", 2.40, 0.00, 20.0),
            ParamDef("fx.glitch.spark.size.base", "Glitch sparkles size base", 1.1, 0.1, 80.0),
            ParamDef("fx.glitch.spark.size.extra", "Glitch sparkles size range", 2.2, 0.0, 200.0),
            ParamDef("fx.glitch.spark.alpha.base", "Glitch sparkles alpha base", 0.030, 0.000, 1.000),
            ParamDef("fx.glitch.spark.alpha.extra", "Glitch sparkles alpha range", 0.075, 0.000, 1.000),
            ParamDef("fx.glitch.spark.spread.frac", "Glitch sparkles spread (screen %)", 0.45, 0.00, 5.00),
            ParamDef("fx.glitch.spark.density.exp", "Glitch spark density exponent", 1.00, 0.05, 12.0),
            ParamDef("fx.glitch.text.rate_hz.normal", "Text glitch rate Hz normal", 0.192616, 0.00, 240.0),
            ParamDef("fx.glitch.text.rate_hz.rez", "Text glitch rate Hz rezzing", 5.00803, 0.00, 240.0),
            ParamDef(
                "fx.glitch.text.max_chars",
                "Text glitch intensity",
                1,
                1,
                64,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef("fx.channel_change.duration_s", "Channel change duration (s)", 0.642495, 0.02, 10.0),
            ParamDef("fx.channel_change.tint.hold.frac", "Tint hold fraction", 0.0, 0.00, 0.98),
            ParamDef("fx.channel_change.tint.fade.exp", "Tint fade exponent", 0.771328, 0.05, 12.0),
            ParamDef("fx.channel_change.tint.strength", "Tint strength", 1.0, 0.00, 2.00),
            ParamDef("fx.channel_change.tile.size", "Reveal tile size", 8.47059, 1.0, 128.0),
            ParamDef("fx.channel_change.cover.exp", "Reveal fade exponent", 4.71196, 0.01, 12.0),
            ParamDef("fx.channel_change.band.exp", "Reveal band exponent", 7.76824, 0.01, 12.0),
            ParamDef("fx.channel_change.feather.frac", "Reveal feather (screen %)", 0.72549, 0.00, 2.00),
            ParamDef("fx.channel_change.beam.thick.base", "Beam thickness base", 6.0, 0.00, 60.0),
            ParamDef("fx.channel_change.beam.thick.extra", "Beam thickness extra", 10.4151, 0.00, 240.0),
            ParamDef("fx.channel_change.beam.alpha.glow", "Beam glow alpha", 0.004049, 0.000, 1.000),
            ParamDef("fx.channel_change.beam.alpha.core", "Beam core alpha", 0.068826, 0.000, 1.000),
            ParamDef(
                "fx.channel_change.spark.count.base",
                "Sparkles count base",
                8548,
                0,
                20000,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef(
                "fx.channel_change.spark.count.extra",
                "Sparkles count extra",
                740,
                0,
                60000,
                is_int=True,
                fmt="{:.0f}",
            ),
            ParamDef("fx.channel_change.spark.size.base", "Sparkles size base", 1.17295, 0.1, 80.0),
            ParamDef("fx.channel_change.spark.size.extra", "Sparkles size range", 29.5547, 0.0, 200.0),
            ParamDef("fx.channel_change.spark.alpha.base", "Sparkles alpha base", 0.0, 0.000, 1.000),
            ParamDef("fx.channel_change.spark.alpha.extra", "Sparkles alpha range", 0.0303644, 0.000, 1.000),
            ParamDef("fx.channel_change.spark.spread.frac", "Sparkles spread (screen %)", 0.769231, 0.00, 5.00),
            ParamDef("fx.channel_change.spark.density.exp", "Spark density exponent", 1.50142, 0.05, 12.0),
            ParamDef("fx.channel_change.warp.barrel.extra", "Channel warp barrel extra", 0.41791, -4.00, 4.00),
            ParamDef("fx.channel_change.warp.decay.exp", "Channel warp decay exponent", 1.15, 0.01, 12.0),
            ParamDef("fx.glitch.vignette.strength", "Vignette strength", 0.57931, 0.00, 2.00),
            ParamDef("fx.glitch.vignette.thickness.frac", "Vignette thickness (screen %)", 0.264151, 0.00, 2.00),
            ParamDef("fx.glitch.vignette.thickness.min_px", "Vignette min thickness (px)", 393.103, 0.0, 2000.0),
            ParamDef("fx.glitch.vignette.thickness.max_px", "Vignette max thickness (px)", 1448.28, 0.0, 4000.0),
            ParamDef("fx.glitch.vignette.falloff.exp", "Vignette falloff exp", 1.21494, 0.05, 12.0),
            ParamDef("fx.glitch.ssao.strength", "SSAO strength", 0.35, 0.00, 2.00),
            ParamDef("fx.glitch.ssao.radius.blocks", "SSAO radius (blocks)", 0.75, 0.00, 16.0),
            ParamDef("fx.glitch.ssao.bias", "SSAO bias (blocks)", 0.02, 0.0, 1.0),
            ParamDef("fx.glitch.ssao.brightness", "SSAO brightness compensation", 1.12, 0.0, 4.0),
        ]
        """

        param_store = load_default_param_store()
        test_banner_text = str(test_banner).strip() if isinstance(test_banner, str) else ""
        test_build = _enderterm_version()

        def _viewport_rotate_x_deg(v: tuple[float, float, float], deg: float) -> tuple[float, float, float]:
            rad = math.radians(deg)
            x, y, z = v
            cy = math.cos(rad)
            sy = math.sin(rad)
            return (x, y * cy - z * sy, y * sy + z * cy)

        def _viewport_rotate_y_deg(v: tuple[float, float, float], deg: float) -> tuple[float, float, float]:
            rad = math.radians(deg)
            x, y, z = v
            cx = math.cos(rad)
            sx = math.sin(rad)
            return (x * cx + z * sx, y, -x * sx + z * cx)

        def _viewport_camera_world_position(view: object) -> tuple[float, float, float]:
            ox, oy, oz = getattr(view, "_orbit_target")
            v = (-float(getattr(view, "pan_x")), -float(getattr(view, "pan_y")), float(getattr(view, "distance")))
            v = _viewport_rotate_x_deg(v, -float(getattr(view, "pitch")))
            v = _viewport_rotate_y_deg(v, -float(getattr(view, "yaw")))
            return (ox + v[0], oy + v[1], oz + v[2])

        def _viewport_camera_u_position(view: object) -> tuple[float, float, float]:
            cx, cy, cz = getattr(view, "_pivot_center")
            cam_world = _viewport_camera_world_position(view)
            return (cam_world[0] + cx, cam_world[1] + cy, cam_world[2] + cz)

        def _viewport_camera_safety_strengths(view: object) -> tuple[float, float]:
            cam_u = _viewport_camera_u_position(view)
            ix = int(math.floor(cam_u[0]))
            iy = int(math.floor(cam_u[1]))
            iz = int(math.floor(cam_u[2]))

            inside = False
            positions = getattr(view, "_rez_live_positions") if bool(getattr(view, "_rez_active")) else getattr(view, "_pick_positions")
            if positions and (ix, iy, iz) in positions:
                inside = True

            low_p = 0.0
            try:
                warn_above = float(param_store.get("fx.glitch.void_wash.warn_above.blocks"))
            except Exception:
                warn_above = 32.0
            if warn_above < 0.0:
                warn_above = 0.0
            fade_range = max(1e-6, float(warn_above))
            safe_y = float(WORLD_MIN_Y) + float(warn_above)
            if cam_u[1] < safe_y:
                low_p = max(0.0, min(1.0, (safe_y - float(cam_u[1])) / float(fade_range)))

            if not getattr(view, "_env_preset")().is_space():
                top_y = getattr(view, "_env_top_y_cached_at")(ix, iz)
                if top_y is not None:
                    bottom = int(getattr(view, "_env_ground_bottom"))
                    if bottom <= iy <= int(top_y):
                        inside = True

            return (1.0 if inside else 0.0, float(low_p))

        def _viewport_draw_camera_safety_overlay(view: object, *, sidebar_px: int, view_w_px: int, view_h_px: int) -> None:
            fx_mod.draw_camera_safety_overlay(
                view,
                sidebar_px=sidebar_px,
                view_w_px=view_w_px,
                view_h_px=view_h_px,
                gl=gl,
                param_store=param_store,
            )

        def _viewport_set_orbit_target(view: object, target: tuple[float, float, float]) -> None:
            old = getattr(view, "_orbit_target")
            if bool(getattr(view, "_ortho_enabled")):
                delta = (float(target[0]) - float(old[0]), float(target[1]) - float(old[1]), float(target[2]) - float(old[2]))
                delta_cam = _viewport_rotate_y_deg(delta, float(getattr(view, "yaw")))
                delta_cam = _viewport_rotate_x_deg(delta_cam, float(getattr(view, "pitch")))
                setattr(view, "_orbit_target", target)
                view.pan_x += float(delta_cam[0])
                view.pan_y += float(delta_cam[1])
                return

            cam = _viewport_camera_world_position(view)
            setattr(view, "_orbit_target", target)
            to_cam = (cam[0] - target[0], cam[1] - target[1], cam[2] - target[2])
            v = _viewport_rotate_y_deg(to_cam, float(getattr(view, "yaw")))
            v = _viewport_rotate_x_deg(v, float(getattr(view, "pitch")))
            view.pan_x = -v[0]
            view.pan_y = -v[1]
            view.distance = max(0.5, float(v[2]))

        def _viewport_size_for_pick(view: object, *, safe: bool) -> tuple[int, int] | None:
            if safe and hasattr(view, "_safe_get_viewport_size"):
                try:
                    vp = getattr(view, "_safe_get_viewport_size")()
                except Exception:
                    return None
                if vp is None:
                    return None
                return (max(1, int(vp[0])), max(1, int(vp[1])))
            try:
                vp_w_px, vp_h_px = getattr(view, "get_viewport_size")()
            except Exception:
                return None
            return (max(1, int(vp_w_px)), max(1, int(vp_h_px)))

        def _viewport_screen_ray(view: object, x: int, y: int, *, safe_viewport: bool) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
            if float(x) < float(getattr(view, "sidebar_width", 0.0)):
                return None

            viewport = _viewport_size_for_pick(view, safe=safe_viewport)
            if viewport is None:
                return None
            vp_w_px, vp_h_px = viewport
            ratio = float(getattr(view, "get_pixel_ratio")())
            sidebar_px = int(float(getattr(view, "sidebar_width", 0.0)) * ratio)
            view_w_px = max(1, int(vp_w_px) - sidebar_px)
            view_h_px = max(1, int(vp_h_px))

            x_px = float(x) * ratio - float(sidebar_px)
            y_px = float(y) * ratio
            ndc_x = 2.0 * (x_px + 0.5) / float(view_w_px) - 1.0
            ndc_y = 2.0 * (y_px + 0.5) / float(view_h_px) - 1.0

            fovy = 55.0
            tan_y = math.tan(math.radians(fovy) / 2.0)
            aspect = float(view_w_px) / float(view_h_px)
            tan_x = tan_y * aspect

            if bool(getattr(view, "_ortho_enabled")):
                half_y = float(getattr(view, "distance")) * tan_y
                half_x = half_y * aspect
                x_eye = ndc_x * half_x
                y_eye = ndc_y * half_y

                v = (x_eye - float(getattr(view, "pan_x")), y_eye - float(getattr(view, "pan_y")), float(getattr(view, "distance")))
                v = _viewport_rotate_x_deg(v, -float(getattr(view, "pitch")))
                v = _viewport_rotate_y_deg(v, -float(getattr(view, "yaw")))
                orbit_target = getattr(view, "_orbit_target")
                cam_world = (orbit_target[0] + v[0], orbit_target[1] + v[1], orbit_target[2] + v[2])

                dir_cam = (0.0, 0.0, -1.0)
                dir_world = _viewport_rotate_x_deg(dir_cam, -float(getattr(view, "pitch")))
                dir_world = _viewport_rotate_y_deg(dir_world, -float(getattr(view, "yaw")))
            else:
                dir_cam = (ndc_x * tan_x, ndc_y * tan_y, -1.0)
                mag = math.sqrt(dir_cam[0] ** 2 + dir_cam[1] ** 2 + dir_cam[2] ** 2)
                if mag <= 0.0:
                    return None
                dir_cam = (dir_cam[0] / mag, dir_cam[1] / mag, dir_cam[2] / mag)
                dir_world = _viewport_rotate_x_deg(dir_cam, -float(getattr(view, "pitch")))
                dir_world = _viewport_rotate_y_deg(dir_world, -float(getattr(view, "yaw")))
                cam_world = _viewport_camera_world_position(view)

            cx, cy, cz = getattr(view, "_pivot_center")
            origin_u = (cam_world[0] + cx, cam_world[1] + cy, cam_world[2] + cz)
            return (origin_u, dir_world)

        def _viewport_pick_orbit_target(view: object, x: int, y: int, *, safe_viewport: bool) -> tuple[float, float, float] | None:
            ray = _viewport_screen_ray(view, x, y, safe_viewport=safe_viewport)
            if ray is None:
                return None
            origin_u, dir_world = ray
            hit_u = getattr(view, "_raycast_blocks_u")(origin_u, dir_world)
            env_hit_u = getattr(view, "_raycast_terrain_u")(origin_u, dir_world)
            if hit_u is None and env_hit_u is None:
                return None
            if hit_u is None:
                hit_u = env_hit_u
            elif env_hit_u is not None:
                ox, oy, oz = origin_u
                dx, dy, dz = dir_world
                t_block = (hit_u[0] - ox) * dx + (hit_u[1] - oy) * dy + (hit_u[2] - oz) * dz
                t_env = (env_hit_u[0] - ox) * dx + (env_hit_u[1] - oy) * dy + (env_hit_u[2] - oz) * dz
                if t_env >= 0.0 and t_env < t_block:
                    hit_u = env_hit_u
            cx, cy, cz = getattr(view, "_pivot_center")
            return (hit_u[0] - cx, hit_u[1] - cy, hit_u[2] - cz)

        def _hit_t_for_pick(
            p: tuple[int, int, int],
            face_n: tuple[int, int, int],
            *,
            origin_u: tuple[float, float, float],
            dir_world: tuple[float, float, float],
        ) -> float:
            ox, oy, oz = origin_u
            dx, dy, dz = dir_world
            if face_n == (0, 0, 0):
                hx = float(p[0]) + 0.5
                hy = float(p[1]) + 0.5
                hz = float(p[2]) + 0.5
                return (hx - ox) * dx + (hy - oy) * dy + (hz - oz) * dz
            if face_n[0] != 0 and abs(dx) > 1e-9:
                plane = float(p[0]) + (1.0 if face_n[0] > 0 else 0.0)
                return (plane - ox) / dx
            if face_n[1] != 0 and abs(dy) > 1e-9:
                plane = float(p[1]) + (1.0 if face_n[1] > 0 else 0.0)
                return (plane - oy) / dy
            if face_n[2] != 0 and abs(dz) > 1e-9:
                plane = float(p[2]) + (1.0 if face_n[2] > 0 else 0.0)
                return (plane - oz) / dz
            hx = float(p[0]) + 0.5
            hy = float(p[1]) + 0.5
            hz = float(p[2]) + 0.5
            return (hx - ox) * dx + (hy - oy) * dy + (hz - oz) * dz

        def _viewport_pick_block_hit(
            view: object,
            x: int,
            y: int,
            *,
            safe_viewport: bool,
        ) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
            ray = _viewport_screen_ray(view, x, y, safe_viewport=safe_viewport)
            if ray is None:
                return None
            origin_u, dir_world = ray
            hit = getattr(view, "_raycast_block_hit_u")(origin_u, dir_world)
            env_hit = getattr(view, "_raycast_terrain_u")(origin_u, dir_world)
            if hit is None and env_hit is None:
                return None
            if hit is None:
                bx = int(math.floor(float(env_hit[0]) + 1e-6))
                by = int(math.floor(float(env_hit[1]) - 1e-6))
                bz = int(math.floor(float(env_hit[2]) + 1e-6))
                return ((bx, by, bz), (0, 1, 0))
            pos, n = hit
            if env_hit is None:
                return (pos, n)

            ox, oy, oz = origin_u
            dx, dy, dz = dir_world
            t_block = _hit_t_for_pick(pos, n, origin_u=origin_u, dir_world=dir_world)
            t_env = (float(env_hit[0]) - ox) * dx + (float(env_hit[1]) - oy) * dy + (float(env_hit[2]) - oz) * dz
            if t_env >= 0.0 and (t_block < 0.0 or t_env < t_block):
                bx = int(math.floor(float(env_hit[0]) + 1e-6))
                by = int(math.floor(float(env_hit[1]) - 1e-6))
                bz = int(math.floor(float(env_hit[2]) + 1e-6))
                return ((bx, by, bz), (0, 1, 0))
            return (pos, n)

        def _viewport_zoom_to_distance_at_cursor(view: object, x: int, y: int, new_distance: float) -> None:
            old_distance = float(getattr(view, "distance"))
            if abs(new_distance - old_distance) < 1e-6:
                return

            now = time.monotonic()
            hit: tuple[float, float, float] | None
            cache_xy = getattr(view, "_zoom_pick_cache_xy", None)
            cache_t = float(getattr(view, "_zoom_pick_cache_t", 0.0))
            if cache_xy == (x, y) and (now - cache_t) < 0.20:
                hit = getattr(view, "_zoom_pick_cache_hit", None)
            else:
                hit = getattr(view, "_pick_orbit_target")(x, y)
                setattr(view, "_zoom_pick_cache_xy", (x, y))
                setattr(view, "_zoom_pick_cache_hit", hit)
                setattr(view, "_zoom_pick_cache_t", now)

            if hit is None:
                setattr(view, "distance", new_distance)
                return

            ox, oy, oz = getattr(view, "_orbit_target")
            rel = (hit[0] - ox, hit[1] - oy, hit[2] - oz)
            rel = _viewport_rotate_y_deg(rel, float(getattr(view, "yaw")))
            rel = _viewport_rotate_x_deg(rel, float(getattr(view, "pitch")))

            if bool(getattr(view, "_ortho_enabled")):
                if old_distance <= 1e-6:
                    setattr(view, "distance", new_distance)
                    return
                scale = new_distance / old_distance
                setattr(view, "distance", new_distance)
                setattr(view, "pan_x", (rel[0] + float(getattr(view, "pan_x"))) * scale - rel[0])
                setattr(view, "pan_y", (rel[1] + float(getattr(view, "pan_y"))) * scale - rel[1])
                return

            denom_old = old_distance - rel[2]
            denom_new = new_distance - rel[2]
            if abs(denom_old) < 1e-6 or abs(denom_new) < 1e-6:
                setattr(view, "distance", new_distance)
                return

            ratio_x = (rel[0] + float(getattr(view, "pan_x"))) / denom_old
            ratio_y = (rel[1] + float(getattr(view, "pan_y"))) / denom_old
            setattr(view, "distance", new_distance)
            setattr(view, "pan_x", ratio_x * denom_new - rel[0])
            setattr(view, "pan_y", ratio_y * denom_new - rel[1])

        class CompanionViewportWindow(pyglet.window.Window):
            """Model-only companion viewport with independent camera state."""

            def __init__(self, *, owner: object, viewport_id: int) -> None:
                self._owner = owner
                self._viewport_id = int(viewport_id)
                self._closing = False
                self._tick_scheduled = False

                desired_vsync = True
                try:
                    desired_vsync = bool(param_store.get_int("render.vsync"))
                except Exception:
                    desired_vsync = True

                try:
                    owner_w = int(getattr(owner, "width", 1400))
                    owner_h = int(getattr(owner, "height", 850))
                except Exception:
                    owner_w = 1400
                    owner_h = 850

                super().__init__(
                    width=max(640, owner_w - 140),
                    height=max(380, owner_h - 140),
                    resizable=True,
                    caption=(
                        "EnderTerm: second viewport"
                        if int(self._viewport_id) == 1
                        else f"EnderTerm: viewport {int(self._viewport_id) + 1}"
                    ),
                    vsync=desired_vsync,
                )

                self.sidebar_width = 0.0
                self._model_rt = ModelRenderTarget()
                self._present_cache_tex = gl.GLuint(0)
                self._present_cache_w = 0
                self._present_cache_h = 0
                self._present_cache_valid = False
                self._ender_vignette_tex = gl.GLuint(0)
                self._ender_vignette_tex_w = 0
                self._ender_vignette_tex_h = 0
                self._ender_vignette_prog = gl.GLuint(0)
                self._ender_vignette_u_tex = -1
                self._ender_vignette_u_view_px = -1
                self._ender_vignette_u_ender_rgb = -1
                self._ender_vignette_u_strength = -1
                self._ender_vignette_u_thick_px = -1
                self._ender_vignette_u_falloff_exp = -1
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
                self._fx_parity_world_draws = 0
                self._fx_parity_post_fx_draws = 0
                self._fx_parity_last_error: str | None = None

                self._fps_last_t = time.monotonic()
                self._fps_frames = 0
                self._fps_value = 0.0
                self._render_cap_hz = max(0, int(param_store.get_int("render.frame_cap_hz")))
                render_now = time.monotonic()
                self._render_cap_next_deadline_t = float(render_now)
                self._render_cap_startup_until_t = float(render_now + 0.75)
                self._render_cap_force_next = True
                self._render_cap_last_ratio = -1.0
                self._render_cap_last_view_px: tuple[int, int] = (0, 0)

                self._hover_block: tuple[int, int, int] | None = None
                self._hover_block_is_env = False
                self._mouse_x = 0
                self._mouse_y = 0

                self._zoom_pick_cache_xy: tuple[int, int] | None = None
                self._zoom_pick_cache_hit: tuple[float, float, float] | None = None
                self._zoom_pick_cache_t = 0.0

                self._scroll_pan_until_t = 0.0
                self._scroll_last_sx = 0.0
                self._scroll_last_sy = 0.0
                self._scroll_last_mode = "zoom"
                self._mac_scroll_pan_enabled = bool(getattr(owner, "_mac_scroll_pan_enabled", False))
                self._mac_pan_gesture_enabled = False

                self._cam_tween_distance: Tween | None = None
                self._cam_tween_pan_x: Tween | None = None
                self._cam_tween_pan_y: Tween | None = None
                self._cam_tween_orbit: tuple[Tween, Tween, Tween] | None = None
                self._camera_last_user_input_t = time.monotonic()

                self._init_gl_defaults()
                self._init_ender_vignette()
                self._init_ssao()
                self._sync_camera_from_owner()
                pyglet.clock.schedule_interval(self._on_tick, 1.0 / 60.0)
                self._tick_scheduled = True

            def __getattr__(self, name: str) -> object:
                return getattr(self._owner, name)

            def _init_gl_defaults(self) -> None:
                gl.glClearColor(0.0, 0.0, 0.0, 1.0)
                gl.glEnable(gl.GL_DEPTH_TEST)
                gl.glDepthFunc(gl.GL_LEQUAL)
                gl.glShadeModel(gl.GL_SMOOTH)

                gl.glEnable(gl.GL_LIGHTING)
                gl.glEnable(gl.GL_LIGHT0)
                gl.glEnable(gl.GL_COLOR_MATERIAL)
                gl.glLightfv(gl.GL_LIGHT0, gl.GL_AMBIENT, (GLfloat * 4)(0.2, 0.2, 0.2, 1.0))
                gl.glLightfv(gl.GL_LIGHT0, gl.GL_DIFFUSE, (GLfloat * 4)(0.9, 0.9, 0.9, 1.0))
                gl.glLightfv(gl.GL_LIGHT0, gl.GL_POSITION, (GLfloat * 4)(0.35, 0.9, 0.5, 0.0))

                gl.glEnable(gl.GL_BLEND)
                gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
                gl.glEnable(gl.GL_CULL_FACE)
                gl.glCullFace(gl.GL_BACK)
                gl.glFrontFace(gl.GL_CCW)

            def _delete_ender_vignette(self) -> None:
                fx_mod.delete_ender_vignette(self, gl=gl)

            def _init_ender_vignette(self) -> None:
                fx_mod.init_ender_vignette(self, gl=gl)

            def _delete_ssao(self) -> None:
                fx_mod.delete_ssao(self, gl=gl)

            def _init_ssao(self) -> None:
                fx_mod.init_ssao(self, gl=gl)

            def _ensure_ender_vignette_tex(self, w: int, h: int) -> bool:
                return bool(fx_mod.ensure_ender_vignette_tex(self, w, h, gl=gl))

            def _context_active(self) -> bool:
                if self._closing:
                    return False
                if bool(getattr(self, "has_exit", False)):
                    return False
                return getattr(self, "context", None) is not None

            def _safe_get_viewport_size(self) -> tuple[int, int] | None:
                if not self._context_active():
                    return None
                viewport_px = _render_cap_read_viewport_px(self)
                if viewport_px is None:
                    return None
                return (int(viewport_px[0]), int(viewport_px[1]))

            def _refresh_render_cap_hz(self) -> None:
                _render_cap_refresh_hz(self, param_store=param_store)

            def _mark_render_dirty(self) -> None:
                self._render_cap_force_next = _render_cap_mark_dirty_state(force_draw=bool(self._render_cap_force_next))
                if self._should_render_frame(now_s=time.monotonic()):
                    self.invalid = True

            def _should_render_frame(self, *, now_s: float) -> bool:
                self._refresh_render_cap_hz()
                if _render_cap_is_uncapped(float(self._render_cap_hz)):
                    self._render_cap_next_deadline_t = float(now_s)
                    self._render_cap_force_next = False
                    return True
                viewport_px = self._safe_get_viewport_size()
                if _render_cap_view_changed(
                    tuple(self._render_cap_last_view_px),
                    tuple(viewport_px) if viewport_px is not None else None,
                ):
                    self._render_cap_force_next = True
                if viewport_px is not None:
                    self._render_cap_last_view_px = tuple(viewport_px)
                pixel_ratio = _render_cap_read_pixel_ratio(self, default=1.0)
                if _render_cap_ratio_changed(float(self._render_cap_last_ratio), float(pixel_ratio)):
                    self._render_cap_force_next = True
                self._render_cap_last_ratio = float(pixel_ratio)

                should_draw_now, next_deadline_s = _render_cap_schedule_step(
                    now_s=float(now_s),
                    frame_cap_hz=float(self._render_cap_hz),
                    next_deadline_s=float(self._render_cap_next_deadline_t),
                    startup_until_s=float(self._render_cap_startup_until_t),
                    force_render=bool(self._render_cap_force_next),
                )
                self._render_cap_next_deadline_t = float(next_deadline_s)
                if should_draw_now:
                    self._render_cap_force_next = False
                return bool(should_draw_now)

            def _clear_owner_ref(self) -> None:
                try:
                    self._owner._on_companion_viewport_closed(int(self._viewport_id), self)
                except Exception:
                    pass

            def _unschedule_tick(self) -> None:
                if not self._tick_scheduled:
                    return
                try:
                    pyglet.clock.unschedule(self._on_tick)
                except Exception:
                    pass
                self._tick_scheduled = False

            def _sync_camera_from_owner(self) -> None:
                owner = self._owner
                self.yaw = float(getattr(owner, "yaw", 45.0))
                self.pitch = float(getattr(owner, "pitch", 25.0))
                self.distance = max(0.5, float(getattr(owner, "distance", 10.0)))
                self.pan_x = float(getattr(owner, "pan_x", 0.0))
                self.pan_y = float(getattr(owner, "pan_y", 0.0))
                self._orbit_target = tuple(getattr(owner, "_orbit_target", (0.0, 0.0, 0.0)))
                self._initial_distance = max(0.5, float(getattr(owner, "_initial_distance", 10.0)))
                self._ortho_enabled = bool(getattr(owner, "_ortho_enabled", True))

            def _cancel_camera_tween(self) -> None:
                self._cam_tween_distance = None
                self._cam_tween_pan_x = None
                self._cam_tween_pan_y = None
                self._cam_tween_orbit = None

            def _mark_camera_user_input(self) -> None:
                self._camera_last_user_input_t = time.monotonic()

            def _rotate_x_deg(self, v: tuple[float, float, float], deg: float) -> tuple[float, float, float]:
                return _viewport_rotate_x_deg(v, deg)

            def _rotate_y_deg(self, v: tuple[float, float, float], deg: float) -> tuple[float, float, float]:
                return _viewport_rotate_y_deg(v, deg)

            def _camera_world_position(self) -> tuple[float, float, float]:
                return _viewport_camera_world_position(self)

            def _camera_u_position(self) -> tuple[float, float, float]:
                return _viewport_camera_u_position(self)

            def _camera_safety_strengths(self) -> tuple[float, float]:
                return _viewport_camera_safety_strengths(self)

            def _draw_camera_safety_overlay(self, *, sidebar_px: int, view_w_px: int, view_h_px: int) -> None:
                _viewport_draw_camera_safety_overlay(self, sidebar_px=sidebar_px, view_w_px=view_w_px, view_h_px=view_h_px)

            def _set_orbit_target(self, target: tuple[float, float, float]) -> None:
                _viewport_set_orbit_target(self, target)

            def _reset_view(self) -> None:
                self._cancel_camera_tween()
                self.yaw = 45.0
                self.pitch = 25.0
                self.distance = max(0.5, float(self._initial_distance))
                self.pan_x = 0.0
                self.pan_y = 0.0
                self._orbit_target = (0.0, 0.0, 0.0)

            def _frame_view(self) -> None:
                self._cancel_camera_tween()
                self._orbit_target = (0.0, 0.0, 0.0)
                self.pan_x = 0.0
                self.pan_y = 0.0
                self.distance = max(0.5, float(self._initial_distance))

            def _camera_modifier_active(self, modifiers: int) -> bool:
                return bool(modifiers & pyglet.window.key.MOD_OPTION)

            def _pick_orbit_target(self, x: int, y: int) -> tuple[float, float, float] | None:
                return _viewport_pick_orbit_target(self, x, y, safe_viewport=True)

            def _pick_block_hit(self, x: int, y: int) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
                return _viewport_pick_block_hit(self, x, y, safe_viewport=True)

            def _zoom_to_distance_at_cursor(self, x: int, y: int, new_distance: float) -> None:
                _viewport_zoom_to_distance_at_cursor(self, x, y, new_distance)

            def _update_hover_target(self) -> None:
                if not self._context_active():
                    self._hover_block = None
                    self._hover_block_is_env = False
                    return
                if (not getattr(self._owner, "_build_enabled", False)) or (
                    not getattr(self._owner, "_build_hover_pick_enabled", True)
                ):
                    self._hover_block = None
                    self._hover_block_is_env = False
                    return
                hit = self._pick_block_hit(int(self._mouse_x), int(self._mouse_y))
                if hit is None:
                    self._hover_block = None
                    self._hover_block_is_env = False
                    return
                pos, _n = hit
                self._hover_block = pos
                self._hover_block_is_env = False

            def _on_tick(self, _dt: float) -> None:
                if not self._context_active():
                    self._hover_block = None
                    self._hover_block_is_env = False
                    self._unschedule_tick()
                    self._clear_owner_ref()
                    return
                self._initial_distance = max(0.5, float(getattr(self._owner, "_initial_distance", self._initial_distance)))
                self._mac_scroll_pan_enabled = bool(getattr(self._owner, "_mac_scroll_pan_enabled", False))
                self._update_hover_target()
                self.invalid = True

            def on_resize(self, width: int, height: int) -> None:
                viewport = self._safe_get_viewport_size()
                if viewport is None:
                    return
                vp_w, vp_h = viewport
                gl.glViewport(0, 0, max(1, int(vp_w)), max(1, int(vp_h)))
                self._mark_render_dirty()

            def on_mouse_motion(self, x: int, y: int, dx: int, dy: int) -> None:
                self._mouse_x = int(x)
                self._mouse_y = int(y)

            def on_mouse_drag(self, x: int, y: int, dx: int, dy: int, buttons: int, modifiers: int) -> None:
                self._mouse_x = int(x)
                self._mouse_y = int(y)
                if not self._camera_modifier_active(modifiers):
                    return
                self._cancel_camera_tween()
                if buttons & pyglet.window.mouse.LEFT:
                    self.yaw += dx * 0.35
                    self.pitch -= dy * 0.35
                    self.pitch = max(-89.0, min(89.0, self.pitch))
                    self._mark_camera_user_input()
                elif buttons & pyglet.window.mouse.MIDDLE:
                    fov_rad = math.radians(55.0)
                    units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                    self.pan_x += dx * units_per_point
                    self.pan_y += dy * units_per_point
                    self._mark_camera_user_input()

            def on_mouse_scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
                self._cancel_camera_tween()
                sx = float(scroll_x)
                sy = float(scroll_y)
                self._scroll_last_sx = sx
                self._scroll_last_sy = sy
                now = time.monotonic()

                looks_trackpad = (
                    (abs(sx) > 1e-6)
                    or (abs(sy) < 1.0)
                    or (not sx.is_integer())
                    or (not sy.is_integer())
                )

                if self._mac_pan_gesture_enabled and looks_trackpad:
                    self._scroll_last_mode = "ignored"
                    return

                if self._mac_scroll_pan_enabled:
                    if now < self._scroll_pan_until_t:
                        self._scroll_pan_until_t = now + 0.25
                        self._scroll_last_mode = "pan"
                        fov_rad = math.radians(55.0)
                        units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                        self.pan_x += sx * units_per_point
                        self.pan_y += sy * units_per_point
                        self._mark_camera_user_input()
                        return

                    if looks_trackpad:
                        self._scroll_pan_until_t = now + 0.25
                        self._scroll_last_mode = "pan"
                        fov_rad = math.radians(55.0)
                        units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                        self.pan_x += sx * units_per_point
                        self.pan_y += sy * units_per_point
                        self._mark_camera_user_input()
                        return

                self._scroll_last_mode = "zoom"
                old_distance = float(self.distance)
                factor = 0.9**sy
                new_distance = max(0.5, old_distance * factor)
                self._zoom_to_distance_at_cursor(x, y, new_distance)
                self._mark_camera_user_input()

            def on_mouse_press(self, x: int, y: int, button: int, modifiers: int) -> None:
                self._mouse_x = int(x)
                self._mouse_y = int(y)

                owner = self._owner
                if (
                    bool(getattr(owner, "_build_enabled", False))
                    and (not bool(getattr(owner, "_terminal_busy", lambda: False)()))
                    and (not self._camera_modifier_active(modifiers))
                    and button in {pyglet.window.mouse.LEFT, pyglet.window.mouse.RIGHT, pyglet.window.mouse.MIDDLE}
                ):
                    hit = self._pick_block_hit(x, y)
                    if hit is None:
                        return
                    pos, n = hit
                    if button == pyglet.window.mouse.MIDDLE:
                        owner._build_pick_block(pos)
                        owner.invalid = True
                        return
                    if button == pyglet.window.mouse.LEFT:
                        owner._build_remove_block(pos)
                        owner.invalid = True
                        return
                    if button == pyglet.window.mouse.RIGHT and n != (0, 0, 0):
                        place_pos = (pos[0] + n[0], pos[1] + n[1], pos[2] + n[2])
                        owner._build_place_block(place_pos, face_n=n)
                        owner.invalid = True
                        return
                    return

                if button == pyglet.window.mouse.LEFT:
                    if not self._camera_modifier_active(modifiers):
                        return
                    self._cancel_camera_tween()
                    hit = self._pick_orbit_target(x, y)
                    self._set_orbit_target(hit if hit is not None else (0.0, 0.0, 0.0))
                    self._mark_camera_user_input()

            def on_key_press(self, symbol: int, modifiers: int) -> None:
                cmd_mod = getattr(pyglet.window.key, "MOD_COMMAND", 0) | getattr(pyglet.window.key, "MOD_ACCEL", 0)
                if symbol in {pyglet.window.key.ESCAPE, pyglet.window.key.Q}:
                    self.close()
                    return
                if symbol == pyglet.window.key.C:
                    self.close()
                    return
                if (modifiers & cmd_mod) and symbol == pyglet.window.key.W:
                    self.close()
                    return
                if symbol == pyglet.window.key.R:
                    self._reset_view()
                    self._mark_camera_user_input()
                    return
                if symbol == pyglet.window.key.F:
                    self._frame_view()
                    self._mark_camera_user_input()
                    return
                if symbol == pyglet.window.key.O:
                    self._ortho_enabled = not self._ortho_enabled
                    self._mark_camera_user_input()
                    return
                try:
                    self._owner.on_key_press(symbol, modifiers)
                except Exception:
                    pass

            def on_key_release(self, symbol: int, modifiers: int) -> None:
                try:
                    self._owner.on_key_release(symbol, modifiers)
                except Exception:
                    pass

            def on_activate(self) -> None:
                # Activation-time keyboard priming is intentionally disabled.
                self._mark_render_dirty()

            def on_deactivate(self) -> None:
                from enderterm.termui import route_window_focus_keyboard

                route_window_focus_keyboard(window=self, activated=False)

            def on_text(self, text: str) -> None:
                try:
                    self._owner.on_text(text)
                except Exception:
                    pass

            def on_file_drop(self, x: int, y: int, paths: list[str]) -> None:
                try:
                    self._owner.on_file_drop(x, y, paths)
                except Exception:
                    pass

            def on_draw(self) -> None:
                if not self._context_active():
                    return
                if not _draw_guard_render_cap(self, now_s=time.monotonic()):
                    return
                try:
                    r, g, b = self._env_clear_rgb()
                    gl.glClearColor(float(r), float(g), float(b), 1.0)
                except Exception:
                    gl.glClearColor(0.0, 0.0, 0.0, 1.0)
                self.clear()
                gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
                viewport = self._safe_get_viewport_size()
                if viewport is None:
                    return
                vp_w, vp_h = viewport
                try:
                    fx_mod.draw_world(self, int(vp_w), int(vp_h), gl=gl, param_store=param_store)
                    self._fx_parity_world_draws += 1
                    fx_mod.draw_post_fx_overlay(self, gl=gl, param_store=param_store)
                    fx_mod.apply_copy_glitch(self, int(vp_w), int(vp_h), gl=gl, param_store=param_store)
                    fx_mod.apply_ender_vignette(self, int(vp_w), int(vp_h), gl=gl, param_store=param_store)
                    self._fx_parity_post_fx_draws += 1
                    self._fx_parity_last_error = None
                except Exception as e:
                    self._fx_parity_last_error = f"{type(e).__name__}: {e}"
                    raise

            def _draw_world_3d(self, *, aspect: float) -> None:
                render_world_mod.draw_world_3d(
                    self,
                    aspect=aspect,
                    gl=gl,
                    param_store=param_store,
                    gluPerspective=gluPerspective,
                    pyglet_mod=pyglet,
                    group_cache=group_cache,
                    no_tex_group=no_tex_group,
                )

            def _cleanup_gl_resources(self) -> None:
                try:
                    self._model_rt.delete()
                except Exception:
                    pass
                try:
                    # Use direct instance state so __getattr__ cannot resolve to owner
                    # window resources during companion cleanup.
                    present_cache_tex = _instance_dict_get(self, "_present_cache_tex")
                    if present_cache_tex is not None and int(getattr(present_cache_tex, "value", 0)):
                        gl.glDeleteTextures(1, ctypes.byref(present_cache_tex))
                except Exception:
                    pass
                try:
                    self._delete_ender_vignette()
                except Exception:
                    pass
                try:
                    self._delete_ssao()
                except Exception:
                    pass

            def on_close(self) -> None:
                self._closing = True
                self._unschedule_tick()
                _safe_window_gl_cleanup(
                    make_current=getattr(self, "switch_to", None),
                    cleanup=self._cleanup_gl_resources,
                )
                owner_switch_to = getattr(self._owner, "switch_to", None)
                try:
                    self._clear_owner_ref()
                    super().on_close()
                finally:
                    if callable(owner_switch_to):
                        try:
                            owner_switch_to()
                        except Exception:
                            pass

        class ViewerWindow(pyglet.window.Window):
            def __init__(self) -> None:
                desired_vsync = True
                try:
                    desired_vsync = bool(param_store.get_int("render.vsync"))
                except Exception:
                    desired_vsync = True
                super().__init__(
                    width=1400,
                    height=850,
                    resizable=True,
                    caption="EnderTerm: datapack-view",
                    vsync=desired_vsync,
                )

                self._init_vsync_and_perf_state(
                    desired_vsync=bool(desired_vsync),
                    perf_enabled=bool(perf_enabled),
                    perf_out_path=Path(perf_out_path),
                    perf_s=float(perf_s),
                )
                self._init_structure_and_camera_state(
                    export_dir=export_dir,
                    cinematic_start=bool(cinematic_start),
                    param_store=param_store,
                )
                self._init_sidebar_browser_state(
                    start_browser_mode=str(start_browser_mode),
                    labels=labels,
                    pool_labels=pool_labels,
                    start_load_mode=str(start_load_mode),
                    start_load_idx=int(start_load_idx),
                    datapack_path=datapack_path,
                )
                self._init_build_rez_and_scroll_state(
                    param_store=param_store,
                    shared_term_scrollbar_capture=shared_term_scrollbar_capture,
                )
                self._init_render_environment_state(param_store=param_store, env_decor_cfg=env_decor_cfg)
                self._init_macos_focus_and_font_state()
                self._init_ui_palette_state()

                self.sidebar_shape_batch = pyglet.graphics.Batch()
                self.sidebar_text_batch = pyglet.graphics.Batch()
                self.overlay_shape_batch = pyglet.graphics.Batch()
                self.overlay_text_batch = pyglet.graphics.Batch()
                self.test_banner_shape_batch = pyglet.graphics.Batch()
                self.test_banner_text_batch = pyglet.graphics.Batch()

                self._test_banner_text = str(test_banner_text)
                self._test_banner_build = str(test_build)
                self._test_banner_last_layout: tuple[int, int, float, str, str] = (-1, -1, -1.0, "", "")
                self.test_banner_bg = pyglet.shapes.Rectangle(
                    0,
                    0,
                    1,
                    1,
                    color=(0, 0, 0),
                    batch=self.test_banner_shape_batch,
                    group=ui_group_help_bg,
                )
                self.test_banner_bg.opacity = 210
                self.test_banner_bg.visible = bool(self._test_banner_text)
                self.test_banner_label = pyglet.text.Label(
                    "",
                    x=0,
                    y=0,
                    anchor_x="left",
                    anchor_y="top",
                    font_name=self.ui_font_name,
                    font_size=int(round(14.0 * float(self._ui_font_scale))),
                    color=(235, 235, 245, 255),
                    multiline=True,
                    width=100,
                    batch=self.test_banner_text_batch,
                    group=ui_group_help_text,
                )
                self.test_banner_label.visible = bool(self._test_banner_text)

                # Terminal-style sidebar renderer (glyph grid).
                # We keep the old pyglet sidebar widgets around for now (small,
                # reversible migration), but stop drawing them once the terminal
                # renderer is active.
                from enderterm.termui import TerminalRenderer

                self._sidebar_term_renderer = TerminalRenderer()
                self._sidebar_term_font = None
                self._sidebar_term_surface = None
                self._sidebar_term_scale_last = -1.0
                self._sidebar_term_ratio_last = -1.0

                # Jar alert (TermUI overlay panel over the 3D view).
                self._jar_alert_text = str(startup_jar_banner_text)
                self._jar_alert_kind = str(startup_jar_banner_kind)
                self._jar_alert_dismissed = False
                self._jar_term_renderer = TerminalRenderer()
                self._jar_term_surface = None
                self._jar_term_panel_rect: tuple[float, float, float, float] | None = None
                self._jar_term_dismiss_rect: tuple[float, float, float, float] | None = None

                # Rez overlay uses TermUI too (floating panel over the model view).
                self._rez_termui_enabled = True
                self._rez_term_renderer = TerminalRenderer()
                self._rez_term_surface = None
                self._rez_term_cancel_rect: tuple[float, float, float, float] | None = None

                self.sidebar_bg = pyglet.shapes.Rectangle(
                    0,
                    0,
                    self.sidebar_width,
                    self.height,
                    color=(16, 16, 18),
                    batch=self.sidebar_shape_batch,
                    group=ui_group_bg,
                )
                self.sidebar_bg.opacity = 235
                self.selection_bg = pyglet.shapes.Rectangle(
                    0,
                    0,
                    self.sidebar_width,
                    self.line_height,
                    color=self._ui_purple,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_sel,
                )
                self.selection_bg.opacity = 130
                self.selection_glow = pyglet.shapes.Rectangle(
                    0,
                    0,
                    self.sidebar_width,
                    self.line_height,
                    color=self._ui_purple,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.selection_glow.opacity = 65
                self.selection_glow.visible = False
                self.selection_shine = pyglet.shapes.Rectangle(
                    0,
                    0,
                    90,
                    self.line_height,
                    color=self._ui_amber,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_sel,
                )
                self.selection_shine.opacity = 35
                self.selection_shine.visible = False
                self.sidebar_divider = pyglet.shapes.Rectangle(
                    self.sidebar_width - 1,
                    0,
                    1,
                    self.height,
                    color=(48, 48, 54),
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.sidebar_divider.opacity = 255
                self.log_bg = pyglet.shapes.Rectangle(
                    0,
                    0,
                    self.sidebar_width,
                    self.log_panel_h,
                    color=(24, 24, 28),
                    batch=self.sidebar_shape_batch,
                    group=ui_group_bg,
                )
                self.log_bg.opacity = 230
                self.list_bg = pyglet.shapes.Rectangle(
                    0,
                    self.log_panel_h,
                    self.sidebar_width,
                    max(1, self.height - self.header_h - self.log_panel_h),
                    color=(20, 20, 23),
                    batch=self.sidebar_shape_batch,
                    group=ui_group_bg,
                )
                self.list_bg.opacity = 210

                frame_color = (62, 62, 74)
                shadow_color = (0, 0, 0)
                self.log_frame_top = pyglet.shapes.Rectangle(
                    0,
                    self.log_panel_h - 1,
                    self.sidebar_width,
                    1,
                    color=frame_color,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.log_frame_bottom = pyglet.shapes.Rectangle(
                    0,
                    0,
                    self.sidebar_width,
                    1,
                    color=frame_color,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.log_frame_left = pyglet.shapes.Rectangle(
                    0,
                    0,
                    1,
                    self.log_panel_h,
                    color=frame_color,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.log_frame_right = pyglet.shapes.Rectangle(
                    self.sidebar_width - 1,
                    0,
                    1,
                    self.log_panel_h,
                    color=frame_color,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.list_frame_top = pyglet.shapes.Rectangle(
                    0,
                    self.height - self.header_h - 1,
                    self.sidebar_width,
                    1,
                    color=frame_color,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.list_frame_bottom = pyglet.shapes.Rectangle(
                    0,
                    self.log_panel_h,
                    self.sidebar_width,
                    1,
                    color=frame_color,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.list_frame_left = pyglet.shapes.Rectangle(
                    0,
                    self.log_panel_h,
                    1,
                    max(1, self.height - self.header_h - self.log_panel_h),
                    color=frame_color,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.list_frame_right = pyglet.shapes.Rectangle(
                    self.sidebar_width - 1,
                    self.log_panel_h,
                    1,
                    max(1, self.height - self.header_h - self.log_panel_h),
                    color=frame_color,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.list_shadow = pyglet.shapes.Rectangle(
                    0,
                    self.log_panel_h - 3,
                    self.sidebar_width,
                    3,
                    color=shadow_color,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.list_shadow.opacity = 45

                self.search_bg = pyglet.shapes.Rectangle(
                    12,
                    self.height - self.header_h - 30,
                    max(1, self.sidebar_width - 24),
                    self._ui_i(22.0),
                    color=(34, 24, 44),
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.search_bg.opacity = 235
                self.search_bg.visible = False
                self.search_glow = pyglet.shapes.Rectangle(
                    self.search_bg.x - 2,
                    self.search_bg.y - 2,
                    self.search_bg.width + 4,
                    self.search_bg.height + 4,
                    color=self._ui_purple,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_sel,
                )
                self.search_glow.opacity = 0
                self.search_glow.visible = False
                self.search_label = pyglet.text.Label(
                    "",
                    x=self.search_bg.x + 8,
                    y=self.search_bg.y + self.search_bg.height / 2.0,
                    anchor_x="left",
                    anchor_y="center",
                    font_size=self._ui_i(12.0),
                    font_name=self.ui_font_name,
                    color=(*self._ui_purple, 0),
                    batch=self.sidebar_text_batch,
                    group=ui_group_text,
                )
                self.search_count_label = pyglet.text.Label(
                    "",
                    x=self.search_bg.x + self.search_bg.width - 8,
                    y=self.search_bg.y + self.search_bg.height / 2.0,
                    anchor_x="right",
                    anchor_y="center",
                    font_size=self._ui_i(10.0),
                    font_name=self.ui_font_name,
                    color=(*self._ui_purple, 0),
                    batch=self.sidebar_text_batch,
                    group=ui_group_text,
                )
                self.search_cancel_bg = pyglet.shapes.Rectangle(
                    0,
                    0,
                    18,
                    18,
                    color=(80, 40, 120),
                    batch=self.sidebar_shape_batch,
                    group=ui_group_sel,
                )
                self.search_cancel_bg.opacity = 0
                self.search_cancel_bg.visible = False
                self.search_cancel_glows: list[pyglet.shapes.Rectangle] = []
                for _ in range(10):
                    r = pyglet.shapes.Rectangle(
                        self.search_cancel_bg.x - 6,
                        self.search_cancel_bg.y - 6,
                        self.search_cancel_bg.width + 12,
                        self.search_cancel_bg.height + 12,
                        color=self._ui_purple,
                        batch=self.sidebar_shape_batch,
                        group=ui_group_glow,
                    )
                    r.opacity = 0
                    r.visible = False
                    self.search_cancel_glows.append(r)
                self.search_cancel_label_o_layers: list[tuple[pyglet.text.Label, int, float]] = []
                self.search_cancel_label_x_layers: list[tuple[pyglet.text.Label, int, float]] = []
                for dx_unit, a_mul in ((-1, 0.60), (0, 1.00), (1, 0.60)):
                    lo = pyglet.text.Label(
                        "O",
                        x=0,
                        y=0,
                        anchor_x="center",
                        anchor_y="center",
                        font_size=self._ui_i(12.0),
                        font_name=self.ui_font_name,
                        color=(*self._ui_purple_hi, 0),
                        batch=self.sidebar_text_batch,
                        group=ui_group_glow,
                    )
                    lx = pyglet.text.Label(
                        chr(0xE000 + ord("X")),
                        x=0,
                        y=0,
                        anchor_x="center",
                        anchor_y="center",
                        font_size=self._ui_i(12.0),
                        font_name=self.ui_font_name,
                        color=(*self._ui_purple_hot, 0),
                        batch=self.sidebar_text_batch,
                        group=ui_group_glow,
                    )
                    self.search_cancel_label_o_layers.append((lo, dx_unit, a_mul))
                    self.search_cancel_label_x_layers.append((lx, dx_unit, a_mul))
                self.search_cancel_label_o = self.search_cancel_label_o_layers[1][0]
                self.search_cancel_label_x = self.search_cancel_label_x_layers[1][0]

                for r in (
                    self.log_frame_top,
                    self.log_frame_bottom,
                    self.log_frame_left,
                    self.log_frame_right,
                    self.list_frame_top,
                    self.list_frame_bottom,
                    self.list_frame_left,
                    self.list_frame_right,
                ):
                    r.opacity = 220
                self.scroll_track = pyglet.shapes.Rectangle(
                    0,
                    0,
                    6,
                    10,
                    color=(200, 200, 210),
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.scroll_track.opacity = 55
                self.scroll_thumb_glow = pyglet.shapes.Rectangle(
                    0,
                    0,
                    6,
                    10,
                    color=self._ui_purple,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.scroll_thumb_glow.opacity = 55
                self.scroll_thumb_glow.visible = False
                self.scroll_thumb = pyglet.shapes.Rectangle(
                    0,
                    0,
                    6,
                    10,
                    color=self._ui_purple,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.scroll_thumb.opacity = 150
                self.scroll_thumb_shine = pyglet.shapes.Rectangle(
                    0,
                    0,
                    6,
                    3,
                    color=self._ui_amber,
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.scroll_thumb_shine.opacity = 0
                self.scroll_thumb_shine.visible = False

                self.title = pyglet.text.Label(
                    f"{datapack_path.name}  ({len(labels)} structures)",
                    x=12,
                    y=self.height - 12,
                    anchor_x="left",
                    anchor_y="top",
                    font_size=self._ui_i(13.0),
                    font_name=self.ui_font_name,
                    color=(235, 235, 242, 255),
                    batch=self.sidebar_text_batch,
                    group=ui_group_text,
                )
                self.subtitle = pyglet.text.Label(
                    "",
                    x=12,
                    y=self.height - 32,
                    anchor_x="left",
                    anchor_y="top",
                    font_size=self._ui_i(11.0),
                    font_name=self.ui_font_name,
                    color=(185, 185, 200, 255),
                    batch=self.sidebar_text_batch,
                    group=ui_group_text,
                )
                self.log_title = pyglet.text.Label(
                    "Rez Log",
                    x=12,
                    y=self.log_panel_h - 10,
                    anchor_x="left",
                    anchor_y="top",
                    font_size=self._ui_i(10.0),
                    font_name=self.ui_font_name,
                    color=(150, 150, 160, 255),
                    batch=self.sidebar_text_batch,
                    group=ui_group_text,
                )
                self.log_toggle_bg = pyglet.shapes.Rectangle(
                    self.sidebar_width - 30,
                    self.log_panel_h - 28,
                    18,
                    18,
                    color=(40, 40, 48),
                    batch=self.sidebar_shape_batch,
                    group=ui_group_frame,
                )
                self.log_toggle_bg.opacity = 220
                self.log_toggle_label = pyglet.text.Label(
                    "▸" if self._log_collapsed else "▾",
                    x=self.log_toggle_bg.x + self.log_toggle_bg.width / 2.0,
                    y=self.log_toggle_bg.y + self.log_toggle_bg.height / 2.0,
                    anchor_x="center",
                    anchor_y="center",
                    font_size=self._ui_i(14.0),
                    font_name=self.ui_font_name,
                    color=(200, 200, 210, 255),
                    batch=self.sidebar_text_batch,
                    group=ui_group_text,
                )
                self._effects: list[fx_mod.FlashBox] = []
                self._effects_enabled = bool(params_mod.effects_master_enabled(param_store))
                self._jigsaw_seed_base: int | None = jigsaw_seed_base
                self._jigsaw_seed_tape: list[int] = list(jigsaw_seed_tape)
                self._jigsaw_reroll_counts: list[int] = []
                self.rez_scrim = pyglet.shapes.Rectangle(
                    self.sidebar_width,
                    0,
                    max(1, self.width - self.sidebar_width),
                    self.height,
                    color=(120, 120, 130),
                    batch=self.overlay_shape_batch,
                    group=ui_group_bg,
                )
                # Disabled: the rez bar / messages provide enough feedback, and the
                # gray scrim fights the app aesthetic.
                self.rez_scrim.opacity = 0
                self.rez_scrim.visible = False
                self.rez_label = pyglet.text.Label(
                    "",
                    x=int(self.sidebar_width) + max(1, int(self.width - self.sidebar_width)) // 2,
                    y=self.height / 2.0 + 28,
                    anchor_x="left",
                    anchor_y="top",
                    font_size=self._ui_i(12.0),
                    font_name=self.ui_font_name,
                    color=(235, 235, 245, 255),
                    width=1,
                    multiline=True,
                    align="left",
                    batch=self.overlay_text_batch,
                    group=ui_group_text,
                )
                self.rez_bar_bg = pyglet.shapes.Rectangle(
                    12,
                    self.height - self.header_h + 10,
                    max(1, self.sidebar_width - 24),
                    10,
                    color=(45, 45, 55),
                    batch=self.overlay_shape_batch,
                    group=ui_group_frame,
                )
                self.rez_bar_bg.opacity = 160
                self.rez_bar_fill = pyglet.shapes.Rectangle(
                    12,
                    self.height - self.header_h + 10,
                    1,
                    10,
                    color=self._ui_purple,
                    batch=self.overlay_shape_batch,
                    group=ui_group_sel,
                )
                self.rez_bar_fill.opacity = 215
                self.rez_bar_fill_hi = pyglet.shapes.Rectangle(
                    12,
                    self.height - self.header_h + 15,
                    1,
                    5,
                    color=self._ui_purple_hi,
                    batch=self.overlay_shape_batch,
                    group=ui_group_sel,
                )
                self.rez_bar_fill_hi.opacity = 90
                self.rez_bar_shine = pyglet.shapes.Rectangle(
                    12,
                    self.height - self.header_h + 10,
                    60,
                    10,
                    color=(255, 255, 255),
                    batch=self.overlay_shape_batch,
                    group=ui_group_sel,
                )
                self.rez_bar_shine.opacity = 35
                self.rez_bar_glitch: list[pyglet.shapes.Rectangle] = []
                for _ in range(10):
                    r = pyglet.shapes.Rectangle(
                        0,
                        0,
                        1,
                        1,
                        color=self._ui_purple,
                        batch=self.overlay_shape_batch,
                        group=ui_group_sel,
                    )
                    r.opacity = 0
                    r.visible = False
                    self.rez_bar_glitch.append(r)
                self.rez_cancel_glows: list[pyglet.shapes.Rectangle] = []
                for _ in range(10):
                    r = pyglet.shapes.Rectangle(
                        0,
                        0,
                        32,
                        32,
                        color=self._ui_purple,
                        batch=self.overlay_shape_batch,
                        group=ui_group_glow,
                    )
                    r.opacity = 0
                    r.visible = False
                    self.rez_cancel_glows.append(r)
                self.rez_cancel_bg = pyglet.shapes.Rectangle(
                    0,
                    0,
                    22,
                    22,
                    color=(80, 40, 120),
                    batch=self.overlay_shape_batch,
                    group=ui_group_sel,
                )
                self.rez_cancel_bg.opacity = 175
                self.rez_cancel_label_o_layers: list[tuple[pyglet.text.Label, int, float]] = []
                self.rez_cancel_label_x_layers: list[tuple[pyglet.text.Label, int, float]] = []
                for dx_unit, a_mul in ((-1, 0.60), (0, 1.00), (1, 0.60)):
                    lo = pyglet.text.Label(
                        "O",
                        x=0,
                        y=0,
                        anchor_x="center",
                        anchor_y="center",
                        font_size=self._ui_i(20.0),
                        font_name=self.ui_font_name,
                        color=(*self._ui_purple_hi, 0),
                        batch=self.overlay_text_batch,
                        group=ui_group_glow,
                    )
                    lx = pyglet.text.Label(
                        chr(0xE000 + ord("X")),
                        x=0,
                        y=0,
                        anchor_x="center",
                        anchor_y="center",
                        font_size=self._ui_i(20.0),
                        font_name=self.ui_font_name,
                        color=(*self._ui_purple_hot, 0),
                        batch=self.overlay_text_batch,
                        group=ui_group_glow,
                    )
                    self.rez_cancel_label_o_layers.append((lo, dx_unit, a_mul))
                    self.rez_cancel_label_x_layers.append((lx, dx_unit, a_mul))
                self.rez_cancel_label_o = self.rez_cancel_label_o_layers[1][0]
                self.rez_cancel_label_x = self.rez_cancel_label_x_layers[1][0]

                self.rez_label.color = (235, 235, 245, 0)
                self.rez_cancel_label_o.color = (*self._ui_purple_hi, 0)
                self.rez_cancel_label_x.color = (*self._ui_purple_hot, 0)
                for shape in (
                    self.rez_bar_bg,
                    self.rez_bar_fill,
                    self.rez_bar_fill_hi,
                    self.rez_bar_shine,
                    *self.rez_bar_glitch,
                    self.rez_cancel_bg,
                ):
                    shape.visible = False

                # Hotbar (0-9) — Minecraft-ish quick block slots.
                self.hotbar_panel_bg = pyglet.shapes.Rectangle(
                    0,
                    0,
                    10,
                    10,
                    color=(12, 12, 16),
                    batch=self.overlay_shape_batch,
                    group=ui_group_hotbar_bg,
                )
                self.hotbar_panel_bg.opacity = 160
                self.hotbar_panel_bg.visible = False
                self.hotbar_slot_borders: list[pyglet.shapes.Rectangle] = []
                self.hotbar_slot_fills: list[pyglet.shapes.Rectangle] = []
                self.hotbar_slot_labels: list[pyglet.text.Label] = []
                self.hotbar_slot_numbers: list[pyglet.text.Label] = []
                self.hotbar_slot_icons: list[pyglet.sprite.Sprite] = []
                self._hotbar_slot_icon_keys: list[str] = []
                self._hotbar_has_animated_icons = False
                try:
                    blank = pyglet.image.SolidColorImagePattern((0, 0, 0, 0)).create_image(1, 1).get_texture()
                    missing = (
                        pyglet.image.SolidColorImagePattern((60, 48, 74, 255)).create_image(2, 2).get_texture()
                    )
                    blank.mag_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
                    blank.min_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
                    missing.mag_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
                    missing.min_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
                    self._hotbar_blank_tex = blank
                    self._hotbar_missing_tex = missing
                except Exception:
                    self._hotbar_blank_tex = None
                    self._hotbar_missing_tex = None

                placeholder_tex = self._hotbar_blank_tex
                if placeholder_tex is None:
                    try:
                        placeholder_tex = pyglet.image.Texture.create(1, 1)
                    except Exception:
                        placeholder_tex = pyglet.image.SolidColorImagePattern((0, 0, 0, 0)).create_image(1, 1).get_texture()
                for i in range(10):
                    border = pyglet.shapes.Rectangle(
                        0,
                        0,
                        10,
                        10,
                        color=(60, 60, 72),
                        batch=self.overlay_shape_batch,
                        group=ui_group_hotbar_frame,
                    )
                    border.opacity = 215
                    border.visible = False
                    fill = pyglet.shapes.Rectangle(
                        0,
                        0,
                        8,
                        8,
                        color=(16, 16, 20),
                        batch=self.overlay_shape_batch,
                        group=ui_group_hotbar_bg,
                    )
                    fill.opacity = 190
                    fill.visible = False
                    label = pyglet.text.Label(
                        "",
                        x=0,
                        y=0,
                        anchor_x="center",
                        anchor_y="center",
                        font_size=self._ui_i(13.0),
                        font_name=self.ui_font_name,
                        color=(235, 235, 245, 255),
                        batch=self.overlay_text_batch,
                        group=ui_group_hotbar_text,
                    )
                    label.visible = False
                    num = pyglet.text.Label(
                        str((i + 1) if i < 9 else 0),
                        x=0,
                        y=0,
                        anchor_x="left",
                        anchor_y="top",
                        font_size=self._ui_i(9.0),
                        font_name=self.ui_font_name,
                        color=(160, 160, 170, 255),
                        batch=self.overlay_text_batch,
                        group=ui_group_hotbar_text,
                    )
                    num.visible = False
                    icon = pyglet.sprite.Sprite(
                        placeholder_tex,
                        x=0,
                        y=0,
                        batch=self.overlay_text_batch,
                        group=ui_group_hotbar_icon,
                    )
                    icon.visible = False
                    self.hotbar_slot_borders.append(border)
                    self.hotbar_slot_fills.append(fill)
                    self.hotbar_slot_labels.append(label)
                    self.hotbar_slot_numbers.append(num)
                    self.hotbar_slot_icons.append(icon)
                    self._hotbar_slot_icon_keys.append("")

                # Debug panel (TermUI, toggle with D). Legacy widgets retained but hidden.
                self.help_bg = pyglet.shapes.Rectangle(
                    0,
                    0,
                    10,
                    10,
                    color=(18, 10, 28),
                    batch=self.overlay_shape_batch,
                    group=ui_group_help_bg,
                )
                self.help_bg.opacity = 210
                self.help_frame_top = pyglet.shapes.Rectangle(
                    0,
                    0,
                    10,
                    1,
                    color=self._ui_purple,
                    batch=self.overlay_shape_batch,
                    group=ui_group_help_frame,
                )
                self.help_frame_bottom = pyglet.shapes.Rectangle(
                    0,
                    0,
                    10,
                    1,
                    color=self._ui_purple,
                    batch=self.overlay_shape_batch,
                    group=ui_group_help_frame,
                )
                self.help_frame_left = pyglet.shapes.Rectangle(
                    0,
                    0,
                    1,
                    10,
                    color=self._ui_purple,
                    batch=self.overlay_shape_batch,
                    group=ui_group_help_frame,
                )
                self.help_frame_right = pyglet.shapes.Rectangle(
                    0,
                    0,
                    1,
                    10,
                    color=self._ui_purple,
                    batch=self.overlay_shape_batch,
                    group=ui_group_help_frame,
                )
                for r in (self.help_frame_top, self.help_frame_bottom, self.help_frame_left, self.help_frame_right):
                    r.opacity = 160

                self.help_label = pyglet.text.Label(
                    "",
                    x=0,
                    y=0,
                    anchor_x="left",
                    anchor_y="top",
                    font_size=self._ui_i(12.0),
                    font_name=self.ui_font_name,
                    color=(*self._ui_pink, 220),
                    width=320,
                    multiline=True,
                    align="left",
                    batch=self.overlay_text_batch,
                    group=ui_group_help_text,
                )

                for w in (
                    self.help_bg,
                    self.help_frame_top,
                    self.help_frame_bottom,
                    self.help_frame_left,
                    self.help_frame_right,
                    self.help_label,
                ):
                    w.visible = False

                # Error overlay (shown in model viewport when load/render fails).
                self._viewer_error_kind = ""
                self._viewer_error_text = ""
                self._viewer_error_last_detail = ""
                self._viewer_error_last_t = 0.0
                self._viewer_error_retry_after_t = 0.0
                self._fx_error_pulse_start_t = 0.0
                self._fx_error_pulse_until_t = 0.0

                self.error_bg = pyglet.shapes.Rectangle(
                    self.sidebar_width,
                    0,
                    max(1, self.width - self.sidebar_width),
                    10,
                    color=(18, 10, 28),
                    batch=self.overlay_shape_batch,
                    group=ui_group_error_bg,
                )
                self.error_bg.opacity = 215
                self.error_frame_top = pyglet.shapes.Rectangle(
                    0,
                    0,
                    10,
                    1,
                    color=self._ui_pink,
                    batch=self.overlay_shape_batch,
                    group=ui_group_error_frame,
                )
                self.error_frame_bottom = pyglet.shapes.Rectangle(
                    0,
                    0,
                    10,
                    1,
                    color=self._ui_pink,
                    batch=self.overlay_shape_batch,
                    group=ui_group_error_frame,
                )
                self.error_frame_left = pyglet.shapes.Rectangle(
                    0,
                    0,
                    1,
                    10,
                    color=self._ui_pink,
                    batch=self.overlay_shape_batch,
                    group=ui_group_error_frame,
                )
                self.error_frame_right = pyglet.shapes.Rectangle(
                    0,
                    0,
                    1,
                    10,
                    color=self._ui_pink,
                    batch=self.overlay_shape_batch,
                    group=ui_group_error_frame,
                )
                for r in (self.error_frame_top, self.error_frame_bottom, self.error_frame_left, self.error_frame_right):
                    r.opacity = 180

                self.error_label = pyglet.text.Label(
                    "",
                    x=0,
                    y=0,
                    anchor_x="left",
                    anchor_y="top",
                    font_size=self._ui_i(12.0),
                    font_name=self.ui_font_name,
                    color=(*self._ui_amber, 240),
                    width=1,
                    multiline=True,
                    align="left",
                    batch=self.overlay_text_batch,
                    group=ui_group_error_text,
                )

                for w in (
                    self.error_bg,
                    self.error_frame_top,
                    self.error_frame_bottom,
                    self.error_frame_left,
                    self.error_frame_right,
                    self.error_label,
                ):
                    w.visible = False

                def _to_ender_pua(text: str) -> str:
                    out: list[str] = []
                    for ch in str(text):
                        if ch in {" ", "\n", "\r", "\t"} or ch == "\u00a0":
                            out.append(ch)
                            continue
                        o = ord(ch)
                        if 0 <= o < 128:
                            out.append(chr(0xE000 + o))
                        else:
                            out.append(ch)
                    return "".join(out)

                # Subtle watermark: the Ender Terminal name, rendered in the
                # Ender PUA glyphs (requires `terminal Mixed`).
                self.brand_label = pyglet.text.Label(
                    _to_ender_pua("enderterm"),
                    x=int(self.width - 12),
                    y=12,
                    anchor_x="right",
                    anchor_y="bottom",
                    font_size=self._ui_i(10.0),
                    font_name=self.ui_font_name,
                    color=(*self._ui_purple, 45),
                    batch=self.overlay_text_batch,
                    group=ui_group_text,
                )
                self.walk_mode_label = pyglet.text.Label(
                    "WALK MODE ACTIVE  (Esc exits)",
                    x=int(self.width * 0.5),
                    y=int(self.height - 12),
                    anchor_x="center",
                    anchor_y="top",
                    font_size=self._ui_i(12.0),
                    font_name=self.ui_font_name,
                    color=(*self._ui_amber, 225),
                    batch=self.overlay_text_batch,
                    group=ui_group_text,
                )
                self.walk_mode_label.visible = False

                # Ender Vision overlay (V): faint debug lens for pool sockets, etc.
                self._ender_vision_active = False
                self._jigsaw_state: JigsawExpansionState | None = None
                self._ender_vision_by_pos: dict[Vec3i, JigsawConnector] = {}
                self._ender_vision_open: list[JigsawConnector] = []
                self._ender_vision_used: list[JigsawConnector] = []
                self._ender_vision_dead: list[JigsawConnector] = []
                self._ender_vision_hover: JigsawConnector | None = None
                self._ender_vision_hover_pos: Vec3i | None = None
                self._ender_vision_last_mouse: tuple[int, int] = (-1, -1)
                self._ender_vision_label_last_text: str = ""
                self._jigsaw_selected: JigsawConnector | None = None

                self.ender_vision_label = pyglet.text.Label(
                    "",
                    x=int(self.sidebar_width) + 14,
                    y=int(self.height) - 14,
                    anchor_x="left",
                    anchor_y="top",
                    font_size=self._ui_i(11.0),
                    font_name=self.ui_font_name,
                    color=(*self._ui_purple_hi, 140),
                    width=320,
                    multiline=True,
                    align="left",
                    batch=self.overlay_text_batch,
                    group=ui_group_text,
                )
                self.ender_vision_label.visible = False

                # Block palette overlay (toggle with I; used by Build Mode).
                self.palette_bg = pyglet.shapes.Rectangle(
                    0,
                    0,
                    10,
                    10,
                    color=(10, 10, 12),
                    batch=self.overlay_shape_batch,
                    group=ui_group_palette_bg,
                )
                self.palette_bg.opacity = 240
                self.palette_frame_top = pyglet.shapes.Rectangle(
                    0,
                    0,
                    10,
                    1,
                    color=self._ui_ender_yellow,
                    batch=self.overlay_shape_batch,
                    group=ui_group_palette_frame,
                )
                self.palette_frame_bottom = pyglet.shapes.Rectangle(
                    0,
                    0,
                    10,
                    1,
                    color=self._ui_ender_yellow,
                    batch=self.overlay_shape_batch,
                    group=ui_group_palette_frame,
                )
                self.palette_frame_left = pyglet.shapes.Rectangle(
                    0,
                    0,
                    1,
                    10,
                    color=self._ui_ender_yellow,
                    batch=self.overlay_shape_batch,
                    group=ui_group_palette_frame,
                )
                self.palette_frame_right = pyglet.shapes.Rectangle(
                    0,
                    0,
                    1,
                    10,
                    color=self._ui_ender_yellow,
                    batch=self.overlay_shape_batch,
                    group=ui_group_palette_frame,
                )
                for r in (self.palette_frame_top, self.palette_frame_bottom, self.palette_frame_left, self.palette_frame_right):
                    r.opacity = 170

                self.palette_title = pyglet.text.Label(
                    "Palette",
                    x=0,
                    y=0,
                    anchor_x="left",
                    anchor_y="top",
                    font_size=self._ui_i(14.0),
                    font_name=self.ui_font_name,
                    color=(*self._ui_purple_hi, 255),
                    batch=self.overlay_text_batch,
                    group=ui_group_palette_text,
                )
                self.palette_search_bg = pyglet.shapes.Rectangle(
                    0,
                    0,
                    10,
                    self._ui_i(18.0),
                    color=(24, 18, 34),
                    batch=self.overlay_shape_batch,
                    group=ui_group_palette_bg,
                )
                self.palette_search_bg.opacity = 220
                self.palette_search_label = pyglet.text.Label(
                    "",
                    x=0,
                    y=0,
                    anchor_x="left",
                    anchor_y="center",
                    font_size=self._ui_i(12.0),
                    font_name=self.ui_font_name,
                    color=(*self._ui_purple_hi, 240),
                    batch=self.overlay_text_batch,
                    group=ui_group_palette_text,
                )
                self.palette_hint_label = pyglet.text.Label(
                    "",
                    x=0,
                    y=0,
                    anchor_x="left",
                    anchor_y="bottom",
                    font_size=self._ui_i(10.0),
                    font_name=self.ui_font_name,
                    color=(*self._ui_purple, 200),
                    batch=self.overlay_text_batch,
                    group=ui_group_palette_text,
                )

                self.palette_sel = pyglet.shapes.Rectangle(
                    0,
                    0,
                    10,
                    10,
                    color=self._ui_purple,
                    batch=self.overlay_shape_batch,
                    group=ui_group_palette_sel,
                )
                self.palette_sel.opacity = 110

                self._palette_rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
                self._palette_grid_rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
                self._palette_cols = 1
                self._palette_rows = 1
                self._palette_cell_px = 34.0
                self._palette_icon_px = 28.0
                self._palette_sprites: list[pyglet.sprite.Sprite] = []
                self._palette_sprite_indices: list[int] = []
                try:
                    blank = pyglet.image.SolidColorImagePattern((0, 0, 0, 0)).create_image(1, 1).get_texture()
                    missing = pyglet.image.SolidColorImagePattern((60, 48, 74, 255)).create_image(2, 2).get_texture()
                    blank.mag_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
                    blank.min_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
                    missing.mag_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
                    missing.min_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
                    self._palette_blank_tex = blank
                    self._palette_missing_tex = missing
                except Exception:
                    self._palette_blank_tex = None
                    self._palette_missing_tex = None

                for w in (
                    self.palette_bg,
                    self.palette_frame_top,
                    self.palette_frame_bottom,
                    self.palette_frame_left,
                    self.palette_frame_right,
                    self.palette_title,
                    self.palette_search_bg,
                    self.palette_search_label,
                    self.palette_hint_label,
                    self.palette_sel,
                ):
                    w.visible = False

                self.status_labels: list[pyglet.text.Label] = []
                self._ensure_status_labels()

                self.line_labels: list[pyglet.text.Label] = []
                self._ensure_line_labels()
                self.log_labels: list[pyglet.text.Label] = []
                self._ensure_log_labels()

                gl.glClearColor(0.0, 0.0, 0.0, 1.0)
                gl.glEnable(gl.GL_DEPTH_TEST)
                gl.glDepthFunc(gl.GL_LEQUAL)
                gl.glShadeModel(gl.GL_SMOOTH)

                gl.glEnable(gl.GL_LIGHTING)
                gl.glEnable(gl.GL_LIGHT0)
                gl.glEnable(gl.GL_COLOR_MATERIAL)
                gl.glLightfv(gl.GL_LIGHT0, gl.GL_AMBIENT, (GLfloat * 4)(0.2, 0.2, 0.2, 1.0))
                gl.glLightfv(gl.GL_LIGHT0, gl.GL_DIFFUSE, (GLfloat * 4)(0.9, 0.9, 0.9, 1.0))
                gl.glLightfv(gl.GL_LIGHT0, gl.GL_POSITION, (GLfloat * 4)(0.35, 0.9, 0.5, 0.0))

                gl.glEnable(gl.GL_BLEND)
                gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
                gl.glEnable(gl.GL_CULL_FACE)
                gl.glCullFace(gl.GL_BACK)
                gl.glFrontFace(gl.GL_CCW)

                self._sync_ui_palette()
                self._init_ender_vignette()
                self._init_ssao()
                if start_load_mode == "structures":
                    idx = max(0, min(len(items) - 1, int(start_load_idx)))
                    self._load_index(idx, reset_view=True)
                else:
                    if pool_labels:
                        self._load_pool_index(int(self.selected), reset_view=True)
                    else:
                        self._load_index(0, reset_view=True)
                pyglet.clock.schedule_interval(self._on_tick, 1.0 / 60.0)
                self._install_macos_gestures()

            def _init_vsync_and_perf_state(
                self,
                *,
                desired_vsync: bool,
                perf_enabled: bool,
                perf_out_path: Path,
                perf_s: float,
            ) -> None:
                self._closing = False
                self._batch: pyglet.graphics.Batch = pyglet.graphics.Batch()
                self._vsync_enabled: bool | None = None
                self._vsync_apply_error: str | None = None
                try:
                    self._vsync_enabled = bool(desired_vsync)
                    self.set_vsync(bool(self._vsync_enabled))
                    self._vsync_apply_error = None
                except Exception as e:
                    self._vsync_enabled = None
                    self._vsync_apply_error = f"{type(e).__name__}: {e}"
                self._perf_enabled = bool(perf_enabled)
                self._perf_out_path = Path(perf_out_path)
                self._perf_start_t = time.monotonic()
                self._perf_end_t = float(self._perf_start_t + float(perf_s)) if self._perf_enabled else 0.0
                self._perf_written = False
                self._perf_frames: list[dict[str, object]] = []
                self._perf_patches: list[dict[str, object]] = []
                self._perf_last_env_fade_ms = 0.0
                self._perf_last_env_built = 0
                self._perf_last_env_queue_len = 0
                self._perf_last_env_tick_ms = 0.0
                self._perf_last_draw_ms = 0.0
                self._perf_last_world_ms = 0.0
                self._perf_last_ui_ms = 0.0
                self._perf_closing = False

            def _init_structure_and_camera_state(
                self,
                *,
                export_dir: Path,
                cinematic_start: bool,
                param_store: object,
            ) -> None:
                self._initial_distance = 10.0
                self._pivot_center: tuple[float, float, float] = (0.0, 0.0, 0.0)
                self._orbit_target: tuple[float, float, float] = (0.0, 0.0, 0.0)
                self._current_structure: Structure | None = None
                self._batch_pivot_center: tuple[float, float, float] = self._pivot_center
                self._structure_delta_fades: list[fx_mod.StructureDeltaFade] = []
                # Full batch for the current (latest) structure state, even while
                # one or more delta-fades are in flight.
                self._structure_full_batch: object | None = None
                self._structure_full_pivot_center: tuple[float, float, float] = self._pivot_center
                self._current_label: str = ""
                self._last_export: Path | None = None
                self._pick_positions: set[Vec3i] = set()
                self._pick_bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None
                self._pick_bounds_i: tuple[int, int, int, int, int, int] | None = None

                self.export_dir = export_dir
                self.export_dir.mkdir(parents=True, exist_ok=True)

                self.yaw = 45.0
                self.pitch = 25.0
                self.distance = 10.0
                self.pan_x = 0.0
                self.pan_y = 0.0
                self._camera_last_user_input_t = time.monotonic()
                self._cam_tween_distance: Tween | None = None
                self._cam_tween_pan_x: Tween | None = None
                self._cam_tween_pan_y: Tween | None = None
                self._cam_tween_orbit: tuple[Tween, Tween, Tween] | None = None

                self.sidebar_width = 460
                self._sidebar_width_default = self.sidebar_width
                self._ui_hidden = bool(cinematic_start)
                if self._ui_hidden:
                    self.sidebar_width = 0
                self._sidebar_width_tween: Tween | None = None
                self._sidebar_resize_active = False
                self._sidebar_resize_start_x = 0.0
                self._sidebar_resize_start_w = float(self.sidebar_width)
                self._cursor_kind = "default"
                self._cursor_default = None
                self._cursor_resize_lr = None
                # UI scale (primarily for font readability on retina displays).
                try:
                    ui_scale = float(param_store.get("ui.font.scale") or 1.0)  # type: ignore[attr-defined]
                except Exception:
                    ui_scale = 1.0
                if not math.isfinite(ui_scale):
                    ui_scale = 1.0
                self._ui_font_scale = max(0.5, min(3.0, float(ui_scale)))
                self._ui_font_scale_last = float(self._ui_font_scale)

                self.line_height = int(round(18.0 * float(self._ui_font_scale)))
                self.status_lines_max = 4
                self.status_line_height = int(round(15.0 * float(self._ui_font_scale)))
                self.header_h = int(round(54.0 * float(self._ui_font_scale))) + (
                    self.status_lines_max * self.status_line_height
                ) + int(round(8.0 * float(self._ui_font_scale)))
                self.log_line_height = int(round(14.0 * float(self._ui_font_scale)))
                self._log_collapsed = True
                self.log_panel_h = float(self._compute_log_panel_height())
                self._log_panel_tween: Tween | None = None
                # Terminal sidebar animation: log "opens" by gaining rows (fixed glyph size).
                self._term_log_open_p = 0.0
                self._term_log_open_tween: Tween | None = None
                self.scroll_top = 0
                self._scroll_pos_f = 0.0

            def _init_sidebar_browser_state(
                self,
                *,
                start_browser_mode: str,
                labels: list[str],
                pool_labels: list[str],
                start_load_mode: str,
                start_load_idx: int,
                datapack_path: Path,
            ) -> None:
                start_mode = str(start_browser_mode)
                # Sidebar modes: NBT structure list vs pool templates.
                if start_mode == "pools" and not pool_labels:
                    start_mode = "structures"
                if start_mode == "structures" and not labels and pool_labels:
                    start_mode = "pools"
                self._browser_mode: Literal["pools", "structures", "datasets"] = (
                    "structures" if start_mode == "structures" else "pools"
                )
                self._browser_saved_mode: Literal["pools", "structures"] = (
                    "structures" if self._browser_mode == "structures" else "pools"
                )
                self._browser_saved_selected = 0
                self._browser_saved_scroll_pos_f = 0.0
                self._browser_scroll_pos_pools = 0.0
                self._browser_scroll_pos_structures = 0.0
                self.selected = 0
                self._structures_selected = 0
                self._worldgen_selected = 0  # legacy (browser mode no longer exposes worldgen list)
                if start_load_mode == "structures":
                    self._structures_selected = max(0, min(len(labels) - 1, int(start_load_idx)))
                elif pool_labels:
                    self.selected = max(0, min(len(pool_labels) - 1, int(start_load_idx)))
                self._loaded_mode: Literal["pools", "worldgen", "structures"] = "pools"
                self._loaded_index = 0
                self._dataset_root = datapack_path.parent
                self._dataset_paths: list[Path] = self._scan_dataset_root(self._dataset_root)
                self._dataset_labels: list[str] = [p.name for p in self._dataset_paths]
                self._dataset_selected = 0
                idx = self._dataset_index_for_path(datapack_path)
                if idx is not None:
                    self._dataset_selected = idx

                active_len = len(labels) if self._browser_mode == "structures" else len(pool_labels)
                self._filtered_indices: list[int] = list(range(active_len))
                self._filtered_pos_by_index: dict[int, int] = {i: i for i in range(active_len)}

            def _init_build_rez_and_scroll_state(
                self,
                *,
                param_store: object,
                shared_term_scrollbar_capture: TermMouseCapture,
            ) -> None:
                self.jigsaw_seeds: list[int] = []
                self._base_template: StructureTemplate | None = None
                self._base_projection = "rigid"
                self._expansion_report: list[str] = []

                # Key-repeat for Up/Down selection navigation (pyglet doesn't always
                # deliver OS key-repeat as repeated on_key_press events).
                self._repeat_symbol: int | None = None
                self._repeat_next_at_s = 0.0
                self._repeat_hold_s = 0.0
                self._repeat_step_s = 0.0
                self._repeat_delay_s = 0.35
                self._repeat_rate_s = 0.06
                self._zoom_pick_cache_xy: tuple[int, int] | None = None
                self._zoom_pick_cache_hit: tuple[float, float, float] | None = None
                self._zoom_pick_cache_t = 0.0
                self._search_active = False
                self._search_query = ""
                self._search_origin_selected = 0

                # Build mode (v0): default mouse is Minecraft-style place/break.
                # Hold ⌥ (Option) to temporarily enable camera controls.
                self._build_enabled = True
                self._walk_mode_active = False
                self._walk_mode_capture_active = False
                self._walk_mode_scaffold_pressed: set[int] = set()
                self._walk_mode_move_accum_s = 0.0
                self._walk_mode_move_fixed_step_s = 1.0 / 120.0
                self._walk_mode_move_max_steps = 8
                self._walk_mode_move_speed_u_s = 6.0
                self._walk_mode_collision_substep_u = 0.25
                self._build_hover_pick_enabled = bool(params_mod.hover_pick_enabled(param_store))
                self._hotbar_panel_p = 1.0
                self._hotbar_panel_tween: Tween | None = None
                self._hotbar_panel_target_show = True
                self._hotbar_slots: list[str] = [
                    "minecraft:stone",
                    "minecraft:dirt",
                    "minecraft:grass_block[snowy=false]",
                    "minecraft:sand",
                    "minecraft:oak_planks",
                    "minecraft:bricks",
                    "minecraft:glass",
                    "minecraft:torch",
                    "minecraft:oak_log[axis=y]",
                    "minecraft:cobblestone",
                ]
                self._build_selected_block_id = self._hotbar_slots[0]
                self._hotbar_selected = 0
                self._build_last_edit_t = 0.0
                self._build_undo: list[tuple[Vec3i, BlockInstance | None, BlockInstance | None, str]] = []
                self._build_redo: list[tuple[Vec3i, BlockInstance | None, BlockInstance | None, str]] = []

                # Block palette (toggle with I; opened as a separate TermUI window).
                self._palette_window: object | None = None
                self._palette_selected = 0
                self._palette_entries: list[PaletteEntry] = []

                self._rez_active = False
                self._rez_progress = 0.0
                self._rez_message = ""
                self._rez_last_label = ""
                self._rez_anim_s = 0.0
                self._rez_gen = 0
                self._rez_active_gen: int | None = None
                self._rez_proc: multiprocessing.Process | None = None
                self._rez_queue: multiprocessing.Queue | None = None
                self._rez_template_id: str | None = None
                self._rez_env_key: str = ""
                self._rez_seeds_snapshot: tuple[int, ...] = ()
                self._rez_reset_view = False
                self._rez_adjust_distance = True
                self._rez_live_positions: set[Vec3i] = set()
                self._rez_live_solids: set[Vec3i] = set()
                self._rez_live_pending: list[tuple[Vec3i, str, str]] = []
                self._rez_live_pending_positions: set[Vec3i] = set()
                self._rez_live_chunks: list[fx_mod.RezLiveFadeChunk] = []
                self._rez_live_bounds: tuple[int, int, int, int, int, int] | None = None
                self._rez_live_fit_distance = self._initial_distance
                self._rez_piece_queue: deque[list[object]] = deque()
                self._rez_piece_tokens = 0.0
                self._rez_pending_result: tuple[Structure, list[str], JigsawExpansionState] | None = None
                # Rez progress is two-phase: (1) worker planning/build, (2) main-thread
                # application of piece payloads (possibly throttled).
                self._rez_worker_progress = 0.0
                self._rez_worker_message = ""
                self._rez_result_received = False
                self._rez_pieces_received = 0
                self._rez_pieces_applied = 0
                self._hud_blocks_live = -1
                self._hud_entities_live = -1
                self._hud_block_entities_live = -1
                self._jigsaw_cache_template_id: str = ""
                self._jigsaw_cache_env_key: str = ""
                self._jigsaw_cache: dict[tuple[int, ...], tuple[Structure, list[str], JigsawExpansionState]] = {}
                # TermUI list scrolling: by default, keep selection in view.
                # Mouse-wheel scrolling disables this and allows free scroll.
                self._scroll_follow_selection = True
                # Horizontal pan (columns) for the terminal list (trackpad swipe / shift-wheel).
                self._scroll_x_cols = 0
                from enderterm.termui import TermScrollbar

                self._term_list_scrollbar = TermScrollbar()
                self._term_list_mouse_capture = shared_term_scrollbar_capture
                self._term_list_mouse_context = f"sidebar-list:{id(self)}"
                self._term_list_mouse_target = "scrollbar"

            def _init_render_environment_state(self, *, param_store: object, env_decor_cfg: dict[str, object]) -> None:
                self._fx_seed = secrets.randbits(32)
                self._fx_t = 0.0
                self._fx_frame = 0
                self._fx_last_dt = 1.0 / 60.0
                self._channel_change_start_t: float | None = None
                self._channel_change_seed = secrets.randbits(32)
                self._param_window: object | None = None
                self._worldgen_window: object | None = None
                self._viewport_windows = ViewportRegistry()
                self._secondary_viewport_id: int | None = None
                self._model_rt = ModelRenderTarget()
                self._present_cache_tex = gl.GLuint(0)
                self._present_cache_w = 0
                self._present_cache_h = 0
                self._present_cache_valid = False
                self._ender_vignette_tex = gl.GLuint(0)
                self._ender_vignette_tex_w = 0
                self._ender_vignette_tex_h = 0
                self._ender_vignette_prog = gl.GLuint(0)
                self._ender_vignette_u_tex = -1
                self._ender_vignette_u_view_px = -1
                self._ender_vignette_u_ender_rgb = -1
                self._ender_vignette_u_strength = -1
                self._ender_vignette_u_thick_px = -1
                self._ender_vignette_u_falloff_exp = -1
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
                self._debug_panel_active = False
                self._debug_panel_last_text = ""
                self._debug_window: object | None = None
                self._fps_last_t = time.monotonic()
                self._fps_frames = 0
                self._fps_value = 0.0
                self._draw_skip_cap_count = 0
                self._draw_cache_present_count = 0
                self._tick_fps_last_t = time.monotonic()
                self._tick_fps_frames = 0
                self._tick_fps_value = 60.0
                self._render_cap_hz = max(0, int(param_store.get_int("render.frame_cap_hz")))  # type: ignore[attr-defined]
                render_now = time.monotonic()
                self._render_cap_next_deadline_t = float(render_now)
                self._render_cap_startup_until_t = float(render_now + 0.75)
                self._render_cap_force_next = True
                self._render_cap_last_ratio = -1.0
                self._render_cap_last_view_px: tuple[int, int] = (0, 0)
                self._ortho_enabled = True
                self._env_index = 0
                # Each time you cycle environments (E), we reroll the underlying
                # terrain noise field so returning to (say) grassy hills doesn't
                # always produce the exact same landscape.
                self._env_shape_nonce = int(secrets.randbits(32))
                if self._perf_enabled:
                    try:
                        for i, p in enumerate(ENVIRONMENT_PRESETS):
                            if getattr(p, "name", "") == "grassy_hills":
                                self._env_index = int(i)
                                break
                    except Exception:
                        pass
                self._env_sky_tween: Tween | None = None
                self._env_sky_rgb_from: tuple[float, float, float] = self._env_preset().sky_rgb
                self._env_sky_rgb_to: tuple[float, float, float] = self._env_sky_rgb_from
                self._env_template_id: str | None = None
                self._env_base_y: int | None = None
                self._env_patch_step = max(1, int(param_store.get_int("env.ground.patch.size")))  # type: ignore[attr-defined]
                self._env_patches_per_tick = max(1, int(param_store.get_int("env.ground.patches_per_tick")))  # type: ignore[attr-defined]
                self._env_ground_radius = max(0, int(param_store.get_int("env.ground.radius")))  # type: ignore[attr-defined]
                self._env_ground_bottom = int(param_store.get_int("env.ground.bottom"))  # type: ignore[attr-defined]
                self._env_strip_fade_h = max(0, int(param_store.get_int("env.ground.strip_fade.height")))  # type: ignore[attr-defined]
                self._env_strip_fade_levels = max(2, int(param_store.get_int("env.ground.strip_fade.levels")))  # type: ignore[attr-defined]
                self._env_terrain_amp = max(0, int(param_store.get_int("env.terrain.amp")))  # type: ignore[attr-defined]
                self._env_terrain_scale = float(param_store.get("env.terrain.scale"))  # type: ignore[attr-defined]
                self._env_terrain_octaves = max(1, int(param_store.get_int("env.terrain.octaves")))  # type: ignore[attr-defined]
                self._env_terrain_lacunarity = float(param_store.get("env.terrain.lacunarity"))  # type: ignore[attr-defined]
                self._env_terrain_h = float(param_store.get("env.terrain.h"))  # type: ignore[attr-defined]
                self._env_terrain_ridged_offset = float(param_store.get("env.terrain.ridged.offset"))  # type: ignore[attr-defined]
                self._env_terrain_ridged_gain = float(param_store.get("env.terrain.ridged.gain"))  # type: ignore[attr-defined]
                self._env_height_seed = 0
                self._env_height_anchor_off = 0
                self._env_height_origin_x = 0
                self._env_height_origin_z = 0
                self._env_batch = pyglet.graphics.Batch()
                self._env_patches: set[tuple[int, int]] = set()
                self._env_top_y_by_xz: dict[tuple[int, int], int] = {}
                self._env_tex_vlists: dict[str, object] = {}
                self._env_tex_counts: dict[str, int] = {}
                self._env_colored_vlist: object | None = None
                self._env_colored_count = 0
                # Ambient environment decoration ("plants"): stored separately from
                # terrain so it can be drawn under the same alpha-cutout rules as
                # structures, and masked by overlap naturally.
                self._env_decor_cfg = dict(env_decor_cfg)
                self._env_decor_by_pos: dict[Vec3i, str] = {}
                self._env_decor_batch = pyglet.graphics.Batch()
                self._env_decor_tex_vlists: dict[str, object] = {}
                self._env_decor_tex_counts: dict[str, int] = {}
                self._env_decor_colored_vlist: object | None = None
                self._env_decor_colored_count = 0
                self._env_patch_queue: list[tuple[int, int, int, tuple[int, int]]] = []
                self._env_patch_pending: set[tuple[int, int]] = set()
                self._env_patch_seq = 0
                self._env_patch_best_prio: dict[tuple[int, int], tuple[int, int]] = {}
                # Patch fade-in: key -> (start_t, ranges[(kind, jar_rel, start, count, target_alpha)], last_factor)
                self._env_patch_fade: dict[tuple[int, int], tuple[float, list[tuple[str, str, int, int, int]], int]] = {}
                # Patch ranges persist for sorted transparent draws.
                self._env_patch_ranges: dict[tuple[int, int], list[tuple[str, str, int, int, int]]] = {}
                # Per-patch contiguous spans per vlist for fast sorted transparent draws.
                self._env_patch_spans: dict[tuple[int, int], list[tuple[str, str, int, int]]] = {}
                # Patches that include any alpha<255 terrain (strip fade).
                self._env_patch_has_transparency: dict[tuple[int, int], bool] = {}
                self._env_xz_bounds: tuple[int, int, int, int] | None = None
                self._env_extent: tuple[int, int, int, int] | None = None
                self._env_suppress_updates = False

            def _init_macos_focus_and_font_state(self) -> None:
                self._mac_gestures_enabled = False
                self._mac_scroll_pan_enabled = False
                self._mac_pan_gesture_enabled = False
                self._mac_pinch_sensitivity = 3.0
                self._mac_rotate_sensitivity = 1.25
                self._mac_gesture_target = ""
                self._mac_gesture_target_size: tuple[int, int] = (0, 0)
                self._mac_gesture_install_note = ""
                self._gesture_last_mag = 0.0
                self._gesture_last_rot = 0.0
                self._gesture_handler = None
                self._gesture_handler_subclass = None
                self._gesture_scroll_view_subclass = None
                self._gesture_recognizers: list[object] = []
                self._mac_2f_last_loc: tuple[int, int] | None = None
                self._mac_2f_last_loc_t = 0.0
                self._mac_2f_last_scroll_pan_t = 0.0
                self._scroll_pan_until_t = 0.0
                self._scroll_last_sx = 0.0
                self._scroll_last_sy = 0.0
                self._scroll_last_mode = "zoom"
                self._dbg_mag_calls = 0
                self._dbg_mag_last = 0.0
                self._dbg_mag_state = 0
                self._dbg_mag_factor = 1.0
                self._dbg_rot_calls = 0
                self._dbg_rot_last = 0.0
                self._dbg_rot_state = 0
                self._dbg_pan_calls = 0
                self._dbg_pan_dx = 0.0
                self._dbg_pan_dy = 0.0
                self._dbg_pan_state = 0
                self._dbg_orbit_calls = 0
                self._dbg_orbit_dx = 0.0
                self._dbg_orbit_dy = 0.0
                self._dbg_orbit_state = 0
                self._dbg_scroll_pan_calls = 0
                self._dbg_scroll_pan_dx = 0.0
                self._dbg_scroll_pan_dy = 0.0
                self._dbg_gesture_loc_pan_calls = 0
                self._dbg_gesture_loc_pan_dx = 0.0
                self._dbg_gesture_loc_pan_dy = 0.0
                self._dbg_pyglet_drag_calls = 0
                self._dbg_pyglet_drag_dx = 0
                self._dbg_pyglet_drag_dy = 0
                self._dbg_pyglet_drag_buttons = 0
                self._dbg_pyglet_scroll_calls = 0
                self._dbg_pyglet_scroll_sx = 0
                self._dbg_pyglet_scroll_sy = 0
                self._dbg_pyglet_press_calls = 0
                self._dbg_last_cocoa_event = ""
                self._dbg_last_cocoa_event_t = time.monotonic()
                self._dbg_last_pyglet_event = ""
                self._dbg_last_pyglet_event_t = time.monotonic()
                self._mouse_x = 0
                self._mouse_y = 0
                self._focus_probe_pending_source = ""
                self._focus_probe_hits = 0
                self._focus_probe_hits_by_source: dict[str, int] = {}
                self._focus_probe_last_kind = ""
                self._focus_probe_last_source = ""
                self._focus_probe_last_t = 0.0
                self._focus_key_hits = 0
                self._focus_key_hits_by_source: dict[str, int] = {}
                self._focus_key_diag_by_source: dict[str, dict[str, bool]] = {}
                self._focus_key_last_source = ""
                self._focus_key_last_t = 0.0
                self._focus_close_request_path_by_source: dict[str, str] = {}
                self._hover_block: Vec3i | None = None
                self._hover_block_is_env = False
                self.ui_font_names = [
                    "terminal Mixed",
                    "terminal English",
                    "Glass TTY VT220",
                    "VT220",
                    "glassTTY",
                    "JetBrains Mono",
                    "Fira Code",
                    "SF Mono",
                    "SFMono-Regular",
                    "Menlo",
                    "Monaco",
                    "Andale Mono",
                    "Courier New",
                ]
                self.ui_font_name: str | None = None
                for name in self.ui_font_names:
                    try:
                        if pyglet.font.have_font(name):
                            self.ui_font_name = name
                            break
                    except Exception:
                        continue
                if self.ui_font_name:
                    print(f"UI font: {self.ui_font_name}")

            def _init_ui_palette_state(self) -> None:
                # UI palette: base "ender purple" lives at the top of the FX tree.
                # All purple accents in the UI should derive from it so the app's
                # look can be tuned in one place (kValue).
                self._ui_purple: tuple[int, int, int] = (205, 140, 255)
                self._ui_purple_hi: tuple[int, int, int] = (255, 210, 255)
                self._ui_purple_hot: tuple[int, int, int] = (255, 250, 255)
                self._ui_pink: tuple[int, int, int] = (255, 64, 200)
                self._ui_amber: tuple[int, int, int] = (255, 190, 90)
                self._ui_ender_yellow: tuple[int, int, int] = (255, 238, 160)
                self._ui_green: tuple[int, int, int] = (150, 255, 190)
                self._ui_cancel_bg: tuple[int, int, int] = (70, 36, 105)
                self._ui_cancel_bg_hot: tuple[int, int, int] = (90, 45, 135)

            def on_close(self) -> None:
                self._closing = True
                self._walk_mode_force_exit(reason="window_close")
                self._perf_write()
                self._terminate_rez_worker()
                self._close_all_viewport_windows()
                try:
                    if self._debug_window is not None:
                        self._debug_window.close()
                except Exception:
                    pass
                try:
                    if self._param_window is not None:
                        self._param_window.close()
                except Exception:
                    pass
                try:
                    if self._palette_window is not None:
                        self._palette_window.close()
                except Exception:
                    pass
                try:
                    self._model_rt.delete()
                except Exception:
                    pass
                try:
                    self._delete_ender_vignette()
                except Exception:
                    pass
                try:
                    self._delete_ssao()
                except Exception:
                    pass
                try:
                    if (not self._perf_enabled) and (not smoke_enabled):
                        param_store.save()
                except Exception:
                    pass
                super().on_close()

            def _perf_write(self) -> None:
                if not getattr(self, "_perf_enabled", False):
                    return
                if getattr(self, "_perf_written", False):
                    return
                self._perf_written = True

                try:
                    out_path = Path(self._perf_out_path)
                except Exception:
                    out_path = Path("/tmp/enderterm_perf.json")

                def _pct(values: list[float], pct: float) -> float:
                    if not values:
                        return 0.0
                    xs = sorted(float(v) for v in values)
                    if len(xs) == 1:
                        return float(xs[0])
                    p = max(0.0, min(100.0, float(pct))) / 100.0
                    idx = (len(xs) - 1) * p
                    lo = int(math.floor(idx))
                    hi = int(math.ceil(idx))
                    if lo == hi:
                        return float(xs[lo])
                    t = float(idx - lo)
                    return float(xs[lo]) * (1.0 - t) + float(xs[hi]) * t

                try:
                    frames = list(self._perf_frames)
                    patches = list(self._perf_patches)
                except Exception:
                    frames = []
                    patches = []

                tick_ms = [float(f.get("tick_ms", 0.0)) for f in frames if isinstance(f.get("tick_ms"), (int, float))]
                env_ms = [float(f.get("env_ms", 0.0)) for f in frames if isinstance(f.get("env_ms"), (int, float))]
                draw_ms = [float(f.get("draw_ms", 0.0)) for f in frames if isinstance(f.get("draw_ms"), (int, float))]
                patch_ms = [
                    float(p.get("total_ms", 0.0)) for p in patches if isinstance(p.get("total_ms"), (int, float))
                ]
                patch_upload_ms = [
                    float(p.get("upload_ms", 0.0)) for p in patches if isinstance(p.get("upload_ms"), (int, float))
                ]
                patch_faces_ms = [
                    float(p.get("faces_ms", 0.0)) for p in patches if isinstance(p.get("faces_ms"), (int, float))
                ]
                patch_tops_ms = [
                    float(p.get("tops_ms", 0.0)) for p in patches if isinstance(p.get("tops_ms"), (int, float))
                ]

                summary = {
                    "frames": len(frames),
                    "patches_built": len(patches),
                    "tick_ms_p50": _pct(tick_ms, 50.0),
                    "tick_ms_p90": _pct(tick_ms, 90.0),
                    "tick_ms_p99": _pct(tick_ms, 99.0),
                    "env_ms_p50": _pct(env_ms, 50.0),
                    "env_ms_p90": _pct(env_ms, 90.0),
                    "env_ms_p99": _pct(env_ms, 99.0),
                    "draw_ms_p50": _pct(draw_ms, 50.0),
                    "draw_ms_p90": _pct(draw_ms, 90.0),
                    "draw_ms_p99": _pct(draw_ms, 99.0),
                    "patch_total_ms_p50": _pct(patch_ms, 50.0),
                    "patch_total_ms_p90": _pct(patch_ms, 90.0),
                    "patch_total_ms_p99": _pct(patch_ms, 99.0),
                    "patch_tops_ms_p99": _pct(patch_tops_ms, 99.0),
                    "patch_faces_ms_p99": _pct(patch_faces_ms, 99.0),
                    "patch_upload_ms_p99": _pct(patch_upload_ms, 99.0),
                }

                meta = {
                    "cmd": "datapack-view",
                    "datapack": str(datapack_path),
                    "mode": str(mode),
                    "textured": bool(textured),
                    "env_patch_size": int(getattr(self, "_env_patch_step", 0)),
                    "env_radius": int(getattr(self, "_env_ground_radius", 0)),
                    "env_bottom": int(getattr(self, "_env_ground_bottom", 0)),
                    "strip_fade_h": int(getattr(self, "_env_strip_fade_h", 0)),
                    "strip_fade_levels": int(getattr(self, "_env_strip_fade_levels", 0)),
                    "perf_seconds": float(perf_s),
                    "started_monotonic": float(getattr(self, "_perf_start_t", 0.0)),
                    "ended_monotonic": float(time.monotonic()),
                }

                data = {"meta": meta, "summary": summary, "frames": frames, "patches": patches}

                try:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                try:
                    out_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
                    print(f"perf: wrote {out_path}")
                except Exception as e:
                    try:
                        print(f"perf: failed to write {out_path}: {e}")
                    except Exception:
                        pass

            def _delete_ender_vignette(self) -> None:
                fx_mod.delete_ender_vignette(self, gl=gl)

            def _init_ender_vignette(self) -> None:
                fx_mod.init_ender_vignette(self, gl=gl)

            def _delete_ssao(self) -> None:
                fx_mod.delete_ssao(self, gl=gl)

            def _init_ssao(self) -> None:
                fx_mod.init_ssao(self, gl=gl)

            def _ensure_ender_vignette_tex(self, w: int, h: int) -> bool:
                return bool(fx_mod.ensure_ender_vignette_tex(self, w, h, gl=gl))

            def _terminate_rez_worker(self) -> None:
                proc = self._rez_proc
                self._rez_proc = None
                if proc is not None:
                    try:
                        if proc.is_alive():
                            proc.terminate()
                    except Exception:
                        pass
                    try:
                        proc.join(timeout=0.2)
                    except Exception:
                        pass

                q = self._rez_queue
                self._rez_queue = None
                if q is not None:
                    try:
                        q.cancel_join_thread()
                    except Exception:
                        pass
                    try:
                        q.close()
                    except Exception:
                        pass

            def _focus_probe_arm(self, source: str) -> None:
                src = str(source or "").strip().lower()
                if not src:
                    return
                self._focus_probe_pending_source = src

            def _focus_key_mark(self, source: str, *, diag: dict[str, bool] | None = None) -> bool:
                src = str(source or "").strip().lower()
                if not src:
                    return False
                self._focus_key_hits = int(getattr(self, "_focus_key_hits", 0)) + 1
                hits = dict(getattr(self, "_focus_key_hits_by_source", {}))
                hits[src] = int(hits.get(src, 0)) + 1
                self._focus_key_hits_by_source = hits
                if isinstance(diag, dict):
                    diag_copy: dict[str, bool] = {}
                    for key, value in diag.items():
                        diag_copy[str(key)] = bool(value)
                    key_diags = dict(getattr(self, "_focus_key_diag_by_source", {}))
                    key_diags[src] = diag_copy
                    self._focus_key_diag_by_source = key_diags
                self._focus_key_last_source = src
                self._focus_key_last_t = time.monotonic()
                return True

            def _focus_probe_consume(self, kind: str) -> bool:
                src = str(getattr(self, "_focus_probe_pending_source", "") or "").strip().lower()
                if not src:
                    return False
                self._focus_probe_pending_source = ""
                self._focus_probe_hits = int(getattr(self, "_focus_probe_hits", 0)) + 1
                hits = dict(getattr(self, "_focus_probe_hits_by_source", {}))
                hits[src] = int(hits.get(src, 0)) + 1
                self._focus_probe_hits_by_source = hits
                self._focus_probe_last_kind = str(kind or "")
                self._focus_probe_last_source = src
                self._focus_probe_last_t = time.monotonic()
                return True

            def _restore_main_window_focus(self, *, source: str = "") -> None:
                if bool(getattr(self, "_closing", False)) or bool(getattr(self, "has_exit", False)):
                    return
                src = str(source or "").strip().lower()
                if src:
                    self._focus_probe_arm(src)
                else:
                    src = str(getattr(self, "_focus_probe_pending_source", "") or "").strip().lower()
                    if src:
                        self._focus_probe_arm(src)
                if not src:
                    return
                handoff_window_focus(self)
                diag = window_key_focus_diagnostics(self)
                if bool(diag.get("strict", False)):
                    self._focus_key_mark(src, diag=diag)

            def _on_tool_window_closed(self, *, source: str, attr_name: str | None = None) -> None:
                _apply_tool_window_close_focus_handoff(
                    owner=self,
                    source=str(source),
                    attr_name=attr_name,
                    restore_focus=lambda src: self._restore_main_window_focus(source=src),
                )

            def _record_tool_close_request_path(self, *, source: str, path: str) -> None:
                src = str(source or "").strip().lower()
                close_path = str(path or "").strip()
                if not src or not close_path:
                    return
                close_map = dict(getattr(self, "_focus_close_request_path_by_source", {}))
                close_map[src] = close_path
                self._focus_close_request_path_by_source = close_map

            def _record_tool_close_request_path_if_present(self, *, source: str, path: str) -> str:
                close_path = str(path or "").strip()
                if close_path:
                    self._record_tool_close_request_path(source=str(source), path=close_path)
                return close_path

            def _consume_tool_window_close_request_path(self, *, source: str, window_obj: object | None) -> str:
                close_path = ""
                try:
                    if window_obj is not None:
                        close_path = str(getattr(window_obj, "_close_request_path", "") or "")
                except Exception:
                    close_path = ""
                return self._record_tool_close_request_path_if_present(source=str(source), path=close_path)

            def _request_tool_window_close_handoff(
                self,
                *,
                source: str,
                palette_window: object | None,
                debug_window: object | None,
                viewport_window: object | None,
                close_trigger: str = "parent_toggle",
                close_palette_fallback: Callable[[], None] | None = None,
                close_debug_fallback: Callable[[], None] | None = None,
                close_viewport_fallback: Callable[[], None] | None = None,
            ) -> str:
                close_path = ""
                try:
                    close_path = _close_focus_handoff_window(
                        source=str(source),
                        palette_window=palette_window,
                        debug_window=debug_window,
                        viewport_window=viewport_window,
                        close_trigger=str(close_trigger),
                        close_palette_fallback=close_palette_fallback,
                        close_debug_fallback=close_debug_fallback,
                        close_viewport_fallback=close_viewport_fallback,
                    )
                except Exception:
                    close_path = ""
                return self._record_tool_close_request_path_if_present(source=str(source), path=close_path)

            def _toggle_param_window(self) -> None:
                self._walk_mode_force_exit(reason="tool_window")
                if _close_and_clear_window_attr(owner=self, attr_name="_param_window"):
                    return

                def _on_closed() -> None:
                    self._on_tool_window_closed(source="param", attr_name="_param_window")

                self._param_window = kvalue_window_mod.create_term_param_window(
                    pyglet=pyglet,
                    store=param_store,
                    font_name=self.ui_font_name,
                    is_rezzing=lambda: bool(self._rez_active),
                    on_closed=_on_closed,
                    theme_from_store=_termui_theme_from_store,
                )
                try:
                    vx, vy = self.get_location()
                    self._param_window.set_location(int(vx + 40), int(vy + 40))
                except Exception:
                    pass

            def _toggle_worldgen_window(self) -> None:
                self._walk_mode_force_exit(reason="tool_window")
                if _close_and_clear_window_attr(owner=self, attr_name="_worldgen_window"):
                    return

                def _on_closed() -> None:
                    self._on_tool_window_closed(source="worldgen", attr_name="_worldgen_window")

                self._worldgen_window = jigsaw_editor_window_mod.create_jigsaw_editor_window(
                    pyglet=pyglet,
                    store=param_store,
                    get_stack=lambda: pack_stack,
                    on_regrow=self._regrow_from_editor,
                    on_closed=_on_closed,
                    font_name=self.ui_font_name,
                )
                try:
                    vx, vy = self.get_location()
                    self._worldgen_window.set_location(int(vx + 60), int(vy + 60))
                except Exception:
                    pass

            def _open_pool_in_worldgen(self, pool_id: str, *, fork: bool) -> None:
                if not pool_id:
                    return
                if self._worldgen_window is None:
                    self._toggle_worldgen_window()
                w = self._worldgen_window
                if w is None:
                    return
                try:
                    w.open_pool(pool_id, fork=fork)
                except Exception:
                    pass

            def _regrow_from_editor(self) -> None:
                base = self._base_template
                if base is None:
                    return
                self._cancel_rez()
                self._jigsaw_cache = {}
                blocks_by_pos = {b.pos: b for b in base.blocks}
                block_entities_by_pos = {be.pos: be for be in base.block_entities}
                sx, sy, sz = base.size
                base_state = JigsawExpansionState(
                    connectors=base.connectors,
                    consumed=frozenset(),
                    dead_end=frozenset(),
                    piece_bounds=((0, 0, 0, int(sx) - 1, int(sy) - 1, int(sz) - 1),),
                )
                apply_jigsaw_final_states_to_blocks(blocks_by_pos, block_entities_by_pos, base_state.connectors)
                base_struct = Structure(
                    size=base.size,
                    blocks=tuple(blocks_by_pos.values()),
                    block_entities=tuple(sorted(block_entities_by_pos.values(), key=lambda be: be.pos)),
                    entities=base.entities,
                )
                self._jigsaw_cache[()] = (base_struct, [], base_state)
                self._rebuild_scene(reset_view=False, adjust_distance=False)
                self._update_status()

            def _install_macos_gestures(self) -> None:
                if sys.platform != "darwin":
                    self._mac_gesture_install_note = "not darwin"
                    return

                if not bool(params_mod.macos_gestures_enabled(param_store, platform=sys.platform)):
                    self._mac_gesture_install_note = (
                        "gestures off "
                        f"({params_mod.MACOS_GESTURES_ENABLED_KEY}=1)"
                    )
                    return

                # Enable "precise scroll = pan" fallback on macOS. If we can
                # install real Cocoa gesture recognizers, we'll disable the
                # scroll-based pan path so inputs don't fight.
                self._mac_scroll_pan_enabled = True
                self._mac_pan_gesture_enabled = False

                try:
                    from pyglet.libs.darwin import cocoapy
                except Exception:
                    self._mac_gesture_install_note = "no cocoapy"
                    return

                nsview = getattr(self, "_nsview", None)
                if nsview is None:
                    self._mac_gesture_install_note = "no _nsview"
                    return

                view = nsview
                try:
                    if not isinstance(nsview, cocoapy.ObjCInstance):
                        view = cocoapy.ObjCInstance(nsview)
                except Exception:
                    view = nsview

                # Pyglet uses a transparent NSTextView subview for text input,
                # but it defaults to a 0x0 frame. Attaching recognizers to it
                # means they never receive events. Use the main NSView unless
                # the text view has a real size.
                gesture_view = view
                try:
                    tv = getattr(view, "_textview", None)
                    if tv is not None:
                        fr = tv.frame()
                        w = int(getattr(fr.size, "width", 0))
                        h = int(getattr(fr.size, "height", 0))
                        if w >= 8 and h >= 8:
                            gesture_view = tv
                except Exception:
                    gesture_view = view
                try:
                    gesture_view.setAcceptsTouchEvents_(True)
                except Exception:
                    pass
                try:
                    gesture_view.setWantsRestingTouches_(True)
                except Exception:
                    pass
                try:
                    fr = gesture_view.frame()
                    self._mac_gesture_target_size = (
                        int(getattr(fr.size, "width", 0)),
                        int(getattr(fr.size, "height", 0)),
                    )
                except Exception:
                    self._mac_gesture_target_size = (0, 0)
                try:
                    self._mac_gesture_target = "textview" if gesture_view is tv else "nsview"
                except Exception:
                    self._mac_gesture_target = "nsview"

                suffix = secrets.token_hex(4)
                Handler = cocoapy.ObjCSubclass("NSObject", f"NBTToolGestureHandler_{suffix}")

                @Handler.method("v@")
                def handleMagnify_(self_obj, recognizer) -> None:  # type: ignore[no-redef]
                    viewer = getattr(self_obj, "_py_viewer", None)
                    if viewer is None:
                        return
                    try:
                        viewer._on_macos_magnify(recognizer)
                    except Exception:
                        return

                @Handler.method("v@")
                def handleRotate_(self_obj, recognizer) -> None:  # type: ignore[no-redef]
                    viewer = getattr(self_obj, "_py_viewer", None)
                    if viewer is None:
                        return
                    try:
                        viewer._on_macos_rotate(recognizer)
                    except Exception:
                        return

                @Handler.method("v@")
                def handlePan_(self_obj, recognizer) -> None:  # type: ignore[no-redef]
                    viewer = getattr(self_obj, "_py_viewer", None)
                    if viewer is None:
                        return
                    try:
                        viewer._on_macos_pan(recognizer)
                    except Exception:
                        return

                @Handler.method("v@")
                def handleOrbitPan_(self_obj, recognizer) -> None:  # type: ignore[no-redef]
                    viewer = getattr(self_obj, "_py_viewer", None)
                    if viewer is None:
                        return
                    try:
                        viewer._on_macos_orbit_pan(recognizer)
                    except Exception:
                        return

                # Keep the subclass wrapper alive: it owns the IMP function
                # pointers for the dynamic methods we add.
                self._gesture_handler_subclass = Handler

                handler_cls = None
                try:
                    handler_cls = cocoapy.ObjCClass(f"NBTToolGestureHandler_{suffix}")
                except Exception:
                    return
                try:
                    handler = handler_cls.alloc().init()
                except Exception:
                    return
                handler._py_viewer = self

                recognizers: list[object] = []

                try:
                    NSMagnificationGestureRecognizer = cocoapy.ObjCClass("NSMagnificationGestureRecognizer")
                    mag = NSMagnificationGestureRecognizer.alloc().initWithTarget_action_(
                        handler, cocoapy.get_selector("handleMagnify:")
                    )
                    gesture_view.addGestureRecognizer_(mag)
                    recognizers.append(mag)
                except Exception:
                    pass

                # Optional rotate gesture; harmless if unsupported.
                try:
                    NSRotationGestureRecognizer = cocoapy.ObjCClass("NSRotationGestureRecognizer")
                    rot = NSRotationGestureRecognizer.alloc().initWithTarget_action_(handler, cocoapy.get_selector("handleRotate:"))
                    gesture_view.addGestureRecognizer_(rot)
                    recognizers.append(rot)
                except Exception:
                    pass

                # Two-finger pan (trackpad) for camera translation.
                try:
                    NSPanGestureRecognizer = cocoapy.ObjCClass("NSPanGestureRecognizer")
                    pan = NSPanGestureRecognizer.alloc().initWithTarget_action_(handler, cocoapy.get_selector("handlePan:"))
                    try:
                        pan.setMinimumNumberOfTouches_(2)
                        pan.setMaximumNumberOfTouches_(2)
                    except Exception:
                        pass
                    # Prevent this recognizer from swallowing primary-mouse drags
                    # (we want LMB drags for UI/build; camera orbit is ⌥-drag).
                    try:
                        pan.setButtonMask_(0)
                    except Exception:
                        pass
                    gesture_view.addGestureRecognizer_(pan)
                    recognizers.append(pan)
                    self._mac_pan_gesture_enabled = True
                except Exception:
                    pass

                # Intentionally do NOT install a left-mouse click+drag orbit
                # recognizer. We use pyglet mouse drags gated behind ⌥ (Option)
                # so LMB drags can be used for editing/UI interactions (like
                # resizing the sidebar divider) without fighting camera orbit.

                # Middle-mouse drag for camera translation (legacy mouse support).
                try:
                    NSPanGestureRecognizer = cocoapy.ObjCClass("NSPanGestureRecognizer")
                    mouse_pan = NSPanGestureRecognizer.alloc().initWithTarget_action_(handler, cocoapy.get_selector("handlePan:"))
                    try:
                        mouse_pan.setMinimumNumberOfTouches_(1)
                        mouse_pan.setMaximumNumberOfTouches_(1)
                    except Exception:
                        pass
                    try:
                        mouse_pan.setButtonMask_(1 << 2)
                    except Exception:
                        pass
                    gesture_view.addGestureRecognizer_(mouse_pan)
                    recognizers.append(mouse_pan)
                except Exception:
                    pass

                if recognizers:
                    self._mac_gestures_enabled = True
                    self._gesture_handler = handler
                    self._gesture_recognizers = recognizers
                    try:
                        print(f"mac gestures: installed ({len(recognizers)})")
                    except Exception:
                        pass

                    # Two-finger "pan" on a Mac trackpad is delivered as
                    # scrollWheel events, not NSPanGestureRecognizer. Install a
                    # per-window NSView subclass to intercept scrollWheel_ and
                    # route it into our Cocoa camera controls.
                    try:
                        super_name = getattr(view.objc_class, "name", b"")
                        if isinstance(super_name, bytes):
                            super_name = super_name.decode("utf-8", errors="ignore")
                        view_suffix = secrets.token_hex(4)
                        ScrollView = cocoapy.ObjCSubclass(super_name or "PygletView", f"NBTToolScrollView_{view_suffix}")

                        @ScrollView.method("v@")
                        def scrollWheel_(self_obj, nsevent) -> None:  # type: ignore[no-redef]
                            win = getattr(self_obj, "_window", None)
                            handled = False
                            if win is not None:
                                try:
                                    handled = bool(win._on_macos_scroll_wheel(self_obj, nsevent))
                                except Exception:
                                    handled = False
                            if handled:
                                return
                            try:
                                cocoapy.send_super(self_obj, "scrollWheel:", nsevent)
                            except Exception:
                                return

                        self._gesture_scroll_view_subclass = ScrollView
                        cocoapy.runtime.objc.object_setClass(view.ptr, ScrollView.objc_cls)
                    except Exception as e:
                        self._mac_gesture_install_note = f"scroll hook failed: {e!r}"
                else:
                    self._mac_gesture_install_note = "no recognizers installed"
                    try:
                        print("mac gestures: unavailable (no recognizers)")
                    except Exception:
                        pass

            def _macos_gesture_location(self, recognizer) -> tuple[int, int] | None:
                nsview = getattr(self, "_nsview", None)
                if nsview is None:
                    return None
                try:
                    from pyglet.libs.darwin import cocoapy
                except Exception:
                    cocoapy = None  # type: ignore[assignment]
                view = nsview
                if cocoapy is not None:
                    try:
                        if not isinstance(nsview, cocoapy.ObjCInstance):
                            view = cocoapy.ObjCInstance(nsview)
                    except Exception:
                        view = nsview
                try:
                    pt = recognizer.locationInView_(view)
                    return (int(pt.x), int(pt.y))
                except Exception:
                    return None

            def _on_macos_magnify(self, recognizer) -> None:
                self._dbg_mag_calls += 1
                loc = self._macos_gesture_location(recognizer)
                if loc is None:
                    return
                x, y = loc
                if x < self.sidebar_width:
                    return

                self._cancel_camera_tween()
                now = time.monotonic()
                try:
                    state = int(recognizer.state())
                except Exception:
                    state = 0
                try:
                    mag = float(recognizer.magnification())
                except Exception:
                    return

                self._dbg_mag_state = state
                self._dbg_mag_last = mag

                # NSMagnificationGestureRecognizer.magnification behaves like a
                # per-event delta on macOS (often reset by the system).
                delta = mag

                pan_dx = 0
                pan_dy = 0
                if state == 1:
                    hit = self._pick_orbit_target(x, y)
                    if hit is not None:
                        self._set_orbit_target(hit)
                        self._mark_camera_user_input()
                    self._mac_2f_last_loc = (x, y)
                    self._mac_2f_last_loc_t = now
                else:
                    if self._mac_2f_last_loc is not None and (now - self._mac_2f_last_loc_t) < 0.20:
                        if (now - self._mac_2f_last_scroll_pan_t) > 0.06:
                            lx, ly = self._mac_2f_last_loc
                            pan_dx = x - lx
                            pan_dy = y - ly
                    self._mac_2f_last_loc = (x, y)
                    self._mac_2f_last_loc_t = now

                if abs(delta) < 1e-6:
                    delta = 0.0

                if abs(delta) >= 1e-6:
                    factor = math.exp(-delta * float(self._mac_pinch_sensitivity))
                    self._dbg_mag_factor = factor
                    self._dbg_last_cocoa_event = f"pinch Δ={delta:+.3f} factor={factor:.3f}"
                    self._dbg_last_cocoa_event_t = now
                    old_distance = float(self.distance)
                    new_distance = max(0.5, old_distance * factor)
                    self._zoom_to_distance_at_cursor(x, y, new_distance)
                    self._mark_camera_user_input()
                    try:
                        recognizer.setMagnification_(0.0)
                    except Exception:
                        pass

                if abs(pan_dx) + abs(pan_dy) >= 2:
                    self._dbg_gesture_loc_pan_calls += 1
                    self._dbg_gesture_loc_pan_dx = float(pan_dx)
                    self._dbg_gesture_loc_pan_dy = float(pan_dy)
                    fov_rad = math.radians(55.0)
                    units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                    self.pan_x += float(pan_dx) * units_per_point
                    self.pan_y += float(pan_dy) * units_per_point
                    self._mark_camera_user_input()

            def _on_macos_rotate(self, recognizer) -> None:
                self._dbg_rot_calls += 1
                loc = self._macos_gesture_location(recognizer)
                if loc is None:
                    return
                x, y = loc
                if x < self.sidebar_width:
                    return

                self._cancel_camera_tween()
                now = time.monotonic()
                try:
                    state = int(recognizer.state())
                except Exception:
                    state = 0
                try:
                    rot = float(recognizer.rotation())
                except Exception:
                    return

                self._dbg_rot_state = state
                self._dbg_rot_last = rot

                # NSRotationGestureRecognizer.rotation behaves like a per-event
                # delta on macOS.
                delta = rot

                pan_dx = 0
                pan_dy = 0
                if state == 1:
                    hit = self._pick_orbit_target(x, y)
                    if hit is not None:
                        self._set_orbit_target(hit)
                        self._mark_camera_user_input()
                    self._mac_2f_last_loc = (x, y)
                    self._mac_2f_last_loc_t = now
                else:
                    if self._mac_2f_last_loc is not None and (now - self._mac_2f_last_loc_t) < 0.20:
                        if (now - self._mac_2f_last_scroll_pan_t) > 0.06:
                            lx, ly = self._mac_2f_last_loc
                            pan_dx = x - lx
                            pan_dy = y - ly
                    self._mac_2f_last_loc = (x, y)
                    self._mac_2f_last_loc_t = now

                if abs(delta) < 1e-6:
                    delta = 0.0

                if abs(delta) >= 1e-6:
                    self._dbg_last_cocoa_event = f"rotate Δ={delta:+.3f} rad"
                    self._dbg_last_cocoa_event_t = now
                    self.yaw += math.degrees(delta) * float(self._mac_rotate_sensitivity)
                    self._mark_camera_user_input()
                    try:
                        recognizer.setRotation_(0.0)
                    except Exception:
                        pass

                if abs(pan_dx) + abs(pan_dy) >= 2:
                    self._dbg_gesture_loc_pan_calls += 1
                    self._dbg_gesture_loc_pan_dx = float(pan_dx)
                    self._dbg_gesture_loc_pan_dy = float(pan_dy)
                    fov_rad = math.radians(55.0)
                    units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                    self.pan_x += float(pan_dx) * units_per_point
                    self.pan_y += float(pan_dy) * units_per_point
                    self._mark_camera_user_input()

            def _on_macos_pan(self, recognizer) -> None:
                self._dbg_pan_calls += 1
                loc = self._macos_gesture_location(recognizer)
                if loc is None:
                    return
                x, _y = loc
                if x < self.sidebar_width:
                    return

                self._cancel_camera_tween()
                try:
                    state = int(recognizer.state())
                except Exception:
                    state = 0
                self._dbg_pan_state = state

                nsview = getattr(self, "_nsview", None)
                if nsview is None:
                    return
                try:
                    from pyglet.libs.darwin import cocoapy
                except Exception:
                    return

                option_down = False
                button_mask = 0
                try:
                    button_mask = int(recognizer.buttonMask())
                except Exception:
                    button_mask = 0
                try:
                    flags = int(cocoapy.ObjCClass("NSApplication").sharedApplication().currentEvent().modifierFlags())
                    option_down = bool(flags & (1 << 19))  # NSEventModifierFlagOption
                except Exception:
                    option_down = False

                view = nsview
                try:
                    if not isinstance(nsview, cocoapy.ObjCInstance):
                        view = cocoapy.ObjCInstance(nsview)
                except Exception:
                    view = nsview

                try:
                    pt = recognizer.translationInView_(view)
                    dx = float(pt.x)
                    dy = float(pt.y)
                except Exception:
                    return

                self._dbg_pan_dx = dx
                self._dbg_pan_dy = dy
                self._dbg_last_cocoa_event = f"pan Δ=({dx:+.1f},{dy:+.1f})"
                self._dbg_last_cocoa_event_t = time.monotonic()

                try:
                    recognizer.setTranslation_inView_(cocoapy.NSPoint(0.0, 0.0), view)
                except Exception:
                    pass

                # Mouse-button drags should never move the camera unless ⌥ is
                # held. (Two-finger trackpad pans are handled separately via
                # scrollWheel_ and do not set a buttonMask.)
                if button_mask and (not option_down):
                    return

                if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                    return
                fov_rad = math.radians(55.0)
                units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                self.pan_x += dx * units_per_point
                self.pan_y += dy * units_per_point
                self._mark_camera_user_input()

            def _on_macos_orbit_pan(self, recognizer) -> None:
                self._dbg_orbit_calls += 1
                loc = self._macos_gesture_location(recognizer)
                if loc is None:
                    return
                x, y = loc
                if x < self.sidebar_width:
                    return

                self._cancel_camera_tween()
                try:
                    state = int(recognizer.state())
                except Exception:
                    state = 0
                self._dbg_orbit_state = state
                try:
                    from pyglet.libs.darwin import cocoapy
                except Exception:
                    return

                shift_down = False
                option_down = False
                try:
                    flags = int(cocoapy.ObjCClass("NSApplication").sharedApplication().currentEvent().modifierFlags())
                    shift_down = bool(flags & (1 << 17))  # NSEventModifierFlagShift
                    option_down = bool(flags & (1 << 19))  # NSEventModifierFlagOption
                except Exception:
                    shift_down = False
                    option_down = False

                nsview = getattr(self, "_nsview", None)
                if nsview is None:
                    return
                view = nsview
                try:
                    if not isinstance(nsview, cocoapy.ObjCInstance):
                        view = cocoapy.ObjCInstance(nsview)
                except Exception:
                    view = nsview

                try:
                    pt = recognizer.translationInView_(view)
                    dx = float(pt.x)
                    dy = float(pt.y)
                except Exception:
                    return

                self._dbg_orbit_dx = dx
                self._dbg_orbit_dy = dy

                try:
                    recognizer.setTranslation_inView_(cocoapy.NSPoint(0.0, 0.0), view)
                except Exception:
                    pass

                # Disable camera orbit/pan on plain click+drag. Use ⌥ as the
                # explicit "camera mode" modifier so it doesn't fight with
                # build/edit clicks or UI drags.
                if not option_down:
                    self._dbg_last_cocoa_event = f"orbit ignored (no ⌥) Δ=({dx:+.1f},{dy:+.1f})"
                    self._dbg_last_cocoa_event_t = time.monotonic()
                    return

                if shift_down:
                    self._dbg_last_cocoa_event = f"shift-pan Δ=({dx:+.1f},{dy:+.1f})"
                else:
                    self._dbg_last_cocoa_event = f"orbit Δ=({dx:+.1f},{dy:+.1f})"
                self._dbg_last_cocoa_event_t = time.monotonic()

                if state == 1 and (not shift_down):
                    hit = self._pick_orbit_target(x, y)
                    self._set_orbit_target(hit if hit is not None else (0.0, 0.0, 0.0))
                    self._mark_camera_user_input()

                if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                    return
                if shift_down:
                    fov_rad = math.radians(55.0)
                    units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                    self.pan_x += dx * units_per_point
                    self.pan_y += dy * units_per_point
                    self._mark_camera_user_input()
                    return
                self.yaw += dx * 0.35
                self.pitch -= dy * 0.35
                self.pitch = max(-89.0, min(89.0, self.pitch))
                self._mark_camera_user_input()

            def _on_macos_scroll_wheel(self, nsview, nsevent) -> bool:
                # Called from our NSView scrollWheel_ override. Returns True when
                # handled (so the event won't be forwarded to pyglet).
                if not self._mac_gestures_enabled:
                    return False
                try:
                    pt = nsevent.locationInWindow()
                    pt = nsview.convertPoint_fromView_(pt, None)
                    x = int(pt.x)
                    y = int(pt.y)
                except Exception:
                    return False
                if x < self.sidebar_width:
                    return False

                now = time.monotonic()
                self._dbg_scroll_pan_calls += 1

                try:
                    precise = bool(nsevent.hasPreciseScrollingDeltas())
                except Exception:
                    precise = False
                try:
                    dx = float(nsevent.scrollingDeltaX() if precise else nsevent.deltaX())
                    dy = float(-(nsevent.scrollingDeltaY() if precise else nsevent.deltaY()))
                except Exception:
                    return True

                self._dbg_scroll_pan_dx = dx
                self._dbg_scroll_pan_dy = dy

                if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                    return True

                self._cancel_camera_tween()
                if not precise:
                    self._dbg_last_cocoa_event = f"wheel-zoom Δy={dy:+.1f}"
                    self._dbg_last_cocoa_event_t = now
                    old_distance = float(self.distance)
                    factor = 0.9**dy
                    new_distance = max(0.5, old_distance * factor)
                    self._zoom_to_distance_at_cursor(x, y, new_distance)
                    self._mark_camera_user_input()
                    return True

                self._dbg_last_cocoa_event = f"scroll-pan Δ=({dx:+.1f},{dy:+.1f})"
                self._dbg_last_cocoa_event_t = now

                fov_rad = math.radians(55.0)
                units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                self.pan_x += dx * units_per_point
                self.pan_y += dy * units_per_point
                self._mac_2f_last_scroll_pan_t = now
                self._mac_2f_last_loc = (x, y)
                self._mac_2f_last_loc_t = now
                self._mark_camera_user_input()
                return True

            def _clear_rez_live_preview(self) -> None:
                for chunk in list(getattr(self, "_rez_live_chunks", [])):
                    for vl in getattr(chunk, "vlists", []):
                        try:
                            delete = getattr(vl, "delete", None)
                            if callable(delete):
                                delete()
                        except Exception:
                            pass
                self._rez_live_chunks = []
                self._rez_live_pending = []
                self._rez_live_pending_positions = set()
                self._rez_live_positions = set()
                self._rez_live_solids = set()
                self._rez_live_bounds = None
                self._rez_piece_queue.clear()
                self._rez_piece_tokens = 0.0
                self._rez_pending_result = None

            def _init_rez_live_preview_from_current(self) -> None:
                self._rez_live_positions = set()
                self._rez_live_solids = set()
                self._rez_live_pending_positions = set()
                self._rez_live_bounds = None
                cur = self._current_structure
                if cur is None:
                    return
                prev_suppress = bool(self._env_suppress_updates)
                self._env_suppress_updates = True
                try:
                    for b in cur.blocks:
                        self._rez_live_positions.add(b.pos)
                        base_id = _block_id_base(b.block_id)
                        if base_id not in {"minecraft:jigsaw", "minecraft:structure_void"}:
                            if resolver is None:
                                self._rez_live_solids.add(b.pos)
                            else:
                                bm = resolver.resolve_block_model(b.block_id)
                                if bm is None or _block_model_is_full_cube(bm) or _block_model_bottom_coverage_frac(bm) >= 0.60:
                                    self._rez_live_solids.add(b.pos)
                        if base_id != "minecraft:structure_void":
                            self._rez_bounds_add(b.pos)
                finally:
                    self._env_suppress_updates = prev_suppress
                if self._rez_live_bounds is not None:
                    self._rez_live_fit_distance = self._fit_distance_for_bounds(self._rez_live_bounds)
                else:
                    self._rez_live_fit_distance = self._initial_distance
                # Environment updates are driven by model loads and newly-rezzed blocks.

            def _rez_bounds_add(self, pos: Vec3i) -> None:
                x, y, z = pos
                b = self._rez_live_bounds
                if b is None:
                    self._rez_live_bounds = (x, y, z, x, y, z)
                    return
                min_x, min_y, min_z, max_x, max_y, max_z = b
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if z < min_z:
                    min_z = z
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
                if z > max_z:
                    max_z = z
                self._rez_live_bounds = (min_x, min_y, min_z, max_x, max_y, max_z)

            def _fit_distance_for_bounds(self, bounds: tuple[int, int, int, int, int, int]) -> float:
                min_x, min_y, min_z, max_x, max_y, max_z = bounds
                center_x, center_y, center_z = self._pivot_center
                min_geom_x = float(min_x) - center_x
                max_geom_x = float(max_x + 1) - center_x
                min_geom_y = float(min_y) - center_y
                max_geom_y = float(max_y + 1) - center_y
                min_geom_z = float(min_z) - center_z
                max_geom_z = float(max_z + 1) - center_z
                far_x = min_geom_x if abs(min_geom_x) >= abs(max_geom_x) else max_geom_x
                far_y = min_geom_y if abs(min_geom_y) >= abs(max_geom_y) else max_geom_y
                far_z = min_geom_z if abs(min_geom_z) >= abs(max_geom_z) else max_geom_z
                radius = math.sqrt(far_x * far_x + far_y * far_y + far_z * far_z)
                return max(2.0, radius * 2.5)

            def _rez_maybe_autoframe_camera(self) -> None:
                if not self._rez_active:
                    return
                bounds = self._rez_live_bounds
                if bounds is None:
                    return
                vp_w, vp_h = self.get_viewport_size()
                ratio = float(self.get_pixel_ratio())
                sidebar_px = int(float(self.sidebar_width) * ratio)
                view_w_px = max(1, int(vp_w) - sidebar_px)
                view_h_px = max(1, int(vp_h))
                aspect = float(view_w_px) / float(max(1, view_h_px))

                min_x, min_y, min_z, max_x, max_y, max_z = bounds
                center_x, center_y, center_z = self._pivot_center
                min_geom_x = float(min_x) - center_x
                max_geom_x = float(max_x + 1) - center_x
                min_geom_y = float(min_y) - center_y
                max_geom_y = float(max_y + 1) - center_y
                min_geom_z = float(min_z) - center_z
                max_geom_z = float(max_z + 1) - center_z

                corners = [
                    (x, y, z)
                    for x in (min_geom_x, max_geom_x)
                    for y in (min_geom_y, max_geom_y)
                    for z in (min_geom_z, max_geom_z)
                ]
                tan_y = math.tan(math.radians(55.0) / 2.0)
                denom_x = max(1e-9, tan_y * max(1e-6, float(aspect)))
                denom_y = max(1e-9, tan_y)

                ox, oy, oz = self._orbit_target
                required = 0.5
                near = ORTHO_CLIP_NEAR_DEFAULT if self._ortho_enabled else PERSPECTIVE_CLIP_NEAR_DEFAULT
                for x, y, z in corners:
                    v = (x - ox, y - oy, z - oz)
                    v = self._rotate_y_deg(v, self.yaw)
                    v = self._rotate_x_deg(v, self.pitch)
                    vx = float(v[0]) + float(self.pan_x)
                    vy = float(v[1]) + float(self.pan_y)
                    vz = float(v[2])

                    req_x = abs(vx) / denom_x
                    req_y = abs(vy) / denom_y
                    if self._ortho_enabled:
                        req = max(req_x, req_y, vz + near)
                    else:
                        req = vz + max(req_x, req_y, near)
                    if req > required:
                        required = req

                required = max(0.5, float(required) * 1.03)
                self._rez_live_fit_distance = required
                # Keep "fit distance" coherent during live rez so finalization
                # doesn't apply a big extra step.
                self._initial_distance = max(float(self._initial_distance), required)

                cooldown_s = float(param_store.get("camera.autoframe.cooldown_s"))
                if cooldown_s > 1e-6:
                    now = time.monotonic()
                    if (now - float(self._camera_last_user_input_t)) < cooldown_s:
                        return

                cur = float(self.distance)
                if required <= cur:
                    return
                if cur > 0.0 and (required - cur) / cur < 0.01:
                    return
                tween = self._cam_tween_distance
                if tween is not None and float(tween.end) >= required:
                    return
                self._animate_camera_to(distance=required, duration_s=0.30)

            def _rez_live_apply_pending(self, *, max_blocks: int = 2400, time_budget_s: float = 0.0) -> None:
                if not self._rez_live_pending:
                    return
                if max_blocks < 1:
                    return
                start_t = time.monotonic()
                remaining = int(max_blocks)
                added_blocks: list[BlockInstance] = []
                while self._rez_live_pending and remaining > 0:
                    # Keep chunks moderately sized to avoid exploding the number of
                    # pyglet vertex lists while still staying responsive.
                    chunk_n = min(remaining, len(self._rez_live_pending))
                    chunk_n = min(chunk_n, 700)
                    chunk = self._rez_live_pending[:chunk_n]
                    del self._rez_live_pending[:chunk_n]

                    to_add: list[BlockInstance] = []
                    for pos, block_id, color_key in chunk:
                        self._rez_live_pending_positions.discard(pos)
                        if pos in self._rez_live_positions:
                            continue
                        to_add.append(BlockInstance(pos=pos, block_id=block_id, color_key=color_key))

                    remaining -= int(chunk_n)
                    if not to_add:
                        if time_budget_s > 1e-6 and (time.monotonic() - start_t) >= float(time_budget_s):
                            break
                        continue

                    # Update occupancy before building geometry so faces between newly-added blocks can be culled.
                    for b in to_add:
                        self._rez_live_positions.add(b.pos)
                        base_id = _block_id_base(b.block_id)
                        if base_id in {"minecraft:jigsaw", "minecraft:structure_void"}:
                            continue
                        if resolver is None:
                            self._rez_live_solids.add(b.pos)
                            continue
                        bm = resolver.resolve_block_model(b.block_id)
                        if bm is None or _block_model_is_full_cube(bm) or _block_model_bottom_coverage_frac(bm) >= 0.60:
                            self._rez_live_solids.add(b.pos)

                    added_blocks.extend(to_add)

                    if time_budget_s > 1e-6 and (time.monotonic() - start_t) >= float(time_budget_s):
                        break

                if not added_blocks:
                    return
                fade_s = 0.0
                try:
                    fade_s = float(param_store.get("rez.fade_s"))
                except Exception:
                    fade_s = 0.0
                if not math.isfinite(fade_s) or fade_s < 0.05:
                    fade_s = 0.0

                # Build the new geometry into its own batch so we can apply a
                # per-chunk stipple fade without re-uploading existing geometry.
                chunk_batch = pyglet.graphics.Batch()
                vlists: list[object] = []
                self._rez_live_add_blocks_geometry(added_blocks, batch=chunk_batch, out_vlists=vlists)
                if vlists:
                    now = time.monotonic()
                    self._rez_live_chunks.append(
                        fx_mod.RezLiveFadeChunk(
                            start_t=now,
                            duration_s=float(fade_s),
                            phase_seed=int(secrets.randbits(32)),
                            batch=chunk_batch,
                            vlists=vlists,
                        )
                    )
                    # Sync the per-block rez glow to when geometry becomes visible
                    # (same moment the fade begins).
                    positions = [b.pos for b in added_blocks if _block_id_base(b.block_id) != "minecraft:structure_void"]
                    max_flashes = 1200
                    if len(positions) > max_flashes:
                        step = float(len(positions)) / float(max_flashes)
                        positions = [positions[int(i * step)] for i in range(max_flashes) if int(i * step) < len(positions)]
                    cx, cy, cz = self._pivot_center
                    pad = 0.03
                    for x, y, z in positions:
                        self._spawn_flash_box(
                            (float(x) - cx - pad, float(y) - cy - pad, float(z) - cz - pad),
                            (float(x + 1) - cx + pad, float(y + 1) - cy + pad, float(z + 1) - cz + pad),
                            duration_s=2.0,
                        )

            def _rez_live_add_blocks_geometry(
                self, blocks: list[BlockInstance], *, batch: pyglet.graphics.Batch, out_vlists: list[object]
            ) -> None:
                if not blocks:
                    return
                center_x, center_y, center_z = self._pivot_center

                verts_by_tex: dict[str, list[float]] = {}
                norms_by_tex: dict[str, list[float]] = {}
                uvs_by_tex: dict[str, list[float]] = {}
                cols_by_tex: dict[str, list[int]] = {}
                colored_verts: list[float] = []
                colored_norms: list[float] = []
                colored_cols: list[int] = []

                for block in blocks:
                    base_id = _block_id_base(block.block_id)
                    x, y, z = block.pos
                    tx = x + 0.5 - center_x
                    ty = y + 0.5 - center_y
                    tz = z + 0.5 - center_z

                    appearance = None
                    block_model = None
                    if resolver is not None:
                        force_color = base_id == "minecraft:jigsaw"
                        if not force_color:
                            appearance = resolver.resolve_block_appearance(block.block_id)
                            block_model = resolver.resolve_block_model(block.block_id)
                    rx = appearance.rotate_x_deg if appearance is not None else 0
                    ry = appearance.rotate_y_deg if appearance is not None else 0

                    if block_model is not None and texture_source is not None and resolver is not None:
                        solid_block = _block_model_is_full_cube(block_model)
                        for part in block_model.parts:
                            rx_p = int(part.rotate_x_deg)
                            ry_p = int(part.rotate_y_deg)
                            internal_cull = resolver.internal_face_cull_for_model(part.model_ref, part.model)
                            for idx_el, el in enumerate(part.model.elements or []):
                                if not isinstance(el, dict):
                                    continue
                                frm = el.get("from")
                                to = el.get("to")
                                if not (isinstance(frm, list) and isinstance(to, list) and len(frm) == 3 and len(to) == 3):
                                    continue
                                try:
                                    fx, fy, fz = (float(frm[0]), float(frm[1]), float(frm[2]))
                                    txe, tye, tze = (float(to[0]), float(to[1]), float(to[2]))
                                except (TypeError, ValueError):
                                    continue
                                xmin_el = min(fx, txe) / 16.0 - 0.5
                                xmax_el = max(fx, txe) / 16.0 - 0.5
                                ymin_el = min(fy, tye) / 16.0 - 0.5
                                ymax_el = max(fy, tye) / 16.0 - 0.5
                                zmin_el = min(fz, tze) / 16.0 - 0.5
                                zmax_el = max(fz, tze) / 16.0 - 0.5

                                faces_obj = el.get("faces")
                                if not isinstance(faces_obj, dict):
                                    continue
                                frm_t = (fx, fy, fz)
                                to_t = (txe, tye, tze)
                                rot_el = el.get("rotation")

                                for face in FACE_DIRS:
                                    if face in internal_cull.get(idx_el, ()):
                                        continue
                                    face_def = faces_obj.get(face)
                                    if not isinstance(face_def, dict):
                                        continue

                                    face_on_boundary = False
                                    if face == "north":
                                        face_on_boundary = abs(zmin_el + 0.5) < 1e-6
                                    elif face == "south":
                                        face_on_boundary = abs(zmax_el - 0.5) < 1e-6
                                    elif face == "west":
                                        face_on_boundary = abs(xmin_el + 0.5) < 1e-6
                                    elif face == "east":
                                        face_on_boundary = abs(xmax_el - 0.5) < 1e-6
                                    elif face == "down":
                                        face_on_boundary = abs(ymin_el + 0.5) < 1e-6
                                    elif face == "up":
                                        face_on_boundary = abs(ymax_el - 0.5) < 1e-6

                                    if solid_block or face_on_boundary:
                                        n_rot = _rot_xy(face_normals[face], rx_deg=rx_p, ry_deg=ry_p)
                                        nx, ny, nz = n_rot
                                        dx = 1 if nx > 0.5 else (-1 if nx < -0.5 else 0)
                                        dy = 1 if ny > 0.5 else (-1 if ny < -0.5 else 0)
                                        dz = 1 if nz > 0.5 else (-1 if nz < -0.5 else 0)
                                        if (x + dx, y + dy, z + dz) in self._rez_live_solids:
                                            continue

                                    raw_tex = face_def.get("texture")
                                    tex_ref: str | None = raw_tex if isinstance(raw_tex, str) and raw_tex else None
                                    tex_resolved = (
                                        resolver._resolve_texture_ref(tex_ref, part.model.textures) if tex_ref is not None else None
                                    )
                                    jar_rel = resolver._texture_ref_to_jar_rel(tex_resolved) if tex_resolved else None
                                    if jar_rel is None or not texture_source.has(jar_rel):
                                        jar_rel = None

                                    uv_rect: tuple[float, float, float, float] | None = None
                                    uv_obj = face_def.get("uv")
                                    if isinstance(uv_obj, list) and len(uv_obj) == 4:
                                        try:
                                            uv_rect = (
                                                float(uv_obj[0]),
                                                float(uv_obj[1]),
                                                float(uv_obj[2]),
                                                float(uv_obj[3]),
                                            )
                                        except (TypeError, ValueError):
                                            uv_rect = None
                                    if uv_rect is None:
                                        uv_rect = _default_uv_rect_for_face(face, frm=frm_t, to=to_t)

                                    rot_deg = face_def.get("rotation", 0)
                                    rot_deg = int(rot_deg) if isinstance(rot_deg, (int, float)) else 0

                                    tint = (255, 255, 255)
                                    tint_obj = face_def.get("tintindex")
                                    if isinstance(tint_obj, (int, float)):
                                        tint = _tint_rgb(texture_source, block.block_id, int(tint_obj))

                                    quad = _element_face_points(
                                        face,
                                        xmin=xmin_el,
                                        xmax=xmax_el,
                                        ymin=ymin_el,
                                        ymax=ymax_el,
                                        zmin=zmin_el,
                                        zmax=zmax_el,
                                    )
                                    tri_uv = _uv_tri_for_face_rect(face, uv_rect, rotation_deg=rot_deg, quad_points=quad)
                                    quad = _apply_element_rotation(quad, rot_el)
                                    quad_r = [_rot_xy(p, rx_deg=rx_p, ry_deg=ry_p) for p in quad]
                                    quad_w = [(px + tx, py + ty, pz + tz) for (px, py, pz) in quad_r]
                                    p0, p1, p2, p3 = quad_w
                                    normal = _tri_normal(p0, p1, p2)
                                    tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]

                                    if jar_rel is not None:
                                        tex = load_tex_from_jar(texture_source, jar_rel)
                                        if tex is not None:
                                            verts_by_tex.setdefault(jar_rel, []).extend(tri_verts)
                                            norms_by_tex.setdefault(jar_rel, []).extend([*normal, *normal, *normal, *normal, *normal, *normal])
                                            uvs_by_tex.setdefault(jar_rel, []).extend(tri_uv)
                                            cols_by_tex.setdefault(jar_rel, []).extend([*tint, *tint, *tint, *tint, *tint, *tint])
                                            continue

                                    r, g, b = _stable_rgb(block.color_key)
                                    col = (int(r * 255), int(g * 255), int(b * 255))
                                    colored_verts.extend(tri_verts)
                                    colored_norms.extend([*normal, *normal, *normal, *normal, *normal, *normal])
                                    colored_cols.extend([*col, *col, *col, *col, *col, *col])

                        continue

                    for face in FACE_DIRS:
                        dx, dy, dz = neighbor_delta[face]
                        if (x + dx, y + dy, z + dz) in self._rez_live_solids:
                            continue

                        quad = _UNIT_CUBE_FACE_QUADS[face]
                        if base_id == "minecraft:jigsaw":
                            quad = [(px * 1.5, py * 1.5, pz * 1.5) for (px, py, pz) in quad]
                        normal = face_normals[face]
                        normal_r = _rot_xy(normal, rx_deg=rx, ry_deg=ry)

                        quad_r = [_rot_xy(p, rx_deg=rx, ry_deg=ry) for p in quad]
                        quad_w = [(px + tx, py + ty, pz + tz) for (px, py, pz) in quad_r]
                        p0, p1, p2, p3 = quad_w
                        tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]

                        if appearance is not None and texture_source is not None:
                            jar_rel = appearance.face_texture_png_by_dir.get(face) or ""
                            if jar_rel:
                                tex = load_tex_from_jar(texture_source, jar_rel)
                                if tex is not None:
                                    verts_by_tex.setdefault(jar_rel, []).extend(tri_verts)
                                    norms_by_tex.setdefault(jar_rel, []).extend(
                                        [*normal_r, *normal_r, *normal_r, *normal_r, *normal_r, *normal_r]
                                    )
                                    uvs_by_tex.setdefault(jar_rel, []).extend(_UNIT_CUBE_FACE_UV_TRI[face])
                                    cols_by_tex.setdefault(jar_rel, []).extend([255, 255, 255] * 6)
                                    continue

                        r, g, b = _stable_rgb(block.color_key)
                        col = (int(r * 255), int(g * 255), int(b * 255))
                        colored_verts.extend(tri_verts)
                        colored_norms.extend([*normal_r, *normal_r, *normal_r, *normal_r, *normal_r, *normal_r])
                        colored_cols.extend([*col, *col, *col, *col, *col, *col])

                for jar_rel in sorted(verts_by_tex.keys()):
                    tex = tex_cache.get(jar_rel)
                    if tex is None:
                        continue
                    group = group_cache.get(jar_rel)
                    if group is None:
                        group = pyglet.graphics.TextureGroup(tex)
                        group_cache[jar_rel] = group
                    verts = verts_by_tex[jar_rel]
                    norms = norms_by_tex[jar_rel]
                    uvs = uvs_by_tex[jar_rel]
                    cols = cols_by_tex.get(jar_rel) or [255, 255, 255] * (len(verts) // 3)
                    vl = batch.add(
                        len(verts) // 3,
                        gl.GL_TRIANGLES,
                        group,
                        ("v3f/static", verts),
                        ("n3f/static", norms),
                        ("t2f/static", uvs),
                        ("c3B/static", cols),
                    )
                    out_vlists.append(vl)

                if colored_verts:
                    vl = batch.add(
                        len(colored_verts) // 3,
                        gl.GL_TRIANGLES,
                        no_tex_group,
                        ("v3f/static", colored_verts),
                        ("n3f/static", colored_norms),
                        ("c3B/static", colored_cols),
                    )
                    out_vlists.append(vl)

            def _ui_i(self, px: float, *, min_value: int = 1) -> int:
                return max(min_value, int(round(float(px) * float(getattr(self, "_ui_font_scale", 1.0)))))

            def _ui_f(self, px: float) -> float:
                return float(px) * float(getattr(self, "_ui_font_scale", 1.0))

            def _compute_log_panel_height(self) -> int:
                if self._log_collapsed:
                    return self._ui_i(34.0)
                return int(min(320, max(140, self.height * 0.28)))

            def _search_ui_visible(self) -> bool:
                return self._search_active or bool(self._search_query)

            def _scan_dataset_root(self, root: Path) -> list[Path]:
                out: list[Path] = []
                try:
                    candidates = list(root.iterdir())
                except Exception:
                    return [datapack_path]

                def is_datapack_dir(p: Path) -> bool:
                    try:
                        return p.is_dir() and (p / "pack.mcmeta").is_file() and (p / "data").is_dir()
                    except Exception:
                        return False

                for p in sorted(candidates, key=lambda p: p.name.lower()):
                    try:
                        if p.is_file() and p.suffix.lower() in {".zip", ".jar"}:
                            out.append(p)
                            continue
                        if is_datapack_dir(p):
                            out.append(p)
                    except Exception:
                        continue
                if datapack_path not in out:
                    out.insert(0, datapack_path)
                return out

            def _active_labels(self) -> list[str]:
                if self._browser_mode == "datasets":
                    return self._dataset_labels
                if self._browser_mode == "structures":
                    return labels
                return pool_labels

            def _dataset_index_for_path(self, path: Path) -> int | None:
                for i, p in enumerate(self._dataset_paths):
                    if p == path:
                        return i
                return None

            def _active_selected_index(self) -> int:
                if self._browser_mode == "datasets":
                    return int(self._dataset_selected)
                if self._browser_mode == "structures":
                    return int(self._structures_selected)
                return int(self.selected)

            def _set_active_selected_index(self, idx: int) -> None:
                if self._browser_mode == "datasets":
                    self._dataset_selected = max(0, min(len(self._dataset_labels) - 1, int(idx)))
                    return
                if self._browser_mode == "structures":
                    self._structures_selected = max(0, min(len(labels) - 1, int(idx)))
                    return
                self.selected = max(0, min(len(pool_labels) - 1, int(idx)))

            def _reset_search_state(self) -> None:
                self._search_active = False
                self._search_query = ""
                self._update_search_ui()

            def _refresh_browser_header(self) -> None:
                if self._browser_mode == "datasets":
                    self.title.text = f"{self._dataset_root.name}  ({len(self._dataset_labels)} datapacks)"
                    self.set_caption(f"EnderTerm: datapack-view  {self._dataset_root.name}")
                    return
                if self._browser_mode == "structures":
                    self.title.text = f"NBT  {datapack_path.name}  ({len(labels)} structures)"
                    return
                self.title.text = f"Pool  {datapack_path.name}  ({len(pool_labels)} template pools)"

            def _enter_dataset_browser(self) -> None:
                if self._rez_active:
                    return
                if self._browser_mode == "datasets":
                    return
                if len(self._dataset_paths) <= 1:
                    return
                self._browser_saved_mode = "structures" if self._browser_mode == "structures" else "pools"
                self._browser_saved_selected = int(self._active_selected_index())
                self._browser_saved_scroll_pos_f = float(self._scroll_pos_f)
                self._browser_mode = "datasets"
                self._reset_search_state()
                # Keep selection on current datapack if possible.
                idx = self._dataset_index_for_path(datapack_path)
                if idx is not None:
                    self._dataset_selected = idx
                self._scroll_pos_f = 0.0
                self.scroll_top = 0
                self._update_filtered_indices()
                self._layout_ui()
                self._update_list_labels()
                self._refresh_browser_header()

            def _exit_dataset_browser(self) -> None:
                if self._browser_mode != "datasets":
                    return
                self._browser_mode = self._browser_saved_mode
                self._reset_search_state()
                self._scroll_pos_f = float(self._browser_saved_scroll_pos_f)
                self.scroll_top = int(self._scroll_pos_f)
                if self._browser_mode == "structures":
                    self._structures_selected = max(0, min(len(labels) - 1, int(self._browser_saved_selected)))
                else:
                    self.selected = max(0, min(len(pool_labels) - 1, int(self._browser_saved_selected)))
                self._update_filtered_indices()
                self._layout_ui()
                self._update_list_labels()
                self._refresh_browser_header()
                self._refresh_structure_caption()

            def _toggle_structure_browser_mode(self) -> None:
                if self._rez_active:
                    return
                if self._browser_mode == "datasets":
                    return

                if self._browser_mode == "structures":
                    self._browser_scroll_pos_structures = float(self._scroll_pos_f)
                    self._browser_mode = "pools"
                    self._scroll_pos_f = float(self._browser_scroll_pos_pools)
                    self.scroll_top = int(self._scroll_pos_f)
                    self.selected = max(0, min(len(pool_labels) - 1, int(self.selected)))
                else:
                    self._browser_scroll_pos_pools = float(self._scroll_pos_f)
                    self._browser_mode = "structures"
                    self._scroll_pos_f = float(self._browser_scroll_pos_structures)
                    self.scroll_top = int(self._scroll_pos_f)
                    self._structures_selected = max(0, min(len(labels) - 1, int(self._structures_selected)))

                self._reset_search_state()
                self._update_filtered_indices()
                self._layout_ui()
                self._update_list_labels()
                self._refresh_browser_header()

            def _loaded_mode_labels(self) -> list[str]:
                """Return the label list backing the currently loaded viewer mode."""
                if self._loaded_mode == "worldgen":
                    return worldgen_labels
                if self._loaded_mode == "pools":
                    return pool_labels
                return labels

            def _refresh_structure_caption(self) -> None:
                depth = len(self.jigsaw_seeds)
                depth_suffix = f" depth={depth}" if depth else ""
                active_labels = self._loaded_mode_labels()
                idx = int(self._loaded_index)
                if 0 <= idx < len(active_labels):
                    self.set_caption(f"EnderTerm: datapack-view{depth_suffix}  {idx + 1}/{len(active_labels)}  {active_labels[idx]}")
                    return
                self.set_caption(f"EnderTerm: datapack-view{depth_suffix}  {datapack_path.name}")

            def _switch_datapack(self, path: Path) -> bool:
                nonlocal datapack_path, items, labels, zip_file, load_root_by_index, dp_source, jigsaw_index, pack_stack, pool_items, pool_labels, worldgen_labels

                try:
                    path = path.resolve()
                except Exception:
                    path = Path(path)

                if path == datapack_path:
                    return True

                next_items: list[tuple[str, object]]
                next_zip: zipfile.ZipFile | None

                if path.is_file() and path.suffix.lower() in {".zip", ".jar"}:
                    next_zip = zipfile.ZipFile(path, "r")
                    next_items = list(_iter_structure_entries_in_datapack_zip(next_zip))
                elif path.is_dir():
                    next_items = list(_iter_structure_paths_in_datapack_dir(path))
                    next_zip = None
                else:
                    self._expansion_report = [f"Not a datapack: {path}"]
                    self._update_status()
                    return False
                next_loader = _make_structure_root_loader(items=next_items, zip_file=next_zip)

                if not next_items:
                    try:
                        if next_zip is not None:
                            next_zip.close()
                    except Exception:
                        pass
                    self._expansion_report = [f"No structure .nbt files found in {path.name}"]
                    self._update_status()
                    return False

                try:
                    if zip_file is not None:
                        zip_file.close()
                except Exception:
                    pass

                datapack_path = path
                items = next_items
                labels = [out_rel.removesuffix(".usdz") for (out_rel, _) in items]
                zip_file = next_zip
                load_root_by_index = next_loader

                dp_source = DatapackSource(datapack_path, zip_file)
                pack_stack = PackStack(work_dir=work_pack_dir, vendors=[dp_source])
                pack_stack.ensure_work_pack()
                jigsaw_index = JigsawDatapackIndex(pack_stack.source)
                pool_items = list_template_pools(pack_stack)
                pool_labels = [pid for pid, _owner in pool_items]
                worldgen_labels = list_worldgen_jigsaw_structures(pack_stack)

                self._dataset_root = datapack_path.parent
                self._dataset_paths = self._scan_dataset_root(self._dataset_root)
                self._dataset_labels = [p.name for p in self._dataset_paths]
                self._dataset_selected = 0
                idx = self._dataset_index_for_path(datapack_path)
                if idx is not None:
                    self._dataset_selected = idx

                return True

            def _open_selected_dataset(self) -> None:
                if self._rez_active or not self._dataset_paths:
                    return
                idx = max(0, min(len(self._dataset_paths) - 1, int(self._dataset_selected)))
                path = self._dataset_paths[idx]
                if not self._switch_datapack(path):
                    return

                self._browser_mode = "pools"
                self._reset_search_state()
                self._scroll_pos_f = 0.0
                self.scroll_top = 0
                self.selected = 0
                self._worldgen_selected = 0
                self._update_filtered_indices()
                self._layout_ui()
                self._refresh_browser_header()
                if pool_labels:
                    self._load_pool_index(0, reset_view=True)
                else:
                    self._load_index(0, reset_view=True)

            def _update_filtered_indices(self) -> None:
                active = self._active_labels()
                q = self._search_query.strip().lower()
                if not q:
                    self._filtered_indices = list(range(len(active)))
                    self._filtered_pos_by_index = {i: i for i in range(len(active))}
                    return

                tokens = [t for t in q.split() if t]
                indices: list[int] = []
                for i, lbl in enumerate(active):
                    l = lbl.lower()
                    if all(t in l for t in tokens):
                        indices.append(i)
                self._filtered_indices = indices
                self._filtered_pos_by_index = {idx: pos for pos, idx in enumerate(indices)}

            def _list_items_top_y(self) -> float:
                y_top = float(self.height - self.header_h)
                if self._search_ui_visible():
                    y_top = float(self.search_bg.y) - self._ui_f(6.0)
                return y_top

            def _selected_list_pos(self) -> int | None:
                return self._filtered_pos_by_index.get(self._active_selected_index())

            def _load_list_pos(self, pos: int) -> None:
                indices = self._filtered_indices
                if not indices:
                    return
                # Any explicit selection change re-enables "follow selection"
                # scrolling (free scroll is a mouse-wheel override).
                self._scroll_follow_selection = True
                pos = max(0, min(len(indices) - 1, pos))
                idx = indices[pos]
                if self._browser_mode == "datasets":
                    if idx != self._dataset_selected:
                        self._dataset_selected = idx
                        self._update_list_labels(ensure_selection_visible=False)
                    return
                if self._browser_mode == "pools":
                    if idx != int(self.selected):
                        self._load_pool_index(idx, reset_view=False)
                    return
                if self._browser_mode == "structures":
                    if idx != int(self._structures_selected):
                        self._load_index(idx, reset_view=False)
                    return

            def _set_log_collapsed(self, collapsed: bool, *, animate: bool = True) -> None:
                if collapsed == self._log_collapsed and self._log_panel_tween is None:
                    return
                self._log_collapsed = collapsed
                self.log_toggle_label.text = "▸" if self._log_collapsed else "▾"
                target = float(self._compute_log_panel_height())

                # Terminal sidebar: animate by changing how many rows are allocated
                # to the log box (no font scaling).
                try:
                    cur_p = float(getattr(self, "_term_log_open_p", 0.0))
                except Exception:
                    cur_p = 0.0
                cur_p = max(0.0, min(1.0, cur_p))
                end_p = 0.0 if collapsed else 1.0
                if not animate:
                    self._term_log_open_p = float(end_p)
                    self._term_log_open_tween = None
                else:
                    now = time.monotonic()
                    self._term_log_open_tween = Tween(now, 0.22, start=float(cur_p), end=float(end_p), ease=ease_smoothstep)

                if not animate:
                    self._log_panel_tween = None
                    self.log_panel_h = target
                    self._layout_ui()
                    self._update_status()
                    return
                now = time.monotonic()
                self._log_panel_tween = Tween(now, 0.22, start=float(self.log_panel_h), end=target, ease=ease_smoothstep)

            def _tick_log_panel_tween(self) -> None:
                tween = self._log_panel_tween
                if tween is None:
                    return
                now = time.monotonic()
                new_h = tween.value(now)
                if abs(new_h - float(self.log_panel_h)) > 0.25:
                    self.log_panel_h = new_h
                    self._layout_ui(ensure_labels=False)
                if tween.done(now):
                    self.log_panel_h = tween.end
                    self._log_panel_tween = None
                    self._layout_ui()
                    self._update_status()

            def _safe_export_stem(self, label: str) -> str:
                # Make a filesystem-friendly name.
                cleaned: list[str] = []
                last_dash = False
                for ch in label:
                    ok = ch.isalnum() or ch in {"-", "_"}
                    if ok:
                        cleaned.append(ch)
                        last_dash = False
                    else:
                        if not last_dash:
                            cleaned.append("-")
                            last_dash = True
                stem = "".join(cleaned).strip("-") or "structure"
                if len(stem) > 80:
                    stem = stem[:80].rstrip("-")
                return stem

            def _export_current(self) -> None:
                if self._current_structure is None:
                    return
                depth = len(self.jigsaw_seeds)
                seed = self.jigsaw_seeds[-1] if depth else None
                stem = self._safe_export_stem(self._current_label)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                depth_part = f"d{depth}"
                seed_part = f"-s{seed:08x}" if seed is not None else ""
                filename = f"{stem}-{depth_part}{seed_part}-{stamp}.usdz"
                out_path = self.export_dir / filename

                try:
                    export_structure = apply_render_mode(self._current_structure, mode, auto_threshold)
                    if textured and texture_source is not None:
                        usda_text, extra_files = structure_to_usda_textured(export_structure, texture_source)
                        write_usdz(out_path, usda_text, extra_files=extra_files)
                    else:
                        usda_text = structure_to_usda_text(export_structure)
                        write_usdz(out_path, usda_text)
                except Exception as e:
                    self._expansion_report.append(f"Export failed: {e}")
                    self._update_status()
                    return

                self._last_export = out_path
                open_in_viewer(out_path)
                self._expansion_report.append(f"Exported: {out_path}")
                self._update_status()

            def _export_current_nbt(self) -> None:
                if self._current_structure is None:
                    return
                depth = len(self.jigsaw_seeds)
                seed = self.jigsaw_seeds[-1] if depth else None
                stem = self._safe_export_stem(self._current_label)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                depth_part = f"d{depth}"
                seed_part = f"-s{seed:08x}" if seed is not None else ""
                filename = f"{stem}-{depth_part}{seed_part}-{stamp}.nbt"
                out_path = self.export_dir / filename

                try:
                    root, offset = structure_to_nbt_root(self._current_structure)
                    nbtlib.File(root).save(out_path, gzipped=True)  # type: ignore[arg-type]
                except Exception as e:
                    self._expansion_report.append(f"NBT export failed: {e}")
                    self._update_status()
                    return

                self._last_export = out_path
                shift = (-offset[0], -offset[1], -offset[2])
                extra = ""
                if shift != (0, 0, 0):
                    extra = f" (shift {shift[0]},{shift[1]},{shift[2]})"
                self._expansion_report.append(f"Exported NBT: {out_path.name}{extra}")
                self._update_status()

            def _compute_pivot_center(self, template: StructureTemplate) -> tuple[float, float, float]:
                blocks = [b for b in template.blocks if _block_id_base(b.block_id) != "minecraft:structure_void"]
                if blocks:
                    xs = [b.pos[0] for b in blocks]
                    ys = [b.pos[1] for b in blocks]
                    zs = [b.pos[2] for b in blocks]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    min_z, max_z = min(zs), max(zs)
                    return ((min_x + max_x + 1) / 2.0, (min_y + max_y + 1) / 2.0, (min_z + max_z + 1) / 2.0)
                sx, sy, sz = template.size
                return (sx / 2.0, sy / 2.0, sz / 2.0)

            def _env_preset(self) -> EnvironmentPreset:
                if not ENVIRONMENT_PRESETS:
                    return EnvironmentPreset("space", (0.0, 0.0, 0.0))
                idx = int(self._env_index) % len(ENVIRONMENT_PRESETS)
                return ENVIRONMENT_PRESETS[idx]

            def _autopick_environment(self, *, hint: str) -> None:
                tmpl = self._base_template
                desired = infer_environment_preset_name(
                    hint=str(hint),
                    template_id=(tmpl.template_id if tmpl is not None else None),
                    block_ids=((b.block_id for b in tmpl.blocks) if tmpl is not None else None),
                )
                if desired == self._env_preset().name:
                    return
                idx: int | None = None
                for i, p in enumerate(ENVIRONMENT_PRESETS):
                    if getattr(p, "name", "") == desired:
                        idx = int(i)
                        break
                if idx is None:
                    return
                now = time.monotonic()
                cur_rgb = self._env_clear_rgb_now(now)
                self._env_index = int(idx)
                self._begin_environment_transition(now=now, from_rgb=cur_rgb)
                # Reroll the underlying noise field each time we auto-pick, so
                # similar structures don't always land on the exact same hills.
                self._env_shape_nonce = int(secrets.randbits(32))
                # Clear now, but let _rebuild_scene() call _update_environment()
                # after the new model is applied to avoid rebuilding against the
                # old bounds.
                self._env_clear_geometry()

            def _begin_environment_transition(
                self,
                *,
                now: float | None = None,
                from_rgb: tuple[float, float, float] | None = None,
            ) -> None:
                if now is None:
                    now = time.monotonic()
                try:
                    dur_s = float(param_store.get("fx.channel_change.duration_s"))
                except Exception:
                    dur_s = 0.65
                if not math.isfinite(dur_s):
                    dur_s = 0.65
                dur_s = max(0.05, min(3.0, float(dur_s)))

                start = from_rgb if from_rgb is not None else self._env_clear_rgb_now(float(now))
                end = self._env_preset().sky_rgb
                if start == end:
                    self._env_sky_tween = None
                    self._env_sky_rgb_from = end
                    self._env_sky_rgb_to = end
                    return
                self._env_sky_rgb_from = start
                self._env_sky_rgb_to = end
                self._env_sky_tween = Tween(float(now), float(dur_s), start=0.0, end=1.0, ease=ease_smoothstep)

            def _env_clear_rgb_now(self, now: float) -> tuple[float, float, float]:
                tween = self._env_sky_tween
                if tween is None:
                    return self._env_preset().sky_rgb
                try:
                    t = float(tween.value(float(now)))
                except Exception:
                    self._env_sky_tween = None
                    return self._env_preset().sky_rgb

                r0, g0, b0 = self._env_sky_rgb_from
                r1, g1, b1 = self._env_sky_rgb_to
                tt = max(0.0, min(1.0, float(t)))
                r = float(r0) * (1.0 - tt) + float(r1) * tt
                g = float(g0) * (1.0 - tt) + float(g1) * tt
                b = float(b0) * (1.0 - tt) + float(b1) * tt
                if tween.done(float(now)):
                    self._env_sky_tween = None
                    self._env_sky_rgb_from = self._env_sky_rgb_to
                return (float(r), float(g), float(b))

            def _env_clear_rgb(self) -> tuple[float, float, float]:
                return self._env_clear_rgb_now(time.monotonic())

            def _current_model_bounds_i(self) -> tuple[int, int, int, int, int, int] | None:
                if self._rez_active and self._rez_live_bounds is not None:
                    return self._rez_live_bounds
                return self._pick_bounds_i

            def _infer_env_surface_y(self) -> int:
                bounds = self._current_model_bounds_i()
                if bounds is None:
                    return 0
                _min_x, min_y, _min_z, _max_x, _max_y, _max_z = bounds
                if not self.jigsaw_seeds and str(getattr(self, "_base_projection", "")) == "rigid":
                    return int(min_y)

                solids: set[Vec3i] = set()
                if self._rez_active and self._rez_live_solids:
                    solids = {p for p in self._rez_live_solids}
                else:
                    cur = self._current_structure
                    if cur is not None:
                        for b in cur.blocks:
                            base_id = _block_id_base(b.block_id)
                            if base_id in {"minecraft:jigsaw", "minecraft:structure_void"}:
                                continue
                            if resolver is None:
                                solids.add(b.pos)
                                continue
                            bm = resolver.resolve_block_model(b.block_id)
                            if bm is None or _block_model_is_full_cube(bm):
                                solids.add(b.pos)
                                continue
                            if _block_model_bottom_coverage_frac(bm) >= 0.60:
                                solids.add(b.pos)

                if not solids:
                    return int(min_y)

                counts: dict[int, int] = {}
                for x, y, z in solids:
                    if (int(x), int(y) - 1, int(z)) not in solids:
                        iy = int(y)
                        counts[iy] = int(counts.get(iy, 0)) + 1

                if not counts:
                    return int(min_y)

                max_count = max(counts.values())
                min_support = max(8, int(round(float(max_count) * 0.35)))
                candidates = [y for (y, c) in counts.items() if int(c) >= int(min_support)]
                if candidates:
                    # Prefer a higher "dominant" layer if there are multiple strong
                    # bottoms (keeps basements from anchoring the whole scene).
                    return int(max(candidates))

                best_y, _best_count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
                return int(best_y)

            def _env_clear_geometry(self, *, keep_anchor: bool = False) -> None:
                if not keep_anchor:
                    self._env_template_id = None
                    self._env_base_y = None
                    self._env_height_seed = 0
                    self._env_height_anchor_off = 0
                    self._env_height_origin_x = 0
                    self._env_height_origin_z = 0
                for vl in list(self._env_tex_vlists.values()):
                    try:
                        delete = getattr(vl, "delete", None)
                        if callable(delete):
                            delete()
                    except Exception:
                        pass
                self._env_tex_vlists = {}
                self._env_tex_counts = {}
                if self._env_colored_vlist is not None:
                    try:
                        delete = getattr(self._env_colored_vlist, "delete", None)
                        if callable(delete):
                            delete()
                    except Exception:
                        pass
                self._env_colored_vlist = None
                self._env_colored_count = 0
                self._env_batch = pyglet.graphics.Batch()
                self._env_patches = set()
                self._env_top_y_by_xz = {}
                self._env_decor_by_pos = {}
                for vl in list(self._env_decor_tex_vlists.values()):
                    try:
                        delete = getattr(vl, "delete", None)
                        if callable(delete):
                            delete()
                    except Exception:
                        pass
                self._env_decor_tex_vlists = {}
                self._env_decor_tex_counts = {}
                if self._env_decor_colored_vlist is not None:
                    try:
                        delete = getattr(self._env_decor_colored_vlist, "delete", None)
                        if callable(delete):
                            delete()
                    except Exception:
                        pass
                self._env_decor_colored_vlist = None
                self._env_decor_colored_count = 0
                self._env_decor_batch = pyglet.graphics.Batch()
                self._env_patch_queue.clear()
                self._env_patch_pending = set()
                self._env_patch_best_prio = {}
                self._env_patch_fade = {}
                self._env_patch_ranges = {}
                self._env_patch_spans = {}
                self._env_patch_has_transparency = {}
                self._env_xz_bounds = None
                self._env_extent = None

            def _env_request_patches_for_positions(self, positions: Iterable[Vec3i]) -> None:
                step = int(self._env_patch_step)
                radius = int(self._env_ground_radius)
                if step <= 0:
                    step = 10
                if radius < 0:
                    radius = 0
                radius_p = int(math.ceil(float(radius) / float(step))) if radius else 0
                radius_p2 = int(radius_p) * int(radius_p)

                occupied: set[tuple[int, int]] = set()
                density: dict[tuple[int, int], int] = {}
                for x, _y, z in positions:
                    key0 = (int(x) // step, int(z) // step)
                    occupied.add(key0)
                    density[key0] = int(density.get(key0, 0)) + 1
                if not occupied:
                    return

                heat: dict[tuple[int, int], int] = {}
                blur_r = max(1, min(6, int(max(1, radius_p // 2))))
                if density:
                    for (px, pz), count in density.items():
                        for dx in range(-blur_r, blur_r + 1):
                            for dz in range(-blur_r, blur_r + 1):
                                w = (blur_r + 1) - max(abs(dx), abs(dz))
                                if w <= 0:
                                    continue
                                k2 = (int(px) + int(dx), int(pz) + int(dz))
                                heat[k2] = int(heat.get(k2, 0)) + int(count) * int(w)

                # Priority queue: dense/central patches first (low-frequency "heatmap"),
                # then nearest-to-build.
                best_d2: dict[tuple[int, int], int] = {}
                for opx, opz in occupied:
                    for dx in range(-radius_p, radius_p + 1):
                        for dz in range(-radius_p, radius_p + 1):
                            d2 = int(dx) * int(dx) + int(dz) * int(dz)
                            if d2 > radius_p2:
                                continue
                            key = (int(opx) + int(dx), int(opz) + int(dz))
                            if key in self._env_patches:
                                continue
                            cur = best_d2.get(key)
                            if cur is None or d2 < cur:
                                best_d2[key] = d2

                if not best_d2:
                    return
                for key, near_d2 in best_d2.items():
                    h = int(heat.get(key, 0))
                    prio = (-int(h), int(near_d2))
                    prev = self._env_patch_best_prio.get(key)
                    if prev is not None and prio >= prev:
                        continue
                    self._env_patch_best_prio[key] = prio
                    self._env_patch_pending.add(key)
                    self._env_patch_seq += 1
                    heapq.heappush(
                        self._env_patch_queue,
                        (int(prio[0]), int(prio[1]), int(self._env_patch_seq), key),
                    )

            def _update_environment(self, *, positions: Iterable[Vec3i] | None = None) -> None:
                bounds = self._current_model_bounds_i()
                preset = self._env_preset()
                if preset.is_space() or bounds is None:
                    self._env_clear_geometry()
                    return

                min_x, min_y, min_z, max_x, _max_y, max_z = bounds
                template_id = self._base_template.template_id if self._base_template is not None else None
                if self._env_base_y is None or template_id != self._env_template_id:
                    self._env_clear_geometry()
                    self._env_template_id = template_id
                    self._env_height_seed = (
                        int(_stable_seed("env-height", preset.name, int(self._env_shape_nonce))) & 0xFFFFFFFF
                    )
                    self._env_base_y = int(self._infer_env_surface_y())
                    anchor_x = (int(min_x) + int(max_x)) // 2
                    anchor_z = (int(min_z) + int(max_z)) // 2
                    # Pick a terrain origin offset that avoids anchoring the scene
                    # in a pit (prefer peak-ish height at the anchor).
                    rng = random.Random(_stable_seed("env-origin", preset.name, int(self._env_height_seed)))
                    span = int(max(512, min(16_384, float(self._env_terrain_scale) * 32.0)))
                    samples: list[tuple[int, int]] = [(0, 0)]
                    while len(samples) < 10:
                        ox = int(rng.randint(-span, span))
                        oz = int(rng.randint(-span, span))
                        if (ox, oz) in samples:
                            continue
                        samples.append((ox, oz))

                    def height_at(x: int, z: int, *, ox: int, oz: int) -> int:
                        return env_height_offset(
                            preset=preset.name,
                            seed=int(self._env_height_seed),
                            x=int(x) + int(ox),
                            z=int(z) + int(oz),
                            amp=int(self._env_terrain_amp),
                            scale=float(self._env_terrain_scale),
                            octaves=int(self._env_terrain_octaves),
                            lacunarity=float(self._env_terrain_lacunarity),
                            h=float(self._env_terrain_h),
                            ridged_offset=float(self._env_terrain_ridged_offset),
                            ridged_gain=float(self._env_terrain_ridged_gain),
                        )

                    r = int(max(6, min(96, round(float(self._env_terrain_scale) * 0.22))))
                    # Prefer terrain that slopes toward the camera (for nicer
                    # framing). We compare the horizontal component of the local
                    # surface normal against the camera->anchor direction.
                    cam_dir_x = 0.0
                    cam_dir_z = 0.0
                    try:
                        yaw_r = math.radians(float(self.yaw))
                        pitch_r = math.radians(float(self.pitch))
                        cyaw = math.cos(yaw_r)
                        syaw = math.sin(yaw_r)
                        cpitch = math.cos(pitch_r)
                        spitch = math.sin(pitch_r)
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
                        cam_z = float(self._orbit_target[2]) + z2
                        dxz_x = cam_x - float(anchor_x)
                        dxz_z = cam_z - float(anchor_z)
                        mag = math.hypot(dxz_x, dxz_z)
                        if mag > 1e-6:
                            cam_dir_x = dxz_x / mag
                            cam_dir_z = dxz_z / mag
                    except Exception:
                        cam_dir_x = 0.0
                        cam_dir_z = 0.0

                    # Camera-facing slope bias strength. Larger values make the
                    # environment "lean" into your current view more strongly.
                    # Peak-avoidance still matters, but can be overridden by a
                    # good camera-facing slope.
                    peak_w = 1.0
                    if abs(cam_dir_x) > 0.0 or abs(cam_dir_z) > 0.0:
                        peak_w = 0.45

                    face_w = float(self._env_terrain_amp) * 0.85
                    if face_w < 0.0:
                        face_w = 0.0
                    if face_w < 4.0:
                        face_w = 4.0
                    if face_w > 96.0:
                        face_w = 96.0
                    neigh = [
                        (r, 0),
                        (-r, 0),
                        (0, r),
                        (0, -r),
                        (r, r),
                        (r, -r),
                        (-r, r),
                        (-r, -r),
                    ]

                    best_ox = 0
                    best_oz = 0
                    best_score = -1.0e30
                    for ox, oz in samples:
                        c = float(height_at(anchor_x, anchor_z, ox=ox, oz=oz))
                        vals = [float(height_at(anchor_x + dx, anchor_z + dz, ox=ox, oz=oz)) for (dx, dz) in neigh]
                        avg = sum(vals) / float(len(vals)) if vals else 0.0
                        mx = max(vals) if vals else avg
                        # Favor anchor points that are "peak-ish": above their
                        # neighborhood, and also high in absolute terms.
                        peak_score = (c - avg) * 1.2 + c * 0.35 + (c - mx) * 0.25
                        score = float(peak_score) * float(peak_w)
                        if face_w > 0.0 and (abs(cam_dir_x) > 0.0 or abs(cam_dir_z) > 0.0):
                            try:
                                hx_p = float(height_at(anchor_x + r, anchor_z, ox=ox, oz=oz))
                                hx_m = float(height_at(anchor_x - r, anchor_z, ox=ox, oz=oz))
                                hz_p = float(height_at(anchor_x, anchor_z + r, ox=ox, oz=oz))
                                hz_m = float(height_at(anchor_x, anchor_z - r, ox=ox, oz=oz))
                                nx = -(hx_p - hx_m)
                                nz = -(hz_p - hz_m)
                                nmag = math.hypot(nx, nz)
                                if nmag > 1e-6:
                                    nx /= nmag
                                    nz /= nmag
                                    facing = nx * cam_dir_x + nz * cam_dir_z
                                    score += float(facing) * float(face_w)
                            except Exception:
                                pass
                        if score > best_score:
                            best_score = score
                            best_ox = int(ox)
                            best_oz = int(oz)

                    self._env_height_origin_x = int(best_ox)
                    self._env_height_origin_z = int(best_oz)
                    self._env_height_anchor_off = height_at(
                        anchor_x,
                        anchor_z,
                        ox=int(self._env_height_origin_x),
                        oz=int(self._env_height_origin_z),
                    )
                self._env_xz_bounds = (int(min_x), int(max_x), int(min_z), int(max_z))
                if positions is None:
                    if self._rez_active and self._rez_live_positions:
                        positions = self._rez_live_positions
                    else:
                        positions = self._pick_positions
                if positions:
                    self._env_request_patches_for_positions(positions)

            def _env_maybe_expand_extent(self, extent: tuple[int, int, int, int]) -> None:
                old = self._env_extent
                if old is None:
                    self._env_extent = extent
                    self._env_enqueue_patches_for_extent(extent)
                    return
                x0 = min(int(old[0]), int(extent[0]))
                x1 = max(int(old[1]), int(extent[1]))
                z0 = min(int(old[2]), int(extent[2]))
                z1 = max(int(old[3]), int(extent[3]))
                merged = (x0, x1, z0, z1)
                if merged == old:
                    return
                self._env_extent = merged
                self._env_enqueue_patches_for_extent(merged)

            def _env_enqueue_patches_for_extent(self, extent: tuple[int, int, int, int]) -> None:
                step = int(self._env_patch_step)
                x0, x1, z0, z1 = extent
                px0 = int(x0) // step
                px1 = int(x1) // step
                pz0 = int(z0) // step
                pz1 = int(z1) // step
                for px in range(px0, px1 + 1):
                    for pz in range(pz0, pz1 + 1):
                        key = (px, pz)
                        if key in self._env_patches or key in self._env_patch_pending:
                            continue
                        self._env_patch_pending.add(key)
                        self._env_patch_best_prio[key] = (0, 0)
                        self._env_patch_seq += 1
                        heapq.heappush(self._env_patch_queue, (0, 0, int(self._env_patch_seq), key))

            def _env_height_offset(self, x: int, z: int) -> int:
                preset = self._env_preset().name
                return env_height_offset(
                    preset=preset,
                    seed=int(self._env_height_seed),
                    x=int(x) + int(self._env_height_origin_x),
                    z=int(z) + int(self._env_height_origin_z),
                    amp=int(self._env_terrain_amp),
                    scale=float(self._env_terrain_scale),
                    octaves=int(self._env_terrain_octaves),
                    lacunarity=float(self._env_terrain_lacunarity),
                    h=float(self._env_terrain_h),
                    ridged_offset=float(self._env_terrain_ridged_offset),
                    ridged_gain=float(self._env_terrain_ridged_gain),
                )

            def _env_build_patch(self, key: tuple[int, int]) -> None:
                preset = self._env_preset()
                if preset.is_space() or self._env_base_y is None:
                    return

                perf = getattr(self, "_perf_enabled", False)
                t0 = time.perf_counter() if perf else 0.0
                t_tops = float(t0)
                t_faces = float(t0)

                top_id = preset.top_block_id or "minecraft:stone"
                fill_id = preset.fill_block_id or top_id
                deep_id = preset.deep_block_id or fill_id
                surface_y = int(self._env_base_y)
                bottom_y_base = int(self._env_ground_bottom)
                anchor_off = int(self._env_height_anchor_off)
                black_id = "minecraft:black_concrete"
                if resolver is not None and resolver.resolve_block_appearance(black_id) is None:
                    black_id = "minecraft:stone"

                strata: list[tuple[int, str]] = []
                layer_ids: set[str] = set()
                desert_rock_palette: list[str] = []
                if preset.name == "grassy_hills":
                    strata = []
                    grass_id = "minecraft:grass_block[snowy=false]"
                    dirt_id = "minecraft:dirt"
                    rock_palette = [
                        "minecraft:stone",
                        "minecraft:andesite",
                        "minecraft:diorite",
                        "minecraft:granite",
                        "minecraft:deepslate",
                    ]
                    if resolver is not None:
                        rock_palette = [bid for bid in rock_palette if resolver.resolve_block_appearance(bid) is not None] or [
                            "minecraft:stone"
                        ]
                    layer_ids = {grass_id, dirt_id, black_id, *rock_palette}
                elif preset.name == "desert":
                    desert_rock_palette = [
                        "minecraft:stone",
                        "minecraft:andesite",
                        "minecraft:diorite",
                        "minecraft:granite",
                        "minecraft:deepslate",
                    ]
                    if resolver is not None:
                        desert_rock_palette = [
                            bid for bid in desert_rock_palette if resolver.resolve_block_appearance(bid) is not None
                        ] or ["minecraft:stone"]
                    strata = [
                        (0, "minecraft:sand"),
                        (8, "minecraft:sandstone"),
                        (10**9, "minecraft:stone"),
                    ]
                    layer_ids = {black_id, *(bid for (_max_depth, bid) in strata), *desert_rock_palette}
                else:
                    strata = [
                        (0, top_id),
                        (3, fill_id),
                        (10**9, deep_id),
                    ]
                    layer_ids = {black_id, *(bid for (_max_depth, bid) in strata)}

                decor_cfg = self._env_decor_cfg.get(preset.name)
                if isinstance(decor_cfg, EnvironmentDecorConfig) and decor_cfg.blocks and decor_cfg.density > 0.0:
                    for b in decor_cfg.blocks:
                        layer_ids.add(b.block_id)
                        alias = _ENV_DECOR_ALIASES.get(b.block_id)
                        if alias:
                            layer_ids.add(alias)

                strip_fade_h = max(0, int(param_store.get_int("env.ground.strip_fade.height")))
                strip_fade_levels = max(2, int(param_store.get_int("env.ground.strip_fade.levels")))
                strip_side_alpha_cache: dict[int, int] = {}

                def _strip_target_alpha(face: str, *, y: int) -> int:
                    if strip_fade_h <= 0:
                        return 255
                    if face == "up":
                        return 255
                    if face in {"north", "south", "west", "east"}:
                        return _strip_fade_side_alpha_cached(
                            int(y),
                            bottom_y_base=int(bottom_y_base),
                            strip_fade_h=int(strip_fade_h),
                            strip_fade_levels=int(strip_fade_levels),
                            cache=strip_side_alpha_cache,
                        )
                    return 255

                def _hash01_2d(seed: int, ix: int, iz: int) -> float:
                    v = (int(ix) * 0x9E3779B1) ^ (int(iz) * 0x85EBCA77) ^ int(seed)
                    v &= 0xFFFFFFFF
                    v ^= v >> 16
                    v = (v * 0x7FEB352D) & 0xFFFFFFFF
                    v ^= v >> 15
                    v = (v * 0x846CA68B) & 0xFFFFFFFF
                    v ^= v >> 16
                    return float(v) / 4294967296.0

                def _smoothstep(t: float) -> float:
                    t = 0.0 if t <= 0.0 else (1.0 if t >= 1.0 else t)
                    return t * t * (3.0 - 2.0 * t)

                def _lerp(a: float, b: float, t: float) -> float:
                    return a + (b - a) * t

                def _value_noise01(seed: int, xf: float, zf: float, s: float) -> float:
                    if s <= 1e-6:
                        s = 1.0
                    fx = xf / s
                    fz = zf / s
                    ix0 = int(math.floor(fx))
                    iz0 = int(math.floor(fz))
                    tx = fx - float(ix0)
                    tz = fz - float(iz0)
                    sx = _smoothstep(tx)
                    sz = _smoothstep(tz)
                    v00 = _hash01_2d(seed, ix0, iz0)
                    v10 = _hash01_2d(seed, ix0 + 1, iz0)
                    v01 = _hash01_2d(seed, ix0, iz0 + 1)
                    v11 = _hash01_2d(seed, ix0 + 1, iz0 + 1)
                    ix_a = _lerp(v00, v10, sx)
                    ix_b = _lerp(v01, v11, sx)
                    return _lerp(ix_a, ix_b, sz)

                def _fbm01(
                    seed: int,
                    xf: float,
                    zf: float,
                    *,
                    scale: float,
                    octaves: int,
                    lac: float,
                    gain: float,
                ) -> float:
                    octaves = max(1, int(octaves))
                    if scale <= 1e-6:
                        scale = 1.0
                    if lac <= 1e-6:
                        lac = 2.0
                    gain = 0.0 if gain <= 0.0 else (1.0 if gain >= 1.0 else gain)
                    freq = 1.0
                    amp = 1.0
                    acc = 0.0
                    norm = 0.0
                    for oi in range(octaves):
                        s = float(scale) / freq
                        acc += _value_noise01(seed + (oi * 0x9E3779B1), xf, zf, s) * amp
                        norm += amp
                        amp *= gain
                        freq *= lac
                    if norm <= 1e-9:
                        return 0.0
                    return acc / norm

                grassy_seed_base = int(self._env_height_seed) & 0xFFFFFFFF
                grassy_params_cache: dict[tuple[int, int], tuple[int, int, int]] = {}
                grassy_wobble_cache: dict[tuple[int, int, int], int] = {}
                grassy_patch_off_cache: dict[tuple[int, int, int], int] = {}

                def bid_at_depth(depth: int, *, x: int, y: int, z: int, top_y: int) -> str:
                    if y <= int(bottom_y_base) + 1:
                        return black_id
                    if preset.name == "desert":
                        if depth <= 0:
                            return "minecraft:sand"
                        sand_th = 8
                        if depth <= sand_th:
                            return "minecraft:sandstone"
                        if not desert_rock_palette:
                            return "minecraft:stone"

                        rock_depth = int(depth) - int(sand_th)
                        seed_base = int(self._env_height_seed) & 0xFFFFFFFF
                        sx = float(x) + float(seed_base % 1024)
                        sz = float(z) - float((seed_base >> 10) % 1024)
                        band_size = 14 + int(round((math.sin(sx * 0.010) * 0.5 + 0.5) * 12.0))  # 14..26
                        if band_size < 6:
                            band_size = 6
                        warp = int(round((math.sin(sz * 0.021) + math.cos(sx * 0.017)) * 2.2))
                        band = int((rock_depth + warp) // band_size)
                        idx = int(band) % len(desert_rock_palette)
                        return desert_rock_palette[idx]
                    if preset.name != "grassy_hills":
                        for max_depth, bid in strata:
                            if depth <= int(max_depth):
                                return bid
                        return strata[-1][1]

                    # Bottom "void slab": always black.
                    if y <= int(bottom_y_base) + 1:
                        return black_id
                    if depth <= 0:
                        return grass_id

                    # Grassy hills geology is expensive if computed per voxel.
                    # Cache per-column parameters and per-band values so deep
                    # vertical walls remain cheap to emit.
                    params = grassy_params_cache.get((int(x), int(z)))
                    if params is None:
                        seed_base = int(grassy_seed_base)
                        dirt_seed = seed_base ^ 0xD17D00D
                        dirt_n = _fbm01(dirt_seed, float(x), float(z), scale=84.0, octaves=3, lac=2.0, gain=0.5)
                        dirt_th = 3 + int(round(dirt_n * 6.0))  # 3..9

                        band_seed = seed_base ^ 0xBADC0DE
                        band_n = _fbm01(band_seed, float(x), float(z), scale=160.0, octaves=2, lac=2.2, gain=0.55)
                        band_size = 9 + int(round(band_n * 10.0))  # 9..19
                        if band_size < 2:
                            band_size = 2

                        warp_seed = seed_base ^ 0xBEEF123
                        warp_n = _fbm01(warp_seed, float(x), float(z), scale=68.0, octaves=3, lac=2.0, gain=0.55)
                        warp = int(round((warp_n * 2.0 - 1.0) * float(band_size) * 0.55))

                        params = (int(dirt_th), int(band_size), int(warp))
                        grassy_params_cache[(int(x), int(z))] = params

                    dirt_th, band_size, warp = params
                    if depth <= dirt_th:
                        return dirt_id

                    rock_depth = depth - dirt_th
                    band_base = int((int(rock_depth) + int(warp)) // int(band_size))
                    wobble = grassy_wobble_cache.get((int(x), int(z), int(band_base)))
                    if wobble is None:
                        seed_base = int(grassy_seed_base)
                        wobble_seed = seed_base ^ 0xB17B1E
                        wobble_n = _fbm01(
                            wobble_seed ^ (int(band_base) * 0x9E3779B1),
                            float(x),
                            float(z),
                            scale=420.0,
                            octaves=2,
                            lac=2.0,
                            gain=0.5,
                        )
                        wobble = int(round((wobble_n * 2.0 - 1.0) * 3.0))
                        if wobble < -3:
                            wobble = -3
                        if wobble > 3:
                            wobble = 3
                        grassy_wobble_cache[(int(x), int(z), int(band_base))] = int(wobble)

                    band = int((int(rock_depth) + int(warp) + int(wobble)) // int(band_size))
                    idx = int(band) % len(rock_palette)

                    patch_off = grassy_patch_off_cache.get((int(x), int(z), int(band)))
                    if patch_off is None:
                        seed_base = int(grassy_seed_base)
                        patch_seed = seed_base ^ 0xFACEB00C
                        patch_n = _fbm01(
                            patch_seed,
                            float(x + band * 11),
                            float(z - band * 7),
                            scale=44.0,
                            octaves=4,
                            lac=2.0,
                            gain=0.5,
                        )
                        if patch_n > 0.78:
                            patch_off = 1
                        elif patch_n < 0.18:
                            patch_off = 3
                        else:
                            patch_off = 0
                        grassy_patch_off_cache[(int(x), int(z), int(band))] = int(patch_off)

                    idx = (idx + int(patch_off)) % len(rock_palette)
                    return rock_palette[int(idx)]

                step = int(self._env_patch_step)
                px, pz = key
                x0 = int(px) * step
                z0 = int(pz) * step
                tops: dict[tuple[int, int], int] = {}
                for x in range(x0, x0 + step):
                    for z in range(z0, z0 + step):
                        delta = clamp_terrain_delta(
                            int(self._env_height_offset(int(x), int(z))) - anchor_off,
                            max_delta=max(ENV_HEIGHT_MAX_DELTA, int(self._env_terrain_amp)),
                        )
                        top_y = int(surface_y) + int(delta)
                        if top_y < int(WORLD_MIN_Y):
                            top_y = int(WORLD_MIN_Y)
                        tops[(int(x), int(z))] = int(top_y)

                if not tops:
                    return

                # Register heights before emitting faces so neighbor queries work
                # within the patch and against already-built patches.
                self._env_top_y_by_xz.update(tops)
                if perf:
                    t_tops = time.perf_counter()

                cx, cy, cz = self._pivot_center
                tex_groups: dict[str, dict[int, tuple[list[float], list[float], list[float], list[int]]]] = {}
                col_groups: dict[int, tuple[list[float], list[float], list[int]]] = {}
                decor_tex_groups: dict[str, tuple[list[float], list[float], list[float], list[int]]] = {}
                decor_col_groups: tuple[list[float], list[float], list[int]] = ([], [], [])
                fade_ranges: list[tuple[str, str, int, int, int]] = []
                patch_spans: list[tuple[str, str, int, int]] = []

                appearance_cache: dict[str, ResolvedBlockAppearance | None] = {}
                face_tex: dict[tuple[str, str], str] = {}
                face_tint: dict[tuple[str, str], tuple[int, int, int]] = {}
                for bid in layer_ids:
                    app = resolver.resolve_block_appearance(bid) if resolver is not None else None
                    appearance_cache[bid] = app
                    for face in ("north", "south", "west", "east", "down", "up"):
                        jar_rel = app.face_texture_png_by_dir.get(face) if app is not None else ""
                        if jar_rel and texture_source is not None:
                            tex = load_tex_from_jar(texture_source, jar_rel)
                            if tex is None:
                                jar_rel = ""
                        face_tex[(bid, face)] = jar_rel or ""
                        tint = (255, 255, 255)
                        if app is not None:
                            tintindex = int(app.face_tintindex_by_dir.get(face, -1))
                            if tintindex >= 0:
                                tint = _tint_rgb(texture_source, bid, tintindex)
                        face_tint[(bid, face)] = tint

                def _decor_best_face(bid: str) -> str:
                    for f in ("north", "east", "south", "west", "up", "down"):
                        if face_tex.get((bid, f)):
                            return f
                    return "north"

                def _resolve_decor_bid(bid: str) -> str | None:
                    if resolver is None:
                        return bid
                    if appearance_cache.get(bid) is not None:
                        return bid
                    alias = _ENV_DECOR_ALIASES.get(bid)
                    if alias:
                        if appearance_cache.get(alias) is not None:
                            return alias
                        if resolver.resolve_block_appearance(alias) is not None:
                            return alias
                    return None

                def top_y_at(x: int, z: int) -> int | None:
                    y = self._env_top_y_by_xz.get((int(x), int(z)))
                    return int(y) if y is not None else None

                def add_face(*, bid: str, face: str, x: int, y: int, z: int) -> None:
                    jar_rel = face_tex.get((bid, face)) or ""
                    tint = face_tint.get((bid, face), (255, 255, 255))
                    target_a = _strip_target_alpha(face, y=int(y))
                    tx = float(x) + 0.5 - float(cx)
                    ty = float(y) + 0.5 - float(cy)
                    tz = float(z) + 0.5 - float(cz)
                    quad = _UNIT_CUBE_FACE_QUADS[face]
                    p0, p1, p2, p3 = [(px + tx, py + ty, pz + tz) for (px, py, pz) in quad]
                    normal = face_normals[face]
                    tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]
                    if not jar_rel:
                        r, g, b = _stable_rgb(bid)
                        col = (int(r * 255), int(g * 255), int(b * 255))
                        g_verts, g_norms, g_cols = col_groups.setdefault(int(target_a), ([], [], []))
                        g_verts.extend(tri_verts)
                        g_norms.extend([*normal, *normal, *normal, *normal, *normal, *normal])
                        g_cols.extend([col[0], col[1], col[2], 0] * 6)
                        return
                    by_alpha = tex_groups.setdefault(jar_rel, {})
                    g_verts, g_norms, g_uvs, g_cols = by_alpha.setdefault(int(target_a), ([], [], [], []))
                    g_verts.extend(tri_verts)
                    g_norms.extend([*normal, *normal, *normal, *normal, *normal, *normal])
                    g_uvs.extend(_UNIT_CUBE_FACE_UV_TRI[face])
                    g_cols.extend([tint[0], tint[1], tint[2], 0] * 6)

                def add_face_span(
                    *,
                    bid: str,
                    face: str,
                    x: int,
                    y0: int,
                    y1: int,
                    z: int,
                    target_a: int,
                ) -> None:
                    jar_rel = face_tex.get((bid, face)) or ""
                    tint = face_tint.get((bid, face), (255, 255, 255))

                    x0 = float(x) - float(cx)
                    x1 = float(x + 1) - float(cx)
                    z0 = float(z) - float(cz)
                    z1 = float(z + 1) - float(cz)
                    yy0 = float(y0) - float(cy)
                    yy1 = float(y1 + 1) - float(cy)

                    if face not in {"north", "south", "west", "east"}:
                        return

                    p0, p1, p2, p3 = _cube_face_quad_points(
                        face,
                        xmin=float(x0),
                        xmax=float(x1),
                        ymin=float(yy0),
                        ymax=float(yy1),
                        zmin=float(z0),
                        zmax=float(z1),
                    )
                    normal = face_normals[face]
                    h = float(int(y1) - int(y0) + 1)
                    # Merge multiple stacked cube faces into a single span. Use a repeated UV rect
                    # (height = 16*h) so textures tile vertically like individual blocks.
                    uv_tri_h = _uv_tri_for_face_rect(
                        face,
                        (0.0, 0.0, 16.0, 16.0 * float(h)),
                        quad_points=(p0, p1, p2, p3),
                    )
                    tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]

                    if not jar_rel:
                        r, g, b = _stable_rgb(bid)
                        col = (int(r * 255), int(g * 255), int(b * 255))
                        g_verts, g_norms, g_cols = col_groups.setdefault(int(target_a), ([], [], []))
                        g_verts.extend(tri_verts)
                        g_norms.extend([*normal, *normal, *normal, *normal, *normal, *normal])
                        g_cols.extend([col[0], col[1], col[2], 0] * 6)
                        return

                    by_alpha = tex_groups.setdefault(jar_rel, {})
                    g_verts, g_norms, g_uvs, g_cols = by_alpha.setdefault(int(target_a), ([], [], [], []))
                    g_verts.extend(tri_verts)
                    g_norms.extend([*normal, *normal, *normal, *normal, *normal, *normal])
                    g_uvs.extend(uv_tri_h)
                    g_cols.extend([tint[0], tint[1], tint[2], 0] * 6)

                def add_decor_cross(*, bid: str, x: int, y: int, z: int, scale: float) -> None:
                    best_face = _decor_best_face(bid)
                    jar_rel = face_tex.get((bid, best_face)) or ""
                    tint = face_tint.get((bid, best_face), (255, 255, 255))
                    if resolver is not None and not jar_rel:
                        return
                    if scale <= 0.0:
                        return
                    s = float(scale)
                    if not math.isfinite(s):
                        s = 0.65
                    s = max(0.05, min(1.0, s))

                    cx0 = float(x) + 0.5 - float(cx)
                    cz0 = float(z) + 0.5 - float(cz)
                    y0 = float(y) - float(cy)
                    y1 = float(y) + s - float(cy)
                    half = 0.5 * s

                    def emit_quad(p0: tuple[float, float, float], p1: tuple[float, float, float], p2: tuple[float, float, float], p3: tuple[float, float, float]) -> None:
                        normal = _tri_normal(p0, p1, p2)
                        tri_verts = [*p0, *p1, *p2, *p0, *p2, *p3]
                        # Full-texture UVs in GL space (Minecraft UVs are in image
                        # space; see `_uv_quad_from_rect` for the v-flip).
                        u0, u1, u2, u3 = _uv_quad_from_rect((0.0, 0.0, 16.0, 16.0))
                        # Our quad points are in order bottom-left, bottom-right,
                        # top-right, top-left; map that to the uv quad which is
                        # ordered top-left, top-right, bottom-right, bottom-left.
                        uv = [*u3, *u2, *u1, *u3, *u1, *u0]
                        if not jar_rel:
                            r, g, b = _stable_rgb(bid)
                            col = (int(r * 255), int(g * 255), int(b * 255))
                            g_verts, g_norms, g_cols = decor_col_groups
                            g_verts.extend(tri_verts)
                            g_norms.extend([*normal, *normal, *normal, *normal, *normal, *normal])
                            g_cols.extend([col[0], col[1], col[2], 255] * 6)
                        else:
                            g_verts, g_norms, g_uvs, g_cols = decor_tex_groups.setdefault(jar_rel, ([], [], [], []))
                            g_verts.extend(tri_verts)
                            g_norms.extend([*normal, *normal, *normal, *normal, *normal, *normal])
                            g_uvs.extend(uv)
                            g_cols.extend([tint[0], tint[1], tint[2], 255] * 6)

                        # Back face (for culling-on mode).
                        normal_b = (-normal[0], -normal[1], -normal[2])
                        tri_verts_b = [*p0, *p2, *p1, *p0, *p3, *p2]
                        uv_b = [*u3, *u1, *u2, *u3, *u0, *u1]
                        if not jar_rel:
                            r, g, b = _stable_rgb(bid)
                            col = (int(r * 255), int(g * 255), int(b * 255))
                            g_verts, g_norms, g_cols = decor_col_groups
                            g_verts.extend(tri_verts_b)
                            g_norms.extend([*normal_b, *normal_b, *normal_b, *normal_b, *normal_b, *normal_b])
                            g_cols.extend([col[0], col[1], col[2], 255] * 6)
                        else:
                            g_verts, g_norms, g_uvs, g_cols = decor_tex_groups.setdefault(jar_rel, ([], [], [], []))
                            g_verts.extend(tri_verts_b)
                            g_norms.extend([*normal_b, *normal_b, *normal_b, *normal_b, *normal_b, *normal_b])
                            g_uvs.extend(uv_b)
                            g_cols.extend([tint[0], tint[1], tint[2], 255] * 6)

                    # Minecraft-style "cross" uses two diagonal quads.
                    p0 = (cx0 - half, y0, cz0 - half)
                    p1 = (cx0 + half, y0, cz0 + half)
                    p2 = (cx0 + half, y1, cz0 + half)
                    p3 = (cx0 - half, y1, cz0 - half)
                    emit_quad(p0, p1, p2, p3)

                    q0 = (cx0 - half, y0, cz0 + half)
                    q1 = (cx0 + half, y0, cz0 - half)
                    q2 = (cx0 + half, y1, cz0 - half)
                    q3 = (cx0 - half, y1, cz0 + half)
                    emit_quad(q0, q1, q2, q3)

                # Heightmap geometry: emit only visible faces (top and vertical
                # sides where neighbor columns are lower / missing).
                for x in range(x0, x0 + step):
                    for z in range(z0, z0 + step):
                        top_y = int(tops[(int(x), int(z))])
                        add_face(bid=top_id, face="up", x=int(x), y=top_y, z=int(z))
                        bottom_y = int(min(int(top_y), int(bottom_y_base)))

                        for face, (dx, dz) in (
                            ("north", (0, -1)),
                            ("south", (0, 1)),
                            ("west", (-1, 0)),
                            ("east", (1, 0)),
                        ):
                            n_top = top_y_at(int(x) + dx, int(z) + dz)
                            if n_top is None:
                                start_y = bottom_y
                                end_y = top_y
                            elif top_y > int(n_top):
                                start_y = max(bottom_y, int(n_top) + 1)
                                end_y = top_y
                            elif top_y < int(n_top):
                                n_bottom = int(min(int(n_top), int(bottom_y_base)))
                                start_y = bottom_y
                                end_y = min(top_y, n_bottom - 1)
                            else:
                                continue
                            if start_y > end_y:
                                continue
                            y = int(start_y)
                            y_end = int(end_y)
                            while y <= y_end:
                                bid = bid_at_depth(int(top_y) - int(y), x=int(x), y=int(y), z=int(z), top_y=int(top_y))
                                a = int(
                                    _strip_fade_side_alpha_cached(
                                        int(y),
                                        bottom_y_base=int(bottom_y_base),
                                        strip_fade_h=int(strip_fade_h),
                                        strip_fade_levels=int(strip_fade_levels),
                                        cache=strip_side_alpha_cache,
                                    )
                                )
                                y2 = int(y)
                                while y2 + 1 <= y_end:
                                    yn = int(y2 + 1)
                                    if (
                                        int(
                                            _strip_fade_side_alpha_cached(
                                                int(yn),
                                                bottom_y_base=int(bottom_y_base),
                                                strip_fade_h=int(strip_fade_h),
                                                strip_fade_levels=int(strip_fade_levels),
                                                cache=strip_side_alpha_cache,
                                            )
                                        )
                                        != int(a)
                                    ):
                                        break
                                    bn = bid_at_depth(
                                        int(top_y) - int(yn),
                                        x=int(x),
                                        y=int(yn),
                                        z=int(z),
                                        top_y=int(top_y),
                                    )
                                    if bn != bid:
                                        break
                                    y2 = int(yn)
                                add_face_span(bid=bid, face=face, x=int(x), y0=int(y), y1=int(y2), z=int(z), target_a=int(a))
                                y = int(y2 + 1)

                # Ambient decoration (plants): deterministic per (x,z) and stored
                # independently from structures, so it can "reappear" when a
                # structure is removed.
                if isinstance(decor_cfg, EnvironmentDecorConfig) and decor_cfg.blocks and decor_cfg.density > 0.0:
                    blocks = decor_cfg.blocks
                    total_w = sum(int(b.weight) for b in blocks)
                    if total_w > 0:
                        density = float(decor_cfg.density)
                        decor_scale = float(decor_cfg.scale)
                        if not math.isfinite(decor_scale) or decor_scale <= 0.0:
                            decor_scale = 0.65
                        if decor_scale > 1.0:
                            decor_scale = 1.0
                        if decor_scale < 0.05:
                            decor_scale = 0.05
                        seed_a = int(_stable_seed("env-decor", preset.name, int(self._env_height_seed))) & 0xFFFFFFFF
                        seed_b = (seed_a ^ 0x9E3779B9) & 0xFFFFFFFF
                        for x in range(x0, x0 + step):
                            for z in range(z0, z0 + step):
                                r = _hash01_2d(int(seed_a), int(x), int(z))
                                if r >= density:
                                    continue
                                top_y = int(tops[(int(x), int(z))])
                                pos = (int(x), int(top_y) + 1, int(z))
                                if pos in self._env_decor_by_pos:
                                    continue
                                pick = _hash01_2d(int(seed_b), int(x), int(z)) * float(total_w)
                                acc = 0.0
                                chosen = blocks[-1].block_id
                                for b in blocks:
                                    acc += float(int(b.weight))
                                    if pick < acc:
                                        chosen = b.block_id
                                        break
                                resolved_bid = _resolve_decor_bid(chosen)
                                if resolved_bid is None:
                                    continue
                                self._env_decor_by_pos[pos] = resolved_bid
                                add_decor_cross(
                                    bid=resolved_bid,
                                    x=int(pos[0]),
                                    y=int(pos[1]),
                                    z=int(pos[2]),
                                    scale=decor_scale,
                                )

                if perf:
                    t_faces = time.perf_counter()

                def _append_tex(jar_rel: str, groups: dict[int, tuple[list[float], list[float], list[float], list[int]]]) -> None:
                    tex = tex_cache.get(jar_rel)
                    if tex is None:
                        return
                    group = group_cache.get(jar_rel)
                    if group is None:
                        group = pyglet.graphics.TextureGroup(tex)
                        group_cache[jar_rel] = group
                    items: list[tuple[int, list[float], list[float], list[float], list[int]]] = []
                    total = 0
                    for a in sorted(groups.keys()):
                        v, n, u, c = groups[int(a)]
                        cnt = len(v) // 3
                        if cnt <= 0:
                            continue
                        items.append((int(a), v, n, u, c))
                        total += int(cnt)
                    if total <= 0:
                        return
                    vl = self._env_tex_vlists.get(jar_rel)
                    if vl is None:
                        all_v: list[float] = []
                        all_n: list[float] = []
                        all_u: list[float] = []
                        all_c: list[int] = []
                        off = 0
                        for a, v, n, u, c in items:
                            cnt = len(v) // 3
                            if cnt <= 0:
                                continue
                            all_v.extend(v)
                            all_n.extend(n)
                            all_u.extend(u)
                            all_c.extend(c)
                            fade_ranges.append(("tex", jar_rel, int(off), int(cnt), int(a)))
                            off += int(cnt)
                        vl_new = self._env_batch.add(
                            int(off),
                            gl.GL_TRIANGLES,
                            group,
                            ("v3f/stream", all_v),
                            ("n3f/stream", all_n),
                            ("t2f/stream", all_u),
                            ("c4B/stream", all_c),
                        )
                        self._env_tex_vlists[jar_rel] = vl_new
                        self._env_tex_counts[jar_rel] = int(off)
                        patch_spans.append(("tex", str(jar_rel), 0, int(off)))
                        return
                    old = int(self._env_tex_counts.get(jar_rel, 0))
                    new = old + int(total)
                    try:
                        vl.resize(new)
                        off = 0
                        for a, v, n, u, c in items:
                            cnt = len(v) // 3
                            if cnt <= 0:
                                continue
                            s = int(old) + int(off)
                            e = int(s) + int(cnt)
                            vl.vertices[s * 3 : e * 3] = v
                            vl.normals[s * 3 : e * 3] = n
                            vl.tex_coords[s * 2 : e * 2] = u
                            vl.colors[s * 4 : e * 4] = c
                            fade_ranges.append(("tex", jar_rel, int(s), int(cnt), int(a)))
                            off += int(cnt)
                        self._env_tex_counts[jar_rel] = int(new)
                        patch_spans.append(("tex", str(jar_rel), int(old), int(total)))
                    except Exception:
                        # If a driver/domain resize fails, fall back to leaving
                        # the environment as-is; better to keep running.
                        return

                for jar_rel in sorted(tex_groups.keys()):
                    _append_tex(jar_rel, tex_groups[jar_rel])

                if col_groups:
                    items = []
                    total = 0
                    for a in sorted(col_groups.keys()):
                        v, n, c = col_groups[int(a)]
                        cnt = len(v) // 3
                        if cnt <= 0:
                            continue
                        items.append((int(a), v, n, c))
                        total += int(cnt)
                    if total > 0:
                        vl = self._env_colored_vlist
                        if vl is None:
                            all_v: list[float] = []
                            all_n: list[float] = []
                            all_c: list[int] = []
                            off = 0
                            for a, v, n, c in items:
                                cnt = len(v) // 3
                                if cnt <= 0:
                                    continue
                                all_v.extend(v)
                                all_n.extend(n)
                                all_c.extend(c)
                                fade_ranges.append(("col", "", int(off), int(cnt), int(a)))
                                off += int(cnt)
                            self._env_colored_vlist = self._env_batch.add(
                                int(off),
                                gl.GL_TRIANGLES,
                                no_tex_group,
                                ("v3f/stream", all_v),
                                ("n3f/stream", all_n),
                                ("c4B/stream", all_c),
                            )
                            self._env_colored_count = int(off)
                            patch_spans.append(("col", "", 0, int(off)))
                        else:
                            old = int(self._env_colored_count)
                            new = old + int(total)
                            try:
                                vl.resize(new)
                                off = 0
                                for a, v, n, c in items:
                                    cnt = len(v) // 3
                                    if cnt <= 0:
                                        continue
                                    s = int(old) + int(off)
                                    e = int(s) + int(cnt)
                                    vl.vertices[s * 3 : e * 3] = v
                                    vl.normals[s * 3 : e * 3] = n
                                    vl.colors[s * 4 : e * 4] = c
                                    fade_ranges.append(("col", "", int(s), int(cnt), int(a)))
                                    off += int(cnt)
                                self._env_colored_count = int(new)
                                patch_spans.append(("col", "", int(old), int(total)))
                            except Exception:
                                pass

                def _append_decor_tex(jar_rel: str, verts: list[float], norms: list[float], uvs: list[float], cols: list[int]) -> None:
                    tex = tex_cache.get(jar_rel)
                    if tex is None:
                        return
                    group = group_cache.get(jar_rel)
                    if group is None:
                        group = pyglet.graphics.TextureGroup(tex)
                        group_cache[jar_rel] = group
                    cnt = len(verts) // 3
                    if cnt <= 0:
                        return
                    vl = self._env_decor_tex_vlists.get(jar_rel)
                    if vl is None:
                        try:
                            vl_new = self._env_decor_batch.add(
                                int(cnt),
                                gl.GL_TRIANGLES,
                                group,
                                ("v3f/stream", verts),
                                ("n3f/stream", norms),
                                ("t2f/stream", uvs),
                                ("c4B/stream", cols),
                            )
                            self._env_decor_tex_vlists[jar_rel] = vl_new
                            self._env_decor_tex_counts[jar_rel] = int(cnt)
                        except Exception:
                            return
                        return
                    old = int(self._env_decor_tex_counts.get(jar_rel, 0))
                    new = old + int(cnt)
                    try:
                        vl.resize(new)
                        s = int(old)
                        e = int(new)
                        vl.vertices[s * 3 : e * 3] = verts
                        vl.normals[s * 3 : e * 3] = norms
                        vl.tex_coords[s * 2 : e * 2] = uvs
                        vl.colors[s * 4 : e * 4] = cols
                        self._env_decor_tex_counts[jar_rel] = int(new)
                    except Exception:
                        return

                def _append_decor_col(verts: list[float], norms: list[float], cols: list[int]) -> None:
                    cnt = len(verts) // 3
                    if cnt <= 0:
                        return
                    vl = self._env_decor_colored_vlist
                    if vl is None:
                        try:
                            self._env_decor_colored_vlist = self._env_decor_batch.add(
                                int(cnt),
                                gl.GL_TRIANGLES,
                                no_tex_group,
                                ("v3f/stream", verts),
                                ("n3f/stream", norms),
                                ("c4B/stream", cols),
                            )
                            self._env_decor_colored_count = int(cnt)
                        except Exception:
                            return
                        return
                    old = int(self._env_decor_colored_count)
                    new = old + int(cnt)
                    try:
                        vl.resize(new)
                        s = int(old)
                        e = int(new)
                        vl.vertices[s * 3 : e * 3] = verts
                        vl.normals[s * 3 : e * 3] = norms
                        vl.colors[s * 4 : e * 4] = cols
                        self._env_decor_colored_count = int(new)
                    except Exception:
                        return

                for jar_rel in sorted(decor_tex_groups.keys()):
                    v, n, u, c = decor_tex_groups[jar_rel]
                    _append_decor_tex(str(jar_rel), v, n, u, c)
                dv, dn, dc = decor_col_groups
                if dv:
                    _append_decor_col(dv, dn, dc)

                try:
                    min_top_y = min(int(v) for v in tops.values())
                    max_top_y = max(int(v) for v in tops.values())
                except Exception:
                    min_top_y = int(surface_y)
                    max_top_y = int(surface_y)

                self._env_patches.add(key)
                try:
                    self._env_patch_fade[key] = (time.monotonic(), fade_ranges, -1)
                except Exception:
                    return
                try:
                    self._env_patch_ranges[key] = list(fade_ranges)
                    has_transparent = any(int(a) < 255 for (_k, _j, _s, _c, a) in fade_ranges)
                    if has_transparent:
                        self._env_patch_has_transparency[key] = True
                except Exception:
                    pass
                try:
                    if patch_spans:
                        self._env_patch_spans[key] = list(patch_spans)
                except Exception:
                    pass
                if perf:
                    try:
                        t_end = time.perf_counter()
                        verts = 0
                        spans = 0
                        for _kind, _jar, _start, _count in patch_spans:
                            spans += 1
                            if int(_count) > 0:
                                verts += int(_count)
                        self._perf_patches.append(
                            {
                                "key": [int(key[0]), int(key[1])],
                                "total_ms": float((t_end - float(t0)) * 1000.0),
                                "tops_ms": float((float(t_tops) - float(t0)) * 1000.0),
                                "faces_ms": float((float(t_faces) - float(t_tops)) * 1000.0),
                                "upload_ms": float((t_end - float(t_faces)) * 1000.0),
                                "verts": int(verts),
                                "spans": int(spans),
                                "top_y_min": int(min_top_y),
                                "top_y_max": int(max_top_y),
                                "bottom_y": int(bottom_y_base),
                                "step": int(step),
                                "strip_fade_h": int(strip_fade_h),
                            }
                        )
                    except Exception:
                        pass

            def _tick_env_patch_fades(self) -> None:
                fades = self._env_patch_fade
                if not fades:
                    return
                try:
                    fade_s = float(param_store.get("rez.fade_s"))
                except Exception:
                    fade_s = 5.0
                if fade_s < 0.05:
                    fade_s = 0.05
                try:
                    use_stipple = bool(int(param_store.get_int("rez.fade.mode")))
                except Exception:
                    use_stipple = False

                now = time.monotonic()
                if use_stipple:
                    done: list[tuple[int, int]] = []
                    for key, (start_t, ranges, _last_a) in list(fades.items()):
                        if (now - float(start_t)) < float(fade_s):
                            continue
                        for kind, jar_rel, start, count, target_a in list(ranges):
                            if int(count) <= 0:
                                continue
                            a_scaled = int(target_a)
                            if a_scaled < 0:
                                a_scaled = 0
                            if a_scaled > 255:
                                a_scaled = 255
                            try:
                                if kind == "tex":
                                    vl = self._env_tex_vlists.get(str(jar_rel))
                                else:
                                    vl = self._env_colored_vlist
                                if vl is None:
                                    continue
                                cols = vl.colors
                                i0 = int(start) * 4 + 3
                                i1 = (int(start) + int(count)) * 4
                                if i0 < 0:
                                    continue
                                if i1 > len(cols):
                                    i1 = len(cols)
                                alpha_count = (int(i1) - int(i0) + 3) // 4
                                if alpha_count <= 0:
                                    continue
                                try:
                                    cols[i0:i1:4] = bytes((int(a_scaled),)) * int(alpha_count)
                                except Exception:
                                    try:
                                        cols[i0:i1:4] = [int(a_scaled)] * int(alpha_count)
                                    except Exception:
                                        for i in range(i0, int(i1), 4):
                                            cols[i] = int(a_scaled)
                            except Exception:
                                continue
                        done.append(key)
                    for key in done:
                        fades.pop(key, None)
                    return

                done: list[tuple[int, int]] = []
                for key, (start_t, ranges, last_a) in list(fades.items()):
                    p = (now - float(start_t)) / float(fade_s)
                    if p >= 1.0:
                        a = 255
                        done.append(key)
                    elif p <= 0.0:
                        a = 0
                    else:
                        a = int(round(p * 255.0))
                        if a < 0:
                            a = 0
                        if a > 255:
                            a = 255
                        # Quantize alpha changes to keep terrain fades cheap.
                        # This avoids re-writing huge color buffers every frame.
                        quant = 8
                        if quant > 1 and a < 255:
                            a = int((int(a) // int(quant)) * int(quant))
                    if int(a) == int(last_a):
                        continue
                    fades[key] = (float(start_t), ranges, int(a))
                    for kind, jar_rel, start, count, target_a in list(ranges):
                        if int(count) <= 0:
                            continue
                        a_scaled = int((int(target_a) * int(a) + 127) // 255)
                        if a_scaled < 0:
                            a_scaled = 0
                        if a_scaled > 255:
                            a_scaled = 255
                        try:
                            if kind == "tex":
                                vl = self._env_tex_vlists.get(str(jar_rel))
                            else:
                                vl = self._env_colored_vlist
                            if vl is None:
                                continue
                            cols = vl.colors
                            i0 = int(start) * 4 + 3
                            i1 = (int(start) + int(count)) * 4
                            if i0 < 0:
                                continue
                            if i1 > len(cols):
                                i1 = len(cols)
                            alpha_count = (int(i1) - int(i0) + 3) // 4
                            if alpha_count <= 0:
                                continue
                            try:
                                cols[i0:i1:4] = bytes((int(a_scaled),)) * int(alpha_count)
                            except Exception:
                                try:
                                    cols[i0:i1:4] = [int(a_scaled)] * int(alpha_count)
                                except Exception:
                                    for i in range(i0, int(i1), 4):
                                        cols[i] = int(a_scaled)
                        except Exception:
                            continue
                for key in done:
                    fades.pop(key, None)

            def _draw_env_patch_stipple_fades(self) -> None:
                fx_mod.draw_env_patch_stipple_fades(
                    self,
                    gl=gl,
                    param_store=param_store,
                    stable_seed=_stable_seed,
                    pyglet_mod=pyglet,
                    group_cache=group_cache,
                    no_tex_group=no_tex_group,
                )

            def _draw_env_strip_stipple_fade(self) -> None:
                fx_mod.draw_env_strip_stipple_fade(
                    self,
                    gl=gl,
                    param_store=param_store,
                    stable_seed=_stable_seed,
                    pyglet_mod=pyglet,
                    group_cache=group_cache,
                    no_tex_group=no_tex_group,
                )

            def _tick_environment(self, dt: float) -> None:
                perf = getattr(self, "_perf_enabled", False)
                if perf:
                    self._perf_last_env_built = 0
                    self._perf_last_env_queue_len = int(len(self._env_patch_queue))
                    self._perf_last_env_fade_ms = 0.0
                if self._env_preset().is_space():
                    return
                if self._env_base_y is None:
                    return
                if perf:
                    fade_t0 = time.perf_counter()
                    self._tick_env_patch_fades()
                    self._perf_last_env_fade_ms = (time.perf_counter() - fade_t0) * 1000.0
                else:
                    self._tick_env_patch_fades()
                if not self._env_patch_queue:
                    return

                fps = _adaptive_update_budget_fps(dt_s=float(dt), tick_fps_smooth=float(self._tick_fps_value))
                ratio = fps / 60.0
                ratio = max(0.15, min(2.0, ratio))

                patches = max(1, int(round(float(self._env_patches_per_tick) * ratio)))
                # Keep environment generation from tanking the frame-rate: build
                # patches incrementally with a small per-tick time budget.
                start_t = time.monotonic()
                budget_s = 0.004 * ratio
                if self._rez_active:
                    budget_s *= 0.625
                budget_s = max(0.0005, min(0.012, float(budget_s)))
                built = 0
                for _ in range(patches):
                    key: tuple[int, int] | None = None
                    while self._env_patch_queue:
                        prio0, prio1, _seq, candidate = heapq.heappop(self._env_patch_queue)
                        if candidate in self._env_patches:
                            self._env_patch_pending.discard(candidate)
                            self._env_patch_best_prio.pop(candidate, None)
                            continue
                        best = self._env_patch_best_prio.get(candidate)
                        if best is None or best != (int(prio0), int(prio1)):
                            continue
                        self._env_patch_best_prio.pop(candidate, None)
                        key = candidate
                        break
                    if key is None:
                        break
                    self._env_patch_pending.discard(key)
                    self._env_build_patch(key)
                    built += 1
                    if built >= 1 and (time.monotonic() - start_t) >= budget_s:
                        break
                if perf:
                    self._perf_last_env_built = int(built)
                    self._perf_last_env_queue_len = int(len(self._env_patch_queue))

            def _cycle_environment(self, *, delta: int = 1) -> None:
                if not ENVIRONMENT_PRESETS:
                    return
                now = time.monotonic()
                cur_rgb = self._env_clear_rgb_now(now)
                self._env_index = (int(self._env_index) + int(delta)) % len(ENVIRONMENT_PRESETS)
                self._begin_environment_transition(now=now, from_rgb=cur_rgb)
                self._env_shape_nonce = int(secrets.randbits(32))
                self._env_clear_geometry()
                self._update_environment()
                # Environment affects terrain-matching projection; invalidate caches.
                self._jigsaw_cache_env_key = self._env_preset().name
                self._jigsaw_cache = {}
                if self._base_template is not None:
                    sx, sy, sz = self._base_template.size
                    base_struct = Structure(
                        size=self._base_template.size,
                        blocks=self._base_template.blocks,
                        block_entities=self._base_template.block_entities,
                        entities=self._base_template.entities,
                    )
                    base_state = JigsawExpansionState(
                        connectors=self._base_template.connectors,
                        consumed=frozenset(),
                        dead_end=frozenset(),
                        piece_bounds=((0, 0, 0, int(sx) - 1, int(sy) - 1, int(sz) - 1),),
                    )
                    self._jigsaw_cache[()] = (base_struct, [], base_state)
                self._expansion_report.append(f"Environment: {self._env_preset().name}")
                self._update_status()
                # Environment affects terrain-matching projection; rebuild the current
                # expansion so projected pieces snap to the new terrain.
                if self._base_template is not None and self.jigsaw_seeds:
                    self._rebuild_scene(reset_view=False, adjust_distance=False)

            def _ensure_line_labels(self) -> None:
                y_top = self._list_items_top_y()
                available_h = max(0.0, y_top - float(self.log_panel_h) - self._ui_f(18.0))
                visible_full = max(1, int(available_h / self.line_height))
                visible = visible_full + 1
                if len(self.line_labels) == visible:
                    return
                for lbl in self.line_labels:
                    lbl.delete()
                self.line_labels = []
                for i in range(visible):
                    lbl = pyglet.text.Label(
                        "",
                        x=self._ui_i(12.0),
                        y=y_top - i * self.line_height,
                        anchor_x="left",
                        anchor_y="top",
                        font_size=self._ui_i(11.0),
                        font_name=self.ui_font_name,
                        color=(0, 0, 0, 210),
                        batch=self.sidebar_text_batch,
                        group=ui_group_text,
                    )
                    self.line_labels.append(lbl)

            def _ensure_log_labels(self) -> None:
                available_h = max(0.0, float(self.log_panel_h) - float(self._ui_i(28.0)))
                visible = 0 if self._log_collapsed else max(1, int(available_h / self.log_line_height))
                if len(self.log_labels) == visible:
                    return
                for lbl in self.log_labels:
                    lbl.delete()
                self.log_labels = []
                y_top = float(self.log_panel_h) - float(self._ui_i(28.0))
                for i in range(visible):
                    lbl = pyglet.text.Label(
                        "",
                        x=self._ui_i(12.0),
                        y=y_top - i * self.log_line_height,
                        anchor_x="left",
                        anchor_y="top",
                        font_size=self._ui_i(10.0),
                        font_name=self.ui_font_name,
                        color=(170, 170, 178, 255),
                        batch=self.sidebar_text_batch,
                        group=ui_group_text,
                    )
                    self.log_labels.append(lbl)

            def _ensure_status_labels(self) -> None:
                if len(self.status_labels) == self.status_lines_max:
                    return
                for lbl in self.status_labels:
                    lbl.delete()
                self.status_labels = []
                status_top = self.height - self._ui_i(52.0)
                for i in range(self.status_lines_max):
                    lbl = pyglet.text.Label(
                        "",
                        x=self._ui_i(12.0),
                        y=status_top - i * self.status_line_height,
                        anchor_x="left",
                        anchor_y="top",
                        font_size=self._ui_i(10.0),
                        font_name=self.ui_font_name,
                        color=(*self._ui_pink, 255),
                        batch=self.sidebar_text_batch,
                        group=ui_group_text,
                    )
                    self.status_labels.append(lbl)

            def _set_status(self, lines: list[str]) -> None:
                for i, lbl in enumerate(self.status_labels):
                    if i < len(lines):
                        lbl.text = lines[i]
                        lbl.color = (*self._ui_pink, 255)
                    else:
                        lbl.text = ""
                        lbl.color = (*self._ui_pink, 0)

            def _set_log(self, lines: list[str]) -> None:
                for i, lbl in enumerate(self.log_labels):
                    if i < len(lines):
                        raw = lines[i]
                        text = raw
                        if len(text) > 84:
                            text = "…" + text[-83:]
                        lbl.text = text
                        if (
                            "unhandled_proc=" in raw
                            or "unhandled_pred=" in raw
                            or "-> no compatible pieces" in raw
                            or raw.startswith("Export failed:")
                        ):
                            lbl.color = (*self._ui_pink, 255)
                        elif "used_pool=" in raw:
                            lbl.color = (*self._ui_pink, 255)
                        elif text.startswith("L"):
                            lbl.color = (*self._ui_ender_yellow, 235)
                        elif text.startswith("Exported:"):
                            lbl.color = (*self._ui_purple, 255)
                        elif text.startswith("  "):
                            lbl.color = (*self._ui_ender_yellow, 190)
                        else:
                            lbl.color = (*self._ui_ender_yellow, 170)
                    else:
                        lbl.text = ""
                        lbl.color = (*self._ui_ender_yellow, 0)

            def _update_scrollbar(self) -> None:
                list_top = self._list_items_top_y()
                list_bottom = self.log_panel_h
                track_pad_y = 8
                track_y0 = int(list_bottom + track_pad_y)
                track_y1 = int(list_top - track_pad_y)
                track_h = max(0, track_y1 - track_y0)

                track_w = 6
                track_x = int(self.sidebar_width - track_w - 7)

                self.scroll_track.x = track_x
                self.scroll_track.y = track_y0
                self.scroll_track.width = track_w
                self.scroll_track.height = track_h

                total = len(self._filtered_indices)
                visible_full = max(1, int(self._visible_list_rows_termui()))
                if total <= visible_full or track_h <= 0:
                    self.scroll_track.visible = False
                    self.scroll_thumb.visible = False
                    self.scroll_thumb_glow.visible = False
                    self.scroll_thumb_shine.visible = False
                    return

                self.scroll_track.visible = True
                self.scroll_thumb.visible = True

                max_scroll = max(0, total - visible_full)
                if max_scroll <= 0:
                    thumb_h = track_h
                    thumb_y = track_y0
                else:
                    thumb_h = int(track_h * (float(visible_full) / float(total)))
                    thumb_h = max(18, min(track_h, thumb_h))
                    scroll = max(0.0, min(float(max_scroll), float(self._scroll_pos_f)))
                    frac = scroll / float(max_scroll)
                    thumb_y = track_y0 + int((track_h - thumb_h) * (1.0 - frac))

                self.scroll_thumb.x = track_x
                self.scroll_thumb.y = thumb_y
                self.scroll_thumb.width = track_w
                self.scroll_thumb.height = thumb_h

                self.scroll_thumb_glow.visible = True
                self.scroll_thumb_glow.x = track_x - 2
                self.scroll_thumb_glow.y = thumb_y - 2
                self.scroll_thumb_glow.width = track_w + 4
                self.scroll_thumb_glow.height = thumb_h + 4

                self.scroll_thumb_shine.visible = True
                self.scroll_thumb_shine.x = track_x
                self.scroll_thumb_shine.width = track_w
                self.scroll_thumb_shine.height = 3
                self.scroll_thumb_shine.opacity = 45
                self.scroll_thumb_shine.y = thumb_y + max(0, thumb_h - 4)

            def _update_rez_bar(self) -> None:
                if getattr(self, "_rez_termui_enabled", False):
                    return
                overlay_x0 = int(self.sidebar_width)
                overlay_w = max(1, int(self.width - self.sidebar_width))
                overlay_h = max(1, int(self.height))

                self.rez_scrim.x = overlay_x0
                self.rez_scrim.y = 0
                self.rez_scrim.width = overlay_w
                self.rez_scrim.height = overlay_h

                bar_w = int(min(620, max(260, overlay_w * 0.64)))
                bar_h = 14
                bar_x = overlay_x0 + (overlay_w - bar_w) // 2
                bar_y = int(self.height * 0.22)

                progress = self._rez_progress
                message = self._rez_message

                self.rez_bar_bg.x = bar_x
                self.rez_bar_bg.y = bar_y
                self.rez_bar_bg.width = bar_w
                self.rez_bar_bg.height = bar_h

                fill_w = max(1.0, float(bar_w) * max(0.0, min(1.0, progress)))
                self.rez_bar_fill.x = bar_x
                self.rez_bar_fill.y = bar_y
                self.rez_bar_fill.width = fill_w
                self.rez_bar_fill.height = bar_h

                self.rez_bar_fill_hi.x = bar_x
                self.rez_bar_fill_hi.y = bar_y + bar_h // 2
                self.rez_bar_fill_hi.width = fill_w
                self.rez_bar_fill_hi.height = max(1, bar_h // 2)

                shine_w = int(min(80, max(24, bar_w * 0.18)))
                t = self._rez_anim_s
                period = 1.6
                pos = ((t % period) / period) * float(bar_w + shine_w) - float(shine_w)
                pos += math.sin(time.monotonic() * 38.0) * 6.0
                self.rez_bar_shine.x = bar_x + int(pos)
                self.rez_bar_shine.y = bar_y
                self.rez_bar_shine.width = shine_w
                self.rez_bar_shine.height = bar_h

                # Glitch styling: subtle flicker + a few terminal-colored "static" blocks.
                now = time.monotonic()
                flicker = 0.88 + 0.12 * math.sin((now * 17.0) + (progress * 9.0))
                self.rez_bar_fill.opacity = int(215 * flicker)
                hi_flicker = 0.80 + 0.20 * math.sin((now * 23.0) + (progress * 11.0))
                self.rez_bar_fill_hi.opacity = int(90 * hi_flicker)
                self.rez_bar_shine.opacity = int(24 + 14 * (0.5 + 0.5 * math.sin(now * 4.5)))

                filled_x0 = float(bar_x)
                filled_x1 = float(bar_x) + float(fill_w)

                def _lcg(s: int) -> int:
                    return (s * 1664525 + 1013904223) & 0xFFFFFFFF

                def _rand01(s: int) -> tuple[float, int]:
                    s = _lcg(s)
                    return (float(s) / 4294967296.0), s

                frame = int(now * 24.0)
                seed = (self._fx_seed ^ (frame * 0x9E3779B1) ^ int(progress * 1_000_003.0)) & 0xFFFFFFFF
                for i, rect in enumerate(self.rez_bar_glitch):
                    if fill_w < 10.0:
                        rect.opacity = 0
                        continue
                    s = (seed ^ ((i + 1) * 0x85EBCA6B)) & 0xFFFFFFFF
                    show, s = _rand01(s)
                    if show > 0.55:
                        rect.opacity = 0
                        continue
                    rx, s = _rand01(s)
                    ry, s = _rand01(s)
                    rw, s = _rand01(s)
                    rh, s = _rand01(s)
                    rc, s = _rand01(s)
                    ra, s = _rand01(s)

                    x0 = filled_x0 + rx * max(1.0, (filled_x1 - filled_x0) - 6.0)
                    ww = 6.0 + rw * 54.0
                    if x0 + ww > filled_x1:
                        ww = max(1.0, filled_x1 - x0)
                    y0 = float(bar_y) + ry * float(max(0, bar_h - 2))
                    hh = 1.0 + rh * 3.0

                    if rc < 0.78:
                        rect.color = self._ui_purple
                    elif rc < 0.92:
                        rect.color = self._ui_amber
                    else:
                        rect.color = self._ui_green

                    rect.x = int(x0)
                    rect.y = int(y0)
                    rect.width = max(1, int(ww))
                    rect.height = max(1, int(hh))
                    rect.opacity = int(35 + 70 * ra)

                self.rez_label.x = bar_x
                self.rez_label.width = max(1, int(float(bar_w) * (2.0 / 3.0)))
                if message != self._rez_last_label:
                    self.rez_label.text = message
                    self._rez_last_label = message
                # If the label wraps to multiple lines, move it up so it doesn't
                # overwrite the progress bar.
                label_h = getattr(self.rez_label, "content_height", 0) or 0
                label_pad = 10
                self.rez_label.y = bar_y + bar_h + label_pad + int(label_h)

                cancel_size = 33
                cancel_gap = 10
                max_x = overlay_x0 + overlay_w - 12
                cancel_x = bar_x + bar_w + cancel_gap
                if cancel_x + cancel_size > max_x:
                    cancel_x = bar_x + bar_w - cancel_size
                cancel_x = max(overlay_x0 + 12, min(cancel_x, max_x - cancel_size))
                cancel_y = bar_y - (cancel_size - bar_h) // 2
                self.rez_cancel_bg.x = int(cancel_x)
                self.rez_cancel_bg.y = int(cancel_y)
                self.rez_cancel_bg.width = cancel_size
                self.rez_cancel_bg.height = cancel_size
                glow_pad = 14
                for r in self.rez_cancel_glows:
                    r.x = int(cancel_x - glow_pad)
                    r.y = int(cancel_y - glow_pad)
                    r.width = int(cancel_size + glow_pad * 2)
                    r.height = int(cancel_size + glow_pad * 2)
                cx = int(cancel_x + cancel_size // 2)
                cy = int(cancel_y + cancel_size // 2)
                spread_px = max(1, int(round(float(cancel_size) * 0.10)))
                for lbl, dx_unit, _ in self.rez_cancel_label_o_layers:
                    lbl.x = cx + dx_unit * spread_px
                    lbl.y = cy
                for lbl, dx_unit, _ in self.rez_cancel_label_x_layers:
                    lbl.x = cx + dx_unit * spread_px
                    lbl.y = cy

            def _set_rez_ui(self, active: bool) -> None:
                self._rez_active = active
                if getattr(self, "_rez_termui_enabled", False):
                    # TermUI renders the rez overlay; keep legacy widgets hidden.
                    try:
                        self.rez_label.text = ""
                    except Exception:
                        pass
                    try:
                        self.rez_label.color = (235, 235, 245, 0)
                    except Exception:
                        pass
                    for shape in (
                        self.rez_bar_bg,
                        self.rez_bar_fill,
                        self.rez_bar_fill_hi,
                        self.rez_bar_shine,
                        *self.rez_bar_glitch,
                        self.rez_cancel_bg,
                    ):
                        try:
                            shape.visible = False
                        except Exception:
                            pass
                    for r in getattr(self, "rez_cancel_glows", []):
                        try:
                            r.visible = False
                        except Exception:
                            pass
                    for lbl, _, _ in getattr(self, "rez_cancel_label_o_layers", []):
                        try:
                            lbl.color = (*self._ui_purple_hi, 0)
                        except Exception:
                            pass
                    for lbl, _, _ in getattr(self, "rez_cancel_label_x_layers", []):
                        try:
                            lbl.color = (*self._ui_purple_hot, 0)
                        except Exception:
                            pass
                    self._layout_hotbar_overlay()
                    return
                if active:
                    self.rez_label.color = (235, 235, 245, 255)
                    for lbl, _, a_mul in self.rez_cancel_label_o_layers:
                        lbl.color = (*self._ui_purple_hi, int(220 * a_mul))
                    for lbl, _, a_mul in self.rez_cancel_label_x_layers:
                        lbl.color = (*self._ui_purple_hot, int(255 * a_mul))
                    for shape in (
                        self.rez_bar_bg,
                        self.rez_bar_fill,
                        self.rez_bar_fill_hi,
                        self.rez_bar_shine,
                        *self.rez_bar_glitch,
                        self.rez_cancel_bg,
                    ):
                        shape.visible = True
                    for r in self.rez_cancel_glows:
                        r.visible = True
                    self._layout_hotbar_overlay()
                    return
                self.rez_label.text = ""
                self.rez_label.color = (235, 235, 245, 0)
                for lbl, _, _ in self.rez_cancel_label_o_layers:
                    lbl.color = (*self._ui_purple_hi, 0)
                for lbl, _, _ in self.rez_cancel_label_x_layers:
                    lbl.color = (*self._ui_purple_hot, 0)
                for shape in (
                    self.rez_bar_bg,
                    self.rez_bar_fill,
                    self.rez_bar_fill_hi,
                    self.rez_bar_shine,
                    *self.rez_bar_glitch,
                    self.rez_cancel_bg,
                ):
                    shape.visible = False
                for r in self.rez_cancel_glows:
                    r.visible = False
                self._layout_hotbar_overlay()

            def _terminal_busy(self) -> bool:
                # "Rezzing" is the umbrella state for tool-driven operations
                # (pool expansion, etc.). Environment generation is part of
                # the universe and should not lock build-mode interactions.
                return bool(self._rez_active)

            def _cancel_rez(self) -> None:
                self._rez_active_gen = None
                self._rez_progress = 0.0
                self._rez_message = ""
                self._rez_last_label = ""
                self._terminate_rez_worker()
                self._clear_rez_live_preview()
                self._set_rez_ui(False)
                self._effects = []

            def _commit_structure_delta_fade(self) -> None:
                fades = getattr(self, "_structure_delta_fades", [])
                if not fades:
                    return
                fade = fades.pop(0)
                if fades:
                    # Keep subsequent fades alive: switch to the next base batch.
                    next_fade = fades[0]
                    try:
                        self._batch = next_fade.base_batch  # type: ignore[assignment]
                    except Exception:
                        pass
                    self._batch_pivot_center = tuple(next_fade.pivot_center)
                else:
                    # No more fades: show the fully-committed final batch.
                    try:
                        self._batch = fade.final_batch  # type: ignore[assignment]
                    except Exception:
                        pass
                    self._batch_pivot_center = tuple(fade.pivot_center)

            def _tick_structure_delta_fade(self) -> None:
                fades = getattr(self, "_structure_delta_fades", [])
                if not fades:
                    return
                now = time.monotonic()
                while fades:
                    fade = fades[0]
                    if fade.duration_s <= 0.0:
                        self._commit_structure_delta_fade()
                        continue
                    if now >= (float(fade.start_t) + float(fade.duration_s)):
                        self._commit_structure_delta_fade()
                        continue
                    break

            def _apply_structure_and_batch(
                self,
                structure: Structure,
                report: list[str],
                *,
                reset_view: bool,
                adjust_distance: bool,
                enable_delta_fade: bool = True,
            ) -> None:
                new_fade: fx_mod.StructureDeltaFade | None = None
                prev_full_batch = getattr(self, "_structure_full_batch", None)
                prev_full_pivot = tuple(getattr(self, "_structure_full_pivot_center", self._batch_pivot_center))
                had_fades = bool(getattr(self, "_structure_delta_fades", []))
                if self._rez_active:
                    cooldown_s = float(param_store.get("camera.autoframe.cooldown_s"))
                    if cooldown_s > 1e-6:
                        if (time.monotonic() - float(self._camera_last_user_input_t)) < cooldown_s:
                            reset_view = False
                            adjust_distance = False
                prev_struct = self._current_structure
                prev_batch = self._batch
                prev_batch_pivot = self._batch_pivot_center
                prev_pick_positions = getattr(self, "_pick_positions", set())
                prev_pick_bounds_i = getattr(self, "_pick_bounds_i", None)
                prev_pick_bounds = getattr(self, "_pick_bounds", None)
                self._expansion_report = report
                self._current_structure = structure
                self._pick_positions = {
                    b.pos for b in structure.blocks if _block_id_base(b.block_id) != "minecraft:structure_void"
                }
                if self._pick_positions:
                    xs = [p[0] for p in self._pick_positions]
                    ys = [p[1] for p in self._pick_positions]
                    zs = [p[2] for p in self._pick_positions]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    min_z, max_z = min(zs), max(zs)
                    self._pick_bounds_i = (min_x, min_y, min_z, max_x, max_y, max_z)
                    self._pick_bounds = (
                        (float(min_x), float(min_y), float(min_z)),
                        (float(max_x + 1), float(max_y + 1), float(max_z + 1)),
                    )
                else:
                    self._pick_bounds_i = None
                    self._pick_bounds = None

                prev_initial = self._initial_distance
                try:
                    final_batch, final_initial = build_batch_for_structure(
                        structure,
                        source=texture_source,
                        resolver=resolver,
                        center_override=self._pivot_center,
                    )
                except Exception as e:
                    self._current_structure = prev_struct
                    self._batch = prev_batch
                    self._batch_pivot_center = prev_batch_pivot
                    self._pick_positions = prev_pick_positions
                    self._pick_bounds_i = prev_pick_bounds_i
                    self._pick_bounds = prev_pick_bounds
                    self._initial_distance = prev_initial
                    self._expansion_report = ["Render build failed"]
                    self._set_viewer_error(
                        "load",
                        "Render build error\n" + self._format_error_one_line(e),
                        detail=traceback.format_exc(limit=20),
                    )
                    self._update_status()
                    self._update_list_labels()
                    return
                self._initial_distance = float(final_initial)
                # Always keep the *full* batch for the latest structure around,
                # even if we are currently drawing a "base" batch for delta fades.
                self._structure_full_batch = final_batch
                self._structure_full_pivot_center = self._pivot_center
                if not had_fades:
                    self._batch = final_batch
                    self._batch_pivot_center = self._pivot_center

                fade_s = 0.0
                try:
                    fade_s = float(param_store.get("rez.fade_s"))
                except Exception:
                    fade_s = 0.0
                if not math.isfinite(fade_s) or fade_s < 0.05:
                    fade_s = 0.0

                if enable_delta_fade and prev_struct is not None and fade_s > 0.0:
                    try:
                        old_by_pos: dict[Vec3i, BlockInstance] = {}
                        new_by_pos: dict[Vec3i, BlockInstance] = {}
                        for b in prev_struct.blocks:
                            if _block_id_base(b.block_id) == "minecraft:structure_void":
                                continue
                            old_by_pos[b.pos] = b
                        for b in structure.blocks:
                            if _block_id_base(b.block_id) == "minecraft:structure_void":
                                continue
                            new_by_pos[b.pos] = b

                        common: list[BlockInstance] = []
                        added: list[BlockInstance] = []
                        removed: list[BlockInstance] = []

                        for pos, old_b in old_by_pos.items():
                            new_b = new_by_pos.get(pos)
                            if new_b is not None and new_b.block_id == old_b.block_id:
                                common.append(new_b)
                            else:
                                removed.append(old_b)
                        for pos, new_b in new_by_pos.items():
                            old_b = old_by_pos.get(pos)
                            if old_b is None or old_b.block_id != new_b.block_id:
                                added.append(new_b)

                        if added or removed:
                            pivot = tuple(self._pivot_center)

                            def _sort_key(b: BlockInstance) -> tuple[int, int, int, str]:
                                return (int(b.pos[1]), int(b.pos[2]), int(b.pos[0]), str(b.block_id))

                            base_batch: object | None = None
                            # Fast path: only-add edits can reuse the previous *full* batch
                            # (avoids rebuilding the base from 'common').
                            if (
                                (not removed)
                                and prev_full_batch is not None
                                and tuple(prev_full_pivot) == tuple(pivot)
                            ):
                                base_batch = prev_full_batch
                            else:
                                base_struct = Structure(size=structure.size, blocks=tuple(sorted(common, key=_sort_key)))
                                base_batch, _ = build_batch_for_structure(
                                    base_struct,
                                    source=texture_source,
                                    resolver=resolver,
                                    center_override=pivot,
                                )

                            added_batch: object | None = None
                            if added:
                                add_struct = Structure(size=structure.size, blocks=tuple(sorted(added, key=_sort_key)))
                                added_batch, _ = build_batch_for_structure(
                                    add_struct,
                                    source=texture_source,
                                    resolver=resolver,
                                    center_override=pivot,
                                )

                            removed_batch: object | None = None
                            if removed:
                                rem_struct = Structure(size=structure.size, blocks=tuple(sorted(removed, key=_sort_key)))
                                removed_batch, _ = build_batch_for_structure(
                                    rem_struct,
                                    source=texture_source,
                                    resolver=resolver,
                                    center_override=pivot,
                                )

                            if base_batch is not None:
                                fades = getattr(self, "_structure_delta_fades", [])
                                was_empty = not bool(fades)
                                new_fade = fx_mod.StructureDeltaFade(
                                    start_t=float(time.monotonic()),
                                    duration_s=float(fade_s),
                                    pivot_center=pivot,
                                    base_batch=base_batch,
                                    final_batch=final_batch,
                                    added_batch=added_batch,
                                    removed_batch=removed_batch,
                                )
                                fades.append(new_fade)

                                # If this is the first fade, switch the drawn batch over to the
                                # base batch so the overlay batches can animate on top.
                                if was_empty:
                                    self._batch = base_batch  # type: ignore[assignment]
                                    self._batch_pivot_center = pivot
                    except Exception:
                        pass
                if reset_view:
                    self._reset_view()
                elif adjust_distance and prev_initial > 0:
                    factor = self._initial_distance / prev_initial
                    target = max(0.5, float(self.distance) * float(factor))
                    self._animate_camera_to(distance=target, duration_s=0.30)
                self._update_environment()
                self._update_status()
                # Start the delta fade *after* expensive work (env rebuild, layout, etc.)
                # so it can't "finish" before the next frame is drawn.
                if new_fade is not None:
                    try:
                        new_fade.start_t = float(time.monotonic())
                    except Exception:
                        pass

            def _set_jigsaw_state(self, state: JigsawExpansionState | None) -> None:
                self._jigsaw_state = state
                self._jigsaw_selected = None
                self._ender_vision_by_pos.clear()
                self._ender_vision_open.clear()
                self._ender_vision_used.clear()
                self._ender_vision_dead.clear()
                self._ender_vision_hover = None
                self._ender_vision_hover_pos = None
                self._ender_vision_last_mouse = (-1, -1)
                if state is None:
                    self._update_ender_vision_overlay()
                    return

                def _is_open(conn: JigsawConnector) -> bool:
                    return conn.pool not in {"", "minecraft:empty"} and conn.target not in {"", "minecraft:empty"}

                for c in state.connectors:
                    # Best-effort: keep the first connector if duplicates exist.
                    self._ender_vision_by_pos.setdefault(c.pos, c)
                    if _is_open(c) and c.pos not in state.consumed and c.pos not in state.dead_end:
                        self._ender_vision_open.append(c)
                for pos in state.consumed:
                    c = self._ender_vision_by_pos.get(pos)
                    if c is not None and _is_open(c):
                        self._ender_vision_used.append(c)
                for pos in state.dead_end:
                    c = self._ender_vision_by_pos.get(pos)
                    if c is not None and _is_open(c):
                        self._ender_vision_dead.append(c)
                self._update_ender_vision_overlay()

            def _layout_ender_vision_overlay(self) -> None:
                # Ender Vision is a purely-graphical "lens" overlay (no text).
                self.ender_vision_label.visible = False

            def _update_ender_vision_hover(self) -> None:
                if not self._ender_vision_active:
                    self._ender_vision_hover = None
                    self._ender_vision_hover_pos = None
                    return
                if not self._ender_vision_by_pos:
                    self._ender_vision_hover = None
                    self._ender_vision_hover_pos = None
                    return
                mx = int(self._mouse_x)
                my = int(self._mouse_y)
                if (mx, my) == self._ender_vision_last_mouse:
                    return
                self._ender_vision_last_mouse = (mx, my)
                ray = self._screen_ray_u(mx, my)
                if ray is None:
                    self._ender_vision_hover = None
                    self._ender_vision_hover_pos = None
                    return
                origin_u, dir_world = ray
                ox, oy, oz = origin_u
                dx, dy, dz = dir_world

                best: JigsawConnector | None = None
                best_pos: Vec3i | None = None
                best_d2 = 1e30

                for pos, c in self._ender_vision_by_pos.items():
                    px = float(pos[0]) + 0.5
                    py = float(pos[1]) + 0.5
                    pz = float(pos[2]) + 0.5
                    vx = px - ox
                    vy = py - oy
                    vz = pz - oz
                    t = vx * dx + vy * dy + vz * dz
                    if t <= 0.0:
                        continue
                    qx = ox + dx * t
                    qy = oy + dy * t
                    qz = oz + dz * t
                    ex = px - qx
                    ey = py - qy
                    ez = pz - qz
                    d2 = ex * ex + ey * ey + ez * ez
                    if d2 < best_d2:
                        best_d2 = d2
                        best = c
                        best_pos = pos

                # World-space pick radius (blocks). This is intentionally fairly
                # forgiving; the hovered connector is disambiguated by "closest
                # to the ray" anyway.
                pick_r = 0.45
                if best is None or best_d2 > pick_r * pick_r:
                    self._ender_vision_hover = None
                    self._ender_vision_hover_pos = None
                    return

                self._ender_vision_hover = best
                self._ender_vision_hover_pos = best_pos

            def _update_ender_vision_overlay(self) -> None:
                self.ender_vision_label.text = ""
                self.ender_vision_label.visible = False
                self._ender_vision_label_last_text = ""
                self._layout_ender_vision_overlay()

            def _start_rez_build(self, *, base: StructureTemplate, seeds: list[int], reset_view: bool, adjust_distance: bool) -> None:
                seeds_snapshot = list(seeds)
                self._rez_gen += 1
                gen = self._rez_gen
                self._rez_active_gen = gen
                self._rez_progress = 0.0
                self._rez_message = f"rezzing depth={len(seeds_snapshot)}…"
                self._rez_last_label = ""
                self._rez_worker_progress = 0.0
                self._rez_worker_message = str(self._rez_message)
                self._rez_result_received = False
                self._rez_pieces_received = 0
                self._rez_pieces_applied = 0
                self._rez_reset_view = reset_view
                self._rez_adjust_distance = adjust_distance
                self._rez_anim_s = 0.0
                self._clear_rez_live_preview()
                # Allow the first piece to land immediately (otherwise 2 pieces/s waits ~0.5s).
                self._rez_piece_tokens = 1.0
                self._init_rez_live_preview_from_current()
                self._set_rez_ui(True)
                self._update_rez_bar()

                self._terminate_rez_worker()
                ctx = multiprocessing.get_context("spawn")
                q: multiprocessing.Queue = ctx.Queue(maxsize=512)
                template_id = base.template_id
                self._rez_template_id = template_id
                self._rez_env_key = self._env_preset().name
                self._rez_seeds_snapshot = tuple(seeds_snapshot)
                seeds_to_compute = seeds_snapshot
                initial_structure: Structure | None = None
                initial_state: JigsawExpansionState | None = None
                initial_report: list[str] | None = None
                level_offset = 0
                total_depth = len(seeds_snapshot)
                if seeds_snapshot:
                    prefix_key = tuple(seeds_snapshot[:-1])
                    cached_prefix = self._jigsaw_cache.get(prefix_key)
                    if cached_prefix is not None:
                        initial_structure, initial_report, initial_state = cached_prefix
                        seeds_to_compute = [seeds_snapshot[-1]]
                        level_offset = len(prefix_key)
                proc = ctx.Process(
                    target=_rez_worker_main,
                    kwargs={
                        "out_q": q,
                        "gen": gen,
                        "datapack_path": datapack_path,
                        "work_pack_dir": work_pack_dir,
                        "template_id": template_id,
                        "seeds": seeds_to_compute,
                        "env_preset": self._env_preset().name,
                        "env_height_seed": int(self._env_height_seed),
                        "env_height_anchor_off": int(self._env_height_anchor_off),
                        "env_height_origin_x": int(self._env_height_origin_x),
                        "env_height_origin_z": int(self._env_height_origin_z),
                        "env_height_amp": int(self._env_terrain_amp),
                        "env_height_scale": float(self._env_terrain_scale),
                        "env_height_octaves": int(self._env_terrain_octaves),
                        "env_height_lacunarity": float(self._env_terrain_lacunarity),
                        "env_height_h": float(self._env_terrain_h),
                        "env_height_ridged_offset": float(self._env_terrain_ridged_offset),
                        "env_height_ridged_gain": float(self._env_terrain_ridged_gain),
                        "env_surface_y": int(self._env_base_y if self._env_base_y is not None else self._infer_env_surface_y()),
                        "rez_throttle_sleep_ms": float(param_store.get("rez.throttle.sleep_ms")),
                        "rez_throttle_every": int(param_store.get_int("rez.throttle.every")),
                        # Keep worker build pace aligned with the on-screen preview.
                        "rez_pieces_per_s": float(param_store.get("rez.pieces_per_s")),
                        "initial_structure": initial_structure,
                        "initial_state": initial_state,
                        "initial_report": list(initial_report) if initial_report is not None else None,
                        "level_offset": level_offset,
                        "total_depth": total_depth,
                    },
                    daemon=True,
                )
                proc.start()
                self._rez_queue = q
                self._rez_proc = proc

            def _poll_rez(self, dt: float) -> None:
                q = self._rez_queue
                if q is None:
                    return

                gen = self._rez_active_gen
                drained = 0
                hit_empty = False
                queue_broken = False
                while drained < 96:
                    drained += 1
                    try:
                        msg = q.get_nowait()
                    except py_queue.Empty:
                        hit_empty = True
                        break
                    except (EOFError, OSError):
                        queue_broken = True
                        hit_empty = True
                        break

                    if not isinstance(msg, tuple) or not msg:
                        continue
                    kind = msg[0]
                    if kind == "progress" and len(msg) == 4:
                        _, msg_gen, frac, text = msg
                        if gen is None or msg_gen != gen:
                            continue
                        self._rez_worker_progress = float(frac)
                        worker_msg = str(text)
                        # Avoid claiming we're "done" while we're still applying
                        # piece payloads on the main thread.
                        if worker_msg.strip().lower() == "rezzing done":
                            worker_msg = "finalizing…"
                        self._rez_worker_message = worker_msg
                        continue
                    if kind == "piece" and len(msg) == 4:
                        _, msg_gen, payload, _loc = msg
                        if gen is None or msg_gen != gen:
                            continue
                        if isinstance(payload, list):
                            self._rez_piece_queue.append(payload)
                            self._rez_pieces_received += 1
                        continue
                    if kind == "result" and len(msg) == 5:
                        _, msg_gen, structure, report, state = msg
                        if gen is None or msg_gen != gen:
                            continue
                        report_s: list[str] = [str(x) for x in report] if isinstance(report, list) else []
                        if isinstance(structure, Structure) and isinstance(report, list) and isinstance(state, JigsawExpansionState):
                            self._rez_pending_result = (structure, report_s, state)
                            self._rez_result_received = True
                        continue
                    if kind == "error" and len(msg) == 3:
                        _, msg_gen, text = msg
                        if gen is None or msg_gen != gen:
                            continue
                        self._clear_rez_live_preview()
                        self._expansion_report = [f"Rez failed: {text}"]
                        self._update_status()
                        self._rez_active_gen = None
                        self._set_rez_ui(False)
                        self._terminate_rez_worker()
                        return

                if queue_broken:
                    self._clear_rez_live_preview()
                    self._expansion_report = ["Rez failed: worker connection lost"]
                    self._update_status()
                    self._rez_active_gen = None
                    self._set_rez_ui(False)
                    self._terminate_rez_worker()
                    return

                pieces_per_s = 0.0
                try:
                    pieces_per_s = float(param_store.get("rez.pieces_per_s"))
                except Exception:
                    pieces_per_s = 0.0
                if not math.isfinite(pieces_per_s):
                    pieces_per_s = 0.0
                if pieces_per_s <= 0.0:
                    budget_pieces = len(self._rez_piece_queue)
                else:
                    dt_s = max(0.0, float(dt))
                    self._rez_piece_tokens += dt_s * float(pieces_per_s)
                    # Cap to avoid bursty catch-up after a stall.
                    self._rez_piece_tokens = min(self._rez_piece_tokens, max(1.0, float(pieces_per_s)))
                    budget_pieces = min(len(self._rez_piece_queue), int(self._rez_piece_tokens))
                    if budget_pieces > 0:
                        self._rez_piece_tokens -= float(budget_pieces)

                def _apply_piece_payload(payload: list[object]) -> None:
                    new_positions: list[Vec3i] = []
                    for item in payload:
                        if not (
                            isinstance(item, tuple)
                            and len(item) == 3
                            and isinstance(item[0], tuple)
                            and len(item[0]) == 3
                        ):
                            continue
                        pos = (int(item[0][0]), int(item[0][1]), int(item[0][2]))
                        block_id = str(item[1])
                        color_key = str(item[2])
                        base_id = _block_id_base(block_id)
                        if pos in self._rez_live_positions or pos in self._rez_live_pending_positions:
                            continue
                        self._rez_live_pending.append((pos, block_id, color_key))
                        self._rez_live_pending_positions.add(pos)
                        if base_id != "minecraft:structure_void":
                            self._rez_bounds_add(pos)
                        new_positions.append(pos)

                    if not new_positions:
                        return
                    if int(self._env_index) != 0 and not self._env_suppress_updates:
                        self._update_environment(positions=new_positions)
                    self._rez_maybe_autoframe_camera()

                for _ in range(int(budget_pieces)):
                    if not self._rez_piece_queue:
                        break
                    payload = self._rez_piece_queue.popleft()
                    if isinstance(payload, list):
                        _apply_piece_payload(payload)
                        self._rez_pieces_applied += 1

                # Progress UI: until we have the final result from the worker,
                # show the worker's estimate. Once the worker is finished, shift
                # to "apply progress" so the progress bar doesn't claim to be
                # done while pieces are still visually rezzing in.
                worker_frac = max(0.0, min(1.0, float(getattr(self, "_rez_worker_progress", 0.0))))
                worker_msg = str(getattr(self, "_rez_worker_message", ""))
                result_received = bool(getattr(self, "_rez_result_received", False))
                if result_received:
                    total = max(0, int(getattr(self, "_rez_pieces_received", 0)))
                    applied = max(0, int(getattr(self, "_rez_pieces_applied", 0)))
                    if total > 0:
                        applied = min(applied, total)
                        apply_frac = float(applied) / float(total)
                        # Split the bar: 70% worker build, 30% apply-to-screen.
                        worker_weight = 0.70
                        self._rez_progress = worker_weight + (1.0 - worker_weight) * apply_frac
                        if applied < total:
                            self._rez_message = f"rezzing {applied}/{total}…"
                        else:
                            self._rez_message = "finalizing…"
                    else:
                        self._rez_progress = max(self._rez_progress, 0.95)
                        self._rez_message = "finalizing…"
                else:
                    worker_weight = 0.70
                    self._rez_progress = worker_weight * worker_frac
                    self._rez_message = worker_msg

                if not self._rez_piece_queue and self._rez_pending_result is not None:
                    structure, report_s, state = self._rez_pending_result
                    self._rez_pending_result = None
                    self._clear_rez_live_preview()
                    self._set_jigsaw_state(state)
                    self._apply_structure_and_batch(
                        structure,
                        report_s,
                        reset_view=self._rez_reset_view,
                        adjust_distance=self._rez_adjust_distance,
                        enable_delta_fade=False,
                    )
                    if self._rez_template_id == self._jigsaw_cache_template_id and self._rez_env_key == self._jigsaw_cache_env_key:
                        self._jigsaw_cache[tuple(self._rez_seeds_snapshot)] = (structure, report_s, state)
                    self._rez_active_gen = None
                    self._set_rez_ui(False)
                    self._terminate_rez_worker()
                    return

                proc = self._rez_proc
                if gen is not None and proc is not None:
                    try:
                        alive = proc.is_alive()
                    except Exception:
                        alive = False
                    if not alive and self._rez_pending_result is None and hit_empty:
                        self._clear_rez_live_preview()
                        self._expansion_report = ["Rez failed: worker exited"]
                        self._update_status()
                        self._rez_active_gen = None
                        self._set_rez_ui(False)
                        self._terminate_rez_worker()

            def _apply_ui_font_scale(self) -> None:
                scale = float(getattr(self, "_ui_font_scale", 1.0))
                if not math.isfinite(scale) or scale <= 0.0:
                    scale = 1.0

                self.line_height = self._ui_i(18.0)
                self.status_line_height = self._ui_i(15.0)
                self.log_line_height = self._ui_i(14.0)
                self.header_h = self._ui_i(54.0) + (self.status_lines_max * self.status_line_height) + self._ui_i(8.0)

                if getattr(self, "_log_panel_tween", None) is None:
                    self.log_panel_h = float(self._compute_log_panel_height())

                def _set_label_size(label: pyglet.text.Label | None, base_px: float) -> None:
                    if label is None:
                        return
                    try:
                        label.font_size = self._ui_i(base_px)
                    except Exception:
                        pass

                _set_label_size(getattr(self, "title", None), 13.0)
                _set_label_size(getattr(self, "subtitle", None), 11.0)
                _set_label_size(getattr(self, "log_title", None), 10.0)
                _set_label_size(getattr(self, "log_toggle_label", None), 14.0)
                _set_label_size(getattr(self, "search_label", None), 12.0)
                _set_label_size(getattr(self, "search_count_label", None), 10.0)
                _set_label_size(getattr(self, "rez_label", None), 12.0)
                _set_label_size(getattr(self, "help_label", None), 12.0)
                _set_label_size(getattr(self, "error_label", None), 12.0)
                _set_label_size(getattr(self, "brand_label", None), 10.0)
                _set_label_size(getattr(self, "ender_vision_label", None), 11.0)
                _set_label_size(getattr(self, "palette_title", None), 14.0)
                _set_label_size(getattr(self, "palette_search_label", None), 12.0)
                _set_label_size(getattr(self, "palette_hint_label", None), 10.0)

                try:
                    self.search_bg.height = self._ui_i(22.0)
                except Exception:
                    pass
                try:
                    self.palette_search_bg.height = self._ui_i(18.0)
                except Exception:
                    pass

                for lbl in getattr(self, "line_labels", []):
                    _set_label_size(lbl, 11.0)
                for lbl in getattr(self, "log_labels", []):
                    _set_label_size(lbl, 10.0)
                for lbl in getattr(self, "status_labels", []):
                    _set_label_size(lbl, 10.0)
                for lbl, _dx, _a in getattr(self, "search_cancel_label_o_layers", []):
                    _set_label_size(lbl, 12.0)
                for lbl, _dx, _a in getattr(self, "search_cancel_label_x_layers", []):
                    _set_label_size(lbl, 12.0)
                for lbl, _dx, _a in getattr(self, "rez_cancel_label_o_layers", []):
                    _set_label_size(lbl, 20.0)
                for lbl, _dx, _a in getattr(self, "rez_cancel_label_x_layers", []):
                    _set_label_size(lbl, 20.0)
                for lbl in getattr(self, "hotbar_slot_labels", []):
                    _set_label_size(lbl, 13.0)
                for lbl in getattr(self, "hotbar_slot_numbers", []):
                    _set_label_size(lbl, 9.0)

                self._layout_ui()
                self._update_status()

            def _layout_ui(self, *, ensure_labels: bool = True) -> None:
                self.sidebar_bg.width = self.sidebar_width
                self.sidebar_bg.height = self.height
                self.sidebar_divider.x = self.sidebar_width - 1
                self.sidebar_divider.height = self.height
                target_log_h = float(self._compute_log_panel_height())
                if self._log_panel_tween is None:
                    self.log_panel_h = target_log_h
                self.log_bg.width = self.sidebar_width
                self.log_bg.height = self.log_panel_h
                self.log_bg.y = 0
                self.list_bg.x = 0
                self.list_bg.y = self.log_panel_h
                self.list_bg.width = self.sidebar_width
                self.list_bg.height = max(1, self.height - self.header_h - self.log_panel_h)
                self.title.y = self.height - self._ui_i(12.0)
                self.subtitle.y = self.height - self._ui_i(32.0)
                self.log_title.y = self.log_panel_h - float(self._ui_i(10.0))
                toggle_size = self._ui_i(18.0)
                self.log_toggle_bg.width = toggle_size
                self.log_toggle_bg.height = toggle_size
                self.log_toggle_bg.x = int(self.sidebar_width - self._ui_i(12.0) - toggle_size)
                self.log_toggle_bg.y = int(self.log_panel_h - float(self._ui_i(28.0)))
                self.log_toggle_label.x = self.log_toggle_bg.x + self.log_toggle_bg.width / 2.0
                self.log_toggle_label.y = self.log_toggle_bg.y + self.log_toggle_bg.height / 2.0
                self.log_toggle_label.text = "▸" if self._log_collapsed else "▾"

                list_h = max(1, self.height - self.header_h - self.log_panel_h)
                list_top = self.height - self.header_h
                self.log_frame_top.y = self.log_panel_h - 1
                self.log_frame_top.width = self.sidebar_width
                self.log_frame_bottom.width = self.sidebar_width
                self.log_frame_left.height = self.log_panel_h
                self.log_frame_right.x = self.sidebar_width - 1
                self.log_frame_right.height = self.log_panel_h

                self.list_frame_bottom.y = self.log_panel_h
                self.list_frame_bottom.width = self.sidebar_width
                self.list_frame_top.y = list_top - 1
                self.list_frame_top.width = self.sidebar_width
                self.list_frame_left.y = self.log_panel_h
                self.list_frame_left.height = list_h
                self.list_frame_right.x = self.sidebar_width - 1
                self.list_frame_right.y = self.log_panel_h
                self.list_frame_right.height = list_h
                self.list_shadow.y = self.log_panel_h - 3
                self.list_shadow.width = self.sidebar_width
                search_h = float(self.search_bg.height)
                search_y = float(list_top) - search_h - self._ui_f(8.0)
                min_y = float(self.log_panel_h) + self._ui_f(6.0)
                if search_y < min_y:
                    search_y = min_y
                # Keep search text safely left of the scrollbar clip.
                self.search_bg.width = max(1, self.sidebar_width - self._ui_i(29.0))
                self.search_bg.x = self._ui_i(12.0)
                self.search_bg.y = int(search_y)
                self.search_bg.visible = self._search_ui_visible()
                self.search_glow.x = self.search_bg.x - self._ui_i(2.0)
                self.search_glow.y = self.search_bg.y - self._ui_i(2.0)
                self.search_glow.width = self.search_bg.width + self._ui_i(4.0)
                self.search_glow.height = self.search_bg.height + self._ui_i(4.0)
                self.search_glow.visible = self._search_ui_visible()
                self.search_label.x = self.search_bg.x + self._ui_i(8.0)
                self.search_label.y = self.search_bg.y + self.search_bg.height / 2.0
                cancel_size = int(round(float(self.search_bg.height) * 1.5))
                cancel_pad = self._ui_i(2.0)
                self.search_cancel_bg.x = int(self.search_bg.x + self.search_bg.width - cancel_size - cancel_pad)
                self.search_cancel_bg.y = int(self.search_bg.y + (self.search_bg.height - cancel_size) / 2.0)
                self.search_cancel_bg.width = cancel_size
                self.search_cancel_bg.height = cancel_size
                self.search_cancel_bg.visible = self._search_ui_visible()
                cx = self.search_cancel_bg.x + self.search_cancel_bg.width / 2.0
                cy = self.search_cancel_bg.y + self.search_cancel_bg.height / 2.0
                spread_px = max(1, int(round(float(cancel_size) * 0.10)))
                for lbl, dx_unit, _ in self.search_cancel_label_o_layers:
                    lbl.x = cx + dx_unit * spread_px
                    lbl.y = cy
                for lbl, dx_unit, _ in self.search_cancel_label_x_layers:
                    lbl.x = cx + dx_unit * spread_px
                    lbl.y = cy
                glow_pad = self._ui_i(12.0)
                for r in self.search_cancel_glows:
                    r.x = int(self.search_cancel_bg.x - glow_pad)
                    r.y = int(self.search_cancel_bg.y - glow_pad)
                    r.width = int(self.search_cancel_bg.width + glow_pad * 2)
                    r.height = int(self.search_cancel_bg.height + glow_pad * 2)
                    r.visible = self._search_ui_visible()
                self.search_count_label.x = float(self.search_cancel_bg.x) - self._ui_f(6.0)
                self.search_count_label.y = self.search_bg.y + self.search_bg.height / 2.0
                if not self._search_ui_visible():
                    self.search_label.color = (*self._ui_purple, 0)
                    self.search_count_label.color = (*self._ui_purple, 0)
                    self.search_cancel_bg.opacity = 0
                    for r in self.search_cancel_glows:
                        r.opacity = 0
                    for lbl, _, _ in self.search_cancel_label_o_layers:
                        lbl.color = (*self._ui_purple_hi, 0)
                    for lbl, _, _ in self.search_cancel_label_x_layers:
                        lbl.color = (*self._ui_purple_hot, 0)
                # When the search bar is visible, shrink the scroll list area to
                # make room (instead of just pushing the text down).
                list_items_top = float(self._list_items_top_y())
                list_h = max(1, int(list_items_top - float(self.log_panel_h)))
                self.list_bg.y = self.log_panel_h
                self.list_bg.height = list_h
                self.list_frame_bottom.y = self.log_panel_h
                self.list_frame_left.y = self.log_panel_h
                self.list_frame_left.height = list_h
                self.list_frame_right.y = self.log_panel_h
                self.list_frame_right.height = list_h
                self.list_frame_top.y = int(list_items_top) - 1
                self._update_rez_bar()
                if ensure_labels or not self.status_labels:
                    self._ensure_status_labels()
                status_top = self.height - self._ui_i(52.0)
                for i, lbl in enumerate(self.status_labels):
                    lbl.y = status_top - i * self.status_line_height
                if ensure_labels or (not self._log_collapsed and not self.log_labels):
                    self._ensure_log_labels()
                log_top = float(self.log_panel_h) - float(self._ui_i(28.0))
                for i, lbl in enumerate(self.log_labels):
                    lbl.y = log_top - i * self.log_line_height
                if ensure_labels or not self.line_labels:
                    self._ensure_line_labels()
                self._update_list_labels(ensure_selection_visible=False)
                try:
                    self.brand_label.x = int(self.width - 12)
                    self.brand_label.y = 12
                except Exception:
                    pass
                try:
                    self.walk_mode_label.x = int(self.width * 0.5)
                    self.walk_mode_label.y = int(self.height - 12)
                except Exception:
                    pass
                self._layout_ender_vision_overlay()
                self._layout_hotbar_overlay()
                self._layout_error_overlay()

            def _tick_hotbar_panel_tween(self) -> None:
                tween = self._hotbar_panel_tween
                if tween is None:
                    return
                # Update the hotbar layout each frame while animating.
                self._layout_hotbar_overlay()

            def _layout_hotbar_overlay(self) -> None:
                show = bool(self._build_enabled) and (not self._rez_active)

                view_w = float(self.width - self.sidebar_width)
                view_x0 = float(self.sidebar_width)
                if view_w < 220.0:
                    show = False

                now = time.monotonic()
                if bool(show) != bool(self._hotbar_panel_target_show):
                    self._hotbar_panel_target_show = bool(show)
                    start = float(self._hotbar_panel_p)
                    end = 1.0 if show else 0.0
                    if abs(start - end) > 1e-6:
                        self._hotbar_panel_tween = Tween(
                            now,
                            0.18,
                            start=start,
                            end=end,
                            ease=ease_smoothstep,
                        )
                    else:
                        self._hotbar_panel_tween = None
                        self._hotbar_panel_p = end

                tween = self._hotbar_panel_tween
                if tween is not None:
                    p = float(tween.value(now))
                    if tween.done(now):
                        p = float(tween.end)
                        self._hotbar_panel_tween = None
                else:
                    p = 1.0 if show else 0.0
                self._hotbar_panel_p = float(p)

                if p <= 1e-3:
                    self.hotbar_panel_bg.visible = False
                    for seq in (
                        self.hotbar_slot_borders,
                        self.hotbar_slot_fills,
                        self.hotbar_slot_labels,
                        self.hotbar_slot_numbers,
                        self.hotbar_slot_icons,
                    ):
                        for w in seq:
                            w.visible = False
                    return

                pad = 6.0
                slot = 44.0
                total_w = slot * 10.0 + pad * 9.0
                max_w = view_w - 22.0
                if total_w > max_w:
                    slot = max(24.0, (max_w - pad * 9.0) / 10.0)
                    total_w = slot * 10.0 + pad * 9.0

                x0 = view_x0 + (view_w - total_w) * 0.5
                y0 = 16.0

                panel_pad = 10.0
                panel_h = slot + panel_pad * 2.0
                # Slide the hotbar up from below while fading in.
                slide = (panel_h + 26.0) * (1.0 - float(p))
                y0 = float(y0) - float(slide)
                self.hotbar_panel_bg.x = int(x0 - panel_pad)
                self.hotbar_panel_bg.y = int(y0 - panel_pad)
                self.hotbar_panel_bg.width = int(total_w + panel_pad * 2.0)
                self.hotbar_panel_bg.height = int(panel_h)
                self.hotbar_panel_bg.visible = True

                border_frac = float(param_store.get("ui.selection.border.frac") or 0.0)
                if not math.isfinite(border_frac) or border_frac < 0.0:
                    border_frac = 0.0
                ring_px = 0
                if border_frac > 1e-6:
                    ring_px = max(1, int(round(border_frac * slot)))

                for i in range(10):
                    sx = x0 + float(i) * (slot + pad)
                    sy = y0
                    border = self.hotbar_slot_borders[i]
                    fill = self.hotbar_slot_fills[i]
                    label = self.hotbar_slot_labels[i]
                    num = self.hotbar_slot_numbers[i]

                    fill_inset = 0.0 if ring_px <= 0 else 1.0
                    fill.x = int(sx + fill_inset)
                    fill.y = int(sy + fill_inset)
                    fill.width = max(1, int(slot - fill_inset * 2.0))
                    fill.height = max(1, int(slot - fill_inset * 2.0))

                    border.x = int(fill.x - ring_px)
                    border.y = int(fill.y - ring_px)
                    border.width = max(1, int(fill.width + ring_px * 2))
                    border.height = max(1, int(fill.height + ring_px * 2))
                    border.visible = True
                    fill.visible = True

                    label.x = float(fill.x) + float(fill.width) / 2.0
                    label.y = float(fill.y) + float(fill.height) / 2.0
                    label.visible = True

                    num.x = float(sx) + 4.0
                    num.y = float(sy) + float(slot) - 4.0
                    num.visible = True

                self._update_hotbar_ui()
                # Apply the fade after `_update_hotbar_ui` sets selection colors.
                fade = float(p)
                if fade < 0.0:
                    fade = 0.0
                if fade > 1.0:
                    fade = 1.0
                self.hotbar_panel_bg.opacity = int(round(160 * fade))
                for border in self.hotbar_slot_borders:
                    border.opacity = int(round(float(border.opacity) * fade))
                for fill in self.hotbar_slot_fills:
                    fill.opacity = int(round(float(fill.opacity) * fade))
                for lbl in self.hotbar_slot_labels:
                    r, g, b, a = lbl.color
                    lbl.color = (int(r), int(g), int(b), int(round(float(a) * fade)))
                for lbl in self.hotbar_slot_numbers:
                    r, g, b, a = lbl.color
                    lbl.color = (int(r), int(g), int(b), int(round(float(a) * fade)))
                for icon in self.hotbar_slot_icons:
                    try:
                        icon.opacity = int(round(255 * fade))
                    except Exception:
                        pass

            def _toggle_debug_panel(self) -> None:
                self._walk_mode_force_exit(reason="tool_window")
                # Legacy help widgets are retained for now but never shown.
                for w in (
                    self.help_bg,
                    self.help_frame_top,
                    self.help_frame_bottom,
                    self.help_frame_left,
                    self.help_frame_right,
                    self.help_label,
                ):
                    w.visible = False
                if self._debug_window is not None:
                    self._request_tool_window_close_handoff(
                        source="debug",
                        palette_window=None,
                        debug_window=self._debug_window,
                        viewport_window=None,
                        close_trigger="parent_toggle",
                    )
                    self._debug_panel_active = False
                    # Fallback: if the child close callback does not fire
                    # (platform race), still hand focus back to main.
                    self._restore_main_window_focus(source="debug")
                    return

                def _on_closed() -> None:
                    self._consume_tool_window_close_request_path(source="debug", window_obj=self._debug_window)
                    self._debug_panel_active = False
                    self._on_tool_window_closed(source="debug", attr_name="_debug_window")

                self._debug_panel_active = True
                self._debug_panel_last_text = ""
                self._update_debug_panel()
                self._debug_window = debug_window_mod.create_debug_window(
                    pyglet=pyglet,
                    get_text=lambda: (self._update_debug_panel() or self._debug_panel_last_text),
                    store=param_store,
                    font_name=self.ui_font_name,
                    on_closed=_on_closed,
                    theme_from_store=_termui_theme_from_store,
                )
                try:
                    vx, vy = self.get_location()
                    self._debug_window.set_location(int(vx + 80), int(vy + 60))
                except Exception:
                    pass

            def _viewport_window_offset(self, slot_index: int) -> tuple[int, int]:
                n = max(1, int(slot_index))
                return (max(80, int(float(self.width) * 0.55) + (n - 1) * 36), int(40 + (n - 1) * 28))

            def _on_companion_viewport_closed(self, viewport_id: int, win: object | None = None) -> None:
                current = self._viewport_windows.get(int(viewport_id))
                if win is not None and current is not None and current is not win:
                    return
                self._viewport_windows.remove(int(viewport_id))
                if self._secondary_viewport_id == int(viewport_id):
                    self._secondary_viewport_id = None
                self._restore_main_window_focus(source="viewport")

            def _prune_viewport_windows(self) -> tuple[int, ...]:
                removed = self._viewport_windows.prune(is_alive=is_window_alive)
                for viewport_id in removed:
                    if self._secondary_viewport_id == int(viewport_id):
                        self._secondary_viewport_id = None
                return removed

            def _open_viewport_window(self, *, make_secondary: bool) -> int | None:
                self._prune_viewport_windows()
                viewport_id = int(self._viewport_windows.allocate_id())
                try:
                    win = CompanionViewportWindow(owner=self, viewport_id=viewport_id)
                except Exception as e:
                    self._expansion_report.append(f"Viewport open failed: {type(e).__name__}: {e}")
                    self._update_status()
                    return None

                self._viewport_windows.attach(viewport_id, win)
                if make_secondary:
                    self._secondary_viewport_id = int(viewport_id)

                try:
                    vx, vy = self.get_location()
                    ids = self._viewport_windows.ids()
                    try:
                        slot_index = int(ids.index(viewport_id) + 1)
                    except Exception:
                        slot_index = int(self._viewport_windows.count())
                    dx, dy = self._viewport_window_offset(slot_index)
                    win.set_location(int(vx + dx), int(vy + dy))
                except Exception:
                    pass
                return viewport_id

            def _close_viewport_window(self, viewport_id: int) -> None:
                viewport_id = int(viewport_id)
                win = self._viewport_windows.get(viewport_id)
                self._viewport_windows.remove(viewport_id)
                if self._secondary_viewport_id == viewport_id:
                    self._secondary_viewport_id = None
                if win is None:
                    self._restore_main_window_focus(source="viewport")
                    return
                try:
                    win.close()
                except Exception:
                    self._restore_main_window_focus(source="viewport")

            def _close_all_viewport_windows(self) -> None:
                self._secondary_viewport_id = None
                self._viewport_windows.close_all()

            def _open_additional_viewport_window(self) -> None:
                self._open_viewport_window(make_secondary=False)

            def _toggle_second_viewport_window(self) -> None:
                self._prune_viewport_windows()
                viewport_id = self._secondary_viewport_id
                if viewport_id is not None and self._viewport_windows.get(int(viewport_id)) is not None:
                    self._close_viewport_window(int(viewport_id))
                    return
                self._open_viewport_window(make_secondary=True)

            def _layout_help_overlay(self) -> None:
                # Deprecated: the old help overlay has been replaced by the
                # TermUI debug panel (D). Keep legacy widgets hidden.
                for w in (
                    self.help_bg,
                    self.help_frame_top,
                    self.help_frame_bottom,
                    self.help_frame_left,
                    self.help_frame_right,
                    self.help_label,
                ):
                    w.visible = False

            def _format_error_one_line(self, e: Exception) -> str:
                try:
                    msg = str(e).strip()
                except Exception:
                    msg = ""
                msg = msg.replace("\r", " ").replace("\n", " ")
                if msg:
                    out = f"{type(e).__name__}: {msg}"
                else:
                    out = type(e).__name__
                if len(out) > 220:
                    out = out[:219] + "…"
                return out

            def _clear_loaded_model(self) -> None:
                # If a selection fails to load, clear the previous model so we
                # don’t leave stale geometry on screen.
                try:
                    self._structure_delta_fade = None
                except Exception:
                    pass
                try:
                    self._batch = pyglet.graphics.Batch()
                except Exception:
                    pass
                self._current_structure = None
                self._pick_positions = set()
                self._pick_bounds = None
                self._pick_bounds_i = None
                self._base_template = None
                self._jigsaw_cache_template_id = ""
                self._jigsaw_cache_env_key = ""
                self._jigsaw_cache = {}
                self._jigsaw_reset()
                self._set_jigsaw_state(None)

            def _clear_viewer_error(self) -> None:
                if not self._viewer_error_kind and not self._viewer_error_text:
                    return
                self._viewer_error_kind = ""
                self._viewer_error_text = ""
                self._viewer_error_last_detail = ""
                self._viewer_error_last_t = 0.0
                self._viewer_error_retry_after_t = 0.0
                try:
                    self.error_label.text = ""
                except Exception:
                    pass
                self._layout_error_overlay()

            def _set_viewer_error(self, kind: str, text: str, *, detail: str = "") -> None:
                if not isinstance(text, str):
                    text = str(text)
                now = time.monotonic()
                self._viewer_error_kind = str(kind)
                self._viewer_error_text = text.strip()
                self._viewer_error_last_t = float(now)
                if self._viewer_error_kind == "load":
                    self._clear_loaded_model()
                if isinstance(detail, str) and detail:
                    self._viewer_error_last_detail = detail
                if self._viewer_error_kind == "render":
                    self._viewer_error_retry_after_t = float(now + 0.50)
                else:
                    self._viewer_error_retry_after_t = 0.0

                # Pulse existing sparks/glitch overlay as a visual "error" indicator.
                self._fx_error_pulse_start_t = float(now)
                self._fx_error_pulse_until_t = float(now + 2.0)
                try:
                    self._trigger_channel_change_fx()
                except Exception:
                    pass

                try:
                    self.error_label.text = self._viewer_error_text
                except Exception:
                    pass
                self._layout_error_overlay()

                try:
                    if self._viewer_error_last_detail:
                        print(f"[enderterm] {self._viewer_error_kind} error: {self._viewer_error_text}", file=sys.stderr)
                        print(self._viewer_error_last_detail, file=sys.stderr)
                    else:
                        print(f"[enderterm] {self._viewer_error_kind} error: {self._viewer_error_text}", file=sys.stderr)
                except Exception:
                    pass

            def _layout_error_overlay(self) -> None:
                show = bool(self._viewer_error_text)
                for w in (
                    self.error_bg,
                    self.error_frame_top,
                    self.error_frame_bottom,
                    self.error_frame_left,
                    self.error_frame_right,
                    self.error_label,
                ):
                    w.visible = show
                if not show:
                    return

                view_x0 = float(self.sidebar_width)
                view_w = float(self.width) - float(self.sidebar_width)
                pad = float(self._ui_i(12.0))
                if view_w < 60.0:
                    for w in (
                        self.error_bg,
                        self.error_frame_top,
                        self.error_frame_bottom,
                        self.error_frame_left,
                        self.error_frame_right,
                        self.error_label,
                    ):
                        w.visible = False
                    return

                box_w = min(720.0, max(260.0, view_w - pad * 2))
                box_h = float(self._ui_i(56.0))
                x0 = view_x0 + pad
                y1 = float(self.height) - pad
                y0 = y1 - box_h

                self.error_bg.x = x0
                self.error_bg.y = y0
                self.error_bg.width = box_w
                self.error_bg.height = box_h

                self.error_frame_top.x = x0
                self.error_frame_top.y = y1 - 1
                self.error_frame_top.width = box_w
                self.error_frame_top.height = 1

                self.error_frame_bottom.x = x0
                self.error_frame_bottom.y = y0
                self.error_frame_bottom.width = box_w
                self.error_frame_bottom.height = 1

                self.error_frame_left.x = x0
                self.error_frame_left.y = y0
                self.error_frame_left.width = 1
                self.error_frame_left.height = box_h

                self.error_frame_right.x = x0 + box_w - 1
                self.error_frame_right.y = y0
                self.error_frame_right.width = 1
                self.error_frame_right.height = box_h

                self.error_label.x = x0 + pad
                self.error_label.y = y1 - pad
                self.error_label.width = max(1, int(round(box_w - pad * 2)))

            def _palette_visible(self) -> bool:
                return self._palette_window is not None

            def _toggle_palette(self) -> None:
                self._walk_mode_force_exit(reason="tool_window")
                # Palette is a separate window; toggle it on/off.
                win = self._palette_window
                if win is not None:
                    self._request_tool_window_close_handoff(
                        source="palette",
                        palette_window=win,
                        debug_window=None,
                        viewport_window=None,
                        close_trigger="parent_toggle",
                    )
                    # Fallback: if the child close callback does not fire
                    # (platform race), still hand focus back to main.
                    self._restore_main_window_focus(source="palette")
                    return

                self._ensure_palette_loaded()

                # Sync selection to the current build block if possible.
                try:
                    want = str(self._build_selected_block_id)
                    for i, e in enumerate(self._palette_entries):
                        if str(getattr(e, "block_id", "")) == want:
                            self._palette_selected = int(i)
                            break
                except Exception:
                    pass

                def _load_tex(jar_rel: str) -> object | None:
                    tex = None
                    if jar_rel and texture_source is not None:
                        tex, _icon_key = _load_ui_icon_tex(texture_source, str(jar_rel), now_s=time.monotonic())
                    if tex is None:
                        tex = self._palette_missing_tex
                    return tex

                def _on_pick_entry(entry_idx: int) -> None:
                    self._palette_apply_entry(int(entry_idx))

                def _on_select_hotbar_slot(slot_idx: int) -> str | None:
                    try:
                        self._hotbar_select_slot(int(slot_idx))
                        return str(self._build_selected_block_id)
                    except Exception:
                        return None

                def _on_closed() -> None:
                    self._consume_tool_window_close_request_path(source="palette", window_obj=self._palette_window)
                    self._on_tool_window_closed(source="palette", attr_name="_palette_window")

                from enderterm.palette_window import create_palette_window

                self._palette_window = create_palette_window(
                    pyglet=pyglet,
                    store=param_store,
                    font_name=self.ui_font_name,
                    entries=self._palette_entries,
                    load_tex=_load_tex,
                    initial_selected_idx=int(self._palette_selected),
                    initial_block_id=str(self._build_selected_block_id),
                    on_pick_entry=_on_pick_entry,
                    on_select_hotbar_slot=_on_select_hotbar_slot,
                    on_closed=_on_closed,
                    theme_from_store=_termui_theme_from_store,
                )

            def _palette_apply_entry(self, entry_idx: int) -> None:
                try:
                    idx = int(entry_idx)
                except Exception:
                    return
                if idx < 0 or idx >= len(self._palette_entries):
                    return
                self._palette_selected = int(idx)
                bid = str(self._palette_entries[int(idx)].block_id)
                if not bid:
                    return
                self._build_selected_block_id = bid
                if not self._hotbar_slots:
                    self._hotbar_slots = [bid] * 10
                while len(self._hotbar_slots) < 10:
                    self._hotbar_slots.append(bid)
                self._hotbar_slots[int(self._hotbar_selected) % 10] = bid
                self._update_hotbar_ui()
                self._update_status()

            def _ensure_palette_loaded(self) -> None:
                if self._palette_entries:
                    return
                if texture_source is None or resolver is None:
                    # Fallback: minimal palette without textures.
                    seeds = [
                        "minecraft:stone",
                        "minecraft:dirt",
                        "minecraft:grass_block[snowy=false]",
                        "minecraft:sand",
                        "minecraft:oak_planks",
                        "minecraft:bricks",
                        "minecraft:glass",
                        "minecraft:torch",
                        "minecraft:oak_log[axis=y]",
                        "minecraft:cobblestone",
                    ]
                    self._palette_entries = [PaletteEntry(b, b, "") for b in seeds]
                    return

                names: list[str] = []
                prefix = "assets/minecraft/blockstates/"
                if texture_source.path.is_dir():
                    root = texture_source.path / prefix
                    try:
                        for p in sorted(root.glob("*.json"), key=lambda p: p.name):
                            names.append(p.stem)
                    except Exception:
                        names = []
                else:
                    zip_names = getattr(texture_source, "_zip_names", None)
                    if isinstance(zip_names, set):
                        for rel in zip_names:
                            if not (isinstance(rel, str) and rel.startswith(prefix) and rel.endswith(".json")):
                                continue
                            stem = rel[len(prefix) : -len(".json")]
                            if stem and "/" not in stem:
                                names.append(stem)
                        names.sort()

                def _choose_canonical_state(base_id: str) -> str:
                    if resolver is None:
                        return base_id
                    if resolver.resolve_block_appearance(base_id) is not None:
                        return base_id
                    base = base_id.removeprefix("minecraft:")
                    jar_rel = f"{prefix}{base}.json"
                    try:
                        raw = texture_source.read(jar_rel)
                        obj = json.loads(raw.decode("utf-8"))
                    except Exception:
                        obj = {}
                    variants = obj.get("variants") if isinstance(obj, dict) else None
                    if isinstance(variants, dict) and variants:
                        if "" in variants:
                            return base_id
                        keys = [k for k in variants.keys() if isinstance(k, str) and k.strip()]
                        best: str | None = None
                        best_score = 1_000_000
                        for k in keys:
                            cond = MinecraftResourceResolver._parse_variant_key(k)
                            score = len(cond)
                            if "axis" in cond and cond.get("axis") == "y":
                                score -= 1
                            if score < best_score:
                                best_score = score
                                best = k
                        if best:
                            cond = MinecraftResourceResolver._parse_variant_key(best)
                            if cond:
                                props = ",".join(f"{k}={v}" for k, v in sorted(cond.items()))
                                return f"{base_id}[{props}]"
                    return base_id

                entries: list[PaletteEntry] = []
                for name in names:
                    base_id = f"minecraft:{name}"
                    state_id = _choose_canonical_state(base_id)
                    jar_rel_tex = ""
                    try:
                        app = resolver.resolve_block_appearance(state_id)
                    except Exception:
                        app = None
                    if app is not None:
                        for face in ("up", "north", "south", "east", "west", "down"):
                            jar_rel = app.face_texture_png_by_dir.get(face) or ""
                            if jar_rel:
                                jar_rel_tex = jar_rel
                                break
                    entries.append(PaletteEntry(state_id, base_id, jar_rel_tex))
                self._palette_entries = entries

            def _update_palette_filtered(self) -> None:
                q = self._palette_query.strip().lower()
                if not self._palette_entries:
                    self._palette_filtered = []
                    self._palette_pos_by_index = {}
                    return
                if not q:
                    self._palette_filtered = list(range(len(self._palette_entries)))
                    self._palette_pos_by_index = {i: i for i in range(len(self._palette_entries))}
                else:
                    tokens = [t for t in q.split() if t]
                    indices: list[int] = []
                    for i, e in enumerate(self._palette_entries):
                        hay = f"{e.label} {e.block_id}".lower()
                        if all(t in hay for t in tokens):
                            indices.append(i)
                    self._palette_filtered = indices
                    self._palette_pos_by_index = {idx: pos for pos, idx in enumerate(indices)}
                if self._palette_filtered and self._palette_selected not in self._palette_pos_by_index:
                    self._palette_selected = self._palette_filtered[0]

            def _palette_ensure_selection_visible(self) -> None:
                if not self._palette_visible() or not self._palette_filtered:
                    return
                self._layout_palette_overlay()
                grid_cols = max(1, int(self._palette_term_grid_cols))
                grid_rows = int(self._palette_term_grid_rows)
                if grid_rows <= 0:
                    return
                pos = self._palette_pos_by_index.get(int(self._palette_selected))
                if pos is None:
                    return

                sel_row = int(pos) // int(grid_cols)
                scroll = int(self._palette_scroll_row)
                if sel_row < scroll:
                    scroll = int(sel_row)
                if sel_row >= scroll + grid_rows:
                    scroll = int(sel_row - grid_rows + 1)

                total_rows = (len(self._palette_filtered) + grid_cols - 1) // grid_cols
                max_scroll = max(0, int(total_rows) - int(grid_rows))
                self._palette_scroll_row = max(0, min(int(max_scroll), int(scroll)))

            def _layout_palette_overlay(self) -> None:
                # Palette overlay is now drawn with TermUI; keep legacy widgets hidden.
                for w in (
                    self.palette_bg,
                    self.palette_frame_top,
                    self.palette_frame_bottom,
                    self.palette_frame_left,
                    self.palette_frame_right,
                    self.palette_title,
                    self.palette_search_bg,
                    self.palette_search_label,
                    self.palette_hint_label,
                    self.palette_sel,
                ):
                    w.visible = False
                for spr in self._palette_sprites:
                    try:
                        spr.visible = False
                    except Exception:
                        pass

                if not self._palette_visible():
                    self._palette_rect = (0.0, 0.0, 0.0, 0.0)
                    self._palette_term_viewport_px = (0, 0, 0, 0)
                    self._palette_term_cols = 0
                    self._palette_term_rows = 0
                    self._palette_term_cell_w = 0
                    self._palette_term_cell_h = 0
                    self._palette_term_grid_cols = 0
                    self._palette_term_grid_rows = 0
                    return

                margin = 18.0
                view_x0 = float(self.sidebar_width) + margin
                view_x1 = float(self.width) - margin
                view_y0 = margin
                view_y1 = float(self.height) - margin
                if view_x1 - view_x0 < 80.0 or view_y1 - view_y0 < 80.0:
                    self._palette_rect = (0.0, 0.0, 0.0, 0.0)
                    self._palette_term_viewport_px = (0, 0, 0, 0)
                    return

                panel_w = max(420.0, min(760.0, (view_x1 - view_x0) * 0.74))
                panel_h = max(340.0, min(680.0, (view_y1 - view_y0) * 0.78))
                x0 = view_x0 + (view_x1 - view_x0 - panel_w) * 0.5
                y0 = view_y0 + (view_y1 - view_y0 - panel_h) * 0.5
                self._palette_rect = (x0, y0, panel_w, panel_h)

                # Device-pixel viewport for TermUI rendering.
                try:
                    ratio = float(self.get_pixel_ratio())
                except Exception:
                    ratio = 1.0
                if ratio <= 0.0 or not math.isfinite(ratio):
                    ratio = 1.0
                x0_px = int(round(float(x0) * float(ratio)))
                y0_px = int(round(float(y0) * float(ratio)))
                w_px = int(round(float(panel_w) * float(ratio)))
                h_px = int(round(float(panel_h) * float(ratio)))
                self._palette_term_viewport_px = (x0_px, y0_px, w_px, h_px)

                # Terminal grid metrics (cell sizes come from the sidebar TermUI font).
                try:
                    term_font, _surface = self._sync_sidebar_termui(force=False)
                    cell_w = max(1, int(getattr(term_font, "cell_w", 8)))
                    cell_h = max(1, int(getattr(term_font, "cell_h", 14)))
                except Exception:
                    cell_w = 8
                    cell_h = 14
                cols = max(1, int(max(1, w_px) // cell_w))
                rows = max(1, int(max(1, h_px) // cell_h))
                self._palette_term_cols = int(cols)
                self._palette_term_rows = int(rows)
                self._palette_term_cell_w = int(cell_w)
                self._palette_term_cell_h = int(cell_h)

                tile = max(2, int(self._palette_term_tile))
                inner_w = max(0, int(cols) - 2)
                inner_h = max(0, int(rows) - 2)
                # Reserve: search row (1) + hint row (1).
                grid_h_cells = max(0, int(inner_h) - 2)
                grid_cols = max(1, int(inner_w) // int(tile)) if inner_w > 0 else 0
                grid_rows = max(0, int(grid_h_cells) // int(tile)) if grid_h_cells > 0 else 0
                self._palette_term_grid_cols = int(grid_cols)
                self._palette_term_grid_rows = int(grid_rows)

                # Clamp scroll to content.
                if grid_cols > 0 and grid_rows > 0 and self._palette_filtered:
                    total_rows = (len(self._palette_filtered) + int(grid_cols) - 1) // int(grid_cols)
                    max_scroll = max(0, int(total_rows) - int(grid_rows))
                else:
                    max_scroll = 0
                self._palette_scroll_row = max(0, min(int(max_scroll), int(self._palette_scroll_row)))

            def _palette_scroll(self, delta_rows: float) -> None:
                if not self._palette_visible():
                    return
                self._layout_palette_overlay()
                grid_cols = int(self._palette_term_grid_cols)
                grid_rows = int(self._palette_term_grid_rows)
                if grid_cols <= 0 or grid_rows <= 0 or not self._palette_filtered:
                    self._palette_scroll_row = 0
                    return
                total_rows = (len(self._palette_filtered) + grid_cols - 1) // grid_cols
                max_scroll = max(0, int(total_rows) - int(grid_rows))
                step = int(round(float(delta_rows)))
                if step == 0:
                    step = int(delta_rows)
                self._palette_scroll_row = max(0, min(int(max_scroll), int(self._palette_scroll_row) - int(step)))
                self._layout_palette_overlay()

            def _palette_move_selection(self, dcol: int, drow: int) -> None:
                if not self._palette_visible() or not self._palette_filtered:
                    return
                self._layout_palette_overlay()
                cols = max(1, int(self._palette_term_grid_cols))
                pos = self._palette_pos_by_index.get(int(self._palette_selected), 0)
                row = int(pos) // int(cols)
                col = int(pos) % int(cols)
                col = max(0, min(int(cols) - 1, int(col) + int(dcol)))
                row = max(0, int(row) + int(drow))
                new_pos = int(row) * int(cols) + int(col)
                new_pos = max(0, min(len(self._palette_filtered) - 1, int(new_pos)))
                self._palette_selected = self._palette_filtered[int(new_pos)]
                self._palette_ensure_selection_visible()

            def _palette_click(self, x: int, y: int) -> bool:
                if not self._palette_visible():
                    return False
                px, py, pw, ph = self._palette_rect
                if not (px <= float(x) <= px + pw and py <= float(y) <= py + ph):
                    return False
                self._layout_palette_overlay()

                x0_px, y0_px, w_px, h_px = self._palette_term_viewport_px
                cell_w = max(1, int(self._palette_term_cell_w or 8))
                cell_h = max(1, int(self._palette_term_cell_h or 14))
                cols = max(1, int(self._palette_term_cols or 1))
                rows = max(1, int(self._palette_term_rows or 1))

                try:
                    ratio = float(self.get_pixel_ratio())
                except Exception:
                    ratio = 1.0
                if ratio <= 0.0 or not math.isfinite(ratio):
                    ratio = 1.0
                lx_px = int(round(float(x) * float(ratio))) - int(x0_px)
                ly_px = int(round(float(y) * float(ratio))) - int(y0_px)
                if lx_px < 0 or ly_px < 0 or lx_px >= int(w_px) or ly_px >= int(h_px):
                    return True
                col = int(lx_px // cell_w)
                row_from_bottom = int(ly_px // cell_h)
                row = int(rows - 1 - row_from_bottom)

                hs = None
                surf = self._palette_term_surface
                try:
                    hs = surf.hotspot_at(int(col), int(row)) if surf is not None else None
                except Exception:
                    hs = None
                if hs is None:
                    return True

                if hs.kind == "palette.entry":
                    try:
                        entry_idx = int(hs.payload) if hs.payload is not None else -1
                    except Exception:
                        entry_idx = -1
                    if 0 <= entry_idx < len(self._palette_entries):
                        self._palette_selected = entry_idx
                        bid = self._palette_entries[int(entry_idx)].block_id
                        self._build_selected_block_id = bid
                        if self._hotbar_slots:
                            self._hotbar_slots[self._hotbar_selected % 10] = bid
                        self._update_hotbar_ui()
                        self._update_status()
                    return True

                if hs.kind == "palette.clear":
                    self._palette_query = ""
                    self._update_palette_filtered()
                    self._palette_scroll_row = 0
                    self._palette_ensure_selection_visible()
                    return True

                return True

            def _draw_palette_termui(self, *, vp_w_px: int, vp_h_px: int) -> None:
                if not self._palette_visible():
                    return
                renderer = getattr(self, "_sidebar_term_renderer", None)
                if renderer is None:
                    return

                self._layout_palette_overlay()
                x0_px, y0_px, w_px, h_px = self._palette_term_viewport_px
                if int(w_px) <= 0 or int(h_px) <= 0:
                    return

                term_font, _sidebar_surface = self._sync_sidebar_termui(force=False)
                cell_w = max(1, int(getattr(term_font, "cell_w", 8)))
                cell_h = max(1, int(getattr(term_font, "cell_h", 14)))
                cols = max(1, int(max(1, int(w_px)) // cell_w))
                rows = max(1, int(max(1, int(h_px)) // cell_h))

                from enderterm.termui import TerminalSurface

                surface = self._palette_term_surface
                if not isinstance(surface, TerminalSurface):
                    surface = TerminalSurface(1, 1, default_fg=(18, 14, 22, 255), default_bg=(232, 229, 235, 255))
                    self._palette_term_surface = surface
                surface.resize(cols, rows)

                theme = _termui_theme_from_store(param_store)
                bg = theme.bg
                fg = theme.fg
                muted = theme.muted
                box_fg = theme.box_fg
                sel_bg = theme.sel_bg
                sel_fg = theme.sel_fg
                accent = theme.accent

                surface.default_bg = bg
                surface.default_fg = fg
                surface.clear()

                title = "Palette  (I/Esc)"
                surface.draw_box(0, 0, cols, rows, fg=box_fg, bg=bg, title=title)

                inner_x = 1
                inner_w = max(0, cols - 2)
                search_row = 1
                hint_row = rows - 2

                # Search bar (inside box).
                if inner_w > 0 and 0 <= search_row < rows - 1:
                    total = len(self._palette_entries)
                    matches = len(self._palette_filtered)
                    cursor_on = bool(int(time.monotonic() * 2.2) % 2 == 0)
                    cursor = "▌" if cursor_on else ""
                    q = self._palette_query
                    left = "/" + (q or "")
                    right = f"{matches}/{total}" if total else f"{matches}"
                    cancel = "[X]" if q else ""

                    usable = max(0, int(inner_w))
                    tail = ""
                    if cancel and right:
                        tail = f"{cancel} {right}"
                    elif cancel:
                        tail = cancel
                    else:
                        tail = right
                    if len(tail) >= usable:
                        tail = tail[-usable:]
                    left_w = max(0, usable - len(tail))
                    show_left = left
                    if len(show_left) > left_w:
                        show_left = show_left[: max(0, left_w - 1)] + "…"

                    surface.fill_rect(inner_x, search_row, inner_w, 1, bg=bg, fg=fg)
                    surface.put(inner_x, search_row, show_left[:left_w], fg=fg, bg=bg)
                    if cursor and len(show_left) < left_w:
                        cx = inner_x + len(show_left)
                        if inner_x <= cx < inner_x + inner_w:
                            surface.put(cx, search_row, cursor, fg=accent, bg=bg)
                    if tail:
                        tail_x = inner_x + max(0, usable - len(tail))
                        surface.put(tail_x, search_row, tail, fg=muted, bg=bg)
                        if cancel:
                            cancel_idx = tail.find(cancel)
                            if cancel_idx >= 0:
                                surface.add_hotspot(
                                    x=int(tail_x + cancel_idx),
                                    y=int(search_row),
                                    w=len(cancel),
                                    h=1,
                                    kind="palette.clear",
                                    payload=None,
                                )

                # Icon grid.
                tile = max(2, int(self._palette_term_tile))
                icon = max(1, int(self._palette_term_icon))
                if tile >= 3:
                    icon = max(1, min(int(icon), int(tile) - 2))
                else:
                    icon = max(1, min(int(icon), int(tile)))

                grid_y0 = search_row + 1
                grid_h_cells = max(0, int(hint_row) - int(grid_y0))
                grid_cols = max(1, int(inner_w) // int(tile)) if inner_w > 0 else 0
                grid_rows = max(0, int(grid_h_cells) // int(tile)) if grid_h_cells > 0 else 0
                self._palette_term_grid_cols = int(grid_cols)
                self._palette_term_grid_rows = int(grid_rows)

                if grid_cols > 0 and grid_rows > 0 and self._palette_filtered:
                    total_rows = (len(self._palette_filtered) + int(grid_cols) - 1) // int(grid_cols)
                    max_scroll = max(0, int(total_rows) - int(grid_rows))
                else:
                    max_scroll = 0
                self._palette_scroll_row = max(0, min(int(max_scroll), int(self._palette_scroll_row)))

                indices = self._palette_filtered
                scroll_row = int(self._palette_scroll_row)
                start = int(scroll_row) * int(max(1, grid_cols))
                visible = int(max(0, grid_cols * grid_rows))

                for i in range(visible):
                    pos = start + i
                    if pos < 0 or pos >= len(indices):
                        continue
                    entry_idx = indices[pos]
                    entry = self._palette_entries[entry_idx]
                    tcol = i % grid_cols
                    trow = i // grid_cols
                    tx = inner_x + int(tcol) * int(tile)
                    ty = int(grid_y0) + int(trow) * int(tile)

                    surface.add_hotspot(x=tx, y=ty, w=tile, h=tile, kind="palette.entry", payload=int(entry_idx))

                    is_sel = int(entry_idx) == int(self._palette_selected)
                    if is_sel:
                        surface.fill_rect(tx, ty, tile, tile, bg=sel_bg, fg=sel_fg)
                        surface.draw_box(tx, ty, tile, tile, fg=accent, bg=sel_bg, title=None)

                    tex = None
                    if entry.jar_rel_tex and texture_source is not None:
                        tex, _icon_key = _load_ui_icon_tex(texture_source, entry.jar_rel_tex, now_s=time.monotonic())
                    if tex is None:
                        tex = self._palette_missing_tex
                    if tex is None:
                        continue
                    try:
                        tex_w = max(1, int(getattr(tex, "width", 1)))
                        tex_h = max(1, int(getattr(tex, "height", 1)))
                        dim = max(1, max(int(tex_w), int(tex_h)))
                        draw_w = max(1, int(round(float(icon) * float(tex_w) / float(dim))))
                        draw_h = max(1, int(round(float(icon) * float(tex_h) / float(dim))))
                        ix = int(tx + max(0, (tile - draw_w) // 2))
                        iy = int(ty + max(0, (tile - draw_h) // 2))
                        surface.add_sprite(
                            x=ix,
                            y=iy,
                            w=int(draw_w),
                            h=int(draw_h),
                            target=int(getattr(tex, "target", gl.GL_TEXTURE_2D)),
                            tex_id=int(getattr(tex, "id", 0)),
                            tex_coords=tuple(getattr(tex, "tex_coords", ())),
                            tint=(255, 255, 255, 255),
                        )
                    except Exception:
                        pass

                # Selected block hint (bottom row inside box).
                if inner_w > 0 and 0 <= hint_row < rows - 1:
                    if indices and 0 <= int(self._palette_selected) < len(self._palette_entries):
                        sel_bid = str(self._palette_entries[int(self._palette_selected)].block_id)
                        pos = self._palette_pos_by_index.get(int(self._palette_selected))
                        right = f"{(pos + 1) if pos is not None else 0}/{len(indices)}"
                    else:
                        sel_bid = "(no blocks)"
                        right = "0/0"

                    usable = int(inner_w)
                    if len(right) >= usable:
                        right = right[-usable:]
                    left_w = max(0, usable - len(right))
                    show_left = sel_bid
                    if len(show_left) > left_w:
                        show_left = show_left[: max(0, left_w - 1)] + "…"

                    surface.put(inner_x, hint_row, show_left[:left_w], fg=muted, bg=bg)
                    if right:
                        surface.put(inner_x + max(0, usable - len(right)), hint_row, right, fg=muted, bg=bg)

                # Draw into the palette viewport.
                gl.glEnable(gl.GL_SCISSOR_TEST)
                gl.glScissor(int(x0_px), int(y0_px), max(1, int(w_px)), max(1, int(h_px)))
                gl.glViewport(int(x0_px), int(y0_px), max(1, int(w_px)), max(1, int(h_px)))
                renderer.draw(
                    surface=surface,
                    font=term_font,
                    vp_w_px=int(w_px),
                    vp_h_px=int(h_px),
                    param_store=param_store,
                    rez_active=bool(self._rez_active),
                )
                gl.glViewport(0, 0, max(1, int(vp_w_px)), max(1, int(vp_h_px)))
                gl.glDisable(gl.GL_SCISSOR_TEST)

            def _update_debug_panel(self) -> None:
                if not self._debug_panel_active:
                    return

                def _short(s: str, *, max_len: int) -> str:
                    if len(s) <= max_len:
                        return s
                    keep = max(1, max_len - 1)
                    return "…" + s[-keep:]

                active_labels = self._active_labels()
                active_selected = int(self._active_selected_index())
                selected_label = active_labels[active_selected] if 0 <= active_selected < len(active_labels) else ""
                depth = len(self.jigsaw_seeds)
                if self._rez_active and self._rez_live_positions:
                    blocks = len(self._rez_live_positions)
                    pending = len(self._rez_live_pending)
                elif self._current_structure is not None:
                    blocks = len(self._current_structure.blocks)
                    pending = 0
                else:
                    blocks = 0
                    pending = 0

                filter_query = self._search_query
                filt = f"/{filter_query}" if filter_query else "(none)"
                filt_count = f"{len(self._filtered_indices)}/{len(active_labels)}"
                seed_line = (
                    f"seed: 0x{self.jigsaw_seeds[-1]:08x}  (Space reroll)" if depth and self.jigsaw_seeds else "seed: —"
                )

                rez_line = "rez: idle"
                if self._rez_active:
                    pct = int(max(0.0, min(1.0, float(self._rez_progress))) * 100.0)
                    msg = self._rez_message or ""
                    rez_line = f"rez: {pct:3d}%  {pending} pending  {_short(msg, max_len=44)}"

                rt_line = "model rt: off"
                try:
                    if self._model_rt.ok:
                        rt_line = f"model rt: on  {self._model_rt.w}x{self._model_rt.h}"
                except Exception:
                    pass

                now = time.monotonic()
                try:
                    rez_fade_mode = int(param_store.get_int("rez.fade.mode"))
                except Exception:
                    rez_fade_mode = -1
                delta_mode = getattr(self, "_dbg_last_struct_delta_use_stipple", None)
                live_mode = getattr(self, "_dbg_last_rez_live_use_stipple", None)
                cc_mode = getattr(self, "_dbg_last_channel_change_use_stipple", None)
                delta_s = "—" if delta_mode is None else ("stipple" if delta_mode else "alpha")
                live_s = "—" if live_mode is None else ("stipple" if live_mode else "alpha")
                cc_s = "—" if cc_mode is None else ("stipple" if cc_mode else "alpha")

                lines = [
                    f"fps: {self._fps_value:4.1f}",
                    (
                        "vsync: ?"
                        if self._vsync_enabled is None
                        else f"vsync: {'on' if self._vsync_enabled else 'off'}  (render.vsync={int(param_store.get_int('render.vsync'))})"
                    )
                    + (
                        f"  err={_short(self._vsync_apply_error or '', max_len=24)}"
                        if self._vsync_apply_error
                        else ""
                    ),
                    f"frame cap: {int(param_store.get_int('render.frame_cap_hz'))} Hz  (0=tick-paced)",
                    rt_line,
                    (
                        f"effects: {'on' if self._effects_enabled else 'OFF'}  "
                        f"({params_mod.FX_MASTER_ENABLED_KEY}={int(param_store.get_int(params_mod.FX_MASTER_ENABLED_KEY))})"
                    ),
                    f"rez.fade.mode: {rez_fade_mode}  (delta={delta_s} live={live_s} channel={cc_s})",
                    "",
                ]
                if self._build_enabled:
                    lines.append("Mouse: LMB break / RMB place / MMB pick  (hold ⌥ for camera)")
                else:
                    lines.append("Mouse: (hold ⌥ for camera) Wheel zoom")
                lines.append("Camera: ⌥+drag orbit/pan (LMB orbit, MMB pan), ⌥+click pivots, Wheel zoom")
                if sys.platform == "darwin":
                    if self._mac_gestures_enabled:
                        lines.append("Trackpad: two-finger scroll-pan, pinch zoom, rotate (Cocoa gestures)")
                    else:
                        lines.append("Trackpad: (gestures unavailable) scroll/wheel zoom only")
                lines += [
                    "Keys: Up/Down select  PgUp/PgDn page  / filter  D debug  C 2nd viewport  Shift+C add viewport  W walk mode  K kValue  V ender vision",
                    "Pool: Right expand  Left undo  Space reroll",
                    "Build: B toggle  LMB break / RMB place / MMB pick  1-9/0 hotbar  ⌥ camera  I palette  ⌘Z undo / ⌘⇧Z redo",
                    "View: O ortho  F frame  C toggle 2nd viewport  Shift+C add viewport  W walk mode  Esc exits walk/search/rez",
                    "Env: E cycle",
                    "Export: U USDZ  N NBT  P open folder",
                    "",
                    (
                        "cocoa gestures: off"
                        if not self._mac_gestures_enabled
                        else f"cocoa gestures: on  recognizers={len(self._gesture_recognizers)}  target={self._mac_gesture_target}  {self._mac_gesture_target_size[0]}x{self._mac_gesture_target_size[1]}"
                    )
                    + (f"  ({_short(self._mac_gesture_install_note, max_len=32)})" if (not self._mac_gestures_enabled and self._mac_gesture_install_note) else ""),
                    f"last cocoa: {_short(self._dbg_last_cocoa_event or '(none)', max_len=56)}  ({now - self._dbg_last_cocoa_event_t:0.2f}s)",
                    f"last pyglet: {_short(self._dbg_last_pyglet_event or '(none)', max_len=56)}  ({now - self._dbg_last_pyglet_event_t:0.2f}s)",
                    f"pyglet mouse: press={self._dbg_pyglet_press_calls} drag={self._dbg_pyglet_drag_calls} scroll={self._dbg_pyglet_scroll_calls}",
                    f"scroll: {self._scroll_last_mode}  ({self._scroll_last_sx:+.2f}, {self._scroll_last_sy:+.2f})",
                    f"pinch: {self._dbg_mag_calls}  last={self._dbg_mag_last:+.3f}  factor={self._dbg_mag_factor:.3f}  state={self._dbg_mag_state}",
                    f"rotate: {self._dbg_rot_calls}  last={self._dbg_rot_last:+.3f}  state={self._dbg_rot_state}",
                    f"pan: {self._dbg_pan_calls}  last=({self._dbg_pan_dx:+.1f}, {self._dbg_pan_dy:+.1f})  state={self._dbg_pan_state}",
                    f"orbit: {self._dbg_orbit_calls}  last=({self._dbg_orbit_dx:+.1f}, {self._dbg_orbit_dy:+.1f})  state={self._dbg_orbit_state}",
                    f"scroll-pan: {self._dbg_scroll_pan_calls}  last=({self._dbg_scroll_pan_dx:+.1f}, {self._dbg_scroll_pan_dy:+.1f})",
                    f"gesture-pan: {self._dbg_gesture_loc_pan_calls}  last=({self._dbg_gesture_loc_pan_dx:+.1f}, {self._dbg_gesture_loc_pan_dy:+.1f})",
                    f"selection: {active_selected + 1}/{len(active_labels)}  {_short(selected_label, max_len=56)}",
                    f"blocks: {blocks}",
                    f"{rez_line}",
                    f"filter: {filt}  ({filt_count})",
                    f"pool depth: {depth}  {seed_line}",
                ]
                text = "\n".join(lines)
                if text != self._debug_panel_last_text:
                    self._debug_panel_last_text = text

            def _update_help_overlay(self) -> None:
                # Deprecated: debug panel replaced the old help overlay.
                self._update_debug_panel()

            def _update_list_labels(self, *, ensure_selection_visible: bool = True) -> None:
                active_labels = self._active_labels()
                active_selected = self._active_selected_index()
                indices = self._filtered_indices
                visible_full = max(1, int(self._visible_list_rows_termui()))
                max_scroll = max(0, len(indices) - visible_full)
                self._scroll_pos_f = max(0.0, min(float(max_scroll), float(self._scroll_pos_f)))
                scroll_top = int(self._scroll_pos_f)
                self.scroll_top = scroll_top
                sel_pos = self._selected_list_pos()
                if ensure_selection_visible and sel_pos is not None:
                    if sel_pos < scroll_top:
                        self._scroll_pos_f = float(sel_pos)
                    if sel_pos >= scroll_top + visible_full:
                        self._scroll_pos_f = float(sel_pos - visible_full + 1)
                    self._scroll_pos_f = max(0.0, min(float(max_scroll), float(self._scroll_pos_f)))
                    scroll_top = int(self._scroll_pos_f)
                    self.scroll_top = scroll_top

                if 0 <= active_selected < len(active_labels):
                    if indices and (len(indices) != len(active_labels)):
                        pos = self._selected_list_pos()
                        pos_s = f"{(pos + 1)}/{len(indices)}" if pos is not None else f"0/{len(indices)}"
                        self.subtitle.text = f"{pos_s}  {active_labels[active_selected]}"
                    else:
                        self.subtitle.text = f"{active_selected + 1}/{len(active_labels)}  {active_labels[active_selected]}"
                else:
                    self.subtitle.text = ""

            def _reset_view(self) -> None:
                self._cancel_camera_tween()
                self.yaw = 45.0
                self.pitch = 25.0
                self.distance = self._initial_distance
                self.pan_x = 0.0
                self.pan_y = 0.0
                self._orbit_target = (0.0, 0.0, 0.0)

            def _frame_view(self, *, animate: bool = True) -> None:
                # Keep orbit angles, but re-center and frame.
                self._cancel_camera_tween()
                if animate:
                    self._animate_camera_to(
                        orbit_target=(0.0, 0.0, 0.0),
                        pan_x=0.0,
                        pan_y=0.0,
                        distance=self._initial_distance,
                        duration_s=0.35,
                    )
                    return

                self._orbit_target = (0.0, 0.0, 0.0)
                self.pan_x = 0.0
                self.pan_y = 0.0
                self.distance = max(0.5, float(self._initial_distance))

            def _cancel_camera_tween(self) -> None:
                self._cam_tween_distance = None
                self._cam_tween_pan_x = None
                self._cam_tween_pan_y = None
                self._cam_tween_orbit = None

            def _mark_camera_user_input(self) -> None:
                self._camera_last_user_input_t = time.monotonic()

            def _animate_camera_to(
                self,
                *,
                orbit_target: tuple[float, float, float] | None = None,
                pan_x: float | None = None,
                pan_y: float | None = None,
                distance: float | None = None,
                duration_s: float = 0.25,
            ) -> None:
                now = time.monotonic()
                ease = ease_smoothstep
                if orbit_target is not None:
                    ox, oy, oz = self._orbit_target
                    tx, ty, tz = orbit_target
                    self._cam_tween_orbit = (
                        Tween(now, duration_s, start=float(ox), end=float(tx), ease=ease),
                        Tween(now, duration_s, start=float(oy), end=float(ty), ease=ease),
                        Tween(now, duration_s, start=float(oz), end=float(tz), ease=ease),
                    )
                if pan_x is not None:
                    self._cam_tween_pan_x = Tween(now, duration_s, start=float(self.pan_x), end=float(pan_x), ease=ease)
                if pan_y is not None:
                    self._cam_tween_pan_y = Tween(now, duration_s, start=float(self.pan_y), end=float(pan_y), ease=ease)
                if distance is not None:
                    self._cam_tween_distance = Tween(
                        now, duration_s, start=float(self.distance), end=float(max(0.5, distance)), ease=ease
                    )

            def _tick_camera_tween(self) -> None:
                now = time.monotonic()
                active = False
                if self._cam_tween_orbit is not None:
                    tx = self._cam_tween_orbit[0].value(now)
                    ty = self._cam_tween_orbit[1].value(now)
                    tz = self._cam_tween_orbit[2].value(now)
                    self._orbit_target = (tx, ty, tz)
                    active = active or not all(t.done(now) for t in self._cam_tween_orbit)
                    if not active:
                        self._cam_tween_orbit = None
                if self._cam_tween_pan_x is not None:
                    self.pan_x = self._cam_tween_pan_x.value(now)
                    if self._cam_tween_pan_x.done(now):
                        self._cam_tween_pan_x = None
                    else:
                        active = True
                if self._cam_tween_pan_y is not None:
                    self.pan_y = self._cam_tween_pan_y.value(now)
                    if self._cam_tween_pan_y.done(now):
                        self._cam_tween_pan_y = None
                    else:
                        active = True
                if self._cam_tween_distance is not None:
                    self.distance = self._cam_tween_distance.value(now)
                    if self._cam_tween_distance.done(now):
                        self._cam_tween_distance = None
                    else:
                        active = True
                if not active:
                    self._cancel_camera_tween()

            def _rotate_x_deg(self, v: tuple[float, float, float], deg: float) -> tuple[float, float, float]:
                return _viewport_rotate_x_deg(v, deg)

            def _rotate_y_deg(self, v: tuple[float, float, float], deg: float) -> tuple[float, float, float]:
                return _viewport_rotate_y_deg(v, deg)

            def _camera_world_position(self) -> tuple[float, float, float]:
                return _viewport_camera_world_position(self)

            def _camera_u_position(self) -> tuple[float, float, float]:
                return _viewport_camera_u_position(self)

            def _camera_safety_strengths(self) -> tuple[float, float]:
                return _viewport_camera_safety_strengths(self)

            def _draw_camera_safety_overlay(self, *, sidebar_px: int, view_w_px: int, view_h_px: int) -> None:
                _viewport_draw_camera_safety_overlay(self, sidebar_px=sidebar_px, view_w_px=view_w_px, view_h_px=view_h_px)

            def _set_orbit_target(self, target: tuple[float, float, float]) -> None:
                _viewport_set_orbit_target(self, target)

            def _raycast_blocks_u(
                self, origin_u: tuple[float, float, float], dir_u: tuple[float, float, float]
            ) -> tuple[float, float, float] | None:
                positions = self._rez_live_positions if self._rez_active else self._pick_positions
                if not positions:
                    return None
                if self._rez_active:
                    xs = [p[0] for p in positions]
                    ys = [p[1] for p in positions]
                    zs = [p[2] for p in positions]
                    bounds = (
                        (float(min(xs)), float(min(ys)), float(min(zs))),
                        (float(max(xs) + 1), float(max(ys) + 1), float(max(zs) + 1)),
                    )
                else:
                    bounds = self._pick_bounds
                if bounds is None:
                    return None
                (min_x, min_y, min_z), (max_x, max_y, max_z) = bounds

                ox, oy, oz = origin_u
                dx, dy, dz = dir_u
                eps = 1e-9
                tol = 1e-12
                tmin = -1.0e30
                tmax = 1.0e30
                entry_candidates: list[tuple[float, Vec3i]] = []
                for axis, o, d, mn, mx in (
                    ("x", ox, dx, min_x, max_x),
                    ("y", oy, dy, min_y, max_y),
                    ("z", oz, dz, min_z, max_z),
                ):
                    if abs(d) < eps:
                        if o < mn or o > mx:
                            return None
                        continue
                    t1 = (mn - o) / d
                    t2 = (mx - o) / d
                    if t1 > t2:
                        t1, t2 = t2, t1

                    axis_n: Vec3i
                    if axis == "x":
                        axis_n = (-1 if d > 0.0 else 1, 0, 0)
                    elif axis == "y":
                        axis_n = (0, -1 if d > 0.0 else 1, 0)
                    else:
                        axis_n = (0, 0, -1 if d > 0.0 else 1)

                    if t1 > (tmin + tol):
                        entry_candidates = [(abs(float(d)), axis_n)]
                    elif abs(t1 - tmin) <= tol:
                        entry_candidates.append((abs(float(d)), axis_n))
                    tmin = max(tmin, t1)
                    tmax = min(tmax, t2)
                    if tmax < tmin:
                        return None
                if tmax < 0.0:
                    return None

                t = max(0.0, tmin)
                entry_n: Vec3i = (0, 0, 0)
                if tmin > 1e-9 and entry_candidates:
                    # If we start inside a filled cell, seed the face normal
                    # from the bbox entry plane so build-mode can place blocks
                    # outside the current bounds.
                    entry_candidates.sort(key=lambda it: it[0], reverse=True)
                    entry_n = entry_candidates[0][1]
                px = ox + dx * t
                py = oy + dy * t
                pz = oz + dz * t
                nudge = 1e-6
                px += dx * nudge
                py += dy * nudge
                pz += dz * nudge

                vx = int(math.floor(px))
                vy = int(math.floor(py))
                vz = int(math.floor(pz))

                step_x = 1 if dx > 0.0 else (-1 if dx < 0.0 else 0)
                step_y = 1 if dy > 0.0 else (-1 if dy < 0.0 else 0)
                step_z = 1 if dz > 0.0 else (-1 if dz < 0.0 else 0)

                inf = 1.0e30
                if step_x != 0:
                    next_x = float(vx + 1) if step_x > 0 else float(vx)
                    t_max_x = t + (next_x - px) / dx
                    t_delta_x = 1.0 / abs(dx)
                else:
                    t_max_x = inf
                    t_delta_x = inf
                if step_y != 0:
                    next_y = float(vy + 1) if step_y > 0 else float(vy)
                    t_max_y = t + (next_y - py) / dy
                    t_delta_y = 1.0 / abs(dy)
                else:
                    t_max_y = inf
                    t_delta_y = inf
                if step_z != 0:
                    next_z = float(vz + 1) if step_z > 0 else float(vz)
                    t_max_z = t + (next_z - pz) / dz
                    t_delta_z = 1.0 / abs(dz)
                else:
                    t_max_z = inf
                    t_delta_z = inf

                steps = 0
                max_steps = 200000
                while t <= tmax and steps < max_steps:
                    steps += 1
                    if (vx, vy, vz) in positions:
                        return (ox + dx * t, oy + dy * t, oz + dz * t)

                    t_next = min(t_max_x, t_max_y, t_max_z)
                    if t_next == inf:
                        break
                    if t_next > tmax:
                        break
                    if abs(t_next - t_max_x) < 1e-12:
                        vx += step_x
                        t_max_x += t_delta_x
                    if abs(t_next - t_max_y) < 1e-12:
                        vy += step_y
                        t_max_y += t_delta_y
                    if abs(t_next - t_max_z) < 1e-12:
                        vz += step_z
                        t_max_z += t_delta_z
                    t = t_next

                return None

            def _env_top_y_at(self, x: int, z: int) -> int | None:
                preset = self._env_preset()
                if preset.is_space():
                    return None
                if self._env_base_y is None:
                    return None
                cached = self._env_top_y_by_xz.get((int(x), int(z)))
                if cached is not None:
                    return int(cached)

                try:
                    delta = clamp_terrain_delta(
                        int(self._env_height_offset(int(x), int(z))) - int(self._env_height_anchor_off),
                        max_delta=max(ENV_HEIGHT_MAX_DELTA, int(self._env_terrain_amp)),
                    )
                except Exception:
                    return None
                top_y = int(self._env_base_y) + int(delta)
                if top_y < int(WORLD_MIN_Y):
                    top_y = int(WORLD_MIN_Y)
                return int(top_y)

            def _env_top_y_cached_at(self, x: int, z: int) -> int | None:
                """Return height only if we've built terrain geometry for this column."""
                preset = self._env_preset()
                if preset.is_space() or self._env_base_y is None:
                    return None
                cached = self._env_top_y_by_xz.get((int(x), int(z)))
                return int(cached) if cached is not None else None

            def _raycast_terrain_u(
                self,
                origin_u: tuple[float, float, float],
                dir_u: tuple[float, float, float],
                *,
                max_t: float = 5000.0,
            ) -> tuple[float, float, float] | None:
                """Raycast against the environment heightfield (top faces only)."""
                preset = self._env_preset()
                if preset.is_space() or self._env_base_y is None:
                    return None
                ox, oy, oz = origin_u
                dx, dy, dz = dir_u

                if max_t <= 0.0:
                    max_t = 5000.0

                # Vertical ray: stay in the current column.
                if abs(dx) < 1e-12 and abs(dz) < 1e-12:
                    if abs(dy) < 1e-12:
                        return None
                    ix = int(math.floor(ox))
                    iz = int(math.floor(oz))
                    top_y = self._env_top_y_cached_at(ix, iz)
                    if top_y is None:
                        return None
                    y_s = float(int(top_y) + 1)
                    t_hit = (y_s - float(oy)) / float(dy)
                    if t_hit < 0.0 or t_hit > float(max_t):
                        return None
                    return (float(ox), y_s, float(oz))

                ix = int(math.floor(ox))
                iz = int(math.floor(oz))
                step_x = 1 if dx > 0.0 else (-1 if dx < 0.0 else 0)
                step_z = 1 if dz > 0.0 else (-1 if dz < 0.0 else 0)
                inf = 1.0e30
                if step_x != 0:
                    next_x = float(ix + 1) if step_x > 0 else float(ix)
                    t_max_x = (next_x - float(ox)) / float(dx)
                    t_delta_x = 1.0 / abs(float(dx))
                else:
                    t_max_x = inf
                    t_delta_x = inf
                if step_z != 0:
                    next_z = float(iz + 1) if step_z > 0 else float(iz)
                    t_max_z = (next_z - float(oz)) / float(dz)
                    t_delta_z = 1.0 / abs(float(dz))
                else:
                    t_max_z = inf
                    t_delta_z = inf

                t_entry = 0.0
                steps = 0
                max_steps = 200000
                eps = 1e-6
                while t_entry <= float(max_t) and steps < max_steps:
                    steps += 1
                    t_exit = min(float(max_t), float(t_max_x), float(t_max_z))

                    top_y = self._env_top_y_cached_at(ix, iz)
                    if top_y is not None and abs(dy) >= 1e-12:
                        y_s = float(int(top_y) + 1)
                        t_hit = (y_s - float(oy)) / float(dy)
                        if t_hit >= (t_entry - eps) and t_hit <= (t_exit + eps) and t_hit >= 0.0:
                            x_hit = float(ox) + float(dx) * float(t_hit)
                            z_hit = float(oz) + float(dz) * float(t_hit)
                            if int(math.floor(x_hit + eps)) == int(ix) and int(math.floor(z_hit + eps)) == int(iz):
                                return (x_hit, y_s, z_hit)

                    if t_exit >= float(max_t) - 1e-9:
                        break

                    hit_x = abs(t_exit - float(t_max_x)) < 1e-12
                    hit_z = abs(t_exit - float(t_max_z)) < 1e-12
                    if hit_x:
                        ix += step_x
                        t_max_x = float(t_max_x) + float(t_delta_x)
                    if hit_z:
                        iz += step_z
                        t_max_z = float(t_max_z) + float(t_delta_z)
                    t_entry = float(t_exit)
                return None

            def _raycast_block_hit_u(
                self, origin_u: tuple[float, float, float], dir_u: tuple[float, float, float]
            ) -> tuple[Vec3i, Vec3i] | None:
                positions = self._rez_live_positions if self._rez_active else self._pick_positions
                if not positions:
                    return None
                bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None
                if self._rez_active:
                    xs = [p[0] for p in positions]
                    ys = [p[1] for p in positions]
                    zs = [p[2] for p in positions]
                    bounds = (
                        (float(min(xs)), float(min(ys)), float(min(zs))),
                        (float(max(xs) + 1), float(max(ys) + 1), float(max(zs) + 1)),
                    )
                else:
                    bounds = self._pick_bounds
                if bounds is None:
                    return None
                (min_x, min_y, min_z), (max_x, max_y, max_z) = bounds

                ox, oy, oz = origin_u
                dx, dy, dz = dir_u
                eps = 1e-9
                tol = 1e-12
                tmin = -1.0e30
                tmax = 1.0e30
                entry_candidates: list[tuple[float, Vec3i]] = []
                for axis, o, d, mn, mx in (
                    ("x", ox, dx, min_x, max_x),
                    ("y", oy, dy, min_y, max_y),
                    ("z", oz, dz, min_z, max_z),
                ):
                    if abs(d) < eps:
                        if o < mn or o > mx:
                            return None
                        continue
                    t1 = (mn - o) / d
                    t2 = (mx - o) / d
                    if t1 > t2:
                        t1, t2 = t2, t1

                    axis_n: Vec3i
                    if axis == "x":
                        axis_n = (-1 if d > 0.0 else 1, 0, 0)
                    elif axis == "y":
                        axis_n = (0, -1 if d > 0.0 else 1, 0)
                    else:
                        axis_n = (0, 0, -1 if d > 0.0 else 1)

                    if t1 > (tmin + tol):
                        entry_candidates = [(abs(float(d)), axis_n)]
                    elif abs(t1 - tmin) <= tol:
                        entry_candidates.append((abs(float(d)), axis_n))
                    tmin = max(tmin, t1)
                    tmax = min(tmax, t2)
                    if tmax < tmin:
                        return None
                if tmax < 0.0:
                    return None

                t = max(0.0, tmin)
                entry_n: Vec3i = (0, 0, 0)
                if tmin > 1e-9 and entry_candidates:
                    entry_candidates.sort(key=lambda it: it[0], reverse=True)
                    entry_n = entry_candidates[0][1]
                px = ox + dx * t
                py = oy + dy * t
                pz = oz + dz * t
                nudge = 1e-6
                px += dx * nudge
                py += dy * nudge
                pz += dz * nudge

                vx = int(math.floor(px))
                vy = int(math.floor(py))
                vz = int(math.floor(pz))

                step_x = 1 if dx > 0.0 else (-1 if dx < 0.0 else 0)
                step_y = 1 if dy > 0.0 else (-1 if dy < 0.0 else 0)
                step_z = 1 if dz > 0.0 else (-1 if dz < 0.0 else 0)

                inf = 1.0e30
                if step_x != 0:
                    next_x = float(vx + 1) if step_x > 0 else float(vx)
                    t_max_x = t + (next_x - px) / dx
                    t_delta_x = 1.0 / abs(dx)
                else:
                    t_max_x = inf
                    t_delta_x = inf
                if step_y != 0:
                    next_y = float(vy + 1) if step_y > 0 else float(vy)
                    t_max_y = t + (next_y - py) / dy
                    t_delta_y = 1.0 / abs(dy)
                else:
                    t_max_y = inf
                    t_delta_y = inf
                if step_z != 0:
                    next_z = float(vz + 1) if step_z > 0 else float(vz)
                    t_max_z = t + (next_z - pz) / dz
                    t_delta_z = 1.0 / abs(dz)
                else:
                    t_max_z = inf
                    t_delta_z = inf

                last_n: Vec3i = entry_n
                steps = 0
                max_steps = 200000
                while t <= tmax and steps < max_steps:
                    steps += 1
                    if (vx, vy, vz) in positions:
                        return ((vx, vy, vz), last_n)

                    t_next = min(t_max_x, t_max_y, t_max_z)
                    if t_next == inf:
                        break
                    if t_next > tmax:
                        break

                    hit_x = abs(t_next - t_max_x) < 1e-12
                    hit_y = abs(t_next - t_max_y) < 1e-12
                    hit_z = abs(t_next - t_max_z) < 1e-12

                    # Track which face we crossed to enter the next cell. When
                    # multiple axes step at once (edge/corner), prefer the axis
                    # with the dominant ray direction.
                    candidates: list[tuple[float, Vec3i]] = []
                    if hit_x and step_x != 0:
                        candidates.append((abs(dx), (-step_x, 0, 0)))
                    if hit_y and step_y != 0:
                        candidates.append((abs(dy), (0, -step_y, 0)))
                    if hit_z and step_z != 0:
                        candidates.append((abs(dz), (0, 0, -step_z)))
                    if candidates:
                        candidates.sort(key=lambda it: it[0], reverse=True)
                        last_n = candidates[0][1]

                    if hit_x:
                        vx += step_x
                        t_max_x += t_delta_x
                    if hit_y:
                        vy += step_y
                        t_max_y += t_delta_y
                    if hit_z:
                        vz += step_z
                        t_max_z += t_delta_z
                    t = t_next
                return None

            def _pick_orbit_target(self, x: int, y: int) -> tuple[float, float, float] | None:
                return _viewport_pick_orbit_target(self, x, y, safe_viewport=False)

            def _pick_block_hit(self, x: int, y: int) -> tuple[Vec3i, Vec3i] | None:
                return _viewport_pick_block_hit(self, x, y, safe_viewport=False)

            def _screen_ray_u(self, x: int, y: int) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
                """Return (origin_u, dir_world) for a screen coordinate.

                origin_u is in un-centered block coordinates (i.e. in the same
                coordinate space as structure/pool-connector block positions).
                """

                if x < self.sidebar_width:
                    return None
                if self._hotbar_hit_slot(x, y) is not None:
                    return None

                vp_w_px, vp_h_px = self.get_viewport_size()
                ratio = float(self.get_pixel_ratio())
                sidebar_px = int(float(self.sidebar_width) * ratio)
                view_w_px = max(1, int(vp_w_px) - sidebar_px)
                view_h_px = max(1, int(vp_h_px))

                x_px = float(x) * ratio - float(sidebar_px)
                y_px = float(y) * ratio
                ndc_x = 2.0 * (x_px + 0.5) / float(view_w_px) - 1.0
                ndc_y = 2.0 * (y_px + 0.5) / float(view_h_px) - 1.0

                fovy = 55.0
                tan_y = math.tan(math.radians(fovy) / 2.0)
                aspect = float(view_w_px) / float(view_h_px)
                tan_x = tan_y * aspect

                if self._ortho_enabled:
                    half_y = float(self.distance) * tan_y
                    half_x = half_y * aspect
                    x_eye = ndc_x * half_x
                    y_eye = ndc_y * half_y

                    v = (x_eye - float(self.pan_x), y_eye - float(self.pan_y), float(self.distance))
                    v = self._rotate_x_deg(v, -self.pitch)
                    v = self._rotate_y_deg(v, -self.yaw)
                    cam_world = (
                        self._orbit_target[0] + v[0],
                        self._orbit_target[1] + v[1],
                        self._orbit_target[2] + v[2],
                    )

                    dir_cam = (0.0, 0.0, -1.0)
                    dir_world = self._rotate_x_deg(dir_cam, -self.pitch)
                    dir_world = self._rotate_y_deg(dir_world, -self.yaw)
                else:
                    dir_cam = (ndc_x * tan_x, ndc_y * tan_y, -1.0)
                    mag = math.sqrt(dir_cam[0] ** 2 + dir_cam[1] ** 2 + dir_cam[2] ** 2)
                    if mag <= 0.0:
                        return None
                    dir_cam = (dir_cam[0] / mag, dir_cam[1] / mag, dir_cam[2] / mag)
                    dir_world = self._rotate_x_deg(dir_cam, -self.pitch)
                    dir_world = self._rotate_y_deg(dir_world, -self.yaw)
                    cam_world = self._camera_world_position()

                cx, cy, cz = self._pivot_center
                origin_u = (cam_world[0] + cx, cam_world[1] + cy, cam_world[2] + cz)
                return (origin_u, dir_world)

            def _pick_hover_block(self, x: int, y: int) -> tuple[Vec3i, bool] | None:
                if x < self.sidebar_width:
                    return None
                if self._hotbar_hit_slot(x, y) is not None:
                    return None
                # Don't drive 3D hover/picking when the mouse is over an overlay panel.
                panel = getattr(self, "_jar_term_panel_rect", None)
                if panel is not None and (not getattr(self, "_jar_alert_dismissed", False)) and bool(
                    getattr(self, "_jar_alert_text", "")
                ):
                    bx, by, bw, bh = panel
                    if float(bx) <= float(x) <= float(bx + bw) and float(by) <= float(y) <= float(by + bh):
                        return None
                try:
                    tb_bg = getattr(self, "test_banner_bg", None)
                    if tb_bg is not None and bool(getattr(tb_bg, "visible", False)):
                        bx = float(getattr(tb_bg, "x", 0.0))
                        by = float(getattr(tb_bg, "y", 0.0))
                        bw = float(getattr(tb_bg, "width", 0.0))
                        bh = float(getattr(tb_bg, "height", 0.0))
                        if bx <= float(x) <= bx + bw and by <= float(y) <= by + bh:
                            return None
                except Exception:
                    pass

                vp_w_px, vp_h_px = self.get_viewport_size()
                ratio = float(self.get_pixel_ratio())
                sidebar_px = int(float(self.sidebar_width) * ratio)
                view_w_px = max(1, int(vp_w_px) - sidebar_px)
                view_h_px = max(1, int(vp_h_px))

                x_px = float(x) * ratio - float(sidebar_px)
                y_px = float(y) * ratio
                ndc_x = 2.0 * (x_px + 0.5) / float(view_w_px) - 1.0
                ndc_y = 2.0 * (y_px + 0.5) / float(view_h_px) - 1.0

                fovy = 55.0
                tan_y = math.tan(math.radians(fovy) / 2.0)
                aspect = float(view_w_px) / float(view_h_px)
                tan_x = tan_y * aspect
                if self._ortho_enabled:
                    half_y = float(self.distance) * tan_y
                    half_x = half_y * aspect
                    x_eye = ndc_x * half_x
                    y_eye = ndc_y * half_y

                    v = (x_eye - float(self.pan_x), y_eye - float(self.pan_y), float(self.distance))
                    v = self._rotate_x_deg(v, -self.pitch)
                    v = self._rotate_y_deg(v, -self.yaw)
                    cam_world = (
                        self._orbit_target[0] + v[0],
                        self._orbit_target[1] + v[1],
                        self._orbit_target[2] + v[2],
                    )

                    dir_cam = (0.0, 0.0, -1.0)
                    dir_world = self._rotate_x_deg(dir_cam, -self.pitch)
                    dir_world = self._rotate_y_deg(dir_world, -self.yaw)
                else:
                    dir_cam = (ndc_x * tan_x, ndc_y * tan_y, -1.0)
                    mag = math.sqrt(dir_cam[0] ** 2 + dir_cam[1] ** 2 + dir_cam[2] ** 2)
                    if mag <= 0.0:
                        return None
                    dir_cam = (dir_cam[0] / mag, dir_cam[1] / mag, dir_cam[2] / mag)
                    dir_world = self._rotate_x_deg(dir_cam, -self.pitch)
                    dir_world = self._rotate_y_deg(dir_world, -self.yaw)
                    cam_world = self._camera_world_position()

                cx, cy, cz = self._pivot_center
                origin_u = (cam_world[0] + cx, cam_world[1] + cy, cam_world[2] + cz)
                hit = self._raycast_block_hit_u(origin_u, dir_world)
                env_hit = self._raycast_terrain_u(origin_u, dir_world)
                if hit is None and env_hit is None:
                    return None
                if hit is None:
                    bx = int(math.floor(float(env_hit[0]) + 1e-6))
                    by = int(math.floor(float(env_hit[1]) - 1e-6))
                    bz = int(math.floor(float(env_hit[2]) + 1e-6))
                    return ((bx, by, bz), True)
                if env_hit is None:
                    pos, _n = hit
                    return (pos, False)

                pos, n = hit
                ox, oy, oz = origin_u
                dx, dy, dz = dir_world

                def _hit_t(p: Vec3i, face_n: Vec3i) -> float:
                    if face_n == (0, 0, 0):
                        hx = float(p[0]) + 0.5
                        hy = float(p[1]) + 0.5
                        hz = float(p[2]) + 0.5
                        return (hx - ox) * dx + (hy - oy) * dy + (hz - oz) * dz
                    if face_n[0] != 0 and abs(dx) > 1e-9:
                        plane = float(p[0]) + (1.0 if face_n[0] > 0 else 0.0)
                        return (plane - ox) / dx
                    if face_n[1] != 0 and abs(dy) > 1e-9:
                        plane = float(p[1]) + (1.0 if face_n[1] > 0 else 0.0)
                        return (plane - oy) / dy
                    if face_n[2] != 0 and abs(dz) > 1e-9:
                        plane = float(p[2]) + (1.0 if face_n[2] > 0 else 0.0)
                        return (plane - oz) / dz
                    hx = float(p[0]) + 0.5
                    hy = float(p[1]) + 0.5
                    hz = float(p[2]) + 0.5
                    return (hx - ox) * dx + (hy - oy) * dy + (hz - oz) * dz

                t_block = _hit_t(pos, n)
                t_env = (float(env_hit[0]) - ox) * dx + (float(env_hit[1]) - oy) * dy + (float(env_hit[2]) - oz) * dz
                if t_env >= 0.0 and (t_block < 0.0 or t_env < t_block):
                    bx = int(math.floor(float(env_hit[0]) + 1e-6))
                    by = int(math.floor(float(env_hit[1]) - 1e-6))
                    bz = int(math.floor(float(env_hit[2]) + 1e-6))
                    return ((bx, by, bz), True)
                return (pos, False)

            def _update_hover_target(self) -> None:
                if (not self._build_enabled) or (not self._build_hover_pick_enabled):
                    self._hover_block = None
                    self._hover_block_is_env = False
                    return
                hit = self._pick_hover_block(int(self._mouse_x), int(self._mouse_y))
                if hit is None:
                    self._hover_block = None
                    self._hover_block_is_env = False
                    return
                pos, is_env = hit
                self._hover_block = pos
                self._hover_block_is_env = bool(is_env)

            def _set_walk_mode_capture(self, enabled: bool) -> None:
                want = bool(enabled)
                if bool(getattr(self, "_walk_mode_capture_active", False)) == want:
                    return
                try:
                    self.set_exclusive_mouse(bool(want))
                except Exception:
                    pass
                try:
                    self.set_mouse_visible(not bool(want))
                except Exception:
                    pass
                self._walk_mode_capture_active = bool(want)
                if not want:
                    try:
                        self._update_hover_cursor()
                    except Exception:
                        pass

            def _walk_mode_set_active(self, active: bool, *, reason: str) -> None:
                next_active = bool(active)
                prev_active = bool(getattr(self, "_walk_mode_active", False))
                if prev_active == next_active and bool(getattr(self, "_walk_mode_capture_active", False)) == next_active:
                    return
                self._walk_mode_active = bool(next_active)
                self._walk_mode_scaffold_pressed = set()
                self._walk_mode_move_accum_s = 0.0
                self._set_walk_mode_capture(bool(next_active))
                self._expansion_report.append(
                    f"Walk mode: {'ON' if next_active else 'OFF'} ({str(reason or 'unknown')})"
                )
                self._update_status()
                self.invalid = True

            def _walk_mode_force_exit(self, *, reason: str) -> None:
                if bool(getattr(self, "_walk_mode_active", False)):
                    self._walk_mode_set_active(False, reason=str(reason or "forced_exit"))
                    return
                if bool(getattr(self, "_walk_mode_capture_active", False)):
                    self._set_walk_mode_capture(False)
                    self._walk_mode_scaffold_pressed = set()
                    self._walk_mode_move_accum_s = 0.0

            def _camera_modifier_active(self, modifiers: int) -> bool:
                return bool(modifiers & pyglet.window.key.MOD_OPTION)

            def _hotbar_slot_label(self, idx: int) -> str:
                if idx >= 0 and idx < 9:
                    return str(idx + 1)
                return "0"

            def _hotbar_abbrev(self, block_id: str) -> str:
                bid = _block_id_base(str(block_id))
                name = bid.split(":", 1)[-1]
                parts = [p for p in name.split("_") if p]
                if not parts:
                    return ""
                if len(parts) == 1:
                    return parts[0][:4].upper()
                abbr = "".join(p[0] for p in parts)
                return abbr[:4].upper()

            def _hotbar_hit_slot(self, x: int, y: int) -> int | None:
                if not getattr(self.hotbar_panel_bg, "visible", False):
                    return None
                for i, fill in enumerate(self.hotbar_slot_fills):
                    if not getattr(fill, "visible", False):
                        continue
                    if fill.x <= x <= fill.x + fill.width and fill.y <= y <= fill.y + fill.height:
                        return i
                return None

            def _hotbar_select_slot(self, idx: int) -> None:
                if not self._hotbar_slots:
                    self._hotbar_slots = [self._build_selected_block_id] * 10
                idx = int(idx) % 10
                self._hotbar_selected = idx
                if 0 <= idx < len(self._hotbar_slots):
                    self._build_selected_block_id = self._hotbar_slots[idx]
                self._update_hotbar_ui()
                self._update_status()

            def _hotbar_assign_current(self, block_id: str) -> None:
                if not self._hotbar_slots:
                    self._hotbar_slots = [self._build_selected_block_id] * 10
                bid = str(block_id)
                self._hotbar_slots[self._hotbar_selected % 10] = bid
                self._build_selected_block_id = bid
                self._update_hotbar_ui()
                self._update_status()

            def _position_hotbar_icon(self, idx: int, tex: object) -> None:
                icon = self.hotbar_slot_icons[int(idx)]
                fill = self.hotbar_slot_fills[int(idx)]
                icon_px = max(8.0, min(float(fill.width), float(fill.height)) - 10.0)
                tex_w = max(1, int(getattr(tex, "width", 1)))
                tex_h = max(1, int(getattr(tex, "height", 1)))
                dim = max(1, int(max(tex_w, tex_h)))
                scale = float(icon_px) / float(dim)
                icon_w = float(tex_w) * float(scale)
                icon_h = float(tex_h) * float(scale)
                icon.update(
                    x=float(fill.x) + (float(fill.width) - float(icon_w)) * 0.5,
                    y=float(fill.y) + (float(fill.height) - float(icon_h)) * 0.5,
                    scale=scale,
                )

            def _update_hotbar_ui(self) -> None:
                if not hasattr(self, "hotbar_slot_borders"):
                    return
                ui_visible = bool(getattr(self.hotbar_panel_bg, "visible", False))
                border_frac = float(param_store.get("ui.selection.border.frac") or 0.0)
                if not math.isfinite(border_frac) or border_frac < 0.0:
                    border_frac = 0.0
                hide_border = bool(border_frac <= 1e-6)
                if not self._hotbar_slots:
                    self._hotbar_slots = [self._build_selected_block_id] * 10
                selected = int(self._hotbar_selected) % 10
                inactive_border = self._ui_ender_yellow
                active_border = self._ui_purple
                inactive_fill = (16, 16, 20)
                active_fill = self._ui_pink
                use_icons = bool(texture_source is not None and resolver is not None and hasattr(self, "hotbar_slot_icons"))
                now_s = time.monotonic()
                has_animated_icons = False
                for i in range(10):
                    border = self.hotbar_slot_borders[i]
                    fill = self.hotbar_slot_fills[i]
                    label = self.hotbar_slot_labels[i]
                    num = self.hotbar_slot_numbers[i]
                    bid = self._hotbar_slots[i] if i < len(self._hotbar_slots) else ""
                    icon_visible = False
                    if use_icons and bid:
                        jar_rel_tex = ""
                        try:
                            app = resolver.resolve_block_appearance(bid) if resolver is not None else None
                        except Exception:
                            app = None
                        if app is not None:
                            for face in ("up", "north", "south", "east", "west", "down"):
                                jar_rel = app.face_texture_png_by_dir.get(face) or ""
                                if jar_rel:
                                    jar_rel_tex = jar_rel
                                    break
                        tex = None
                        icon_key = ""
                        if jar_rel_tex and texture_source is not None:
                            try:
                                tex, icon_key = _load_ui_icon_tex(texture_source, jar_rel_tex, now_s=float(now_s))
                            except Exception:
                                tex = None
                                icon_key = ""
                            spec = ui_anim_spec_cache.get(jar_rel_tex)
                            if spec is not None and len(spec.frames) > 1:
                                has_animated_icons = True
                        if tex is None and jar_rel_tex:
                            tex = self._hotbar_missing_tex
                            icon_key = f"{jar_rel_tex}#missing"
                            if tex is None:
                                try:
                                    tex = pyglet.image.SolidColorImagePattern((60, 48, 74, 255)).create_image(2, 2).get_texture()
                                except Exception:
                                    tex = None
                                else:
                                    icon_key = f"{jar_rel_tex}#fallback"

                        if tex is not None:
                            try:
                                icon = self.hotbar_slot_icons[i]
                                if i < len(self._hotbar_slot_icon_keys) and self._hotbar_slot_icon_keys[i] != icon_key:
                                    icon.image = tex  # type: ignore[assignment]
                                    self._hotbar_slot_icon_keys[i] = icon_key
                                self._position_hotbar_icon(i, tex)
                                icon.visible = bool(ui_visible)
                                icon_visible = True
                            except Exception:
                                icon_visible = False

                    if use_icons and not icon_visible:
                        try:
                            self.hotbar_slot_icons[i].visible = False
                            if i < len(self._hotbar_slot_icon_keys):
                                self._hotbar_slot_icon_keys[i] = ""
                        except Exception:
                            pass

                    label.text = "" if icon_visible else (self._hotbar_abbrev(bid) if bid else "")
                    if i == selected:
                        border.color = active_border
                        border.opacity = 0 if hide_border else 235
                        fill.color = active_fill
                        fill.opacity = 115
                        label.color = (*self._ui_purple_hot, 255)
                        num.color = (*self._ui_purple_hot, 255)
                    else:
                        border.color = inactive_border
                        border.opacity = 0 if hide_border else 90
                        fill.color = inactive_fill
                        fill.opacity = 180
                        label.color = (*self._ui_purple_hi, 210)
                        num.color = (160, 160, 170, 220)
                self.hotbar_panel_bg.color = (12, 12, 16)
                self._hotbar_has_animated_icons = bool(has_animated_icons)

            def _tick_hotbar_icon_animation(self) -> None:
                if not bool(getattr(self, "_hotbar_has_animated_icons", False)):
                    return
                if texture_source is None:
                    self._hotbar_has_animated_icons = False
                    return
                now_s = time.monotonic()
                ui_visible = bool(getattr(self.hotbar_panel_bg, "visible", False))
                has_animated_icons = False
                for i, key in enumerate(self._hotbar_slot_icon_keys):
                    icon_key = str(key or "")
                    if not icon_key:
                        continue
                    jar_rel_tex = icon_key.split("#", 1)[0]
                    if not jar_rel_tex:
                        continue
                    spec = ui_anim_spec_cache.get(jar_rel_tex)
                    if spec is None:
                        tex0 = load_tex_from_jar(texture_source, jar_rel_tex)
                        if tex0 is not None:
                            spec = _load_ui_anim_spec(texture_source, jar_rel_tex, tex0)
                    if spec is None or len(spec.frames) <= 1:
                        continue
                    has_animated_icons = True
                    tex, next_key = _load_ui_icon_tex(texture_source, jar_rel_tex, now_s=float(now_s))
                    if tex is None:
                        continue
                    if next_key == icon_key:
                        continue
                    try:
                        icon = self.hotbar_slot_icons[i]
                        icon.image = tex  # type: ignore[assignment]
                        if i < len(self._hotbar_slot_icon_keys):
                            self._hotbar_slot_icon_keys[i] = next_key
                        self._position_hotbar_icon(i, tex)
                        icon.visible = bool(ui_visible)
                    except Exception:
                        continue
                self._hotbar_has_animated_icons = bool(has_animated_icons)

            def _build_apply_edit(self, blocks_by_pos: dict[Vec3i, BlockInstance], *, note: str) -> None:
                cur = self._current_structure
                if cur is None:
                    return
                blocks = list(blocks_by_pos.values())
                if blocks:
                    xs = [p.pos[0] for p in blocks]
                    ys = [p.pos[1] for p in blocks]
                    zs = [p.pos[2] for p in blocks]
                    size = (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, max(zs) - min(zs) + 1)
                else:
                    size = cur.size

                report = list(self._expansion_report)
                report.append(note)
                if len(report) > 800:
                    report = report[-800:]

                edited = Structure(
                    size=size,
                    blocks=tuple(sorted(blocks, key=lambda b: (b.pos[1], b.pos[2], b.pos[0], b.block_id))),
                    block_entities=tuple(be for be in cur.block_entities if be.pos in blocks_by_pos),
                    entities=cur.entities,
                )
                self._apply_structure_and_batch(edited, report, reset_view=False, adjust_distance=False)
                self._build_last_edit_t = time.monotonic()

            def _build_set_block(self, pos: Vec3i, block: BlockInstance | None, *, note: str, record: bool = True) -> None:
                cur = self._current_structure
                if cur is None or self._terminal_busy():
                    return
                blocks_by_pos: dict[Vec3i, BlockInstance] = {b.pos: b for b in cur.blocks}
                before = blocks_by_pos.get(pos)
                after = block
                if after is None:
                    if pos not in blocks_by_pos:
                        return
                    del blocks_by_pos[pos]
                else:
                    blocks_by_pos[pos] = after
                self._spawn_flash_box(
                    (float(pos[0]) - self._pivot_center[0], float(pos[1]) - self._pivot_center[1], float(pos[2]) - self._pivot_center[2]),
                    (float(pos[0] + 1) - self._pivot_center[0], float(pos[1] + 1) - self._pivot_center[1], float(pos[2] + 1) - self._pivot_center[2]),
                    duration_s=0.30,
                )
                self._build_apply_edit(blocks_by_pos, note=f"build: {note}")
                if record:
                    self._build_undo.append((pos, before, after, note))
                    self._build_redo.clear()

            def _build_remove_block(self, pos: Vec3i) -> None:
                self._build_set_block(pos, None, note=f"removed {pos}")

            def _build_place_block(
                self,
                pos: Vec3i,
                *,
                block_id: str | None = None,
                face_n: Vec3i | None = None,
            ) -> None:
                cur = self._current_structure
                if cur is None or self._terminal_busy():
                    return
                bid = block_id or self._build_selected_block_id
                bid = _build_place_block_id_for_face(bid, face_n)
                if not isinstance(bid, str) or not bid or _block_id_base(bid) in {"minecraft:air", "minecraft:void_air", "minecraft:cave_air"}:
                    return
                blocks_by_pos: dict[Vec3i, BlockInstance] = {b.pos: b for b in cur.blocks}
                if pos in blocks_by_pos:
                    return
                before = blocks_by_pos.get(pos)
                after = BlockInstance(pos=pos, block_id=bid, color_key=bid)
                blocks_by_pos[pos] = after
                cx, cy, cz = self._pivot_center
                self._spawn_flash_box(
                    (float(pos[0]) - cx, float(pos[1]) - cy, float(pos[2]) - cz),
                    (float(pos[0] + 1) - cx, float(pos[1] + 1) - cy, float(pos[2] + 1) - cz),
                    duration_s=0.50,
                )
                self._build_apply_edit(blocks_by_pos, note=f"build: placed {bid} @ {pos}")
                self._build_undo.append((pos, before, after, f"placed {bid} @ {pos}"))
                self._build_redo.clear()

            def _build_pick_block(self, pos: Vec3i) -> None:
                cur = self._current_structure
                if cur is None:
                    return
                blocks_by_pos: dict[Vec3i, BlockInstance] = {b.pos: b for b in cur.blocks}
                blk = blocks_by_pos.get(pos)
                if blk is None:
                    return
                self._build_selected_block_id = blk.block_id
                if self._hotbar_slots:
                    self._hotbar_slots[self._hotbar_selected % 10] = blk.block_id
                if self._palette_entries:
                    try:
                        for i, e in enumerate(self._palette_entries):
                            if e.block_id == blk.block_id:
                                self._palette_selected = i
                                break
                    except Exception:
                        pass
                self._update_hotbar_ui()
                self._update_status()

            def _build_do_undo(self) -> None:
                if self._terminal_busy() or not self._build_undo:
                    return
                cur = self._current_structure
                if cur is None:
                    return
                pos, before, after, note = self._build_undo.pop()
                blocks_by_pos: dict[Vec3i, BlockInstance] = {b.pos: b for b in cur.blocks}
                if before is None:
                    blocks_by_pos.pop(pos, None)
                else:
                    blocks_by_pos[pos] = before
                self._build_apply_edit(blocks_by_pos, note=f"undo: {note}")
                self._build_redo.append((pos, before, after, note))

            def _build_do_redo(self) -> None:
                if self._terminal_busy() or not self._build_redo:
                    return
                cur = self._current_structure
                if cur is None:
                    return
                pos, before, after, note = self._build_redo.pop()
                blocks_by_pos: dict[Vec3i, BlockInstance] = {b.pos: b for b in cur.blocks}
                if after is None:
                    blocks_by_pos.pop(pos, None)
                else:
                    blocks_by_pos[pos] = after
                self._build_apply_edit(blocks_by_pos, note=f"redo: {note}")
                self._build_undo.append((pos, before, after, note))

            def _spawn_flash_box(
                self,
                min_corner: tuple[float, float, float],
                max_corner: tuple[float, float, float],
                *,
                duration_s: float = 1.0,
                color: tuple[float, float, float] | None = None,
            ) -> None:
                fx_mod.spawn_flash_box(
                    self,
                    min_corner,
                    max_corner,
                    duration_s=duration_s,
                    color=color,
                    param_store=param_store,
                    Tween=Tween,
                    ease_smoothstep=ease_smoothstep,
                    ease_linear=ease_linear,
                )

            def _tick_effects(self) -> None:
                fx_mod.tick_effects(self)

            def _draw_effects(self) -> None:
                fx_mod.draw_effects(self, gl=gl)

            def _new_seed(self) -> int:
                return secrets.randbits(32)

            def _toggle_ui_hidden(self) -> None:
                self._ui_hidden = not self._ui_hidden
                end = 0.0 if self._ui_hidden else float(self._sidebar_width_default)
                start = float(self.sidebar_width)
                if abs(start - end) <= 0.5:
                    self.sidebar_width = float(end)
                    self._sidebar_width_tween = None
                    self._layout_ui()
                    self.invalid = True
                    return
                now = time.monotonic()
                self._sidebar_width_tween = Tween(now, 0.20, start=float(start), end=float(end), ease=ease_smoothstep)
                self.invalid = True

            def _sidebar_divider_hit(self, x: float) -> bool:
                # Hit zone for resizing the sidebar. Make this fairly generous
                # so it's easy to grab on high-DPI displays.
                hit = float(self._ui_f(12.0))
                if not math.isfinite(hit):
                    hit = 12.0
                hit = max(8.0, min(32.0, hit))
                w = float(self.sidebar_width)
                if w <= 0.5:
                    # When the sidebar is collapsed, allow grabbing the left edge
                    # to reopen it.
                    return float(x) <= hit
                return abs(float(x) - w) <= hit

            def _ensure_mouse_cursor_cache(self) -> None:
                if getattr(self, "_cursor_default", None) is None:
                    try:
                        # macOS note: the default cursor can sometimes end up as
                        # an I-beam via Cocoa cursorUpdate. Using an explicit
                        # arrow cursor on darwin makes it easier to re-assert
                        # "arrow" as the baseline cursor.
                        default_cursor = self.CURSOR_DEFAULT
                        if sys.platform == "darwin":
                            default_cursor = self.CURSOR_HELP
                        self._cursor_default = self.get_system_mouse_cursor(default_cursor)
                    except Exception:
                        self._cursor_default = None
                if getattr(self, "_cursor_resize_lr", None) is None:
                    try:
                        self._cursor_resize_lr = self.get_system_mouse_cursor(self.CURSOR_SIZE_LEFT_RIGHT)
                    except Exception:
                        self._cursor_resize_lr = None

            def _set_cursor_kind(self, kind: str) -> None:
                self._ensure_mouse_cursor_cache()
                cursor = getattr(
                    self,
                    "_cursor_resize_lr" if kind == "resize_lr" else "_cursor_default",
                    None,
                )
                if cursor is None:
                    return
                cur_kind = str(getattr(self, "_cursor_kind", "default"))
                # Pyglet on macOS can occasionally reset the system cursor (e.g.
                # via cursorUpdate). Re-apply the desired cursor even when the
                # kind hasn't changed so we can reliably keep an arrow cursor
                # outside the divider hitbox.
                try:
                    self.set_mouse_cursor(cursor)  # type: ignore[arg-type]
                except Exception:
                    return
                self._cursor_kind = str(kind)

            def _update_hover_cursor(self) -> None:
                kind = "default"
                if bool(getattr(self, "_sidebar_resize_active", False)) or self._sidebar_divider_hit(float(self._mouse_x)):
                    kind = "resize_lr"
                self._set_cursor_kind(kind)

            def _tick_sidebar_width_tween(self) -> None:
                tween = self._sidebar_width_tween
                if tween is None:
                    return
                now = time.monotonic()
                new_w = float(tween.value(now))
                if new_w < 0.5:
                    new_w = 0.0
                cur = float(self.sidebar_width)
                if abs(new_w - cur) > 0.25:
                    self.sidebar_width = float(new_w)
                    self._layout_ui(ensure_labels=False)
                    self.invalid = True
                if tween.done(now):
                    self.sidebar_width = float(tween.end)
                    self._sidebar_width_tween = None
                    self._layout_ui()
                    self.invalid = True

            def _jigsaw_seed_for(self, *, depth: int, reroll: int) -> int:
                base = self._jigsaw_seed_base
                if base is None:
                    return self._new_seed()
                return _stable_seed(base, "jigsaw", int(depth), int(reroll)) & 0xFFFFFFFF

            def _jigsaw_push_level(self) -> None:
                depth0 = len(self.jigsaw_seeds)
                if self._jigsaw_seed_tape and depth0 < len(self._jigsaw_seed_tape):
                    seed = int(self._jigsaw_seed_tape[depth0]) & 0xFFFFFFFF
                else:
                    seed = self._jigsaw_seed_for(depth=depth0 + 1, reroll=0)
                self.jigsaw_seeds.append(seed)
                self._jigsaw_reroll_counts.append(0)

            def _jigsaw_pop_level(self) -> bool:
                if not self.jigsaw_seeds:
                    return False
                self.jigsaw_seeds.pop()
                if self._jigsaw_reroll_counts:
                    self._jigsaw_reroll_counts.pop()
                return True

            def _jigsaw_reroll_level(self) -> bool:
                if not self.jigsaw_seeds:
                    return False
                cur = int(self.jigsaw_seeds[-1])
                if self._jigsaw_seed_base is None:
                    nxt = self._new_seed()
                    while nxt == cur:
                        nxt = self._new_seed()
                    self.jigsaw_seeds[-1] = nxt
                    return True

                depth = len(self.jigsaw_seeds)
                while len(self._jigsaw_reroll_counts) < depth:
                    self._jigsaw_reroll_counts.append(0)
                self._jigsaw_reroll_counts[-1] += 1
                nxt = self._jigsaw_seed_for(depth=depth, reroll=self._jigsaw_reroll_counts[-1])
                if nxt == cur:
                    self._jigsaw_reroll_counts[-1] += 1
                    nxt = self._jigsaw_seed_for(depth=depth, reroll=self._jigsaw_reroll_counts[-1])
                self.jigsaw_seeds[-1] = nxt
                return True

            def _jigsaw_reset(self) -> None:
                self.jigsaw_seeds = []
                self._jigsaw_reroll_counts = []

            def _update_hud_blocks_live(self) -> None:
                if not self.status_labels:
                    return

                def _fmt_count(n: int) -> str:
                    if n < 1_000:
                        return str(n)
                    if n < 10_000:
                        return f"{n / 1_000.0:.1f}k"
                    if n < 1_000_000:
                        return f"{n // 1_000}k"
                    if n < 10_000_000:
                        return f"{n / 1_000_000.0:.1f}M"
                    return f"{n // 1_000_000}M"

                if self._rez_active and self._rez_live_positions:
                    blocks = len(self._rez_live_positions)
                elif self._current_structure is not None:
                    blocks = len(self._current_structure.blocks)
                else:
                    blocks = 0
                if self._current_structure is not None:
                    ent_count = len(self._current_structure.entities)
                    be_count = len(self._current_structure.block_entities)
                else:
                    ent_count = 0
                    be_count = 0
                if (
                    blocks == self._hud_blocks_live
                    and ent_count == self._hud_entities_live
                    and be_count == self._hud_block_entities_live
                ):
                    return
                self._hud_blocks_live = blocks
                self._hud_entities_live = ent_count
                self._hud_block_entities_live = be_count
                depth = len(self.jigsaw_seeds)
                fx_state = "OFF" if not self._effects_enabled else "ON"
                walk_state = "WALK" if bool(getattr(self, "_walk_mode_active", False)) else "NAV"
                self.status_labels[0].text = (
                    f"depth {depth}  blocks {_fmt_count(blocks)}  ent {_fmt_count(ent_count)}  be {_fmt_count(be_count)}  FX {fx_state}  {walk_state}"
                )

            def _update_status(self) -> None:
                depth = len(self.jigsaw_seeds)
                if self._rez_active and self._rez_live_positions:
                    blocks = len(self._rez_live_positions)
                else:
                    blocks = len(self._current_structure.blocks) if self._current_structure is not None else 0
                if self._current_structure is not None:
                    ent_count = len(self._current_structure.entities)
                    be_count = len(self._current_structure.block_entities)
                else:
                    ent_count = 0
                    be_count = 0

                def _fmt_count(n: int) -> str:
                    if n < 1_000:
                        return str(n)
                    if n < 10_000:
                        return f"{n / 1_000.0:.1f}k"
                    if n < 1_000_000:
                        return f"{n // 1_000}k"
                    if n < 10_000_000:
                        return f"{n / 1_000_000.0:.1f}M"
                    return f"{n // 1_000_000}M"

                env_name = self._env_preset().name
                fx_state = "OFF" if not self._effects_enabled else "ON"
                walk_state = "WALK" if bool(getattr(self, "_walk_mode_active", False)) else "NAV"
                lines: list[str] = [
                    (
                        f"depth {depth}  blocks {_fmt_count(blocks)}  ent {_fmt_count(ent_count)}  "
                        f"be {_fmt_count(be_count)}  env {env_name}  FX {fx_state}  {walk_state}"
                    )
                ]
                if bool(getattr(self, "_walk_mode_active", False)):
                    lines.append("WALK MODE ACTIVE: Esc exits   W/A/S/D move (XZ, collision-safe)")
                if depth:
                    lines.append(f"seed: 0x{self.jigsaw_seeds[-1]:08x}   (Space reroll)")
                else:
                    lines.append("Right expand   U export USDZ   N export NBT   F frame   C 2nd viewport   Shift+C add viewport")

                if self._jigsaw_selected is not None:
                    c = self._jigsaw_selected
                    vec_to_dir = {
                        (0, 0, -1): "north",
                        (0, 0, 1): "south",
                        (-1, 0, 0): "west",
                        (1, 0, 0): "east",
                        (0, 1, 0): "up",
                        (0, -1, 0): "down",
                    }

                    def _short(s: str, n: int) -> str:
                        if len(s) <= n:
                            return s
                        return "…" + s[-(n - 1) :]

                    pool = _short(str(c.pool), 34)
                    target = _short(str(c.target), 34)
                    name = _short(str(c.name), 34)
                    joint = _short(str(c.joint), 16)
                    facing = vec_to_dir.get(tuple(int(v) for v in c.front), "?")
                    final_state = _short(str(c.final_state), 58)
                    lines.append(f"pool: {pool}  target {target}")
                    lines.append(f"name {name}  joint {joint}  facing {facing}  final {final_state}   J open  Enter regrow")
                if self._build_enabled:
                    bid = self._build_selected_block_id
                    if len(bid) > 52:
                        bid = "…" + bid[-51:]
                    slot = self._hotbar_slot_label(self._hotbar_selected)
                    lines.append(
                        f"Build ON: LMB break / RMB place / MMB pick   ⌥ camera   1-9/0 hotbar {slot}   I palette   {bid}"
                    )
                else:
                    lines.append("Up/Down select   PgUp/PgDn page   E env   O ortho   P open folder   C 2nd viewport   Shift+C add viewport")
                if self._last_export is not None:
                    lines.append(f"last export: {self._last_export.name}")
                self._set_status(lines[: self.status_lines_max])
                try:
                    self.walk_mode_label.visible = bool(getattr(self, "_walk_mode_active", False))
                except Exception:
                    pass
                self._hud_blocks_live = blocks
                self._hud_entities_live = ent_count
                self._hud_block_entities_live = be_count

                log_lines = self._expansion_report[-len(self.log_labels) :] if self._expansion_report else ["(no expansions yet)"]
                self._set_log(log_lines)

            def _rebuild_scene(self, *, reset_view: bool, adjust_distance: bool, enable_delta_fade: bool = True) -> None:
                base = self._base_template
                if base is None:
                    return

                if not self.jigsaw_seeds:
                    self._cancel_rez()
                    cached_base = self._jigsaw_cache.get(())
                    if cached_base is not None:
                        structure, report, state = cached_base
                        self._set_jigsaw_state(state if isinstance(state, JigsawExpansionState) else None)
                        self._apply_structure_and_batch(
                            structure,
                            list(report) if isinstance(report, list) else [],
                            reset_view=reset_view,
                            adjust_distance=adjust_distance,
                            enable_delta_fade=bool(enable_delta_fade),
                        )
                    else:
                        structure = Structure(
                            size=base.size,
                            blocks=base.blocks,
                            block_entities=base.block_entities,
                            entities=base.entities,
                        )
                        self._set_jigsaw_state(
                            JigsawExpansionState(
                                connectors=base.connectors,
                                consumed=frozenset(),
                                dead_end=frozenset(),
                                piece_bounds=((0, 0, 0, int(base.size[0]) - 1, int(base.size[1]) - 1, int(base.size[2]) - 1),),
                            )
                        )
                        self._apply_structure_and_batch(
                            structure,
                            [],
                            reset_view=reset_view,
                            adjust_distance=adjust_distance,
                            enable_delta_fade=bool(enable_delta_fade),
                        )
                    return

                cache_key = tuple(self.jigsaw_seeds)
                cached = self._jigsaw_cache.get(cache_key)
                if cached is not None:
                    self._cancel_rez()
                    structure, report, _state = cached
                    self._set_jigsaw_state(_state if isinstance(_state, JigsawExpansionState) else None)
                    self._apply_structure_and_batch(
                        structure,
                        list(report),
                        reset_view=reset_view,
                        adjust_distance=adjust_distance,
                        enable_delta_fade=bool(enable_delta_fade),
                    )
                    return

                self._start_rez_build(
                    base=base,
                    seeds=list(self.jigsaw_seeds),
                    reset_view=reset_view,
                    adjust_distance=adjust_distance,
                )

            def _move_selection(self, delta: int) -> None:
                if self._rez_active:
                    return
                # Smoke: real-window key injection should validate OS-level key
                # delivery and deterministic selection deltas without depending
                # on datapack/pool load support. In this mode, move the sidebar
                # selection but do not load the target item.
                if smoke_real_window_keys_enabled or smoke_suite_enabled:
                    indices = getattr(self, "_filtered_indices", None)
                    if not isinstance(indices, list) or not indices:
                        return
                    pos = self._selected_list_pos()
                    if pos is None:
                        try:
                            self._set_active_selected_index(int(indices[0]))
                            self._update_list_labels(ensure_selection_visible=False)
                        except Exception:
                            pass
                        return

                    new_pos = max(0, min(len(indices) - 1, int(pos) + int(delta)))
                    idx = int(indices[int(new_pos)])
                    if idx != int(self._active_selected_index()):
                        self._set_active_selected_index(idx)
                        self._update_list_labels(ensure_selection_visible=False)
                    return
                pos = self._selected_list_pos()
                if pos is None:
                    self._load_list_pos(0)
                    return
                self._load_list_pos(pos + delta)

            def _search_find_next(self, needle: str, *, start: int) -> int | None:
                n = needle.lower()
                if not n:
                    return None
                active = self._active_labels()
                start = max(0, min(len(active), start))
                for i in range(start, len(active)):
                    if n in active[i].lower():
                        return i
                for i in range(0, start):
                    if n in active[i].lower():
                        return i
                return None

            def _start_search(self) -> None:
                if self._rez_active:
                    return
                self._search_active = True
                self._search_origin_selected = self._active_selected_index()
                self._update_search_ui()
                self._layout_ui()

            def _end_search(self, *, cancel: bool) -> None:
                if not self._search_ui_visible():
                    return
                if cancel:
                    self._clear_search_filter(keep_selection=False)
                else:
                    self._search_active = False
                self._update_search_ui()
                self._layout_ui()
                self._update_list_labels()

            def _clear_search_filter(self, *, keep_selection: bool) -> None:
                origin = self._search_origin_selected
                self._search_active = False
                self._search_query = ""
                self._update_filtered_indices()
                self.scroll_top = 0
                self._scroll_pos_f = 0.0
                if keep_selection:
                    return
                if self._browser_mode == "datasets":
                    if 0 <= origin < len(self._dataset_labels) and origin != int(self._dataset_selected):
                        self._dataset_selected = int(origin)
                        self._update_list_labels()
                    return
                if self._browser_mode == "structures":
                    if 0 <= origin < len(labels) and origin != int(self._structures_selected):
                        self._load_index(origin, reset_view=False)
                    return
                if 0 <= origin < len(pool_labels) and origin != int(self.selected):
                    self._load_pool_index(origin, reset_view=False)

            def _update_search_ui(self) -> None:
                if not self._search_ui_visible():
                    self.search_label.text = ""
                    self.search_label.color = (*self._ui_purple, 0)
                    self.search_count_label.text = ""
                    self.search_count_label.color = (*self._ui_purple, 0)
                    for lbl, _, _ in self.search_cancel_label_o_layers:
                        lbl.color = (*self._ui_purple_hi, 0)
                    for lbl, _, _ in self.search_cancel_label_x_layers:
                        lbl.color = (*self._ui_purple_hot, 0)
                    self.search_glow.opacity = 0
                    return

                total = len(self._active_labels())
                matches = len(self._filtered_indices)
                q = self._search_query.strip()
                if q:
                    self.search_count_label.text = f"{matches}/{total}"
                else:
                    self.search_count_label.text = ""
                self.search_count_label.color = (*self._ui_purple, 170)
                for lbl, _, a_mul in self.search_cancel_label_o_layers:
                    lbl.color = (*self._ui_purple_hi, int(220 * a_mul))
                for lbl, _, a_mul in self.search_cancel_label_x_layers:
                    lbl.color = (*self._ui_purple_hot, int(255 * a_mul))

                cursor = ""
                if self._search_active:
                    cursor = "▌" if int(time.monotonic() * 2.2) % 2 == 0 else ""
                if q:
                    render_q = q
                    if len(render_q) > 56:
                        render_q = "…" + render_q[-55:]
                    self.search_label.text = f"/{render_q}{cursor}"
                    self.search_label.color = (*self._ui_purple, 235)
                else:
                    self.search_label.text = f"/{cursor}" if cursor else "/"
                    self.search_label.color = (*self._ui_purple, 200)

            def _set_search_query(self, query: str) -> None:
                self._search_query = query
                self._update_filtered_indices()
                self.scroll_top = 0
                self._scroll_pos_f = 0.0

                indices = self._filtered_indices
                if indices:
                    active_selected = self._active_selected_index()
                    if active_selected not in self._filtered_pos_by_index:
                        if self._browser_mode == "datasets":
                            self._dataset_selected = indices[0]
                        elif self._browser_mode == "structures":
                            self._load_index(indices[0], reset_view=False)
                        else:
                            self._load_pool_index(indices[0], reset_view=False)
                self._update_search_ui()
                self._layout_ui(ensure_labels=False)
                self._update_list_labels()

            def _sync_ui_palette(self) -> None:
                def _clamp01(v: float) -> float:
                    if v <= 0.0:
                        return 0.0
                    if v >= 1.0:
                        return 1.0
                    return v

                def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
                    t = 0.0 if t <= 0.0 else (1.0 if t >= 1.0 else t)
                    return (
                        max(0, min(255, int(round(float(a[0]) + (float(b[0]) - float(a[0])) * t)))),
                        max(0, min(255, int(round(float(a[1]) + (float(b[1]) - float(a[1])) * t)))),
                        max(0, min(255, int(round(float(a[2]) + (float(b[2]) - float(a[2])) * t)))),
                    )

                er = _clamp01(float(param_store.get("fx.color.ender.r")))
                eg = _clamp01(float(param_store.get("fx.color.ender.g")))
                eb = _clamp01(float(param_store.get("fx.color.ender.b")))
                ender = (int(round(er * 255.0)), int(round(eg * 255.0)), int(round(eb * 255.0)))
                yr = _clamp01(float(param_store.get("fx.color.ender.yellow.r")))
                yg = _clamp01(float(param_store.get("fx.color.ender.yellow.g")))
                yb = _clamp01(float(param_store.get("fx.color.ender.yellow.b")))
                ender_yellow = (int(round(yr * 255.0)), int(round(yg * 255.0)), int(round(yb * 255.0)))
                pr = _clamp01(float(param_store.get("fx.color.ender.pink.r")))
                pg = _clamp01(float(param_store.get("fx.color.ender.pink.g")))
                pb = _clamp01(float(param_store.get("fx.color.ender.pink.b")))
                ender_pink = (int(round(pr * 255.0)), int(round(pg * 255.0)), int(round(pb * 255.0)))
                self._ui_purple = ender
                self._ui_purple_hi = _mix(ender, (255, 255, 255), 0.35)
                self._ui_purple_hot = _mix(ender, (255, 255, 255), 0.55)
                self._ui_ender_yellow = ender_yellow
                self._ui_pink = ender_pink
                cancel_bg = _mix(ender, (0, 0, 0), 0.55)
                cancel_bg = _mix(cancel_bg, (32, 18, 44), 0.35)
                self._ui_cancel_bg = cancel_bg
                self._ui_cancel_bg_hot = _mix(cancel_bg, ender, 0.35)
                try:
                    alpha = 45 if self.ui_font_name == "terminal Mixed" else 0
                    self.brand_label.color = (*ender, int(alpha))
                except Exception:
                    pass
                ui_black = (0, 0, 0)
                sidebar_bg = _mix(ui_black, ender, 0.08)
                ui_frame = ender_yellow
                ui_track = _mix(sidebar_bg, ender_yellow, 0.14)
                ui_panel = _mix(sidebar_bg, (255, 255, 255), 0.02)
                ui_panel_alt = _mix(sidebar_bg, ender, 0.14)

                # Push updated theme into long-lived shapes (tuples don't auto-update).
                for shape, color in (
                    (self.sidebar_bg, sidebar_bg),
                    (self.log_bg, ui_panel),
                    (self.list_bg, ui_panel),
                    (self.sidebar_divider, ui_track),
                    (self.log_frame_top, ui_frame),
                    (self.log_frame_bottom, ui_frame),
                    (self.log_frame_left, ui_frame),
                    (self.log_frame_right, ui_frame),
                    (self.list_frame_top, ui_frame),
                    (self.list_frame_bottom, ui_frame),
                    (self.list_frame_left, ui_frame),
                    (self.list_frame_right, ui_frame),
                    (self.scroll_track, ui_track),
                    (self.search_bg, ui_panel_alt),
                    (self.log_toggle_bg, ui_panel_alt),
                    (self.selection_bg, ender),
                    (self.selection_glow, ender),
                    (self.search_glow, ender),
                    (self.scroll_thumb, ender),
                    (self.scroll_thumb_glow, ender),
                    (self.rez_bar_fill, ender),
                    (self.rez_bar_fill_hi, self._ui_purple_hi),
                    (self.search_cancel_bg, self._ui_cancel_bg),
                    (self.rez_cancel_bg, self._ui_cancel_bg),
                    (self.help_frame_top, ender),
                    (self.help_frame_bottom, ender),
                    (self.help_frame_left, ender),
                    (self.help_frame_right, ender),
                    (self.palette_frame_top, ender_yellow),
                    (self.palette_frame_bottom, ender_yellow),
                    (self.palette_frame_left, ender_yellow),
                    (self.palette_frame_right, ender_yellow),
                ):
                    try:
                        shape.color = color  # type: ignore[attr-defined]
                    except Exception:
                        pass
                for lbl, rgba in (
                    (self.title, (*ender_yellow, 255)),
                    (self.subtitle, (*ender_yellow, 220)),
                    (self.log_title, (*ender_yellow, 210)),
                    (self.log_toggle_label, (*ender_yellow, 255)),
                    (self.help_label, (*ender_pink, 220)),
                ):
                    try:
                        lbl.color = rgba  # type: ignore[attr-defined]
                    except Exception:
                        pass
                for seq in (self.search_cancel_glows, self.rez_bar_glitch, self.rez_cancel_glows):
                    for r in seq:
                        try:
                            r.color = ender
                        except Exception:
                            pass
                self._update_hotbar_ui()

            def _on_tick(self, dt: float) -> None:
                perf = getattr(self, "_perf_enabled", False)
                tick_t0 = time.perf_counter() if perf else 0.0
                tick_now = time.monotonic()
                self._tick_fps_frames += 1
                tick_span = float(tick_now) - float(self._tick_fps_last_t)
                if tick_span >= 0.5:
                    self._tick_fps_value = float(self._tick_fps_frames) / float(max(1e-6, tick_span))
                    self._tick_fps_frames = 0
                    self._tick_fps_last_t = float(tick_now)
                desired_vsync = bool(param_store.get_int("render.vsync"))
                if self._vsync_enabled is None or desired_vsync != self._vsync_enabled:
                    try:
                        self.set_vsync(bool(desired_vsync))
                        self._vsync_enabled = desired_vsync
                        self._vsync_apply_error = None
                    except Exception as e:
                        self._vsync_apply_error = f"{type(e).__name__}: {e}"
                self._refresh_render_cap_hz()

                desired_effects_enabled = bool(params_mod.effects_master_enabled(param_store))
                if desired_effects_enabled != bool(self._effects_enabled):
                    self._effects_enabled = desired_effects_enabled
                    if not self._effects_enabled:
                        self._effects.clear()
                    self._update_status()
                    self._update_debug_panel()
                desired_hover_pick_enabled = bool(params_mod.hover_pick_enabled(param_store))
                if desired_hover_pick_enabled != bool(self._build_hover_pick_enabled):
                    self._build_hover_pick_enabled = desired_hover_pick_enabled
                    if not self._build_hover_pick_enabled:
                        self._hover_block = None
                        self._hover_block_is_env = False

                # UI font scale (readability). Apply live changes from kValue.
                try:
                    desired_scale = float(param_store.get("ui.font.scale") or 1.0)
                except Exception:
                    desired_scale = 1.0
                if not math.isfinite(desired_scale):
                    desired_scale = 1.0
                desired_scale = max(0.5, min(3.0, float(desired_scale)))
                if abs(float(desired_scale) - float(getattr(self, "_ui_font_scale_last", 1.0))) > 1e-6:
                    self._ui_font_scale = float(desired_scale)
                    self._ui_font_scale_last = float(desired_scale)
                    self._apply_ui_font_scale()

                # Live-update hotbar border thickness from kValue.
                try:
                    desired_border_frac = float(param_store.get("ui.selection.border.frac") or 0.0)
                except Exception:
                    desired_border_frac = 0.0
                if not math.isfinite(desired_border_frac) or desired_border_frac < 0.0:
                    desired_border_frac = 0.0
                if abs(desired_border_frac - float(getattr(self, "_ui_selection_border_frac_last", -1.0))) > 1e-6:
                    self._ui_selection_border_frac_last = float(desired_border_frac)
                    self._layout_hotbar_overlay()
                sym = self._repeat_symbol
                if sym is not None:
                    now = float(time.monotonic())
                    if now >= float(self._repeat_next_at_s):
                        if sym == pyglet.window.key.UP:
                            self._move_selection(-1)
                        elif sym == pyglet.window.key.DOWN:
                            self._move_selection(1)
                        # Intentionally schedule from "now" to avoid catch-up bursts
                        # after long/stalled frames.
                        self._repeat_next_at_s = now + float(self._repeat_rate_s)
                self._sync_ui_palette()
                self._tick_camera_tween()
                if bool(getattr(self, "_walk_mode_active", False)):
                    cam_x, _cam_y, cam_z = _viewport_camera_world_position(self)
                    walk_forward_xz = _walk_mode_forward_xz(
                        yaw_deg=float(getattr(self, "yaw", 0.0)),
                        orbit_target=tuple(getattr(self, "_orbit_target", (0.0, 0.0, 0.0))),
                        camera_world=(float(cam_x), 0.0, float(cam_z)),
                    )
                    move_dx, move_dz, move_carry = _walk_mode_integrate_xz(
                        pressed_symbols=set(getattr(self, "_walk_mode_scaffold_pressed", set())),
                        yaw_deg=float(getattr(self, "yaw", 0.0)),
                        forward_xz=walk_forward_xz,
                        frame_dt_s=float(dt),
                        carry_dt_s=float(getattr(self, "_walk_mode_move_accum_s", 0.0)),
                        fixed_dt_s=float(getattr(self, "_walk_mode_move_fixed_step_s", 1.0 / 120.0)),
                        max_steps=int(getattr(self, "_walk_mode_move_max_steps", 8)),
                        speed_u_per_s=float(getattr(self, "_walk_mode_move_speed_u_s", 6.0)),
                        key_w=int(getattr(pyglet.window.key, "W", -1)),
                        key_a=int(getattr(pyglet.window.key, "A", -1)),
                        key_s=int(getattr(pyglet.window.key, "S", -1)),
                        key_d=int(getattr(pyglet.window.key, "D", -1)),
                    )
                    self._walk_mode_move_accum_s = float(move_carry)
                    if abs(move_dx) > 1e-9 or abs(move_dz) > 1e-9:
                        ox, oy, oz = self._orbit_target
                        cam_x, cam_y, cam_z = _viewport_camera_world_position(self)
                        solids = self._rez_live_positions if self._rez_active else self._pick_positions
                        env_top_lookup: Callable[[int, int], int | None] | None = None
                        env_bottom_y: int | None = None
                        try:
                            if not self._env_preset().is_space():
                                env_top_lookup = self._env_top_y_cached_at
                                env_bottom_y = int(self._env_ground_bottom)
                        except Exception:
                            env_top_lookup = None
                            env_bottom_y = None
                        resolved_dx, resolved_dz = _walk_mode_apply_collision_xz(
                            start_x_u=float(cam_x),
                            start_y_u=float(cam_y),
                            start_z_u=float(cam_z),
                            move_dx_u=float(move_dx),
                            move_dz_u=float(move_dz),
                            solid_positions=solids,
                            env_top_y_at_xz=env_top_lookup,
                            env_bottom_y=env_bottom_y,
                            max_substep_u=float(getattr(self, "_walk_mode_collision_substep_u", 0.25)),
                        )
                        if abs(resolved_dx) > 1e-9 or abs(resolved_dz) > 1e-9:
                            self._orbit_target = (float(ox) + float(resolved_dx), float(oy), float(oz) + float(resolved_dz))
                            self._mark_camera_user_input()
                else:
                    self._walk_mode_move_accum_s = 0.0
                self._tick_log_panel_tween()
                self._tick_sidebar_width_tween()
                self._tick_hotbar_panel_tween()
                self._tick_hotbar_icon_animation()
                self._update_hover_cursor()
                self._poll_rez(dt)
                self._tick_structure_delta_fade()
                desired_step = max(1, int(param_store.get_int("env.ground.patch.size")))
                if int(desired_step) != int(self._env_patch_step):
                    self._env_patch_step = int(desired_step)
                    self._env_clear_geometry(keep_anchor=True)
                    self._update_environment()
                    self._expansion_report.append(f"Env patch size: {self._env_patch_step}")
                    self._update_status()
                desired_pp = max(1, int(param_store.get_int("env.ground.patches_per_tick")))
                if int(desired_pp) != int(self._env_patches_per_tick):
                    self._env_patches_per_tick = int(desired_pp)
                desired_radius = max(0, int(param_store.get_int("env.ground.radius")))
                if int(desired_radius) != int(self._env_ground_radius):
                    self._env_ground_radius = int(desired_radius)
                    self._update_environment()
                    self._expansion_report.append(f"Env radius: {self._env_ground_radius}")
                    self._update_status()
                desired_bottom = int(param_store.get_int("env.ground.bottom"))
                if int(desired_bottom) != int(self._env_ground_bottom):
                    self._env_ground_bottom = int(desired_bottom)
                    self._env_clear_geometry(keep_anchor=True)
                    self._update_environment()
                    self._expansion_report.append(f"Env bottom: {self._env_ground_bottom}")
                    self._update_status()
                desired_strip_h = max(0, int(param_store.get_int("env.ground.strip_fade.height")))
                desired_strip_levels = max(2, int(param_store.get_int("env.ground.strip_fade.levels")))
                if int(desired_strip_h) != int(self._env_strip_fade_h) or int(desired_strip_levels) != int(
                    self._env_strip_fade_levels
                ):
                    self._env_strip_fade_h = int(desired_strip_h)
                    self._env_strip_fade_levels = int(desired_strip_levels)
                    self._env_clear_geometry(keep_anchor=True)
                    self._update_environment()
                    self._expansion_report.append(
                        f"Env strip fade: {self._env_strip_fade_h}b/{self._env_strip_fade_levels} levels"
                    )
                    self._update_status()
                desired_terrain_amp = max(0, int(param_store.get_int("env.terrain.amp")))
                desired_terrain_scale = float(param_store.get("env.terrain.scale"))
                desired_terrain_octaves = max(1, int(param_store.get_int("env.terrain.octaves")))
                desired_terrain_lacunarity = float(param_store.get("env.terrain.lacunarity"))
                desired_terrain_h = float(param_store.get("env.terrain.h"))
                desired_terrain_ridged_offset = float(param_store.get("env.terrain.ridged.offset"))
                desired_terrain_ridged_gain = float(param_store.get("env.terrain.ridged.gain"))
                if (
                    int(desired_terrain_amp) != int(self._env_terrain_amp)
                    or abs(desired_terrain_scale - float(self._env_terrain_scale)) > 1e-6
                    or int(desired_terrain_octaves) != int(self._env_terrain_octaves)
                    or abs(desired_terrain_lacunarity - float(self._env_terrain_lacunarity)) > 1e-6
                    or abs(desired_terrain_h - float(self._env_terrain_h)) > 1e-6
                    or abs(desired_terrain_ridged_offset - float(self._env_terrain_ridged_offset)) > 1e-6
                    or abs(desired_terrain_ridged_gain - float(self._env_terrain_ridged_gain)) > 1e-6
                ):
                    self._env_terrain_amp = int(desired_terrain_amp)
                    self._env_terrain_scale = float(desired_terrain_scale)
                    self._env_terrain_octaves = int(desired_terrain_octaves)
                    self._env_terrain_lacunarity = float(desired_terrain_lacunarity)
                    self._env_terrain_h = float(desired_terrain_h)
                    self._env_terrain_ridged_offset = float(desired_terrain_ridged_offset)
                    self._env_terrain_ridged_gain = float(desired_terrain_ridged_gain)
                    self._env_clear_geometry()
                    self._update_environment()
                env_ms = 0.0
                if perf:
                    env_t0 = time.perf_counter()
                    self._tick_environment(dt)
                    env_ms = (time.perf_counter() - env_t0) * 1000.0
                    self._perf_last_env_tick_ms = float(env_ms)
                else:
                    self._tick_environment(dt)
                if self._rez_active:
                    self._rez_anim_s += dt
                    self._update_rez_bar()
                    fps = _adaptive_update_budget_fps(dt_s=float(dt), tick_fps_smooth=float(self._tick_fps_value))
                    fps_ratio = max(0.0, min(2.0, fps / 60.0))
                    warmup_s = 1.50
                    p = 1.0
                    if warmup_s > 1e-6:
                        p = max(0.0, min(1.0, float(self._rez_anim_s) / warmup_s))
                        p = p * p * (3.0 - 2.0 * p)
                    mult = max(0.01, float(p))
                    ratio = max(0.005, min(2.0, fps_ratio * mult))
                    budget_s = 0.0030 * ratio
                    budget_s = max(0.0005, min(0.012, budget_s))
                    min_blocks = max(4, int(round(120.0 * mult)))
                    max_blocks = max(min_blocks, int(round(6500.0 * ratio)))
                    self._rez_live_apply_pending(max_blocks=max_blocks, time_budget_s=budget_s)
                    self._update_hud_blocks_live()
                self._update_hover_target()
                if dt > 0.0:
                    self._fx_last_dt = float(dt)
                self._fx_t += dt
                self._fx_frame += 1
                self._tick_ui_fx()
                self._tick_effects()
                self._update_debug_panel()
                param_store.tick()
                self.invalid = True
                if perf:
                    now = time.monotonic()
                    tick_ms = (time.perf_counter() - tick_t0) * 1000.0
                    try:
                        self._perf_frames.append(
                            {
                                "t_s": float(now - float(self._perf_start_t)),
                                "dt_s": float(dt),
                                "tick_ms": float(tick_ms),
                                "env_ms": float(env_ms),
                                "env_fade_ms": float(self._perf_last_env_fade_ms),
                                "env_built": int(self._perf_last_env_built),
                                "env_queue": int(self._perf_last_env_queue_len),
                                "env_patches": int(len(self._env_patches)),
                                "draw_ms": float(self._perf_last_draw_ms),
                                "world_ms": float(self._perf_last_world_ms),
                                "ui_ms": float(self._perf_last_ui_ms),
                                "fps_smooth": float(self._fps_value),
                                "draw_skip_cap_count": int(self._draw_skip_cap_count),
                                "draw_cache_present_count": int(self._draw_cache_present_count),
                                "rez_active": bool(self._rez_active),
                            }
                        )
                    except Exception:
                        pass
                    if (not self._perf_closing) and now >= float(self._perf_end_t):
                        self._perf_closing = True
                        try:
                            self._perf_write()
                        except Exception:
                            pass
                        try:
                            self.close()
                        except Exception:
                            pass
                        try:
                            pyglet.app.exit()
                        except Exception:
                            pass

            def _tick_ui_fx(self) -> None:
                fx_mod.tick_ui_fx(self)

            def _iter_text_glitch_labels(self) -> Iterable[object]:
                yield from fx_mod.iter_text_glitch_labels(self)

            def _apply_text_glitch_for_draw(self) -> Callable[[], None]:
                return fx_mod.apply_text_glitch_for_draw(self, param_store=param_store)

            def _trigger_channel_change_fx(self) -> None:
                fx_mod.trigger_channel_change_fx(self)

            def _draw_channel_change_in_model_view(self, *, view_w: float, view_h: float) -> None:
                fx_mod.draw_channel_change_in_model_view(self, view_w=view_w, view_h=view_h, gl=gl, param_store=param_store)

            def _draw_channel_change_under_ui(self, *, vp_w: int, vp_h: int) -> None:
                fx_mod.draw_channel_change_under_ui(self, vp_w=vp_w, vp_h=vp_h, gl=gl, param_store=param_store)

            def _draw_post_fx_overlay(self) -> None:
                if not self._effects_enabled:
                    return
                fx_mod.draw_post_fx_overlay(self, gl=gl, param_store=param_store)

            def _apply_ender_vignette(self, vp_w: int, vp_h: int) -> None:
                if not self._effects_enabled:
                    return
                fx_mod.apply_ender_vignette(self, vp_w, vp_h, gl=gl, param_store=param_store)

            def _apply_copy_glitch(self, vp_w: int, vp_h: int) -> None:
                if not self._effects_enabled:
                    return
                fx_mod.apply_copy_glitch(self, vp_w, vp_h, gl=gl, param_store=param_store)

            def _load_pool_index(self, idx: int, *, reset_view: bool) -> None:
                self._cancel_rez()
                self._clear_viewer_error()
                if not pool_labels:
                    self._set_viewer_error("load", "Pool load error\nNo template_pool JSON found in datapack")
                    self._expansion_report = ["No template_pool JSON found in datapack"]
                    self._update_status()
                    self._update_list_labels()
                    return

                idx = max(0, min(len(pool_labels) - 1, int(idx)))
                self.selected = int(idx)
                pool_id = pool_labels[idx]
                self._loaded_mode = "pools"
                self._loaded_index = int(idx)
                self._current_label = pool_id
                self._trigger_channel_change_fx()

                seed = _stable_seed(0, "pool_start", pool_id, int(idx)) & 0xFFFFFFFF
                rng = random.Random(int(seed))

                def pick_from_pool(pool: PoolDefinition) -> PoolElement | None:
                    candidates = [(e, int(e.weight)) for e in pool.elements]
                    return _choose_weighted(rng, candidates) if candidates else None

                pool = jigsaw_index.load_pool(pool_id)
                elem = pick_from_pool(pool)
                if elem is None and isinstance(pool.fallback, str) and pool.fallback and pool.fallback != "minecraft:empty":
                    pool2 = jigsaw_index.load_pool(pool.fallback)
                    elem = pick_from_pool(pool2)
                if elem is None:
                    self._set_viewer_error("load", f"Pool load error: {pool_id}\nEmpty/unsupported pool")
                    self._expansion_report = [f"{pool_id}: empty pool"]
                    self._update_status()
                    self._refresh_structure_caption()
                    self._update_list_labels()
                    return

                tmpl = jigsaw_index.load_template(elem.location_id)
                if tmpl is None:
                    self._set_viewer_error("load", f"Pool load error: {pool_id}\nMissing/invalid template: {elem.location_id}")
                    self._expansion_report = [f"{pool_id}: missing template ({elem.location_id})"]
                    self._update_status()
                    self._refresh_structure_caption()
                    self._update_list_labels()
                    return

                self._loaded_mode = "pools"
                self._loaded_index = int(idx)
                self._current_label = pool_id
                self._base_template = tmpl
                self._base_projection = str(getattr(elem, "projection", "rigid") or "rigid")
                self._autopick_environment(hint=pool_id)
                self._jigsaw_cache_template_id = elem.location_id
                self._jigsaw_cache_env_key = self._env_preset().name
                self._jigsaw_cache = {}
                if self._base_template is not None:
                    self._pivot_center = self._compute_pivot_center(self._base_template)
                    sx, sy, sz = self._base_template.size
                    base_state = JigsawExpansionState(
                        connectors=self._base_template.connectors,
                        consumed=frozenset(),
                        dead_end=frozenset(),
                        piece_bounds=((0, 0, 0, int(sx) - 1, int(sy) - 1, int(sz) - 1),),
                    )
                    blocks_by_pos = {b.pos: b for b in self._base_template.blocks}
                    block_entities_by_pos = {be.pos: be for be in self._base_template.block_entities}
                    apply_jigsaw_final_states_to_blocks(blocks_by_pos, block_entities_by_pos, base_state.connectors)
                    base_struct = Structure(
                        size=self._base_template.size,
                        blocks=tuple(blocks_by_pos.values()),
                        block_entities=tuple(sorted(block_entities_by_pos.values(), key=lambda be: be.pos)),
                        entities=self._base_template.entities,
                    )
                    self._jigsaw_cache[()] = (base_struct, [], base_state)
                self._jigsaw_reset()
                self._expansion_report = []
                self._rebuild_scene(reset_view=reset_view, adjust_distance=True, enable_delta_fade=False)
                self._frame_view(animate=False)
                self._refresh_structure_caption()
                self._update_list_labels()

            def _load_worldgen_index(self, idx: int, *, reset_view: bool) -> None:
                self._cancel_rez()
                self._clear_viewer_error()
                if not worldgen_labels:
                    self._set_viewer_error("load", "Worldgen load error\nNo worldgen structure JSON found in datapack")
                    self._expansion_report = ["No worldgen structure JSON found in datapack"]
                    self._update_status()
                    self._refresh_structure_caption()
                    self._update_list_labels()
                    return

                idx = max(0, min(len(worldgen_labels) - 1, int(idx)))
                self._worldgen_selected = int(idx)

                structure_id = worldgen_labels[idx]
                self._loaded_mode = "worldgen"
                self._loaded_index = int(idx)
                self._current_label = structure_id
                self._trigger_channel_change_fx()
                canonical = canonical_worldgen_structure_json(structure_id)
                obj = pack_stack.source.read_json(canonical) or {}

                start_pool = obj.get("start_pool")
                if not isinstance(start_pool, str) or not start_pool:
                    stype = obj.get("type")
                    stype_s = stype if isinstance(stype, str) and stype else "unknown"
                    self._set_viewer_error("load", f"Worldgen load error: {structure_id}\nUnsupported structure type: {stype_s}")
                    self._expansion_report = [f"{structure_id}: unsupported (type={stype_s})"]
                    self._update_status()
                    self._refresh_structure_caption()
                    self._update_list_labels()
                    return

                seed = _stable_seed(0, "worldgen_start", structure_id, start_pool) & 0xFFFFFFFF
                rng = random.Random(int(seed))

                def pick_from_pool(pool: PoolDefinition) -> PoolElement | None:
                    candidates = [(e, int(e.weight)) for e in pool.elements]
                    return _choose_weighted(rng, candidates) if candidates else None

                pool = jigsaw_index.load_pool(start_pool)
                elem = pick_from_pool(pool)
                if elem is None and isinstance(pool.fallback, str) and pool.fallback and pool.fallback != "minecraft:empty":
                    pool2 = jigsaw_index.load_pool(pool.fallback)
                    elem = pick_from_pool(pool2)
                if elem is None:
                    self._set_viewer_error("load", f"Worldgen load error: {structure_id}\nStart pool empty/unsupported: {start_pool}")
                    self._expansion_report = [f"{structure_id}: start_pool empty ({start_pool})"]
                    self._update_status()
                    self._refresh_structure_caption()
                    self._update_list_labels()
                    return

                tmpl = jigsaw_index.load_template(elem.location_id)
                if tmpl is None:
                    self._set_viewer_error("load", f"Worldgen load error: {structure_id}\nMissing/invalid template: {elem.location_id}")
                    self._expansion_report = [f"{structure_id}: missing template ({elem.location_id})"]
                    self._update_status()
                    self._refresh_structure_caption()
                    self._update_list_labels()
                    return

                self._loaded_mode = "worldgen"
                self._loaded_index = int(idx)
                self._current_label = structure_id
                self._base_template = tmpl
                self._base_projection = str(getattr(elem, "projection", "rigid") or "rigid")
                self._autopick_environment(hint=f"{structure_id} {start_pool}")
                self._jigsaw_cache_template_id = elem.location_id
                self._jigsaw_cache_env_key = self._env_preset().name
                self._jigsaw_cache = {}
                if self._base_template is not None:
                    self._pivot_center = self._compute_pivot_center(self._base_template)
                    sx, sy, sz = self._base_template.size
                    base_state = JigsawExpansionState(
                        connectors=self._base_template.connectors,
                        consumed=frozenset(),
                        dead_end=frozenset(),
                        piece_bounds=((0, 0, 0, int(sx) - 1, int(sy) - 1, int(sz) - 1),),
                    )
                    blocks_by_pos = {b.pos: b for b in self._base_template.blocks}
                    block_entities_by_pos = {be.pos: be for be in self._base_template.block_entities}
                    apply_jigsaw_final_states_to_blocks(blocks_by_pos, block_entities_by_pos, base_state.connectors)
                    base_struct = Structure(
                        size=self._base_template.size,
                        blocks=tuple(blocks_by_pos.values()),
                        block_entities=tuple(sorted(block_entities_by_pos.values(), key=lambda be: be.pos)),
                        entities=self._base_template.entities,
                    )
                    self._jigsaw_cache[()] = (base_struct, [], base_state)
                self._jigsaw_reset()
                self._expansion_report = []
                self._rebuild_scene(reset_view=reset_view, adjust_distance=True, enable_delta_fade=False)
                self._frame_view(animate=False)
                self._refresh_structure_caption()
                self._update_list_labels()

            def _load_index(self, idx: int, *, reset_view: bool) -> None:
                self._cancel_rez()
                idx = max(0, min(len(items) - 1, idx))
                self._structures_selected = int(idx)
                label = labels[idx]
                self._clear_viewer_error()
                self._loaded_mode = "structures"
                self._loaded_index = int(idx)
                self._current_label = label
                self._trigger_channel_change_fx()
                ns, _, path = label.partition("/")
                template_id = f"{ns}:{path}" if path else f"{ns}:{label}"
                try:
                    root = load_root_by_index(idx)
                    base_template = parse_structure_template(root, template_id=template_id)
                except Exception as e:
                    self._set_viewer_error(
                        "load",
                        f"Load error: {label}\n" + self._format_error_one_line(e),
                        detail=traceback.format_exc(limit=20),
                    )
                    self._expansion_report = [f"Load failed: {label}"]
                    self._update_status()
                    self._refresh_structure_caption()
                    self._update_list_labels()
                    return

                self._base_template = base_template
                self._base_projection = "rigid"
                self._autopick_environment(hint=label)
                self._jigsaw_cache_template_id = template_id
                self._jigsaw_cache_env_key = self._env_preset().name
                self._jigsaw_cache = {}
                if self._base_template is not None:
                    self._pivot_center = self._compute_pivot_center(self._base_template)
                    sx, sy, sz = self._base_template.size
                    base_state = JigsawExpansionState(
                        connectors=self._base_template.connectors,
                        consumed=frozenset(),
                        dead_end=frozenset(),
                        piece_bounds=((0, 0, 0, int(sx) - 1, int(sy) - 1, int(sz) - 1),),
                    )
                    blocks_by_pos = {b.pos: b for b in self._base_template.blocks}
                    block_entities_by_pos = {be.pos: be for be in self._base_template.block_entities}
                    apply_jigsaw_final_states_to_blocks(blocks_by_pos, block_entities_by_pos, base_state.connectors)
                    base_struct = Structure(
                        size=self._base_template.size,
                        blocks=tuple(blocks_by_pos.values()),
                        block_entities=tuple(sorted(block_entities_by_pos.values(), key=lambda be: be.pos)),
                        entities=self._base_template.entities,
                    )
                    self._jigsaw_cache[()] = (base_struct, [], base_state)
                self._jigsaw_reset()
                self._expansion_report = []
                # Channel-change selection should cut to black + fade in the new
                # model; do not run the per-block delta-fade overlay here.
                self._rebuild_scene(reset_view=reset_view, adjust_distance=True, enable_delta_fade=False)
                self._frame_view(animate=False)
                self._refresh_structure_caption()
                self._update_list_labels()

            def _refresh_render_cap_hz(self) -> None:
                _render_cap_refresh_hz(self, param_store=param_store)

            def _mark_render_dirty(self) -> None:
                self._render_cap_force_next = _render_cap_mark_dirty_state(force_draw=bool(self._render_cap_force_next))
                self.invalid = True

            def _should_render_frame(self, *, now_s: float) -> bool:
                self._refresh_render_cap_hz()
                if _render_cap_is_uncapped(float(self._render_cap_hz)):
                    self._render_cap_next_deadline_t = float(now_s)
                    self._render_cap_force_next = False
                    return True
                viewport_px = _render_cap_read_viewport_px(self)
                if viewport_px is None:
                    viewport_px = _render_cap_fallback_viewport_px(self)
                if _render_cap_view_changed(tuple(self._render_cap_last_view_px), tuple(viewport_px)):
                    self._render_cap_force_next = True
                self._render_cap_last_view_px = tuple(viewport_px)
                pixel_ratio = _render_cap_read_pixel_ratio(self, default=1.0)
                if _render_cap_ratio_changed(float(self._render_cap_last_ratio), float(pixel_ratio)):
                    self._render_cap_force_next = True
                self._render_cap_last_ratio = float(pixel_ratio)

                should_draw_now, next_deadline_s = _render_cap_schedule_step(
                    now_s=float(now_s),
                    frame_cap_hz=float(self._render_cap_hz),
                    next_deadline_s=float(self._render_cap_next_deadline_t),
                    startup_until_s=float(self._render_cap_startup_until_t),
                    force_render=bool(self._render_cap_force_next),
                )
                self._render_cap_next_deadline_t = float(next_deadline_s)
                if should_draw_now:
                    self._render_cap_force_next = False
                return bool(should_draw_now)

            def on_resize(self, width: int, height: int) -> None:
                # Keep the full viewport current; we set the 3D viewport per-frame.
                vp_w, vp_h = self.get_viewport_size()
                gl.glViewport(0, 0, max(1, int(vp_w)), max(1, int(vp_h)))
                self._layout_ui()
                self._mark_render_dirty()

            def on_mouse_motion(self, x: int, y: int, dx: int, dy: int) -> None:
                self._mouse_x = int(x)
                self._mouse_y = int(y)
                self._update_hover_cursor()

            def on_mouse_drag(self, x: int, y: int, dx: int, dy: int, buttons: int, modifiers: int) -> None:
                self._mouse_x = int(x)
                self._mouse_y = int(y)
                if self._sidebar_resize_active:
                    if not (buttons & pyglet.window.mouse.LEFT):
                        self._sidebar_resize_active = False
                        return
                    # Dragging the sidebar divider: resize the sidebar in points.
                    new_w = float(self._sidebar_resize_start_w) + (float(x) - float(self._sidebar_resize_start_x))
                    win_w = float(self.width)
                    if not math.isfinite(new_w):
                        new_w = float(self.sidebar_width)
                    if not math.isfinite(win_w) or win_w <= 0.0:
                        win_w = float(self.sidebar_width)
                    # Clamp to keep at least a sliver of 3D view visible.
                    max_w = max(0.0, float(win_w) - 80.0)
                    if new_w < 0.0:
                        new_w = 0.0
                    if new_w > max_w:
                        new_w = max_w
                    if new_w < 10.0:
                        self.sidebar_width = 0.0
                        self._ui_hidden = True
                    else:
                        self.sidebar_width = float(new_w)
                        self._ui_hidden = False
                        self._sidebar_width_default = float(self.sidebar_width)
                    self._layout_ui(ensure_labels=False)
                    self.invalid = True
                    return
                if getattr(self._term_list_scrollbar, "drag_active", False) or bool(
                    getattr(self._term_list_mouse_capture, "active", False)
                ):
                    try:
                        vp_w, vp_h = self.get_viewport_size()
                        ratio = float(self.get_pixel_ratio())
                    except Exception:
                        vp_w, vp_h = (int(self.width), int(self.height))
                        ratio = 1.0
                    try:
                        term_font, _surface = self._sync_sidebar_termui(force=False)
                        cell_w = max(1, int(getattr(term_font, "cell_w", 8)))
                        cell_h = max(1, int(getattr(term_font, "cell_h", 14)))
                    except Exception:
                        cell_w = 8
                        cell_h = 14
                    y_px = int(float(y) * float(ratio))
                    row = int((max(1, int(vp_h)) - 1 - y_px) // cell_h)

                    try:
                        sidebar_px = int(float(self.sidebar_width) * float(ratio))
                        cols = max(1, int(int(sidebar_px) // max(1, int(cell_w))))
                        rows, header_rows, log_rows = self._sidebar_term_layout(vp_h_px=int(vp_h), sidebar_px=int(sidebar_px))
                        self._refresh_term_list_scrollbar(
                            cols=int(cols),
                            rows=int(rows),
                            header_rows=int(header_rows),
                            log_rows=int(log_rows),
                        )
                    except Exception:
                        pass

                    route = route_term_scrollbar_drag(
                        capture=self._term_list_mouse_capture,
                        context_id=str(self._term_list_mouse_context),
                        target_id=str(self._term_list_mouse_target),
                        scrollbar=self._term_list_scrollbar,
                        row=int(row),
                        left_button_down=bool(buttons & pyglet.window.mouse.LEFT),
                    )
                    if route.new_scroll is not None:
                        self._scroll_follow_selection = False
                        self._scroll_pos_f = float(route.new_scroll)
                        self._update_list_labels(ensure_selection_visible=False)
                        self.invalid = True
                    if route.consumed:
                        return
                if bool(getattr(self, "_walk_mode_active", False)) and x >= self.sidebar_width:
                    return
                if x < self.sidebar_width:
                    return
                # Camera controls are only active while holding ⌥ (Option) so
                # they don't fight UI drags (like sidebar resizing) or build/edit
                # interactions.
                if not self._camera_modifier_active(modifiers):
                    return
                self._dbg_pyglet_drag_calls += 1
                self._dbg_pyglet_drag_dx = dx
                self._dbg_pyglet_drag_dy = dy
                self._dbg_pyglet_drag_buttons = buttons
                self._dbg_last_pyglet_event = f"drag dx={dx} dy={dy} buttons={buttons}"
                self._dbg_last_pyglet_event_t = time.monotonic()
                self._cancel_camera_tween()
                if buttons & pyglet.window.mouse.LEFT:
                    self.yaw += dx * 0.35
                    self.pitch -= dy * 0.35
                    self.pitch = max(-89.0, min(89.0, self.pitch))
                    self._mark_camera_user_input()
                elif buttons & pyglet.window.mouse.MIDDLE:
                    fov_rad = math.radians(55.0)
                    units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                    self.pan_x += dx * units_per_point
                    self.pan_y += dy * units_per_point
                    self._mark_camera_user_input()

            def on_mouse_scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
                self._dbg_pyglet_scroll_calls += 1
                self._dbg_pyglet_scroll_sx = scroll_x
                self._dbg_pyglet_scroll_sy = scroll_y
                region = "sidebar" if x < self.sidebar_width else "model"
                self._dbg_last_pyglet_event = f"scroll {region} sx={scroll_x} sy={scroll_y}"
                self._dbg_last_pyglet_event_t = time.monotonic()
                if x < self.sidebar_width:
                    try:
                        vp_w, vp_h = self.get_viewport_size()
                        ratio = float(self.get_pixel_ratio())
                    except Exception:
                        vp_w, vp_h = (int(self.width), int(self.height))
                        ratio = 1.0
                    try:
                        term_font, _surface = self._sync_sidebar_termui(force=False)
                        cell_w = max(1, int(getattr(term_font, "cell_w", 8)))
                        cell_h = max(1, int(getattr(term_font, "cell_h", 14)))
                    except Exception:
                        cell_w = 8
                        cell_h = 14

                    y_px = int(float(y) * float(ratio))
                    row = int((max(1, int(vp_h)) - 1 - y_px) // cell_h)
                    sidebar_px = int(float(self.sidebar_width) * float(ratio))
                    cols = max(1, int(int(sidebar_px) // cell_w))
                    rows, header_rows, log_rows = self._sidebar_term_layout(vp_h_px=int(vp_h), sidebar_px=int(sidebar_px))
                    list_box_y = int(header_rows)
                    list_box_h = max(3, int(rows) - int(header_rows) - int(log_rows))
                    inner_y = int(list_box_y + 1)
                    inner_h = max(0, int(list_box_h - 2))

                    if inner_h > 0 and inner_y <= row < inner_y + inner_h:
                        show_search = int(inner_h) >= 2
                        visible_full = int(inner_h) - (1 if show_search else 0)
                        visible_full = max(1, int(visible_full))
                        max_scroll = max(0, len(self._filtered_indices) - int(visible_full))
                        dx = float(scroll_x)
                        dy = float(scroll_y)
                        changed = False

                        if abs(dx) > 1e-6:
                            text_w = max(1, int(cols - 3))
                            max_len = 0
                            active = self._active_labels()
                            for idx in self._filtered_indices:
                                if 0 <= int(idx) < len(active):
                                    max_len = max(max_len, len(active[int(idx)]))
                            max_x = max(0, int(max_len) - int(text_w))
                            self._scroll_x_cols = max(0, min(int(max_x), int(self._scroll_x_cols) + int(round(dx))))
                            changed = True

                        if abs(dy) > 1e-6:
                            self._scroll_follow_selection = False
                            self._scroll_pos_f = max(0.0, min(float(max_scroll), float(self._scroll_pos_f) - dy))
                            changed = True

                        if changed:
                            self._update_list_labels(ensure_selection_visible=False)
                    return
                if bool(getattr(self, "_walk_mode_active", False)) and x >= self.sidebar_width:
                    return
                if self._mac_gestures_enabled:
                    self._scroll_last_mode = "disabled"
                    self._scroll_last_sx = float(scroll_x)
                    self._scroll_last_sy = float(scroll_y)
                    return
                self._cancel_camera_tween()
                sx = float(scroll_x)
                sy = float(scroll_y)
                self._scroll_last_sx = sx
                self._scroll_last_sy = sy
                now = time.monotonic()

                looks_trackpad = (
                    (abs(sx) > 1e-6)
                    or (abs(sy) < 1.0)
                    or (not sx.is_integer())
                    or (not sy.is_integer())
                )

                # If we have a real trackpad pan recognizer active, ignore
                # trackpad scroll-wheel streams over the model viewport. When
                # macOS gestures are enabled, we rely on Cocoa pan/pinch/rotate
                # instead of scroll-wheel heuristics so inputs don't fight.
                # Mouse wheels still work for zoom.
                if self._mac_pan_gesture_enabled and looks_trackpad:
                    self._scroll_last_mode = "ignored"
                    return

                if self._mac_scroll_pan_enabled:
                    if now < self._scroll_pan_until_t:
                        # Once we see a "trackpad-like" scroll event, keep treating
                        # the stream as pan for a short time so we don't alternate
                        # between pan and zoom within a single gesture.
                        self._scroll_pan_until_t = now + 0.25
                        self._scroll_last_mode = "pan"
                        fov_rad = math.radians(55.0)
                        units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                        self.pan_x += sx * units_per_point
                        self.pan_y += sy * units_per_point
                        self._mark_camera_user_input()
                        return

                    if looks_trackpad:
                        self._scroll_pan_until_t = now + 0.25
                        self._scroll_last_mode = "pan"
                        fov_rad = math.radians(55.0)
                        units_per_point = (2.0 * self.distance * math.tan(fov_rad / 2.0)) / float(max(1, self.height))
                        self.pan_x += sx * units_per_point
                        self.pan_y += sy * units_per_point
                        self._mark_camera_user_input()
                        return

                self._scroll_last_mode = "zoom"
                old_distance = float(self.distance)
                factor = 0.9**sy
                new_distance = max(0.5, old_distance * factor)
                self._zoom_to_distance_at_cursor(x, y, new_distance)
                self._mark_camera_user_input()

            def _zoom_to_distance_at_cursor(self, x: int, y: int, new_distance: float) -> None:
                _viewport_zoom_to_distance_at_cursor(self, x, y, new_distance)

            def on_mouse_press(self, x: int, y: int, button: int, modifiers: int) -> None:
                self._mouse_x = int(x)
                self._mouse_y = int(y)
                self._focus_probe_consume("mouse_press")
                panel = getattr(self, "_jar_term_panel_rect", None)
                if panel is not None and (not getattr(self, "_jar_alert_dismissed", False)) and bool(getattr(self, "_jar_alert_text", "")):
                    bx, by, bw, bh = panel
                    if float(bx) <= float(x) <= float(bx + bw) and float(by) <= float(y) <= float(by + bh):
                        dismiss = getattr(self, "_jar_term_dismiss_rect", None)
                        if dismiss is not None:
                            dx, dy, dw, dh = dismiss
                            if float(dx) <= float(x) <= float(dx + dw) and float(dy) <= float(y) <= float(dy + dh):
                                self._jar_alert_dismissed = True
                                self._jar_term_panel_rect = None
                                self._jar_term_dismiss_rect = None
                        # Swallow clicks on the alert panel so they don't rotate/pick the world.
                        return
                self._dbg_pyglet_press_calls += 1
                region = "sidebar" if x < self.sidebar_width else "model"
                self._dbg_last_pyglet_event = f"press {region} btn={button} x={x} y={y}"
                self._dbg_last_pyglet_event_t = time.monotonic()
                if bool(getattr(self, "_walk_mode_active", False)) and x >= self.sidebar_width:
                    return

                if self._palette_visible():
                    from enderterm.termui import route_tool_window_click

                    if route_tool_window_click(
                        mode="window",
                        click_handler=lambda: self._palette_click(x, y),
                    ):
                        return

                if button == pyglet.window.mouse.LEFT and self._sidebar_divider_hit(float(x)):
                    # Start resizing the sidebar by dragging its divider.
                    self._sidebar_resize_active = True
                    self._sidebar_resize_start_x = float(x)
                    self._sidebar_resize_start_w = float(self.sidebar_width)
                    self._sidebar_width_tween = None
                    self._ui_hidden = False
                    self._set_cursor_kind("resize_lr")
                    return
                if (
                    self._build_enabled
                    and (not self._terminal_busy())
                    and x >= self.sidebar_width
                    and (not self._camera_modifier_active(modifiers))
                    and button in {pyglet.window.mouse.LEFT, pyglet.window.mouse.RIGHT, pyglet.window.mouse.MIDDLE}
                ):
                    slot = self._hotbar_hit_slot(x, y)
                    if slot is not None:
                        self._hotbar_select_slot(slot)
                        return
                    hit = self._pick_block_hit(x, y)
                    if hit is not None:
                        pos, n = hit
                        if button == pyglet.window.mouse.MIDDLE:
                            self._build_pick_block(pos)
                            return
                        if button == pyglet.window.mouse.LEFT:
                            self._build_remove_block(pos)
                            return
                        if button == pyglet.window.mouse.RIGHT and n != (0, 0, 0):
                            place_pos = (pos[0] + n[0], pos[1] + n[1], pos[2] + n[2])
                            self._build_place_block(place_pos, face_n=n)
                            return
                    return

                if button != pyglet.window.mouse.LEFT:
                    return
                if self._rez_active:
                    rect = getattr(self, "_rez_term_cancel_rect", None)
                    if rect is not None:
                        bx, by, bw, bh = rect
                    else:
                        bx = float(self.rez_cancel_bg.x)
                        by = float(self.rez_cancel_bg.y)
                        bw = float(self.rez_cancel_bg.width)
                        bh = float(self.rez_cancel_bg.height)
                    if bx <= float(x) <= bx + bw and by <= float(y) <= by + bh:
                        self._cancel_rez()
                        return
                    if x < self.sidebar_width:
                        return
                if x >= self.sidebar_width:
                    if not self._camera_modifier_active(modifiers):
                        return
                    self._cancel_camera_tween()
                    hit = self._pick_orbit_target(x, y)
                    self._set_orbit_target(hit if hit is not None else (0.0, 0.0, 0.0))
                    self._mark_camera_user_input()
                    return

                # Terminal sidebar hit-testing (row/col in the glyph grid).
                try:
                    vp_w, vp_h = self.get_viewport_size()
                    ratio = float(self.get_pixel_ratio())
                except Exception:
                    vp_w, vp_h = (int(self.width), int(self.height))
                    ratio = 1.0
                try:
                    term_font, _surface = self._sync_sidebar_termui(force=False)
                    cell_w = max(1, int(getattr(term_font, "cell_w", 8)))
                    cell_h = max(1, int(getattr(term_font, "cell_h", 14)))
                except Exception:
                    cell_w = 8
                    cell_h = 14

                x_px = int(float(x) * float(ratio))
                y_px = int(float(y) * float(ratio))
                col = int(max(0, x_px) // cell_w)
                row = int((max(1, int(vp_h)) - 1 - y_px) // cell_h)
                sidebar_px = int(float(self.sidebar_width) * float(ratio))
                cols = max(1, int(int(sidebar_px) // cell_w))
                rows, header_rows, log_rows = self._sidebar_term_layout(vp_h_px=int(vp_h), sidebar_px=int(sidebar_px))

                (
                    list_box_y,
                    list_box_h,
                    log_box_y,
                    _log_box_h,
                    inner_y,
                    inner_h,
                    visible,
                    sb_x,
                ) = self._refresh_term_list_scrollbar(
                    cols=int(cols),
                    rows=int(rows),
                    header_rows=int(header_rows),
                    log_rows=int(log_rows),
                )
                if row == log_box_y:
                    self._set_log_collapsed(not self._log_collapsed)
                    return
                if row == list_box_y:
                    # Box title: NBT / Pool "tabs".
                    tabs: list[tuple[str, str]] = [("NBT", "structures"), ("Pool", "pools")]
                    x = 2 + 2  # "[ " prefix
                    for i, (tab_name, mode) in enumerate(tabs):
                        x0 = int(x)
                        x1 = int(x0 + len(tab_name) - 1)
                        if x0 <= col <= x1:
                            if self._browser_mode == "datasets":
                                self._exit_dataset_browser()
                            if mode in {"pools", "structures"} and self._browser_mode in {"pools", "structures"}:
                                if self._browser_mode != mode:
                                    self._toggle_structure_browser_mode()
                            return
                        x = x1 + 1
                        if i != len(tabs) - 1:
                            x += 3  # " / "
                        if x >= cols - 1:
                            break
                    return

                if (
                    button == pyglet.window.mouse.LEFT
                    and int(col) == int(sb_x)
                    and int(inner_h) > 0
                    and int(inner_y) <= int(row) < int(inner_y + inner_h)
                ):
                    route = route_term_scrollbar_press(
                        capture=self._term_list_mouse_capture,
                        context_id=str(self._term_list_mouse_context),
                        target_id=str(self._term_list_mouse_target),
                        scrollbar=self._term_list_scrollbar,
                        row=int(row),
                        current_scroll=float(self._scroll_pos_f),
                        consume_thumb_press=True,
                    )
                    if route.new_scroll is not None:
                        self._scroll_follow_selection = False
                        self._scroll_pos_f = float(route.new_scroll)
                        self._update_list_labels(ensure_selection_visible=False)
                    if route.consumed:
                        return

                show_search = int(inner_h) >= 2
                visible = max(0, int(visible))
                if show_search and row == inner_y + visible:
                    # Search bar (inside list box).
                    if self._search_ui_visible():
                        # Cancel button is on the right side of the bar; hit-test it.
                        text_w = max(1, int(cols - 3))
                        active = self._active_labels()
                        indices = self._filtered_indices
                        total = len(active)
                        matches = len(indices)
                        right = f"{matches}/{total}"
                        cancel = "[X]"
                        tail = f"{cancel} {right}" if right else cancel
                        if len(tail) >= text_w:
                            tail = tail[-text_w:]
                        tail_start = 1 + max(0, text_w - len(tail))
                        cancel_idx = tail.find(cancel)
                        if cancel_idx >= 0:
                            cancel_start = int(tail_start + cancel_idx)
                            cancel_end = int(cancel_start + len(cancel) - 1)
                        else:
                            cancel_start = -1
                            cancel_end = -1
                        if cancel_start <= col <= cancel_end:
                            self._end_search(cancel=True)
                            return
                    if not self._search_active:
                        self._start_search()
                    return
                if inner_h <= 0 or row < inner_y or row >= inner_y + max(0, visible):
                    return

                idx_in_view = int(row - inner_y)
                scroll_top = int(self._scroll_pos_f)
                pos = scroll_top + idx_in_view
                if 0 <= pos < len(self._filtered_indices):
                    self._load_list_pos(pos)

            def on_mouse_release(self, x: int, y: int, button: int, modifiers: int) -> None:
                self._mouse_x = int(x)
                self._mouse_y = int(y)
                if button == pyglet.window.mouse.LEFT:
                    route_term_scrollbar_release(
                        capture=self._term_list_mouse_capture,
                        context_id=str(self._term_list_mouse_context),
                        target_id=str(self._term_list_mouse_target),
                        scrollbar=self._term_list_scrollbar,
                    )
                if bool(getattr(self, "_walk_mode_active", False)) and x >= self.sidebar_width:
                    return
                if button == pyglet.window.mouse.LEFT and self._sidebar_resize_active:
                    self._sidebar_resize_active = False
                    if float(self.sidebar_width) < 0.5:
                        self.sidebar_width = 0.0
                        self._ui_hidden = True
                    else:
                        self._ui_hidden = False
                    self._layout_ui()
                    self.invalid = True

            def on_key_press(self, symbol: int, modifiers: int) -> None:
                self._focus_probe_consume("key_press")
                try:
                    if bool(getattr(self, "_smoke_record_keys", False)):
                        events = getattr(self, "_smoke_key_events", None)
                        if isinstance(events, list) and len(events) < 2000:
                            events.append((int(symbol), int(modifiers), float(time.monotonic())))
                except Exception:
                    pass
                cmd_mod = getattr(pyglet.window.key, "MOD_COMMAND", 0) | getattr(pyglet.window.key, "MOD_ACCEL", 0)
                walk_action = _walk_mode_key_action(
                    active=bool(getattr(self, "_walk_mode_active", False)),
                    symbol=int(symbol),
                    modifiers=int(modifiers),
                    toggle_symbol=int(getattr(pyglet.window.key, "W", -1)),
                    escape_symbol=int(pyglet.window.key.ESCAPE),
                    cmd_mod=int(cmd_mod),
                    scaffold_symbols={
                        int(getattr(pyglet.window.key, "W", -1)),
                        int(getattr(pyglet.window.key, "A", -1)),
                        int(getattr(pyglet.window.key, "S", -1)),
                        int(getattr(pyglet.window.key, "D", -1)),
                        int(getattr(pyglet.window.key, "SPACE", -1)),
                        int(getattr(pyglet.window.key, "LCTRL", -1)),
                        int(getattr(pyglet.window.key, "RCTRL", -1)),
                        int(getattr(pyglet.window.key, "LSHIFT", -1)),
                        int(getattr(pyglet.window.key, "RSHIFT", -1)),
                    },
                )
                if walk_action == "toggle_on" and bool(self._search_active):
                    walk_action = "pass"
                if walk_action == "toggle_on":
                    self._walk_mode_set_active(True, reason="key_toggle")
                    return
                if walk_action == "toggle_off":
                    self._walk_mode_set_active(False, reason="key_toggle")
                    return
                if walk_action == "exit_escape":
                    self._walk_mode_set_active(False, reason="key_escape")
                    return
                if walk_action == "consume_scaffold":
                    self._walk_mode_scaffold_pressed.add(int(symbol))
                    return
                if symbol == pyglet.window.key.TAB:
                    self._toggle_ui_hidden()
                    return

                if not self._search_active and self._browser_mode == "datasets":
                    if symbol in {pyglet.window.key.ESCAPE, pyglet.window.key.LEFT}:
                        self._exit_dataset_browser()
                        return
                    if symbol in {pyglet.window.key.RIGHT, pyglet.window.key.ENTER, pyglet.window.key.RETURN}:
                        self._open_selected_dataset()
                        return

                if self._search_active:
                    self._repeat_symbol = None
                    self._repeat_next_at_s = 0.0
                    self._repeat_hold_s = 0.0
                    self._repeat_step_s = 0.0
                    if symbol == pyglet.window.key.ESCAPE:
                        if self._rez_active:
                            self._cancel_rez()
                            return
                        self._end_search(cancel=True)
                        return
                    if symbol in {pyglet.window.key.ENTER, pyglet.window.key.RETURN}:
                        self._end_search(cancel=False)
                        return
                    if symbol == pyglet.window.key.BACKSPACE:
                        if self._search_query:
                            self._set_search_query(self._search_query[:-1])
                        else:
                            self._end_search(cancel=True)
                        return
                    if symbol == pyglet.window.key.RIGHT:
                        if self._browser_mode == "datasets":
                            return
                        self._jigsaw_push_level()
                        self._rebuild_scene(reset_view=False, adjust_distance=True)
                        self._refresh_structure_caption()
                        return
                    if symbol == pyglet.window.key.LEFT:
                        if self._browser_mode == "datasets":
                            self._exit_dataset_browser()
                            return
                        if self._jigsaw_pop_level():
                            self._rebuild_scene(reset_view=False, adjust_distance=True)
                        else:
                            self._enter_dataset_browser()
                            if self._browser_mode == "datasets":
                                return
                        self._refresh_structure_caption()
                        return
                    if symbol == pyglet.window.key.UP:
                        self._move_selection(-1)
                        self._repeat_symbol = symbol
                        self._repeat_next_at_s = float(time.monotonic()) + float(self._repeat_delay_s)
                        self._repeat_hold_s = 0.0
                        self._repeat_step_s = 0.0
                        return
                    if symbol == pyglet.window.key.DOWN:
                        self._move_selection(1)
                        self._repeat_symbol = symbol
                        self._repeat_next_at_s = float(time.monotonic()) + float(self._repeat_delay_s)
                        self._repeat_hold_s = 0.0
                        self._repeat_step_s = 0.0
                        return
                    if symbol == pyglet.window.key.PAGEUP:
                        pos = self._selected_list_pos()
                        if pos is not None:
                            page = max(1, int(self._visible_list_rows_termui()))
                            self._load_list_pos(pos - page)
                        return
                    if symbol == pyglet.window.key.PAGEDOWN:
                        pos = self._selected_list_pos()
                        if pos is not None:
                            page = max(1, int(self._visible_list_rows_termui()))
                            self._load_list_pos(pos + page)
                        return
                    if symbol == pyglet.window.key.HOME:
                        self._load_list_pos(0)
                        return
                    if symbol == pyglet.window.key.END:
                        self._load_list_pos(1_000_000_000)
                        return
                    return

                if (modifiers & cmd_mod) and symbol == pyglet.window.key.Z:
                    if modifiers & pyglet.window.key.MOD_SHIFT:
                        self._build_do_redo()
                    else:
                        self._build_do_undo()
                    return
                if (modifiers & cmd_mod) and symbol == pyglet.window.key.Y:
                    self._build_do_redo()
                    return

                if self._build_enabled and not (modifiers & cmd_mod):
                    hotbar_map = {
                        pyglet.window.key._1: 0,
                        pyglet.window.key._2: 1,
                        pyglet.window.key._3: 2,
                        pyglet.window.key._4: 3,
                        pyglet.window.key._5: 4,
                        pyglet.window.key._6: 5,
                        pyglet.window.key._7: 6,
                        pyglet.window.key._8: 7,
                        pyglet.window.key._9: 8,
                        pyglet.window.key._0: 9,
                    }
                    alt = {
                        getattr(pyglet.window.key, "NUM_1", -1): 0,
                        getattr(pyglet.window.key, "NUM_2", -1): 1,
                        getattr(pyglet.window.key, "NUM_3", -1): 2,
                        getattr(pyglet.window.key, "NUM_4", -1): 3,
                        getattr(pyglet.window.key, "NUM_5", -1): 4,
                        getattr(pyglet.window.key, "NUM_6", -1): 5,
                        getattr(pyglet.window.key, "NUM_7", -1): 6,
                        getattr(pyglet.window.key, "NUM_8", -1): 7,
                        getattr(pyglet.window.key, "NUM_9", -1): 8,
                        getattr(pyglet.window.key, "NUM_0", -1): 9,
                    }
                    if symbol in hotbar_map:
                        self._hotbar_select_slot(hotbar_map[symbol])
                        return
                    if symbol in alt and alt[symbol] >= 0:
                        self._hotbar_select_slot(alt[symbol])
                        return

                if symbol == pyglet.window.key.ESCAPE:
                    if self._rez_active:
                        self._cancel_rez()
                        return
                    return
                if symbol == pyglet.window.key.K:
                    if not self._search_active:
                        self._toggle_param_window()
                    return
                if symbol == pyglet.window.key.G:
                    if not self._search_active:
                        self._toggle_worldgen_window()
                    return
                if symbol == pyglet.window.key.V:
                    if not self._search_active:
                        self._ender_vision_active = not self._ender_vision_active
                        if not self._ender_vision_active:
                            self._jigsaw_selected = None
                        self._update_ender_vision_overlay()
                    return
                if symbol == pyglet.window.key.J:
                    if self._search_active:
                        return
                    sel = self._jigsaw_selected
                    if self._ender_vision_active and self._ender_vision_hover is not None:
                        sel = self._ender_vision_hover
                        self._jigsaw_selected = sel
                        self._update_status()
                    if sel is None:
                        return
                    pool_id = str(sel.pool or "")
                    if not pool_id or pool_id == "minecraft:empty":
                        return
                    # Forking is only done from the editor UI (not via hotkeys).
                    self._open_pool_in_worldgen(pool_id, fork=False)
                    return
                if symbol == pyglet.window.key.I:
                    self._toggle_palette()
                    return
                if symbol == pyglet.window.key.B:
                    self._build_enabled = not self._build_enabled
                    self._expansion_report.append(f"Build mode: {'ON' if self._build_enabled else 'OFF'}")
                    self._update_status()
                    self._layout_hotbar_overlay()
                    return
                if symbol == pyglet.window.key.M:
                    self._toggle_structure_browser_mode()
                    return
                if symbol == pyglet.window.key.D:
                    self._toggle_debug_panel()
                    return
                if symbol == pyglet.window.key.C:
                    if bool(modifiers & pyglet.window.key.MOD_SHIFT):
                        self._open_additional_viewport_window()
                    else:
                        self._toggle_second_viewport_window()
                    return
                if symbol == pyglet.window.key.SLASH:
                    self._start_search()
                    return
                if self._rez_active and symbol in {
                    pyglet.window.key.UP,
                    pyglet.window.key.DOWN,
                    pyglet.window.key.PAGEUP,
                    pyglet.window.key.PAGEDOWN,
                    pyglet.window.key.HOME,
                    pyglet.window.key.END,
                }:
                    return
                if symbol in {pyglet.window.key.ENTER, pyglet.window.key.RETURN}:
                    if self._search_active or self._browser_mode == "datasets":
                        return
                    if self._ender_vision_active and self._ender_vision_hover is not None:
                        self._jigsaw_selected = self._ender_vision_hover
                        self._update_status()
                    if self._jigsaw_selected is not None:
                        self._regrow_from_editor()
                        return
                if symbol == pyglet.window.key.R:
                    self._reset_view()
                    self._mark_camera_user_input()
                    return
                if symbol == pyglet.window.key.F:
                    self._frame_view()
                    self._mark_camera_user_input()
                    return
                if symbol == pyglet.window.key.E:
                    self._cycle_environment()
                    return
                if symbol == pyglet.window.key.U:
                    self._export_current()
                    return
                if symbol == pyglet.window.key.N:
                    self._export_current_nbt()
                    return
                if symbol == pyglet.window.key.O:
                    self._ortho_enabled = not self._ortho_enabled
                    self._mark_camera_user_input()
                    return
                if symbol == pyglet.window.key.P:
                    open_in_viewer(self.export_dir)
                    return
                if symbol == pyglet.window.key.RIGHT:
                    if self._rez_active:
                        return
                    self._jigsaw_push_level()
                    self._rebuild_scene(reset_view=False, adjust_distance=True)
                    self._refresh_structure_caption()
                    return
                if symbol == pyglet.window.key.LEFT:
                    if self._rez_active:
                        return
                    if self._jigsaw_pop_level():
                        self._rebuild_scene(reset_view=False, adjust_distance=True)
                    else:
                        self._enter_dataset_browser()
                        if self._browser_mode == "datasets":
                            return
                    self._refresh_structure_caption()
                    return
                if symbol == pyglet.window.key.SPACE:
                    if self._rez_active:
                        return
                    if self._jigsaw_reroll_level():
                        self._rebuild_scene(reset_view=False, adjust_distance=True)
                        self._refresh_structure_caption()
                    return
                if symbol == pyglet.window.key.UP:
                    self._move_selection(-1)
                    self._repeat_symbol = symbol
                    self._repeat_next_at_s = float(time.monotonic()) + float(self._repeat_delay_s)
                    self._repeat_hold_s = 0.0
                    self._repeat_step_s = 0.0
                    return
                if symbol == pyglet.window.key.DOWN:
                    self._move_selection(1)
                    self._repeat_symbol = symbol
                    self._repeat_next_at_s = float(time.monotonic()) + float(self._repeat_delay_s)
                    self._repeat_hold_s = 0.0
                    self._repeat_step_s = 0.0
                    return
                if symbol == pyglet.window.key.PAGEUP:
                    pos = self._selected_list_pos()
                    if pos is not None:
                        page = max(1, int(self._visible_list_rows_termui()))
                        self._load_list_pos(pos - page)
                    return
                if symbol == pyglet.window.key.PAGEDOWN:
                    pos = self._selected_list_pos()
                    if pos is not None:
                        page = max(1, int(self._visible_list_rows_termui()))
                        self._load_list_pos(pos + page)
                    return
                if symbol == pyglet.window.key.HOME:
                    self._load_list_pos(0)
                    return
                if symbol == pyglet.window.key.END:
                    self._load_list_pos(1_000_000_000)
                    return

            def on_text(self, text: str) -> None:
                if not self._search_active:
                    return
                if not isinstance(text, str) or not text:
                    return
                for ch in text:
                    if ch in {"\r", "\n"}:
                        continue
                    if ord(ch) < 32:
                        continue
                    # / is the "enter filter mode" key; don't include it in the query.
                    if ch == "/":
                        continue
                    self._set_search_query(self._search_query + ch)

            def on_key_release(self, symbol: int, modifiers: int) -> None:
                if int(symbol) in set(getattr(self, "_walk_mode_scaffold_pressed", set())):
                    try:
                        self._walk_mode_scaffold_pressed.discard(int(symbol))
                    except Exception:
                        self._walk_mode_scaffold_pressed = set()
                if bool(getattr(self, "_walk_mode_active", False)) and int(symbol) in {
                    int(getattr(pyglet.window.key, "W", -1)),
                    int(getattr(pyglet.window.key, "A", -1)),
                    int(getattr(pyglet.window.key, "S", -1)),
                    int(getattr(pyglet.window.key, "D", -1)),
                    int(getattr(pyglet.window.key, "SPACE", -1)),
                    int(getattr(pyglet.window.key, "LCTRL", -1)),
                    int(getattr(pyglet.window.key, "RCTRL", -1)),
                    int(getattr(pyglet.window.key, "LSHIFT", -1)),
                    int(getattr(pyglet.window.key, "RSHIFT", -1)),
                }:
                    return
                if symbol == self._repeat_symbol:
                    self._repeat_symbol = None
                    self._repeat_next_at_s = 0.0
                    self._repeat_hold_s = 0.0
                    self._repeat_step_s = 0.0

            def on_activate(self) -> None:
                # Activation-time keyboard priming is intentionally disabled.
                self._mark_render_dirty()
                probe_src = str(getattr(self, "_focus_probe_pending_source", "") or "").strip().lower()
                if probe_src and window_has_key_focus(self):
                    self._focus_key_mark(probe_src, diag=window_key_focus_diagnostics(self))

            def on_deactivate(self) -> None:
                from enderterm.termui import route_window_focus_keyboard

                self._walk_mode_force_exit(reason="deactivate")
                route_window_focus_keyboard(window=self, activated=False)

            def on_file_drop(self, x: int, y: int, paths: list[str]) -> None:
                # Drag-drop a Minecraft client jar into the running app to enable textures.
                for raw in paths:
                    p = Path(str(raw)).expanduser()
                    if p.suffix.lower() != ".jar":
                        continue
                    self._apply_minecraft_jar_drop(p)
                    return

            def _set_jar_alert(self, text: str, *, kind: str) -> None:
                self._jar_alert_text = str(text)
                self._jar_alert_kind = str(kind)
                self._jar_alert_dismissed = False
                self._jar_term_panel_rect = None
                self._jar_term_dismiss_rect = None

            def _apply_minecraft_jar_drop(self, path: Path) -> None:
                nonlocal jar_path, texture_source, resolver, textured

                try:
                    p = Path(path).expanduser().resolve()
                except Exception:
                    p = Path(path).expanduser()

                err = validate_minecraft_client_jar(p)
                if err is not None:
                    self._set_jar_alert(
                        f"Dropped file is not a Minecraft client jar: {err}\n"
                        "Drop a vanilla Minecraft client .jar (it contains assets/minecraft/...).",
                        kind="error",
                    )
                    return

                save_configured_minecraft_jar_path(p)
                try:
                    os.environ["MINECRAFT_JAR"] = str(p)
                except Exception:
                    pass

                if texture_source is not None:
                    try:
                        texture_source.close()
                    except Exception:
                        pass

                try:
                    jar_path = p
                    texture_source = TextureSource(jar_path)
                    resolver = MinecraftResourceResolver(texture_source)
                    textured = True
                except Exception as e:
                    jar_path = None
                    texture_source = None
                    resolver = None
                    textured = False
                    self._set_jar_alert(
                        f"Failed to open jar: {type(e).__name__}: {e}\n"
                        "Drag-drop a different Minecraft client .jar to try again.",
                        kind="error",
                    )
                    return

                # Clear cached textures/groups so we reload from the new jar.
                tex_cache.clear()
                group_cache.clear()
                ui_anim_spec_cache.clear()
                ui_anim_region_cache.clear()

                # Refresh palette entries (minimal list is used when no jar is present).
                palette_was_open = _close_and_clear_window_attr(owner=self, attr_name="_palette_window")
                self._palette_entries = []

                # Rebuild current structure + environment using the new texture source.
                cur = self._current_structure
                if cur is not None:
                    report = [str(x) for x in getattr(self, "_expansion_report", [])]
                    self._apply_structure_and_batch(cur, report, reset_view=False, adjust_distance=False, enable_delta_fade=False)
                try:
                    self._env_clear_geometry(keep_anchor=True)
                    self._update_environment(positions=getattr(self, "_pick_positions", set()))
                except Exception:
                    pass
                try:
                    self._update_hotbar_ui()
                except Exception:
                    pass
                try:
                    self._update_status()
                except Exception:
                    pass

                # Success: hide the jar banner if it was just the "no jar" warning.
                self._set_jar_alert("", kind="warn")
                if palette_was_open:
                    try:
                        self._toggle_palette()
                    except Exception:
                        pass

            def on_draw(self) -> None:
                now = time.monotonic()
                if not _draw_guard_render_cap(self, now_s=now, on_skip=self._on_draw_skip_render_cap):
                    return
                if not _draw_guard_render_retry(self, now_s=now, on_retry=self._on_draw_render_retry):
                    return

                try:
                    self._draw_scene()
                    self._capture_present_cache()
                    if self._viewer_error_kind == "render":
                        self._clear_viewer_error()
                except Exception as e:
                    self._set_viewer_error(
                        "render",
                        "Render error\n" + self._format_error_one_line(e),
                        detail=traceback.format_exc(limit=20),
                    )
                    self._draw_error_fallback()
                    self._capture_present_cache()

            def _on_draw_skip_render_cap(self) -> None:
                self._draw_skip_cap_count += 1
                self._draw_present_cache()

            def _on_draw_render_retry(self) -> None:
                self._draw_error_fallback()
                self._capture_present_cache()

            def _draw_scene(self) -> None:
                fx_mod.draw_scene(self, gl=gl, param_store=param_store)
                self._draw_test_banner()
                self._draw_jar_alert_termui()

            def _ensure_present_cache_tex(self, vp_w_px: int, vp_h_px: int) -> bool:
                w = max(1, int(vp_w_px))
                h = max(1, int(vp_h_px))
                try:
                    tex_id = int(getattr(self._present_cache_tex, "value"))
                except Exception:
                    tex_id = int(self._present_cache_tex) if self._present_cache_tex is not None else 0
                if tex_id == 0:
                    try:
                        gl.glGenTextures(1, ctypes.byref(self._present_cache_tex))
                    except Exception:
                        return False
                    try:
                        tex_id = int(getattr(self._present_cache_tex, "value"))
                    except Exception:
                        tex_id = int(self._present_cache_tex) if self._present_cache_tex is not None else 0
                    if tex_id == 0:
                        return False
                    self._present_cache_w = 0
                    self._present_cache_h = 0
                    self._present_cache_valid = False
                try:
                    gl.glBindTexture(gl.GL_TEXTURE_2D, self._present_cache_tex)
                    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
                    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
                    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
                    gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
                    if int(self._present_cache_w) != w or int(self._present_cache_h) != h:
                        gl.glTexImage2D(
                            gl.GL_TEXTURE_2D,
                            0,
                            gl.GL_RGBA,
                            int(w),
                            int(h),
                            0,
                            gl.GL_RGBA,
                            gl.GL_UNSIGNED_BYTE,
                            None,
                        )
                        self._present_cache_w = int(w)
                        self._present_cache_h = int(h)
                        self._present_cache_valid = False
                    gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                    return True
                except Exception:
                    try:
                        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                    except Exception:
                        pass
                    return False

            def _capture_present_cache(self) -> None:
                try:
                    vp_w_px, vp_h_px = self.get_viewport_size()
                except Exception:
                    return
                if not self._ensure_present_cache_tex(int(vp_w_px), int(vp_h_px)):
                    return
                try:
                    gl.glBindTexture(gl.GL_TEXTURE_2D, self._present_cache_tex)
                    gl.glCopyTexSubImage2D(
                        gl.GL_TEXTURE_2D,
                        0,
                        0,
                        0,
                        0,
                        0,
                        max(1, int(vp_w_px)),
                        max(1, int(vp_h_px)),
                    )
                    gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                    self._present_cache_valid = True
                except Exception:
                    try:
                        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                    except Exception:
                        pass

            def _draw_present_cache(self) -> None:
                if not bool(getattr(self, "_present_cache_valid", False)):
                    return
                try:
                    vp_w_px, vp_h_px = _resolve_present_cache_viewport_px(self)
                    gl.glViewport(0, 0, int(vp_w_px), int(vp_h_px))
                    gl.glDisable(gl.GL_LIGHTING)
                    gl.glDisable(gl.GL_DEPTH_TEST)
                    gl.glDepthMask(gl.GL_TRUE)
                    # Present the cached frame opaquely. Blended presents can
                    # amplify undefined/default-framebuffer alpha behavior.
                    gl.glDisable(gl.GL_BLEND)
                    gl.glColor4f(1.0, 1.0, 1.0, 1.0)
                    gl.glMatrixMode(gl.GL_PROJECTION)
                    gl.glLoadIdentity()
                    gl.glOrtho(0.0, float(vp_w_px), 0.0, float(vp_h_px), -1.0, 1.0)
                    gl.glMatrixMode(gl.GL_MODELVIEW)
                    gl.glLoadIdentity()
                    gl.glEnable(gl.GL_TEXTURE_2D)
                    gl.glBindTexture(gl.GL_TEXTURE_2D, self._present_cache_tex)
                    gl.glBegin(gl.GL_QUADS)
                    gl.glTexCoord2f(0.0, 0.0)
                    gl.glVertex2f(0.0, 0.0)
                    gl.glTexCoord2f(1.0, 0.0)
                    gl.glVertex2f(float(vp_w_px), 0.0)
                    gl.glTexCoord2f(1.0, 1.0)
                    gl.glVertex2f(float(vp_w_px), float(vp_h_px))
                    gl.glTexCoord2f(0.0, 1.0)
                    gl.glVertex2f(0.0, float(vp_h_px))
                    gl.glEnd()
                    gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                    self._draw_cache_present_count += 1
                except Exception:
                    try:
                        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
                    except Exception:
                        pass

            def _draw_jar_alert_termui(self) -> None:
                from enderterm.termui import TerminalSurface

                if getattr(self, "_jar_alert_dismissed", False) or not str(getattr(self, "_jar_alert_text", "") or "").strip():
                    self._jar_term_panel_rect = None
                    self._jar_term_dismiss_rect = None
                    return

                term_font, surface = self._sync_jar_termui(force=False)
                renderer = getattr(self, "_jar_term_renderer", None)
                if renderer is None or not isinstance(surface, TerminalSurface):
                    return

                try:
                    ratio = float(self.get_pixel_ratio())
                except Exception:
                    ratio = 1.0
                if ratio <= 0.0 or not math.isfinite(ratio):
                    ratio = 1.0

                vp_w_px, vp_h_px = self.get_viewport_size()
                sidebar_px = int(float(self.sidebar_width) * float(ratio))
                view_w_px = max(1, int(vp_w_px) - int(sidebar_px))
                view_h_px = max(1, int(vp_h_px))

                cell_w = max(1, int(getattr(term_font, "cell_w", 8)))
                cell_h = max(1, int(getattr(term_font, "cell_h", 14)))

                max_cols = max(20, int(view_w_px // cell_w) - 4)
                cols = int(round(float(max_cols) * 0.78))
                cols = max(44, min(int(cols), int(max_cols), 120))

                msg = str(getattr(self, "_jar_alert_text", "") or "").strip()
                kind = str(getattr(self, "_jar_alert_kind", "warn") or "warn").strip().lower()
                label_kind = "ALERT" if kind == "error" else "WARNING"

                inner_w = max(1, int(cols) - 2)
                wrapped: list[str] = []
                for para in msg.splitlines() or [""]:
                    words = [w for w in str(para).split(" ") if w != ""]
                    if not words:
                        wrapped.append("")
                        continue
                    line = ""
                    for w in words:
                        if not line:
                            line = w
                            continue
                        if len(line) + 1 + len(w) <= inner_w:
                            line = f"{line} {w}"
                        else:
                            wrapped.append(line[:inner_w])
                            line = w
                    if line:
                        wrapped.append(line[:inner_w])

                max_rows = max(6, int(view_h_px // cell_h) - 4)
                rows = max(6, min(int(max_rows), int(len(wrapped) + 2)))
                surface.resize(int(cols), int(rows))

                theme = _termui_theme_from_store(param_store)
                panel_bg = (theme.bg[0], theme.bg[1], theme.bg[2], 210)
                fg = theme.fg
                muted = theme.muted
                box_fg = theme.box_fg
                accent = theme.accent

                surface.default_bg = (0, 0, 0, 0)
                surface.default_fg = fg
                surface.clear()
                surface.fill_rect(0, 0, int(cols), int(rows), bg=panel_bg, fg=fg, ch=" ")
                border_fg = accent if kind == "error" else box_fg
                surface.draw_box(0, 0, int(cols), int(rows), fg=border_fg, bg=panel_bg, title=None)

                title = f"{label_kind}: Minecraft JAR"
                surface.put(2, 0, title[: max(0, int(cols) - 4)], fg=fg, bg=panel_bg)
                dismiss = "[X]"
                dismiss_x = max(1, int(cols) - len(dismiss) - 2)
                surface.put(int(dismiss_x), 0, dismiss, fg=accent, bg=panel_bg)

                for i, ln in enumerate(wrapped[: max(0, int(rows) - 2)]):
                    surface.put(1, 1 + i, ln[:inner_w], fg=muted, bg=panel_bg)

                panel_w_px = int(cols) * int(cell_w)
                panel_h_px = int(rows) * int(cell_h)
                panel_x_px = int(sidebar_px) + max(0, (int(view_w_px) - int(panel_w_px)) // 2)
                margin_y = max(0, int(round(float(view_h_px) * 0.06)))
                panel_y_px = int(view_h_px) - int(panel_h_px) - int(margin_y)
                panel_y_px = max(0, min(int(view_h_px) - int(panel_h_px), int(panel_y_px)))
                # Avoid overlapping the top-right --test-banner, which is drawn in window points.
                try:
                    tb_bg = getattr(self, "test_banner_bg", None)
                    if tb_bg is not None and bool(getattr(tb_bg, "visible", False)):
                        tb_bottom_pts = float(getattr(tb_bg, "y", 0.0))
                        tb_bottom_px = int(round(tb_bottom_pts * ratio))
                        gap_px = int(round(8.0 * ratio))
                        max_panel_top_px = int(tb_bottom_px) - int(gap_px)
                        if int(panel_y_px) + int(panel_h_px) > int(max_panel_top_px):
                            panel_y_px = max(0, int(int(max_panel_top_px) - int(panel_h_px)))
                except Exception:
                    pass

                # Click targets in window points.
                self._jar_term_panel_rect = (
                    float(panel_x_px) / ratio,
                    float(panel_y_px) / ratio,
                    float(panel_w_px) / ratio,
                    float(panel_h_px) / ratio,
                )
                dismiss_x0_px = int(panel_x_px + int(dismiss_x) * int(cell_w))
                dismiss_y0_px = int(panel_y_px + (int(panel_h_px) - int(cell_h)))  # top row
                self._jar_term_dismiss_rect = (
                    float(dismiss_x0_px) / ratio,
                    float(dismiss_y0_px) / ratio,
                    (float(len(dismiss) * int(cell_w)) / ratio),
                    (float(int(cell_h)) / ratio),
                )

                gl.glEnable(gl.GL_SCISSOR_TEST)
                gl.glScissor(int(panel_x_px), int(panel_y_px), max(1, int(panel_w_px)), max(1, int(panel_h_px)))
                gl.glViewport(int(panel_x_px), int(panel_y_px), max(1, int(panel_w_px)), max(1, int(panel_h_px)))
                renderer.draw(
                    surface=surface,
                    font=term_font,
                    vp_w_px=int(panel_w_px),
                    vp_h_px=int(panel_h_px),
                    param_store=None,
                    rez_active=False,
                    clear=False,
                )
                gl.glViewport(0, 0, max(1, int(vp_w_px)), max(1, int(vp_h_px)))
                gl.glDisable(gl.GL_SCISSOR_TEST)

            def _draw_test_banner(self) -> None:
                if not getattr(self, "_test_banner_text", ""):
                    return
                self._layout_test_banner(force=False)

                vp_w, vp_h = self.get_viewport_size()
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

                gl.glDisable(gl.GL_SCISSOR_TEST)
                gl.glDisable(gl.GL_DEPTH_TEST)
                gl.glEnable(gl.GL_BLEND)
                gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
                gl.glDisable(gl.GL_TEXTURE_2D)
                self.test_banner_shape_batch.draw()
                gl.glEnable(gl.GL_TEXTURE_2D)
                self.test_banner_text_batch.draw()

            def _layout_test_banner(self, *, force: bool) -> None:
                text = str(getattr(self, "_test_banner_text", "") or "").strip()
                if not text:
                    self.test_banner_bg.visible = False
                    self.test_banner_label.visible = False
                    return

                build = str(getattr(self, "_test_banner_build", "") or "unknown").strip()
                if build:
                    banner_text = f"EnderTerm {build}\\nTEST: {text}"
                else:
                    banner_text = f"TEST: {text}"

                try:
                    ui_scale = float(param_store.get("ui.font.scale") or 1.0)
                except Exception:
                    ui_scale = 1.0
                if not math.isfinite(ui_scale):
                    ui_scale = 1.0
                ui_scale = max(0.5, min(3.0, float(ui_scale)))

                key = (int(self.width), int(self.height), float(ui_scale), str(build), str(text))
                if (not force) and key == getattr(self, "_test_banner_last_layout", None):
                    return
                self._test_banner_last_layout = key

                margin = int(round(10.0 * ui_scale))
                pad = int(round(10.0 * ui_scale))
                max_w = int(round(min(float(self.width) * 0.66, 900.0)))
                max_w = max(260, min(int(self.width) - (2 * margin), int(max_w)))
                inner_w = max(1, int(max_w - (2 * pad)))

                self.test_banner_label.font_size = int(round(14.0 * ui_scale))
                self.test_banner_label.width = int(inner_w)
                self.test_banner_label.text = str(banner_text)

                h = int(self.test_banner_label.content_height) + (2 * pad)
                h = max(int(round(34.0 * ui_scale)), int(h))

                x0 = int(self.width) - margin - int(max_w)
                y1 = int(self.height) - margin
                self.test_banner_bg.x = float(x0)
                self.test_banner_bg.y = float(y1 - h)
                self.test_banner_bg.width = float(max_w)
                self.test_banner_bg.height = float(h)
                self.test_banner_bg.visible = True

                self.test_banner_label.x = float(x0 + pad)
                self.test_banner_label.y = float(y1 - pad)
                self.test_banner_label.visible = True

            def _draw_error_fallback(self) -> None:
                # Avoid repeated exception loops: clear to black + draw only UI overlays.
                try:
                    gl.glClearColor(0.0, 0.0, 0.0, 1.0)
                except Exception:
                    pass
                try:
                    self.clear()
                except Exception:
                    pass
                try:
                    gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
                except Exception:
                    pass
                try:
                    vp_w, vp_h = self.get_viewport_size()
                    fx_mod.draw_ui(self, int(vp_w), int(vp_h), gl=gl, param_store=param_store)
                except Exception:
                    pass
                try:
                    if self._effects_enabled:
                        fx_mod.draw_post_fx_overlay(self, gl=gl, param_store=param_store)
                except Exception:
                    pass

            def _draw_ender_vision_markers(self) -> None:
                fx_mod.draw_ender_vision_markers(
                    self,
                    gl=gl,
                    param_store=param_store,
                    face_dirs=FACE_DIRS,
                    cube_face_quad_points=_cube_face_quad_points,
                )

            def _draw_structure_delta_fade_overlays(self) -> None:
                fx_mod.draw_structure_delta_fade_overlays(self, gl=gl, param_store=param_store, stable_seed=_stable_seed)

            def _draw_rez_live_preview_chunks(self) -> None:
                fx_mod.draw_rez_live_preview_chunks(self, gl=gl, param_store=param_store)

            def _draw_world_3d(self, *, aspect: float) -> None:
                render_world_mod.draw_world_3d(
                    self,
                    aspect=aspect,
                    gl=gl,
                    param_store=param_store,
                    gluPerspective=gluPerspective,
                    pyglet_mod=pyglet,
                    group_cache=group_cache,
                    no_tex_group=no_tex_group,
                )

            def _draw_world(self, vp_w: int, vp_h: int) -> None:
                fx_mod.draw_world(self, vp_w, vp_h, gl=gl, param_store=param_store)

            def _draw_ui(self, vp_w: int, vp_h: int) -> None:
                fx_mod.draw_ui(self, vp_w, vp_h, gl=gl, param_store=param_store)

            def _sync_sidebar_termui(self, *, force: bool) -> tuple[object, object]:
                from enderterm.termui import MinecraftAsciiBitmapFont, TerminalFont, TerminalSurface, _default_minecraft_ascii_png

                try:
                    ui_scale = float(param_store.get("ui.font.scale") or 1.0)
                except Exception:
                    ui_scale = 1.0
                if not math.isfinite(ui_scale):
                    ui_scale = 1.0
                ui_scale = max(0.5, min(3.0, float(ui_scale)))

                try:
                    ratio = float(self.get_pixel_ratio())
                except Exception:
                    ratio = 1.0
                if ratio <= 0.0 or not math.isfinite(ratio):
                    ratio = 1.0

                if (not force) and abs(ui_scale - float(getattr(self, "_sidebar_term_scale_last", -1.0))) < 1e-6 and abs(
                    ratio - float(getattr(self, "_sidebar_term_ratio_last", -1.0))
                ) < 1e-6:
                    if self._sidebar_term_font is not None and self._sidebar_term_surface is not None:
                        return self._sidebar_term_font, self._sidebar_term_surface

                self._sidebar_term_scale_last = float(ui_scale)
                self._sidebar_term_ratio_last = float(ratio)

                font_size_px = max(6, int(round(14.0 * ui_scale * ratio)))
                ascii_png = _default_minecraft_ascii_png()
                if ascii_png is not None:
                    self._sidebar_term_font = MinecraftAsciiBitmapFont(atlas_path=ascii_png, cell_px=font_size_px)
                else:
                    self._sidebar_term_font = TerminalFont(font_name=self.ui_font_name, font_size_px=font_size_px)

                if self._sidebar_term_surface is None:
                    self._sidebar_term_surface = TerminalSurface(
                        1,
                        1,
                        default_fg=(18, 14, 22, 255),
                        default_bg=(232, 229, 235, 255),
                    )
                return self._sidebar_term_font, self._sidebar_term_surface

            def _sync_rez_termui(self, *, force: bool) -> tuple[object, object]:
                from enderterm.termui import TerminalSurface

                term_font, _ = self._sync_sidebar_termui(force=force)
                if self._rez_term_surface is None:
                    # Default to transparent so the overlay can be composited via blending.
                    self._rez_term_surface = TerminalSurface(1, 1, default_fg=(18, 14, 22, 255), default_bg=(0, 0, 0, 0))
                return term_font, self._rez_term_surface

            def _sync_jar_termui(self, *, force: bool) -> tuple[object, object]:
                from enderterm.termui import TerminalSurface

                term_font, _ = self._sync_sidebar_termui(force=force)
                if self._jar_term_surface is None:
                    # Default to transparent so the alert panel can be composited via blending.
                    self._jar_term_surface = TerminalSurface(1, 1, default_fg=(18, 14, 22, 255), default_bg=(0, 0, 0, 0))
                return term_font, self._jar_term_surface

            def _sidebar_term_layout(self, *, vp_h_px: int, sidebar_px: int) -> tuple[int, int, int]:
                # Returns: (rows, header_rows, log_rows)
                term_font, _surface = self._sync_sidebar_termui(force=False)
                cell_h = max(1, int(getattr(term_font, "cell_h", 14)))
                rows = max(1, int(int(vp_h_px) // cell_h))

                header_rows = 2 + int(self.status_lines_max)
                header_rows += 1  # spacer
                header_rows = max(3, min(rows - 2, int(header_rows)))

                closed_rows = 2
                open_rows = max(4, min(rows // 3, 10))
                try:
                    p = float(getattr(self, "_term_log_open_p", 0.0))
                except Exception:
                    p = 0.0
                if not math.isfinite(p):
                    p = 0.0
                p = max(0.0, min(1.0, p))
                # When collapsed, keep the terminal state pinned to 0 so opening is consistent.
                if self._log_collapsed and getattr(self, "_term_log_open_tween", None) is None:
                    p = 0.0
                if (not self._log_collapsed) and getattr(self, "_term_log_open_tween", None) is None:
                    p = 1.0
                log_rows = int(round(float(closed_rows) + (float(open_rows - closed_rows) * ease_smoothstep(p))))
                log_rows = max(int(closed_rows), min(rows - header_rows - 1, int(log_rows)))
                return rows, header_rows, log_rows

            def _visible_list_rows_termui(self) -> int:
                vp_w, vp_h = self.get_viewport_size()
                ratio = float(self.get_pixel_ratio())
                sidebar_px = int(float(self.sidebar_width) * ratio)
                rows, header_rows, log_rows = self._sidebar_term_layout(vp_h_px=int(vp_h), sidebar_px=int(sidebar_px))
                list_rows_total = max(1, int(rows - header_rows - log_rows))
                # Account for list box border.
                inner = max(1, int(list_rows_total - 2))
                # Terminal list reserves 1 inner row for the search bar when space allows.
                if inner >= 2:
                    inner = int(inner) - 1
                return inner

            def _refresh_term_list_scrollbar(
                self,
                *,
                cols: int,
                rows: int,
                header_rows: int,
                log_rows: int,
            ) -> tuple[int, int, int, int, int, int, int, int]:
                # Returns list/log box geometry + scrollbar column:
                # (list_box_y, list_box_h, log_box_y, log_box_h, inner_y, inner_h, visible, sb_x)
                list_box_y = int(header_rows)
                list_box_h = max(3, int(rows) - int(header_rows) - int(log_rows))
                log_box_y = int(list_box_y + list_box_h)
                log_box_h = max(2, int(rows) - int(log_box_y))
                if log_box_h < 2:
                    log_box_h = 2
                    log_box_y = int(rows) - 2
                    list_box_h = max(3, int(log_box_y) - int(list_box_y))

                inner_x = 1
                inner_y = int(list_box_y + 1)
                inner_w = max(0, int(cols) - 2)
                inner_h = max(0, int(list_box_h) - 2)
                text_w = max(1, int(inner_w) - 1)
                sb_x = int(inner_x + text_w)

                show_search = int(inner_h) >= 2
                list_h = max(0, int(inner_h) - (1 if show_search else 0))
                visible = max(1, int(list_h)) if list_h > 0 else max(1, int(inner_h))

                indices = self._filtered_indices
                max_scroll = max(0, len(indices) - int(visible))
                self._scroll_pos_f = max(0.0, min(float(max_scroll), float(self._scroll_pos_f)))
                scroll_top = int(self._scroll_pos_f)
                sel_pos = self._selected_list_pos()
                if getattr(self, "_scroll_follow_selection", True) and sel_pos is not None:
                    if sel_pos < scroll_top:
                        scroll_top = int(sel_pos)
                    if sel_pos >= scroll_top + int(visible):
                        scroll_top = int(sel_pos) - int(visible) + 1
                scroll_top = max(0, min(int(max_scroll), int(scroll_top)))
                self._scroll_pos_f = float(scroll_top)
                self.scroll_top = int(scroll_top)

                self._term_list_scrollbar.update(
                    track_top=int(inner_y),
                    track_rows=int(inner_h),
                    visible_rows=int(visible),
                    total_rows=len(indices),
                    scroll_top=int(scroll_top),
                )
                return (
                    int(list_box_y),
                    int(list_box_h),
                    int(log_box_y),
                    int(log_box_h),
                    int(inner_y),
                    int(inner_h),
                    int(visible),
                    int(sb_x),
                )

            def _draw_sidebar_termui(self, *, vp_w_px: int, vp_h_px: int, sidebar_px: int) -> None:
                from enderterm.termui import TerminalSurface

                term_font, surface = self._sync_sidebar_termui(force=False)
                renderer = getattr(self, "_sidebar_term_renderer", None)
                if renderer is None:
                    return

                # Tick log open/close animation.
                tween = getattr(self, "_term_log_open_tween", None)
                if tween is not None:
                    now = time.monotonic()
                    try:
                        self._term_log_open_p = float(tween.value(now))
                    except Exception:
                        self._term_log_open_p = float(getattr(self, "_term_log_open_p", 0.0))
                    if tween.done(now):
                        try:
                            self._term_log_open_p = float(tween.end)
                        except Exception:
                            pass
                        self._term_log_open_tween = None

                cell_w = max(1, int(getattr(term_font, "cell_w", 8)))
                cell_h = max(1, int(getattr(term_font, "cell_h", 14)))
                cols = max(1, int(int(sidebar_px) // cell_w))
                rows, header_rows, log_rows = self._sidebar_term_layout(vp_h_px=int(vp_h_px), sidebar_px=int(sidebar_px))
                if cols <= 0 or rows <= 0:
                    return

                if not isinstance(surface, TerminalSurface):
                    return
                surface.resize(cols, rows)

                theme = _termui_theme_from_store(param_store)
                bg = theme.bg
                fg = theme.fg
                muted = theme.muted
                box_fg = theme.box_fg
                sel_bg = theme.sel_bg
                sel_fg = theme.sel_fg
                accent = theme.accent

                surface.default_bg = bg
                surface.default_fg = fg
                surface.clear()

                # Header lines (reuse existing text generation).
                row = 0
                title = str(getattr(self.title, "text", "") or "")
                subtitle = str(getattr(self.subtitle, "text", "") or "")
                if title:
                    surface.put(0, row, title[:cols], fg=fg, bg=bg)
                row += 1
                if subtitle:
                    surface.put(0, row, subtitle[:cols], fg=muted, bg=bg)
                row += 1
                for lbl in getattr(self, "status_labels", []):
                    if row >= header_rows - 1:
                        break
                    text = str(getattr(lbl, "text", "") or "")
                    if not text:
                        continue
                    surface.put(0, row, text[:cols], fg=fg, bg=bg)
                    row += 1
                if row < header_rows:
                    row = header_rows

                # List + log boxes.
                (
                    list_box_y,
                    list_box_h,
                    log_box_y,
                    log_box_h,
                    inner_y,
                    inner_h,
                    visible,
                    sb_x,
                ) = self._refresh_term_list_scrollbar(
                    cols=int(cols),
                    rows=int(rows),
                    header_rows=int(header_rows),
                    log_rows=int(log_rows),
                )

                list_box_border = fg
                log_box_border = box_fg if self._log_collapsed else fg
                surface.draw_box(0, list_box_y, cols, list_box_h, fg=list_box_border, bg=bg, title=None)
                surface.draw_box(0, log_box_y, cols, log_box_h, fg=log_box_border, bg=bg, title=None)

                # Box titles / tabs.
                x_title = 2
                tabs: list[tuple[str, str]] = [("NBT", "structures"), ("Pool", "pools")]
                x = int(x_title)
                surface.put(x, list_box_y, "[", fg=box_fg, bg=bg)
                x += 1
                surface.put(x, list_box_y, " ", fg=box_fg, bg=bg)
                x += 1
                for i, (tab_name, mode) in enumerate(tabs):
                    is_active = str(getattr(self, "_browser_mode", "")) == mode
                    tab_fg = fg if is_active else box_fg
                    surface.put(x, list_box_y, tab_name[: max(0, cols - x)], fg=tab_fg, bg=bg)
                    x += len(tab_name)
                    if i != len(tabs) - 1:
                        surface.put(x, list_box_y, " / "[: max(0, cols - x)], fg=box_fg, bg=bg)
                        x += 3
                surface.put(x, list_box_y, " ]"[: max(0, cols - x)], fg=box_fg, bg=bg)
                surface.put(2, log_box_y, "Rez Log"[: max(0, cols - 2)], fg=fg if not self._log_collapsed else box_fg, bg=bg)

                # List contents.
                inner_x = 1
                inner_w = max(0, cols - 2)
                text_w = max(1, inner_w - 1)  # reserve last col for scrollbar
                show_search = inner_h >= 2

                indices = self._filtered_indices
                active = self._active_labels()
                selected = int(self._active_selected_index())
                scroll_top = int(self.scroll_top)

                for i in range(visible):
                    pos = int(scroll_top) + int(i)
                    if pos >= len(indices):
                        break
                    idx = indices[pos]
                    label = active[idx] if 0 <= idx < len(active) else ""
                    hscroll = max(0, int(getattr(self, "_scroll_x_cols", 0)))
                    if hscroll:
                        label = label[hscroll:]
                    if len(label) > text_w:
                        label = label[: max(0, text_w - 1)] + "…"
                    y = inner_y + i
                    is_sel = idx == selected
                    if is_sel:
                        surface.fill_rect(inner_x, y, text_w, 1, bg=sel_bg, fg=sel_fg)
                        surface.put(inner_x, y, label[:text_w], fg=sel_fg, bg=sel_bg)
                        surface.put(0, y, "▌", fg=accent, bg=bg)
                    else:
                        surface.put(inner_x, y, label[:text_w], fg=muted, bg=bg)

                # Search bar (inside list box, bottom row).
                if show_search:
                    search_row = inner_y + max(0, visible)
                    total = len(active)
                    matches = len(indices)
                    cursor_on = bool(self._search_active and (int(time.monotonic() * 2.2) % 2 == 0))
                    cursor = "▌" if cursor_on else ""
                    q = self._search_query
                    left = "/" + q if (q or self._search_active) else "/"
                    right = f"{matches}/{total}"
                    cancel = "[X]" if self._search_ui_visible() else ""
                    # Layout: [ /query▌ ............ ][X] 12/34
                    usable = max(0, text_w)
                    tail = ""
                    if cancel and right:
                        tail = f"{cancel} {right}"
                    elif cancel:
                        tail = cancel
                    else:
                        tail = right
                    if len(tail) >= usable:
                        tail = tail[-usable:]
                    left_w = max(0, usable - len(tail))
                    show_left = left
                    if len(show_left) > left_w:
                        show_left = show_left[: max(0, left_w - 1)] + "…"
                    surface.fill_rect(inner_x, search_row, text_w, 1, bg=bg, fg=fg)
                    surface.put(inner_x, search_row, show_left[:left_w], fg=fg, bg=bg)
                    if cursor and len(show_left) < left_w:
                        cx = inner_x + len(show_left)
                        if inner_x <= cx < inner_x + text_w:
                            surface.put(cx, search_row, cursor, fg=accent, bg=bg)
                    if tail:
                        surface.put(inner_x + max(0, usable - len(tail)), search_row, tail, fg=muted, bg=bg)

                # Scrollbar.
                if inner_h > 0 and sb_x < cols - 1:
                    for yy in range(inner_h):
                        surface.put(sb_x, inner_y + yy, "░", fg=box_fg, bg=bg)
                    thumb_h = int(self._term_list_scrollbar.thumb_rows)
                    thumb_top = int(self._term_list_scrollbar.thumb_top)
                    if thumb_h > 0:
                        for yy in range(thumb_h):
                            ty = int(thumb_top) + int(yy)
                            if int(inner_y) <= ty < int(inner_y + inner_h):
                                surface.put(sb_x, ty, "█", fg=accent, bg=bg)

                # Log lines.
                log_inner_x = 1
                log_inner_y = log_box_y + 1
                log_inner_w = max(0, cols - 2)
                log_inner_h = max(0, log_box_h - 2)
                log_lines = list(self._expansion_report[-log_inner_h :]) if (not self._log_collapsed) else []
                if self._log_collapsed:
                    log_lines = ["(collapsed)"]
                if not log_lines:
                    log_lines = ["(no expansions yet)"]
                for i, ln in enumerate(log_lines[:log_inner_h]):
                    surface.put(log_inner_x, log_inner_y + i, str(ln)[:log_inner_w], fg=fg, bg=bg)

                # Draw into the sidebar viewport.
                gl.glEnable(gl.GL_SCISSOR_TEST)
                gl.glScissor(0, 0, max(1, int(sidebar_px)), max(1, int(vp_h_px)))
                gl.glViewport(0, 0, max(1, int(sidebar_px)), max(1, int(vp_h_px)))
                renderer.draw(
                    surface=surface,
                    font=term_font,
                    vp_w_px=int(sidebar_px),
                    vp_h_px=int(vp_h_px),
                    param_store=param_store,
                    rez_active=bool(self._rez_active),
                )
                gl.glViewport(0, 0, max(1, int(vp_w_px)), max(1, int(vp_h_px)))
                gl.glDisable(gl.GL_SCISSOR_TEST)

            def _draw_rez_termui(self, *, vp_w_px: int, vp_h_px: int, sidebar_px: int) -> None:
                from enderterm.termui import TerminalSurface

                if not getattr(self, "_rez_termui_enabled", False):
                    self._rez_term_cancel_rect = None
                    return
                if not getattr(self, "_rez_active", False):
                    self._rez_term_cancel_rect = None
                    return

                term_font, surface = self._sync_rez_termui(force=False)
                renderer = getattr(self, "_rez_term_renderer", None)
                if renderer is None:
                    return
                if not isinstance(surface, TerminalSurface):
                    return

                try:
                    ratio = float(self.get_pixel_ratio())
                except Exception:
                    ratio = 1.0
                if ratio <= 0.0 or not math.isfinite(ratio):
                    ratio = 1.0

                cell_w = max(1, int(getattr(term_font, "cell_w", 8)))
                cell_h = max(1, int(getattr(term_font, "cell_h", 14)))

                view_w_px = max(1, int(vp_w_px) - int(sidebar_px))
                view_h_px = max(1, int(vp_h_px))

                max_cols = max(10, int(view_w_px // cell_w) - 4)
                cols = int(max_cols * 0.66)
                cols = max(34, min(int(cols), int(max_cols), 120))
                rows = 6
                surface.resize(int(cols), int(rows))

                theme = _termui_theme_from_store(param_store)
                # Semi-transparent panel so it floats above the 3D world.
                panel_bg = (theme.bg[0], theme.bg[1], theme.bg[2], 190)
                fg = theme.fg
                muted = theme.muted
                box_fg = theme.box_fg
                accent = theme.accent

                surface.default_bg = (0, 0, 0, 0)
                surface.default_fg = fg
                surface.clear()
                surface.fill_rect(0, 0, int(cols), int(rows), bg=panel_bg, fg=fg, ch=" ")
                surface.draw_box(0, 0, int(cols), int(rows), fg=box_fg, bg=panel_bg, title=None)

                title = "Rezzing"
                surface.put(2, 0, title[: max(0, int(cols) - 4)], fg=fg, bg=panel_bg)

                cancel = "[X]"
                cancel_x = max(1, int(cols) - len(cancel) - 2)
                surface.put(int(cancel_x), 0, cancel, fg=accent, bg=panel_bg)

                msg = str(getattr(self, "_rez_message", "") or "")
                inner_w = max(1, int(cols) - 2)
                # Wrap to at most 2 lines.
                msg_lines: list[str] = []
                while msg and len(msg_lines) < 2:
                    msg_lines.append(msg[:inner_w])
                    msg = msg[inner_w:]
                if not msg_lines:
                    msg_lines = [""]
                for i, ln in enumerate(msg_lines[:2]):
                    surface.put(1, 1 + i, ln[:inner_w], fg=muted, bg=panel_bg)

                # Progress bar.
                try:
                    p = float(getattr(self, "_rez_progress", 0.0))
                except Exception:
                    p = 0.0
                if not math.isfinite(p):
                    p = 0.0
                p = max(0.0, min(1.0, p))
                pct_s = f"{int(round(p * 100.0)):3d}%"
                bar_row = int(rows) - 2
                bar_w = max(10, int(inner_w) - (len(pct_s) + 1))
                filled = int(round(p * float(bar_w)))
                filled = max(0, min(int(bar_w), int(filled)))
                for i in range(int(bar_w)):
                    ch = "█" if i < filled else "░"
                    c_fg = accent if i < filled else box_fg
                    surface.put(1 + i, bar_row, ch, fg=c_fg, bg=panel_bg)
                surface.put(1 + int(bar_w) + 1, bar_row, pct_s[: max(0, inner_w - bar_w - 1)], fg=fg, bg=panel_bg)

                panel_w_px = int(cols) * int(cell_w)
                panel_h_px = int(rows) * int(cell_h)
                panel_x_px = int(sidebar_px) + max(0, (int(view_w_px) - int(panel_w_px)) // 2)
                panel_y_px = int(float(view_h_px) * 0.22)
                panel_y_px = max(0, min(int(view_h_px) - int(panel_h_px), int(panel_y_px)))

                # Cancel hit target in window points.
                cancel_x0_px = int(panel_x_px + int(cancel_x) * int(cell_w))
                cancel_y0_px = int(panel_y_px + (int(panel_h_px) - int(cell_h)))  # top row
                self._rez_term_cancel_rect = (
                    float(cancel_x0_px) / ratio,
                    float(cancel_y0_px) / ratio,
                    (float(len(cancel) * int(cell_w)) / ratio),
                    (float(int(cell_h)) / ratio),
                )

                # Draw into a scissored viewport over the model view.
                gl.glEnable(gl.GL_SCISSOR_TEST)
                gl.glScissor(int(panel_x_px), int(panel_y_px), max(1, int(panel_w_px)), max(1, int(panel_h_px)))
                gl.glViewport(int(panel_x_px), int(panel_y_px), max(1, int(panel_w_px)), max(1, int(panel_h_px)))
                renderer.draw(
                    surface=surface,
                    font=term_font,
                    vp_w_px=int(panel_w_px),
                    vp_h_px=int(panel_h_px),
                    param_store=None,
                    rez_active=bool(self._rez_active),
                    clear=False,
                )
                gl.glViewport(0, 0, max(1, int(vp_w_px)), max(1, int(vp_h_px)))
                gl.glDisable(gl.GL_SCISSOR_TEST)

        win = ViewerWindow()

        smoke_result: dict[str, object] | None = None
        if smoke_enabled:
            if smoke_suite_enabled:
                smoke_mode = "suite"
            elif smoke_expand_enabled:
                smoke_mode = "expand_once"
            elif smoke_second_viewport_fx_enabled:
                smoke_mode = "second_viewport_fx"
            elif smoke_build_edits_enabled:
                smoke_mode = "build_edits"
            elif smoke_real_window_build_edits_enabled:
                smoke_mode = "real_window_build_edits"
            elif smoke_real_window_keys_enabled:
                smoke_mode = "real_window_keys"
            elif smoke_real_window_click_enabled:
                smoke_mode = "real_window_click"
            else:
                smoke_mode = "focus_handoff"
            smoke_result = {"ok": False, "error": None, "mode": smoke_mode}
            started_t = time.monotonic()
            deadline_t = float(started_t + max(1.0, float(smoke_timeout_s)))
            pressed = False
            saw_rez = False
            opened_second_view = False
            second_fx_info: dict[str, object] | None = None
            second_fx_capture_started_t: float | None = None
            second_fx_capture_attempts = 0
            second_fx_min_luma = 0.5
            second_fx_capture_grace_s = 3.0
            focus_allowed_sources = ("palette", "debug", "param", "viewport")
            build_os_initialized = False
            build_os_action_index = 0
            build_os_action_started_t = time.monotonic()
            build_os_action_timeout_s = 4.0
            build_os_last_nudge_t = 0.0
            build_os_undo_start_len = 0
            build_os_before_positions: set[Vec3i] = set()
            build_os_expected_places: list[Vec3i] = []
            build_os_expected_removes: list[Vec3i] = []
            build_os_click_targets_by_name: dict[str, tuple[int, int]] = {}
            build_os_actions: list[dict[str, object]] = []

            keys_os_initialized = False
            keys_os_step_index = 0
            keys_os_event_index = 0
            keys_os_step_started_t = time.monotonic()
            keys_os_step_timeout_s = 4.0
            keys_os_expected: list[tuple[str, int]] = []
            keys_os_step_states: list[dict[str, object]] = []
            if smoke_real_window_keys_enabled or smoke_suite_enabled:
                try:
                    setattr(win, "_smoke_record_keys", True)
                    setattr(win, "_smoke_key_events", [])
                except Exception:
                    pass

            def _parse_focus_source_list(raw: str | None) -> list[str]:
                out: list[str] = []
                if raw is None:
                    return out
                parts = str(raw).replace(";", ",").split(",")
                for part in parts:
                    src = str(part).strip().lower()
                    if src in focus_allowed_sources and src not in out:
                        out.append(src)
                return out

            focus_sources = _parse_focus_source_list(os.environ.get("ENDERTERM_SMOKE_FOCUS_SOURCES"))
            if not focus_sources:
                focus_sources = list(focus_allowed_sources)
            focus_preopen_sources = _parse_focus_source_list(os.environ.get("ENDERTERM_SMOKE_FOCUS_PREOPEN_SOURCES"))
            focus_preopen_sources = [src for src in focus_preopen_sources if src not in focus_sources]
            focus_preopen_initialized = not bool(focus_preopen_sources)
            focus_preopen_index = 0
            focus_preopen_started_t = time.monotonic()
            focus_preopen_open_t_by_source: dict[str, float] = {}
            focus_step_index = 0
            focus_step_phase = "open_close"
            focus_step_started_t = time.monotonic()
            focus_click_target: tuple[int, int] | None = None
            focus_validated_sources: list[str] = []
            focus_close_path_by_source: dict[str, str] = {}
            focus_close_intent_by_source: dict[str, str] = {}
            focus_close_observed_by_source: dict[str, str] = {}
            focus_open_dwell_s = 1.0
            focus_click_settle_s = 0.25
            focus_last_nudge_t = 0.0
            focus_open_confirmed_t_by_source: dict[str, float] = {}
            focus_close_requested_t_by_source: dict[str, float] = {}
            focus_require_click = str(os.environ.get("ENDERTERM_SMOKE_FOCUS_REQUIRE_CLICK", "")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if smoke_real_window_click_enabled:
                focus_require_click = True
            focus_close_by_click = str(os.environ.get("ENDERTERM_SMOKE_FOCUS_CLOSE_BY_CLICK", "")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            if focus_close_by_click:
                # Closing by native titlebar buttons requires OS click injection.
                focus_require_click = True
            focus_viewport_cycles_raw = str(os.environ.get("ENDERTERM_SMOKE_VIEWPORT_CYCLES", "1")).strip()
            try:
                focus_viewport_cycles = int(focus_viewport_cycles_raw)
            except Exception:
                focus_viewport_cycles = 1
            focus_viewport_cycles = max(1, min(8, int(focus_viewport_cycles)))
            focus_sequence_sources: list[str] = []
            for src in focus_sources:
                if str(src) == "viewport":
                    focus_sequence_sources.extend(["viewport"] * int(focus_viewport_cycles))
                else:
                    focus_sequence_sources.append(str(src))
            if not focus_sequence_sources:
                focus_sequence_sources = list(focus_sources)
            focus_viewport_min_luma = 0.5
            focus_viewport_capture_grace_s = 3.0
            focus_viewport_baseline_main_shot: dict[str, object] | None = None
            focus_viewport_baseline_started_t: float | None = None
            focus_viewport_baseline_ok = False
            focus_viewport_close_main_shots: list[dict[str, object]] = []
            focus_viewport_close_capture_steps: set[int] = set()
            focus_viewport_close_capture_started_t_by_step: dict[int, float] = {}
            focus_viewport_post_main_probe_shots: list[dict[str, object]] = []
            focus_viewport_post_main_probe_capture_steps: set[int] = set()
            done = False
            suite_default_steps = [
                "expand_once",
                "second_viewport_fx",
                "frame_cap_present_stability",
                "focus_handoff",
                "real_window_keys",
                "real_window_build_edits",
            ]
            suite_allowed_steps = set(suite_default_steps)
            suite_allowed_steps.add("build_edits")

            def _parse_suite_steps(raw: str | None) -> list[str]:
                if raw is None:
                    return []
                spec = str(raw).strip()
                if not spec:
                    return []
                parts = spec.replace(";", ",").split(",")
                out: list[str] = []
                for part in parts:
                    step = str(part).strip().lower()
                    if not step or step not in suite_allowed_steps or step in out:
                        continue
                    out.append(step)
                return out

            suite_steps = (
                _parse_suite_steps(os.environ.get("ENDERTERM_SMOKE_SUITE_STEPS"))
                if smoke_suite_enabled
                else []
            )
            if smoke_suite_enabled and not suite_steps:
                suite_steps = list(suite_default_steps)
            suite_step_index = 0
            suite_results: dict[str, dict[str, object]] = {}
            suite_step_started_t = time.monotonic()
            frame_cap_prev_hz: float | None = None
            frame_cap_initialized = False
            frame_cap_started_t = time.monotonic()
            frame_cap_target_hz = 2.0
            frame_cap_duration_s = 8.0
            try:
                frame_cap_target_hz = float(os.environ.get("ENDERTERM_SMOKE_SUITE_FRAME_CAP_HZ", "2") or "2")
            except Exception:
                frame_cap_target_hz = 2.0
            try:
                frame_cap_duration_s = float(
                    os.environ.get("ENDERTERM_SMOKE_SUITE_FRAME_CAP_SECONDS", "8") or "8"
                )
            except Exception:
                frame_cap_duration_s = 8.0

            def _gl_uint_value(obj: object) -> int:
                try:
                    return int(getattr(obj, "value"))
                except Exception:
                    try:
                        return int(obj)  # type: ignore[arg-type]
                    except Exception:
                        return 0

            def _smoke_capture_window_signature(
                window_obj: object,
                *,
                region_x_px: int,
                region_y_px: int,
                region_w_px: int,
                region_h_px: int,
            ) -> dict[str, object]:
                x0 = max(0, int(region_x_px))
                y0 = max(0, int(region_y_px))
                w = max(1, int(region_w_px))
                h = max(1, int(region_h_px))
                try:
                    switch_to = getattr(window_obj, "switch_to", None)
                    if callable(switch_to):
                        switch_to()
                except Exception:
                    pass
                try:
                    buf = (ctypes.c_ubyte * int(w * h * 4))()
                    gl.glPixelStorei(gl.GL_PACK_ALIGNMENT, 1)
                    gl.glReadPixels(
                        int(x0),
                        int(y0),
                        int(w),
                        int(h),
                        gl.GL_RGBA,
                        gl.GL_UNSIGNED_BYTE,
                        buf,
                    )
                    sig = _smoke_signature_from_rgba(bytes(buf), width=int(w), height=int(h))
                    sig["region_px"] = [int(x0), int(y0), int(w), int(h)]
                    return sig
                except Exception as e:
                    return {"error": f"{type(e).__name__}: {e}", "region_px": [int(x0), int(y0), int(w), int(h)]}

            def _smoke_save_window_png(window_obj: object, path: Path) -> str | None:
                try:
                    switch_to = getattr(window_obj, "switch_to", None)
                    if callable(switch_to):
                        switch_to()
                    pyglet.image.get_buffer_manager().get_color_buffer().save(str(path))
                    return str(path)
                except Exception:
                    return None

            def _smoke_capture_region_px(window_obj: object, *, trim_sidebar: bool) -> tuple[int, int, int, int]:
                try:
                    vp_w, vp_h = window_obj.get_viewport_size()
                except Exception:
                    vp_w, vp_h = (int(getattr(window_obj, "width", 1)), int(getattr(window_obj, "height", 1)))
                vp_w = max(1, int(vp_w))
                vp_h = max(1, int(vp_h))
                if not bool(trim_sidebar):
                    return (0, 0, int(vp_w), int(vp_h))

                try:
                    ratio = float(window_obj.get_pixel_ratio())
                except Exception:
                    ratio = 1.0
                if ratio <= 0.0 or (not math.isfinite(ratio)):
                    ratio = 1.0

                sidebar_px = int(round(float(getattr(window_obj, "sidebar_width", 0.0)) * float(ratio)))
                main_x_px = max(0, min(int(vp_w) - 1, int(sidebar_px)))
                return (int(main_x_px), 0, max(1, int(vp_w) - int(main_x_px)), int(vp_h))

            def _smoke_signature_compare(base_sig: dict[str, object], check_sig: dict[str, object]) -> dict[str, object]:
                compare: dict[str, object] = {}
                base_hash = str(base_sig.get("dhash64", "") or "").strip()
                check_hash = str(check_sig.get("dhash64", "") or "").strip()
                if base_hash and check_hash:
                    compare["dhash_hamming"] = int(_smoke_hex_hamming_distance(base_hash, check_hash))
                try:
                    base_luma = float(base_sig.get("mean_luma", 0.0))
                    check_luma = float(check_sig.get("mean_luma", 0.0))
                    compare["mean_luma_delta"] = float(abs(check_luma - base_luma))
                    compare["mean_luma_ratio"] = float(check_luma / base_luma) if abs(base_luma) > 1e-6 else None
                except Exception:
                    pass
                return compare

            def _smoke_capture_main_window_shot(*, label_suffix: str) -> dict[str, object]:
                main_region = _smoke_capture_region_px(win, trim_sidebar=True)
                main_sig = _smoke_capture_window_signature(
                    win,
                    region_x_px=int(main_region[0]),
                    region_y_px=int(main_region[1]),
                    region_w_px=int(main_region[2]),
                    region_h_px=int(main_region[3]),
                )
                shot: dict[str, object] = {"main_signature": main_sig}
                try:
                    smoke_out_path.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                main_png_path = smoke_out_path.parent / f"{smoke_out_path.stem}.focus.main.{str(label_suffix)}.png"
                main_png = _smoke_save_window_png(win, main_png_path)
                shot["main_png"] = str(main_png) if isinstance(main_png, str) and main_png else None
                try:
                    win.switch_to()
                except Exception:
                    pass
                return shot

            def _smoke_write_payload(payload: dict[str, object]) -> None:
                if smoke_suite_enabled:
                    payload.setdefault("smoke_mode", smoke_mode)
                    payload["suite_steps"] = list(suite_steps)
                    payload["suite_done_steps"] = list(suite_results.keys())
                    if suite_steps:
                        idx = int(max(0, min(int(len(suite_steps)), int(suite_step_index))))
                        step = suite_steps[idx] if idx < len(suite_steps) else "done"
                        payload["suite_step"] = str(step)
                        payload["suite_step_index"] = int(idx)
                try:
                    smoke_out_path.parent.mkdir(parents=True, exist_ok=True)
                    smoke_out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                except Exception:
                    pass

            def _smoke_finish(ok: bool, error: str | None = None, *, extra: dict[str, object] | None = None) -> None:
                nonlocal done
                nonlocal suite_step_index, suite_step_started_t
                if done:
                    return

                if smoke_suite_enabled and suite_steps:
                    now = time.monotonic()
                    current_step = ""
                    if 0 <= int(suite_step_index) < len(suite_steps):
                        current_step = str(suite_steps[int(suite_step_index)])
                    step_payload: dict[str, object] = {"ok": bool(ok), "error": error}
                    if extra:
                        step_payload.update(extra)
                    try:
                        step_payload["elapsed_s"] = float(max(0.0, float(now) - float(suite_step_started_t)))
                    except Exception:
                        pass
                    if current_step:
                        suite_results[current_step] = step_payload

                    if bool(ok) and not error:
                        suite_step_index += 1
                        if int(suite_step_index) < len(suite_steps):
                            _smoke_write_payload(
                                {
                                    "ok": False,
                                    "error": None,
                                    "smoke_mode": smoke_mode,
                                    "suite_transition": True,
                                }
                            )
                            _suite_begin_step(str(suite_steps[int(suite_step_index)]), now=now)
                            return
                        extra = {"suite": dict(suite_results), "suite_steps": list(suite_steps)}
                        ok = True
                        error = None
                    else:
                        extra = {
                            "suite": dict(suite_results),
                            "suite_steps": list(suite_steps),
                            "suite_failed_step": str(current_step),
                        }

                done = True
                if smoke_result is not None:
                    smoke_result["ok"] = bool(ok)
                    smoke_result["error"] = error

                report: list[str] = []
                try:
                    report = [str(x) for x in getattr(win, "_expansion_report", [])]
                except Exception:
                    report = []
                seeds: list[int] = []
                try:
                    seeds = [int(s) & 0xFFFFFFFF for s in getattr(win, "jigsaw_seeds", [])]
                except Exception:
                    seeds = []

                payload = {
                    "ok": bool(ok),
                    "error": error,
                    "smoke_mode": smoke_mode,
                    "label": str(getattr(win, "_current_label", "")),
                    "jigsaw_seeds": seeds,
                    "report_tail": report[-20:],
                    "rez_fade_mode": int(param_store.get_int("rez.fade.mode")),
                    "dbg_use_stipple": {
                        "channel_change": getattr(win, "_dbg_last_channel_change_use_stipple", None),
                        "delta": getattr(win, "_dbg_last_struct_delta_use_stipple", None),
                        "live": getattr(win, "_dbg_last_rez_live_use_stipple", None),
                    },
                }
                if extra:
                    payload.update(extra)
                _smoke_write_payload(payload)

                try:
                    win.close()
                except Exception:
                    pass
                try:
                    pyglet.app.exit()
                except Exception:
                    pass

            def _smoke_press_expand(_dt: float) -> None:
                nonlocal pressed
                if done:
                    return
                pressed = True
                try:
                    win.on_key_press(pyglet.window.key.RIGHT, 0)
                except Exception as e:
                    _smoke_finish(False, f"smoke: Right press failed: {type(e).__name__}: {e}")

            def _smoke_poll_expand(_dt: float) -> None:
                nonlocal saw_rez
                if done:
                    return
                now = time.monotonic()
                if now >= deadline_t:
                    _smoke_finish(False, f"smoke: timeout after {smoke_timeout_s:.1f}s")
                    return

                try:
                    if bool(getattr(win, "_rez_active", False)):
                        saw_rez = True
                        return
                except Exception:
                    pass

                if not pressed:
                    return
                if not saw_rez:
                    if now - started_t < 2.0:
                        return
                    _smoke_finish(False, "smoke: expansion did not start (no rez activity)")
                    return

                report: list[str] = []
                try:
                    report = [str(x) for x in getattr(win, "_expansion_report", [])]
                except Exception:
                    report = []
                if any("Rez failed" in ln for ln in report):
                    _smoke_finish(False, "smoke: rez failed (worker exited)")
                    return

                seeds: list[int] = []
                try:
                    seeds = [int(s) & 0xFFFFFFFF for s in getattr(win, "jigsaw_seeds", [])]
                except Exception:
                    seeds = []
                if len(seeds) < 1:
                    _smoke_finish(False, "smoke: expected pool depth >= 1 after expansion")
                    return

                main_region = _smoke_capture_region_px(win, trim_sidebar=True)

                sig = _smoke_capture_window_signature(
                    win,
                    region_x_px=int(main_region[0]),
                    region_y_px=int(main_region[1]),
                    region_w_px=int(main_region[2]),
                    region_h_px=int(main_region[3]),
                )
                try:
                    smoke_out_path.parent.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass
                png_path = smoke_out_path.parent / f"{smoke_out_path.stem}.expand.main.png"
                png = _smoke_save_window_png(win, png_path)
                try:
                    win.switch_to()
                except Exception:
                    pass

                shot: dict[str, object] = {
                    "main_png": str(png) if isinstance(png, str) and png else None,
                    "main_signature": sig,
                }
                if "error" in sig:
                    _smoke_finish(False, "smoke: expansion screenshot signature capture failed", extra={"expand_screenshot": shot})
                    return
                if not png:
                    _smoke_finish(False, "smoke: expansion screenshot PNG capture failed", extra={"expand_screenshot": shot})
                    return

                _smoke_finish(True, None, extra={"expand_screenshot": shot})

            def _smoke_get_second_view_window() -> object | None:
                second = getattr(win, "_second_viewport_window", None)
                if second is not None:
                    return second
                try:
                    secondary_id = getattr(win, "_secondary_viewport_id", None)
                    registry = getattr(win, "_viewport_windows", None)
                    if secondary_id is None or registry is None:
                        return None
                    get_fn = getattr(registry, "get", None)
                    if not callable(get_fn):
                        return None
                    return get_fn(int(secondary_id))
                except Exception:
                    return None

            def _smoke_open_second_view(_dt: float) -> None:
                nonlocal opened_second_view
                if done:
                    return

                try:
                    win._toggle_second_viewport_window()
                except Exception as e:
                    _smoke_finish(False, f"smoke: second viewport open failed: {type(e).__name__}: {e}")
                    return
                opened_second_view = bool(_smoke_get_second_view_window() is not None)
                if not opened_second_view:
                    _smoke_finish(False, "smoke: second viewport did not open")

            def _smoke_poll_second_view_fx(_dt: float) -> None:
                nonlocal second_fx_capture_attempts
                nonlocal second_fx_capture_started_t
                nonlocal second_fx_info
                if done:
                    return
                now = time.monotonic()
                if now >= deadline_t:
                    _smoke_finish(
                        False,
                        f"smoke: timeout after {smoke_timeout_s:.1f}s",
                        extra={"opened_second_view": bool(opened_second_view), "second_fx": second_fx_info},
                    )
                    return
                if not opened_second_view:
                    return

                second = None
                try:
                    second = _smoke_get_second_view_window()
                except Exception:
                    second = None
                if second is None:
                    _smoke_finish(
                        False,
                        "smoke: second viewport closed before FX validation",
                        extra={"opened_second_view": bool(opened_second_view), "second_fx": second_fx_info},
                    )
                    return

                world_draws = int(getattr(second, "_fx_parity_world_draws", 0))
                post_fx_draws = int(getattr(second, "_fx_parity_post_fx_draws", 0))
                last_err = getattr(second, "_fx_parity_last_error", None)
                main_ssao_prog = _gl_uint_value(getattr(win, "_ssao_prog", 0))
                second_ssao_prog = _gl_uint_value(getattr(second, "_ssao_prog", 0))
                main_vignette_prog = _gl_uint_value(getattr(win, "_ender_vignette_prog", 0))
                second_vignette_prog = _gl_uint_value(getattr(second, "_ender_vignette_prog", 0))

                second_fx_info = {
                    "world_draws": int(world_draws),
                    "post_fx_draws": int(post_fx_draws),
                    "last_error": last_err,
                    "main_ssao_prog": int(main_ssao_prog),
                    "second_ssao_prog": int(second_ssao_prog),
                    "main_vignette_prog": int(main_vignette_prog),
                    "second_vignette_prog": int(second_vignette_prog),
                }

                if isinstance(last_err, str) and last_err.strip():
                    _smoke_finish(
                        False,
                        f"smoke: second viewport draw error: {last_err}",
                        extra={"opened_second_view": bool(opened_second_view), "second_fx": second_fx_info},
                    )
                    return

                if world_draws < 1 or post_fx_draws < 1:
                    return

                if bool(main_ssao_prog > 0) != bool(second_ssao_prog > 0):
                    _smoke_finish(
                        False,
                        "smoke: SSAO program readiness mismatch between main and second viewport",
                        extra={"opened_second_view": bool(opened_second_view), "second_fx": second_fx_info},
                    )
                    return
                if bool(main_vignette_prog > 0) != bool(second_vignette_prog > 0):
                    _smoke_finish(
                        False,
                        "smoke: vignette program readiness mismatch between main and second viewport",
                        extra={"opened_second_view": bool(opened_second_view), "second_fx": second_fx_info},
                    )
                    return

                if second_fx_capture_started_t is None:
                    second_fx_capture_started_t = float(now)
                second_fx_capture_attempts += 1
                capture_elapsed_s = max(0.0, float(now) - float(second_fx_capture_started_t))

                main_region = _smoke_capture_region_px(win, trim_sidebar=True)
                main_sig = _smoke_capture_window_signature(
                    win,
                    region_x_px=int(main_region[0]),
                    region_y_px=int(main_region[1]),
                    region_w_px=int(main_region[2]),
                    region_h_px=int(main_region[3]),
                )

                second_region = _smoke_capture_region_px(second, trim_sidebar=False)
                second_sig = _smoke_capture_window_signature(
                    second,
                    region_x_px=int(second_region[0]),
                    region_y_px=int(second_region[1]),
                    region_w_px=int(second_region[2]),
                    region_h_px=int(second_region[3]),
                )

                screenshot_compare: dict[str, object] = {}
                main_hash = str(main_sig.get("dhash64", "") or "").strip()
                second_hash = str(second_sig.get("dhash64", "") or "").strip()
                main_luma = 0.0
                second_luma = 0.0
                if main_hash and second_hash:
                    screenshot_compare["dhash_hamming"] = int(_smoke_hex_hamming_distance(main_hash, second_hash))
                try:
                    main_luma = float(main_sig.get("mean_luma", 0.0))
                    second_luma = float(second_sig.get("mean_luma", 0.0))
                    screenshot_compare["mean_luma_delta"] = float(abs(second_luma - main_luma))
                    screenshot_compare["mean_luma_ratio"] = (
                        float(second_luma / main_luma) if abs(main_luma) > 1e-6 else None
                    )
                except Exception:
                    pass

                second_fx_info["viewport_screenshots"] = {
                    "main_png": None,
                    "second_png": None,
                    "main_signature": main_sig,
                    "second_signature": second_sig,
                    "comparison": screenshot_compare,
                    "capture_attempts": int(second_fx_capture_attempts),
                    "capture_elapsed_s": float(capture_elapsed_s),
                }

                if "error" in main_sig or "error" in second_sig:
                    _smoke_finish(
                        False,
                        "smoke: viewport screenshot signature capture failed",
                        extra={"opened_second_view": bool(opened_second_view), "second_fx": second_fx_info},
                    )
                    return

                if main_luma < float(second_fx_min_luma) or second_luma < float(second_fx_min_luma):
                    if capture_elapsed_s < float(second_fx_capture_grace_s):
                        return
                    _smoke_finish(
                        False,
                        (
                            "smoke: viewport screenshot remained dark after grace "
                            f"(main_luma={main_luma:.3f}, second_luma={second_luma:.3f})"
                        ),
                        extra={"opened_second_view": bool(opened_second_view), "second_fx": second_fx_info},
                    )
                    return

                main_png_path = smoke_out_path.parent / f"{smoke_out_path.stem}.main.png"
                second_png_path = smoke_out_path.parent / f"{smoke_out_path.stem}.second.png"
                main_png = _smoke_save_window_png(win, main_png_path)
                second_png = _smoke_save_window_png(second, second_png_path)
                try:
                    win.switch_to()
                except Exception:
                    pass
                second_fx_info["viewport_screenshots"]["main_png"] = (
                    str(main_png) if isinstance(main_png, str) and main_png else None
                )
                second_fx_info["viewport_screenshots"]["second_png"] = (
                    str(second_png) if isinstance(second_png, str) and second_png else None
                )
                if not main_png or not second_png:
                    _smoke_finish(
                        False,
                        "smoke: viewport screenshot PNG capture failed",
                        extra={"opened_second_view": bool(opened_second_view), "second_fx": second_fx_info},
                    )
                    return

                _smoke_finish(
                    True,
                    None,
                    extra={"opened_second_view": bool(opened_second_view), "second_fx": second_fx_info},
                )

            def _smoke_poll_frame_cap_present_stability(_dt: float) -> None:
                nonlocal frame_cap_initialized, frame_cap_prev_hz, frame_cap_started_t
                if done:
                    return
                now = time.monotonic()
                if now >= deadline_t:
                    _smoke_finish(False, f"smoke: timeout after {smoke_timeout_s:.1f}s")
                    return

                if not bool(frame_cap_initialized):
                    frame_cap_initialized = True
                    frame_cap_started_t = float(now)
                    try:
                        frame_cap_prev_hz = float(param_store.get("render.frame_cap_hz"))
                    except Exception:
                        frame_cap_prev_hz = None
                    try:
                        param_store.set("render.frame_cap_hz", float(frame_cap_target_hz))
                    except Exception:
                        pass
                    try:
                        setattr(win, "_draw_skip_cap_count", 0)
                        setattr(win, "_draw_cache_present_count", 0)
                    except Exception:
                        pass
                    return

                elapsed_s = float(now) - float(frame_cap_started_t)
                if elapsed_s < float(frame_cap_duration_s):
                    return

                try:
                    skip_count = int(getattr(win, "_draw_skip_cap_count", 0))
                except Exception:
                    skip_count = 0
                try:
                    cache_count = int(getattr(win, "_draw_cache_present_count", 0))
                except Exception:
                    cache_count = 0
                try:
                    fps_smooth = float(getattr(win, "_fps_value", 0.0))
                except Exception:
                    fps_smooth = 0.0
                info: dict[str, object] = {
                    "frame_cap_hz": float(frame_cap_target_hz),
                    "duration_s": float(frame_cap_duration_s),
                    "fps_smooth": float(fps_smooth),
                    "draw_skip_cap_count": int(skip_count),
                    "draw_cache_present_count": int(cache_count),
                    "cache_ratio": float(cache_count / skip_count) if skip_count > 0 else None,
                }

                if frame_cap_prev_hz is not None:
                    try:
                        param_store.set("render.frame_cap_hz", float(frame_cap_prev_hz))
                    except Exception:
                        pass

                if skip_count < 40:
                    _smoke_finish(
                        False,
                        "smoke: expected draw skips under frame cap",
                        extra={"frame_cap_present_stability": info},
                    )
                    return
                if cache_count < int(skip_count * 0.95):
                    _smoke_finish(
                        False,
                        "smoke: expected cached present on skipped draws under frame cap",
                        extra={"frame_cap_present_stability": info},
                    )
                    return
                _smoke_finish(True, None, extra={"frame_cap_present_stability": info})

            def _smoke_poll_build_edits(_dt: float) -> None:
                if done:
                    return
                now = time.monotonic()
                if now >= deadline_t:
                    _smoke_finish(False, f"smoke: timeout after {smoke_timeout_s:.1f}s")
                    return

                try:
                    if bool(getattr(win, "_rez_active", False)):
                        return
                except Exception:
                    pass

                cur = getattr(win, "_current_structure", None)
                if cur is None:
                    return
                before_blocks = list(getattr(cur, "blocks", ()) or ())
                before_positions: set[Vec3i] = {tuple(b.pos) for b in before_blocks}
                if len(before_positions) < 4:
                    _smoke_finish(False, "smoke: build-edit source structure too small")
                    return

                sorted_positions = sorted(before_positions, key=lambda p: (int(p[1]), int(p[2]), int(p[0])))
                remove_positions: list[Vec3i] = [tuple(p) for p in sorted_positions[:3]]

                neighbor_offsets: list[Vec3i] = [
                    (1, 0, 0),
                    (-1, 0, 0),
                    (0, 0, 1),
                    (0, 0, -1),
                    (0, 1, 0),
                    (0, -1, 0),
                ]
                place_positions: list[Vec3i] = []
                occupied: set[Vec3i] = set(before_positions)
                for src in sorted_positions:
                    if len(place_positions) >= 3:
                        break
                    sx, sy, sz = int(src[0]), int(src[1]), int(src[2])
                    for nx, ny, nz in neighbor_offsets:
                        candidate = (sx + int(nx), sy + int(ny), sz + int(nz))
                        if candidate in occupied:
                            continue
                        place_positions.append(candidate)
                        occupied.add(candidate)
                        break

                if len(place_positions) < 3:
                    _smoke_finish(False, "smoke: unable to find enough empty neighbors for build-edit test")
                    return

                place_block_id = "minecraft:stone"
                try:
                    for pos in place_positions:
                        win._build_place_block(tuple(pos), block_id=place_block_id)
                    for pos in remove_positions:
                        win._build_remove_block(tuple(pos))
                except Exception as e:
                    _smoke_finish(False, f"smoke: build-edit operations failed: {type(e).__name__}: {e}")
                    return

                after_cur = getattr(win, "_current_structure", None)
                if after_cur is None:
                    _smoke_finish(False, "smoke: structure missing after build edits")
                    return
                after_positions: set[Vec3i] = {tuple(b.pos) for b in list(getattr(after_cur, "blocks", ()) or ())}

                placed_ok: list[list[int]] = []
                for pos in place_positions:
                    tpos = tuple(pos)
                    if (tpos in after_positions) and (tpos not in before_positions):
                        placed_ok.append([int(tpos[0]), int(tpos[1]), int(tpos[2])])

                removed_ok: list[list[int]] = []
                for pos in remove_positions:
                    tpos = tuple(pos)
                    if tpos not in after_positions:
                        removed_ok.append([int(tpos[0]), int(tpos[1]), int(tpos[2])])

                build_info: dict[str, object] = {
                    "before_block_count": int(len(before_positions)),
                    "after_block_count": int(len(after_positions)),
                    "place_attempted": [[int(p[0]), int(p[1]), int(p[2])] for p in place_positions],
                    "remove_attempted": [[int(p[0]), int(p[1]), int(p[2])] for p in remove_positions],
                    "placed_ok": placed_ok,
                    "removed_ok": removed_ok,
                }

                if len(placed_ok) < 2:
                    _smoke_finish(False, "smoke: expected at least 2 successful block placements", extra={"build_edits": build_info})
                    return
                if len(removed_ok) < 2:
                    _smoke_finish(False, "smoke: expected at least 2 successful block removals", extra={"build_edits": build_info})
                    return

                _smoke_finish(True, None, extra={"build_edits": build_info})

            def _smoke_poll_real_window_build_edits(_dt: float) -> None:
                nonlocal build_os_initialized
                nonlocal build_os_action_index, build_os_action_started_t
                nonlocal build_os_undo_start_len, build_os_before_positions
                nonlocal build_os_expected_places, build_os_expected_removes
                nonlocal build_os_click_targets_by_name, build_os_actions
                nonlocal build_os_last_nudge_t
                if done:
                    return
                now = time.monotonic()
                if now >= deadline_t:
                    _smoke_finish(False, f"smoke: timeout after {smoke_timeout_s:.1f}s")
                    return

                if (not bool(window_has_key_focus(win))) and (float(now) - float(build_os_last_nudge_t) > 0.35):
                    try:
                        handoff_window_focus(win)
                    except Exception:
                        pass
                    build_os_last_nudge_t = float(now)

                try:
                    if bool(getattr(win, "_rez_active", False)):
                        return
                except Exception:
                    pass

                cur = getattr(win, "_current_structure", None)
                if cur is None:
                    return

                # First stage: compute click targets + an explicit action plan.
                if not build_os_initialized:
                    before_blocks = list(getattr(cur, "blocks", ()) or ())
                    build_os_before_positions = {tuple(b.pos) for b in before_blocks}
                    if len(build_os_before_positions) < 4:
                        _smoke_finish(False, "smoke: build-edit source structure too small")
                        return
                    try:
                        build_os_undo_start_len = int(len(getattr(win, "_build_undo", []) or []))
                    except Exception:
                        build_os_undo_start_len = 0

                    try:
                        ww = int(getattr(win, "width", 0))
                        wh = int(getattr(win, "height", 0))
                        sidebar = int(float(getattr(win, "sidebar_width", 0.0)))
                    except Exception:
                        _smoke_finish(False, "smoke: unable to read window dimensions for build-edit targeting")
                        return
                    if ww <= 140 or wh <= 140:
                        return

                    model_left = max(int(sidebar) + 40, int(sidebar) + int(float(ww - sidebar) * 0.10))
                    model_right = max(model_left + 1, int(ww) - 40)
                    model_bottom = 80
                    model_top = max(model_bottom + 1, int(wh) - 140)

                    sample_cols = 9
                    sample_rows = 7
                    xs = [
                        int(round(float(model_left) + (float(model_right - model_left) * float(i) / float(sample_cols - 1))))
                        for i in range(sample_cols)
                    ]
                    ys = [
                        int(round(float(model_bottom) + (float(model_top - model_bottom) * float(i) / float(sample_rows - 1))))
                        for i in range(sample_rows)
                    ]

                    candidates: list[dict[str, object]] = []
                    for ly in ys:
                        for lx in xs:
                            try:
                                hit = win._pick_block_hit(int(lx), int(ly))
                            except Exception:
                                hit = None
                            if hit is None:
                                continue
                            pos, n = hit
                            tpos = (int(pos[0]), int(pos[1]), int(pos[2]))
                            tn = (int(n[0]), int(n[1]), int(n[2]))
                            if tpos not in build_os_before_positions:
                                continue
                            screen = _smoke_local_to_screen(int(lx), int(ly))
                            if screen is None:
                                continue
                            place_pos = (tpos[0] + tn[0], tpos[1] + tn[1], tpos[2] + tn[2])
                            candidates.append(
                                {
                                    "local_xy": (int(lx), int(ly)),
                                    "screen_xy": (int(screen[0]), int(screen[1])),
                                    "pos": tpos,
                                    "n": tn,
                                    "place_pos": place_pos,
                                }
                            )

                    occupied: set[Vec3i] = set(build_os_before_positions)
                    place_picks: list[dict[str, object]] = []
                    remove_picks: list[dict[str, object]] = []
                    used_hit: set[Vec3i] = set()
                    used_place: set[Vec3i] = set()

                    for cand in candidates:
                        if len(place_picks) >= 2:
                            break
                        n = tuple(cand.get("n") or ())  # type: ignore[assignment]
                        if len(n) != 3 or tuple(n) == (0, 0, 0):
                            continue
                        place_pos = tuple(cand.get("place_pos") or ())  # type: ignore[assignment]
                        if len(place_pos) != 3:
                            continue
                        tplace: Vec3i = (int(place_pos[0]), int(place_pos[1]), int(place_pos[2]))
                        if tplace in occupied or tplace in used_place:
                            continue
                        pos = tuple(cand.get("pos") or ())  # type: ignore[assignment]
                        if len(pos) != 3:
                            continue
                        tpos: Vec3i = (int(pos[0]), int(pos[1]), int(pos[2]))
                        if tpos in used_hit:
                            continue
                        used_hit.add(tpos)
                        used_place.add(tplace)
                        place_picks.append(cand)

                    for cand in candidates:
                        if len(remove_picks) >= 2:
                            break
                        pos = tuple(cand.get("pos") or ())  # type: ignore[assignment]
                        if len(pos) != 3:
                            continue
                        tpos = (int(pos[0]), int(pos[1]), int(pos[2]))
                        if tpos in used_hit:
                            continue
                        used_hit.add(tpos)
                        remove_picks.append(cand)

                    if len(place_picks) < 2 or len(remove_picks) < 2:
                        _smoke_finish(False, "smoke: unable to compute build-edit click targets (no stable pick hits)")
                        return

                    build_os_expected_places = [tuple(p["place_pos"]) for p in place_picks]  # type: ignore[list-item]
                    build_os_expected_removes = [tuple(p["pos"]) for p in remove_picks]  # type: ignore[list-item]
                    build_os_click_targets_by_name = {}
                    build_os_actions = []
                    for i, cand in enumerate(place_picks[:2]):
                        gx, gy = tuple(cand["screen_xy"])  # type: ignore[misc]
                        lx, ly = tuple(cand["local_xy"])  # type: ignore[misc]
                        name = f"place{i}"
                        build_os_click_targets_by_name[name] = (int(gx), int(gy))
                        build_os_actions.append(
                            {
                                "name": name,
                                "button": "right",
                                "target": name,
                                "local_xy": [int(lx), int(ly)],
                                "expected_pos": [int(x) for x in tuple(cand["place_pos"])],  # type: ignore[misc]
                            }
                        )
                    for i, cand in enumerate(remove_picks[:2]):
                        gx, gy = tuple(cand["screen_xy"])  # type: ignore[misc]
                        lx, ly = tuple(cand["local_xy"])  # type: ignore[misc]
                        name = f"remove{i}"
                        build_os_click_targets_by_name[name] = (int(gx), int(gy))
                        build_os_actions.append(
                            {
                                "name": name,
                                "button": "left",
                                "target": name,
                                "local_xy": [int(lx), int(ly)],
                                "expected_pos": [int(x) for x in tuple(cand["pos"])],  # type: ignore[misc]
                            }
                        )

                    build_os_action_index = 0
                    build_os_action_started_t = float(now)
                    build_os_initialized = True

                # Second stage: drive actions via OS injection.
                if build_os_action_index >= len(build_os_actions):
                    after_blocks = list(getattr(getattr(win, "_current_structure", None), "blocks", ()) or ())
                    after_positions: set[Vec3i] = {tuple(b.pos) for b in after_blocks}
                    undo = list(getattr(win, "_build_undo", []) or [])
                    new_undo = undo[int(build_os_undo_start_len) :]

                    placed: list[Vec3i] = []
                    removed: list[Vec3i] = []
                    for pos, before, after, _note in new_undo:
                        tpos = (int(pos[0]), int(pos[1]), int(pos[2]))
                        if before is None and after is not None:
                            placed.append(tpos)
                        if before is not None and after is None:
                            removed.append(tpos)

                    build_info: dict[str, object] = {
                        "before_block_count": int(len(build_os_before_positions)),
                        "after_block_count": int(len(after_positions)),
                        "click_targets": {k: [int(v[0]), int(v[1])] for k, v in build_os_click_targets_by_name.items()},
                        "actions": list(build_os_actions),
                        "expected_place_positions": [[int(p[0]), int(p[1]), int(p[2])] for p in build_os_expected_places],
                        "expected_remove_positions": [[int(p[0]), int(p[1]), int(p[2])] for p in build_os_expected_removes],
                        "placed_ok": [[int(p[0]), int(p[1]), int(p[2])] for p in placed],
                        "removed_ok": [[int(p[0]), int(p[1]), int(p[2])] for p in removed],
                        "undo_new_count": int(len(new_undo)),
                    }

                    expected_places_set = {tuple(p) for p in build_os_expected_places}
                    expected_removes_set = {tuple(p) for p in build_os_expected_removes}
                    if int(len(new_undo)) != int(len(build_os_actions)):
                        _smoke_finish(
                            False,
                            "smoke: unexpected number of build edits observed from OS clicks",
                            extra={"build_edits": build_info},
                        )
                        return
                    if set(placed) != expected_places_set:
                        _smoke_finish(False, "smoke: OS-injected placements did not match expected targets", extra={"build_edits": build_info})
                        return
                    if set(removed) != expected_removes_set:
                        _smoke_finish(False, "smoke: OS-injected removals did not match expected targets", extra={"build_edits": build_info})
                        return

                    _smoke_finish(True, None, extra={"build_edits": build_info})
                    return

                action = build_os_actions[int(build_os_action_index)]
                target_name = str(action.get("target", "") or "")
                click_xy = build_os_click_targets_by_name.get(target_name)
                if click_xy is None:
                    _smoke_finish(False, f"smoke: missing click target for action {target_name!r}")
                    return

                expected_undo_len = int(build_os_undo_start_len) + int(build_os_action_index) + 1
                undo_len = 0
                try:
                    undo_len = int(len(getattr(win, "_build_undo", []) or []))
                except Exception:
                    undo_len = 0
                if undo_len >= expected_undo_len:
                    build_os_action_index += 1
                    build_os_action_started_t = float(now)
                    return
                if float(now) - float(build_os_action_started_t) > float(build_os_action_timeout_s):
                    _smoke_finish(
                        False,
                        f"smoke: OS click did not apply build edit for action {action.get('name')!r}",
                        extra={
                            "build_edits": {
                                "actions": list(build_os_actions),
                                "click_targets": {
                                    k: [int(v[0]), int(v[1])] for k, v in build_os_click_targets_by_name.items()
                                },
                                "undo_len": int(undo_len),
                                "expected_undo_len": int(expected_undo_len),
                                "dbg_last_pyglet_event": str(getattr(win, "_dbg_last_pyglet_event", "")),
                                "dbg_last_pyglet_event_t": float(getattr(win, "_dbg_last_pyglet_event_t", 0.0) or 0.0),
                            }
                        },
                    )
                    return

                _smoke_write_payload(
                    {
                        "ok": False,
                        "error": None,
                        "smoke_mode": smoke_mode,
                        "awaiting_os_click": True,
                        "pending_action": str(action.get("name", "")),
                        "click_button": str(action.get("button", "")),
                        "click_target": [int(click_xy[0]), int(click_xy[1])],
                        "undo_len": int(undo_len),
                        "expected_undo_len": int(expected_undo_len),
                        "dbg_last_pyglet_event": str(getattr(win, "_dbg_last_pyglet_event", "")),
                        "dbg_last_pyglet_event_t": float(getattr(win, "_dbg_last_pyglet_event_t", 0.0) or 0.0),
                        "build_edits_plan": {
                            "actions": list(build_os_actions),
                            "click_targets": {k: [int(v[0]), int(v[1])] for k, v in build_os_click_targets_by_name.items()},
                        },
                    }
                )

            def _smoke_poll_real_window_keys(_dt: float) -> None:
                nonlocal keys_os_initialized, keys_os_step_index, keys_os_step_started_t
                nonlocal keys_os_event_index
                nonlocal keys_os_expected, keys_os_step_states
                if done:
                    return
                now = time.monotonic()
                if now >= deadline_t:
                    _smoke_finish(False, f"smoke: timeout after {smoke_timeout_s:.1f}s")
                    return

                try:
                    if bool(getattr(win, "_rez_active", False)):
                        return
                except Exception:
                    pass

                if getattr(win, "_current_structure", None) is None and not keys_os_initialized:
                    return

                if not keys_os_initialized:
                    # Normalize selection so ArrowDown has deterministic deltas.
                    try:
                        indices = getattr(win, "_filtered_indices", None)
                        if isinstance(indices, list) and indices:
                            win._set_active_selected_index(int(indices[0]))
                        else:
                            win._set_active_selected_index(0)
                        win._update_list_labels(ensure_selection_visible=False)
                    except Exception:
                        pass
                    keys_os_expected = [
                        ("down", int(pyglet.window.key.DOWN)),
                        ("down", int(pyglet.window.key.DOWN)),
                        ("down", int(pyglet.window.key.DOWN)),
                        ("tab", int(pyglet.window.key.TAB)),
                        ("tab", int(pyglet.window.key.TAB)),
                        ("v", int(pyglet.window.key.V)),
                        ("v", int(pyglet.window.key.V)),
                    ]
                    keys_os_step_index = 0
                    keys_os_event_index = 0
                    keys_os_step_states = []
                    keys_os_step_started_t = float(now)
                    keys_os_initialized = True

                events = getattr(win, "_smoke_key_events", None)
                if not isinstance(events, list):
                    events = []

                if keys_os_step_index >= len(keys_os_expected):
                    _smoke_finish(
                        True,
                        None,
                        extra={
                            "keys": {
                                "expected": [name for name, _sym in keys_os_expected],
                                "states": list(keys_os_step_states),
                            }
                        },
                    )
                    return

                name, expected_sym = keys_os_expected[int(keys_os_step_index)]
                # Consume keypress events in-order, ignoring unrelated keys. OS-level
                # injection can be noisy (or the user might press a key mid-run),
                # but we only care that the expected sequence is delivered.
                if len(events) > int(keys_os_event_index):
                    try:
                        sym, mods, _t = events[int(keys_os_event_index)]
                    except Exception:
                        sym = None
                        mods = 0
                    keys_os_event_index += 1
                    if int(sym or -1) != int(expected_sym):
                        return
                    try:
                        sel = win._selected_list_pos()
                    except Exception:
                        sel = None
                    try:
                        ui_hidden = bool(getattr(win, "_ui_hidden", False))
                    except Exception:
                        ui_hidden = False
                    try:
                        ender = bool(getattr(win, "_ender_vision_active", False))
                    except Exception:
                        ender = False
                    keys_os_step_states.append(
                        {
                            "step": int(keys_os_step_index),
                            "key": str(name),
                            "symbol": int(expected_sym),
                            "mods": int(mods or 0),
                            "selected_pos": int(sel) if sel is not None else None,
                            "ui_hidden": bool(ui_hidden),
                            "ender_vision": bool(ender),
                        }
                    )
                    keys_os_step_index += 1
                    keys_os_step_started_t = float(now)
                    return

                if float(now) - float(keys_os_step_started_t) > float(keys_os_step_timeout_s):
                    _smoke_finish(
                        False,
                        f"smoke: OS key not delivered for step {keys_os_step_index} ({name})",
                        extra={"keys": {"expected": [n for n, _s in keys_os_expected], "events": list(events)}},
                    )
                    return

                focus_click = _smoke_compute_focus_click_target()
                payload: dict[str, object] = {
                    "ok": False,
                    "error": None,
                    "smoke_mode": smoke_mode,
                    "awaiting_os_key": True,
                    "pending_key": str(name),
                    "pending_key_symbol": int(expected_sym),
                    "key_step_index": int(keys_os_step_index),
                    "key_steps_total": int(len(keys_os_expected)),
                    # Help the pytest harness ensure the app is actually the key window before
                    # injecting keys (key routing can be flaky if another app is frontmost).
                    "key_focus_current": bool(window_has_key_focus(win)),
                }
                if focus_click is not None:
                    payload["focus_click_target"] = [int(focus_click[0]), int(focus_click[1])]
                _smoke_write_payload(payload)

            def _smoke_focus_hits_by_source() -> dict[str, int]:
                raw = getattr(win, "_focus_probe_hits_by_source", {})
                if not isinstance(raw, dict):
                    return {}
                out: dict[str, int] = {}
                for key, value in raw.items():
                    out[str(key)] = int(value)
                return out

            def _smoke_focus_key_by_source() -> dict[str, int]:
                raw = getattr(win, "_focus_key_hits_by_source", {})
                if not isinstance(raw, dict):
                    return {}
                out: dict[str, int] = {}
                for key, value in raw.items():
                    out[str(key)] = int(value)
                return out

            def _smoke_focus_key_diag_by_source() -> dict[str, dict[str, bool]]:
                raw = getattr(win, "_focus_key_diag_by_source", {})
                if not isinstance(raw, dict):
                    return {}
                out: dict[str, dict[str, bool]] = {}
                for key, value in raw.items():
                    if not isinstance(value, dict):
                        continue
                    diag: dict[str, bool] = {}
                    for dk, dv in value.items():
                        diag[str(dk)] = bool(dv)
                    out[str(key)] = diag
                return out

            def _smoke_window_diag(window_obj: object | None) -> dict[str, object]:
                diag: dict[str, object] = {"present": bool(window_obj is not None)}
                if window_obj is None:
                    return diag
                try:
                    diag["key_focus_diag"] = window_key_focus_diagnostics(window_obj)
                except Exception:
                    pass
                try:
                    wx, wy = window_obj.get_location()
                    diag["location"] = [int(wx), int(wy)]
                except Exception:
                    pass
                try:
                    ww, wh = window_obj.get_size()
                    diag["size"] = [int(ww), int(wh)]
                except Exception:
                    pass
                try:
                    vpw, vph = window_obj.get_viewport_size()
                    diag["viewport_size"] = [int(vpw), int(vph)]
                except Exception:
                    pass
                return diag

            def _smoke_window_diag_by_source() -> dict[str, dict[str, object]]:
                return {
                    "main": _smoke_window_diag(win),
                    "palette": _smoke_window_diag(getattr(win, "_palette_window", None)),
                    "debug": _smoke_window_diag(getattr(win, "_debug_window", None)),
                    "param": _smoke_window_diag(getattr(win, "_param_window", None)),
                    "viewport": _smoke_window_diag(_smoke_get_second_view_window()),
                }

            def _smoke_focus_payload(
                *,
                awaiting_click: bool,
                pending_source: str,
                click_target: tuple[int, int] | None,
                click_kind: str = "",
            ) -> dict[str, object]:
                focus_diag_current = window_key_focus_diagnostics(win)
                dwell_before_close_s_by_source: dict[str, float] = {}
                for src_key, open_t in focus_open_confirmed_t_by_source.items():
                    close_t = focus_close_requested_t_by_source.get(str(src_key))
                    if close_t is None:
                        continue
                    try:
                        dwell_before_close_s_by_source[str(src_key)] = max(0.0, float(close_t) - float(open_t))
                    except Exception:
                        continue
                payload: dict[str, object] = {
                    "smoke_mode": smoke_mode,
                    "awaiting_os_click": bool(awaiting_click),
                    "pending_source": str(pending_source),
                    "click_kind": str(click_kind or ""),
                    "focus_step_index": int(focus_step_index),
                    "focus_sources": list(focus_sources),
                    "focus_sequence_sources": list(focus_sequence_sources),
                    "focus_preopen_sources": list(focus_preopen_sources),
                    "focus_preopen_initialized": bool(focus_preopen_initialized),
                    "viewport_cycles_target": int(focus_viewport_cycles),
                    "validated_sources": list(focus_validated_sources),
                    "close_path_by_source": dict(focus_close_path_by_source),
                    "close_intent_by_source": dict(focus_close_intent_by_source),
                    "close_observed_by_source": dict(focus_close_observed_by_source),
                    "arm_timeout_s_by_source": {
                        str(src): float(_focus_probe_arm_timeout_s(str(src))) for src in focus_sources
                    },
                    "close_request_path_by_source": dict(getattr(win, "_focus_close_request_path_by_source", {})),
                    "open_dwell_target_s": float(focus_open_dwell_s),
                    "open_confirmed_t_by_source": {
                        str(k): float(v) for k, v in focus_open_confirmed_t_by_source.items()
                    },
                    "close_requested_t_by_source": {
                        str(k): float(v) for k, v in focus_close_requested_t_by_source.items()
                    },
                    "dwell_before_close_s_by_source": dwell_before_close_s_by_source,
                    "viewport_baseline_main_shot": focus_viewport_baseline_main_shot,
                    "viewport_close_main_shots": list(focus_viewport_close_main_shots),
                    "viewport_post_main_probe_shots": list(focus_viewport_post_main_probe_shots),
                    "child_close_path_used_by_source": {
                        "palette": str(focus_close_path_by_source.get("palette", "")).startswith("child_"),
                        "debug": str(focus_close_path_by_source.get("debug", "")).startswith("child_"),
                        "param": str(focus_close_path_by_source.get("param", "")).startswith("child_"),
                        "viewport": str(focus_close_path_by_source.get("viewport", "")).startswith("child_"),
                    },
                    "require_click": bool(focus_require_click),
                    "key_focus_current": bool(window_has_key_focus(win)),
                    "key_focus_diag_current": focus_diag_current,
                    "key_focus_by_source": _smoke_focus_key_by_source(),
                    "strict_key_focus_by_source": _smoke_focus_key_by_source(),
                    "key_focus_diag_by_source": _smoke_focus_key_diag_by_source(),
                    "focus_probe": {
                        "hits": int(getattr(win, "_focus_probe_hits", 0)),
                        "hits_by_source": _smoke_focus_hits_by_source(),
                        "last_kind": str(getattr(win, "_focus_probe_last_kind", "")),
                        "last_source": str(getattr(win, "_focus_probe_last_source", "")),
                    },
                    "window_diag_by_source": _smoke_window_diag_by_source(),
                }
                if smoke_mode == "real_window_click":
                    payload["awaiting_os_clicks"] = bool(awaiting_click)
                    payload["tool_windows"] = {
                        "palette": bool(getattr(win, "_palette_window", None) is not None),
                        "debug": bool(getattr(win, "_debug_window", None) is not None),
                        "param": bool(getattr(win, "_param_window", None) is not None),
                    }
                    payload["click_counts"] = {
                        "focus_probe_hits": int(getattr(win, "_focus_probe_hits", 0)),
                        "hits_by_source": _smoke_focus_hits_by_source(),
                    }
                    if click_target is not None:
                        payload["click_targets"] = {
                            "build": [int(click_target[0]), int(click_target[1])],
                            "orbit": [int(click_target[0]), int(click_target[1])],
                        }
                if click_target is not None:
                    payload["click_target"] = [int(click_target[0]), int(click_target[1])]
                return payload

            def _smoke_compute_focus_click_target() -> tuple[int, int] | None:
                try:
                    ww = int(getattr(win, "width", 0))
                    wh = int(getattr(win, "height", 0))
                    sidebar = int(max(0.0, float(getattr(win, "sidebar_width", 0.0))))
                except Exception:
                    return None
                if ww <= 120 or wh <= 120:
                    return None

                local_x = max(int(sidebar) + 140, int(float(ww) * 0.72))
                local_x = max(int(sidebar) + 20, min(int(ww) - 40, int(local_x)))
                local_y = max(48, min(int(wh) - 48, int(float(wh) * 0.58)))

                try:
                    from pyglet.libs.darwin import cocoapy

                    ns_window = getattr(win, "_nswindow", None)
                    if ns_window is not None:
                        pt = cocoapy.NSPoint(float(local_x), float(local_y))
                        screen_pt = ns_window.convertPointToScreen_(pt)
                        gx = int(round(float(screen_pt.x)))
                        gy = int(round(float(screen_pt.y)))
                        return (gx, gy)
                except Exception:
                    pass

                try:
                    wx, wy = win.get_location()
                    return (int(wx) + int(local_x), int(wy) + int(local_y))
                except Exception:
                    return None

            def _smoke_local_to_screen(local_x: int, local_y: int) -> tuple[int, int] | None:
                """Convert a window-local point (window points) to global screen coordinates."""
                try:
                    from pyglet.libs.darwin import cocoapy

                    ns_window = getattr(win, "_nswindow", None)
                    if ns_window is not None:
                        pt = cocoapy.NSPoint(float(local_x), float(local_y))
                        screen_pt = ns_window.convertPointToScreen_(pt)
                        gx = int(round(float(screen_pt.x)))
                        gy = int(round(float(screen_pt.y)))
                        return (gx, gy)
                except Exception:
                    pass

                try:
                    wx, wy = win.get_location()
                    return (int(wx) + int(local_x), int(wy) + int(local_y))
                except Exception:
                    return None

            def _smoke_close_button_target(window_obj: object | None) -> tuple[int, int] | None:
                """Return the center of the NSWindow close button in screen coordinates (AppKit)."""
                if window_obj is None:
                    return None
                try:
                    from pyglet.libs.darwin import cocoapy

                    ns_window = getattr(window_obj, "_nswindow", None)
                    if ns_window is None:
                        return None
                    close_kind = int(getattr(cocoapy, "NSWindowCloseButton", 0))
                    btn = ns_window.standardWindowButton_(close_kind)
                    if btn is None:
                        return None
                    try:
                        bounds = btn.bounds()
                        cx = float(getattr(bounds.size, "width", 0.0)) * 0.5
                        cy = float(getattr(bounds.size, "height", 0.0)) * 0.5
                    except Exception:
                        frame = btn.frame()
                        cx = float(getattr(frame.size, "width", 0.0)) * 0.5
                        cy = float(getattr(frame.size, "height", 0.0)) * 0.5

                    pt = cocoapy.NSPoint(float(cx), float(cy))
                    nil_view = getattr(cocoapy, "nil", None)
                    try:
                        win_pt = btn.convertPoint_toView_(pt, nil_view)
                    except Exception:
                        win_pt = btn.convertPoint_toView_(pt, None)
                    screen_pt = ns_window.convertPointToScreen_(win_pt)
                    gx = int(round(float(screen_pt.x)))
                    gy = int(round(float(screen_pt.y)))
                    return (gx, gy)
                except Exception:
                    return None

            def _smoke_focus_window_obj(source: str) -> object | None:
                src = str(source or "").strip().lower()
                if src == "palette":
                    obj = getattr(win, "_palette_window", None)
                    if obj is not None and not bool(is_window_alive(obj)):
                        try:
                            setattr(win, "_palette_window", None)
                        except Exception:
                            pass
                        return None
                    return obj
                if src == "debug":
                    obj = getattr(win, "_debug_window", None)
                    if obj is not None and not bool(is_window_alive(obj)):
                        try:
                            setattr(win, "_debug_window", None)
                        except Exception:
                            pass
                        return None
                    return obj
                if src == "param":
                    obj = getattr(win, "_param_window", None)
                    if obj is not None and not bool(is_window_alive(obj)):
                        try:
                            setattr(win, "_param_window", None)
                        except Exception:
                            pass
                        return None
                    return obj
                if src == "viewport":
                    return _smoke_get_second_view_window()
                return None

            def _smoke_focus_open_window(source: str) -> None:
                src = str(source or "").strip().lower()
                if src == "palette":
                    if getattr(win, "_palette_window", None) is None:
                        win._toggle_palette()
                    return
                if src == "debug":
                    if getattr(win, "_debug_window", None) is None:
                        win._toggle_debug_panel()
                    return
                if src == "param":
                    if getattr(win, "_param_window", None) is None:
                        win._toggle_param_window()
                    return
                if src == "viewport":
                    if _smoke_get_second_view_window() is None:
                        win._toggle_second_viewport_window()

            def _smoke_focus_close_window(source: str) -> str:
                src = str(source or "").strip().lower()
                return _close_focus_handoff_window(
                    source=src,
                    palette_window=getattr(win, "_palette_window", None),
                    debug_window=getattr(win, "_debug_window", None),
                    param_window=getattr(win, "_param_window", None),
                    viewport_window=_smoke_get_second_view_window(),
                    close_trigger="child_close",
                    close_palette_fallback=getattr(win, "_toggle_palette", None),
                    close_debug_fallback=getattr(win, "_toggle_debug_panel", None),
                    close_param_fallback=getattr(win, "_toggle_param_window", None),
                    close_viewport_fallback=getattr(win, "_toggle_second_viewport_window", None),
                    on_param_window_close=lambda target: (
                        win._on_tool_window_closed(source="param", attr_name="_param_window")
                        if getattr(win, "_param_window", None) is target
                        else None
                    ),
                )

            def _smoke_focus_prepare_preopen(now: float) -> bool:
                nonlocal focus_preopen_initialized, focus_preopen_index
                if focus_preopen_initialized:
                    return True
                if not focus_preopen_sources:
                    focus_preopen_initialized = True
                    return True
                if now - focus_preopen_started_t > 8.0:
                    _smoke_finish(
                        False,
                        "smoke: pre-open windows did not become available",
                        extra=_smoke_focus_payload(
                            awaiting_click=False,
                            pending_source=str(getattr(win, "_focus_probe_pending_source", "")),
                            click_target=focus_click_target,
                        ),
                    )
                    return False

                while focus_preopen_index < len(focus_preopen_sources):
                    src = str(focus_preopen_sources[int(focus_preopen_index)])
                    if _smoke_focus_window_obj(src) is None:
                        open_t = focus_preopen_open_t_by_source.get(src)
                        if open_t is None:
                            try:
                                _smoke_focus_open_window(src)
                            except Exception as e:
                                _smoke_finish(False, f"smoke: pre-open {src} failed: {type(e).__name__}: {e}")
                                return False
                            focus_preopen_open_t_by_source[src] = float(now)
                        elif now - float(open_t) > 4.0:
                            _smoke_finish(False, f"smoke: pre-open {src} window did not become available")
                        return False
                    focus_preopen_index += 1

                focus_preopen_initialized = True
                return True

            def _smoke_poll_focus_handoff(_dt: float) -> None:
                nonlocal focus_step_index, focus_step_phase, focus_step_started_t, focus_click_target
                nonlocal focus_last_nudge_t
                nonlocal focus_viewport_baseline_main_shot
                nonlocal focus_viewport_baseline_started_t, focus_viewport_baseline_ok
                if done:
                    return
                now = time.monotonic()
                if now >= deadline_t:
                    _smoke_finish(
                        False,
                        f"smoke: timeout after {smoke_timeout_s:.1f}s",
                        extra=_smoke_focus_payload(
                            awaiting_click=(focus_step_phase == "await_click"),
                            pending_source=str(getattr(win, "_focus_probe_pending_source", "")),
                            click_target=focus_click_target,
                        ),
                    )
                    return

                if not _smoke_focus_prepare_preopen(now):
                    return

                if focus_step_index >= len(focus_sequence_sources):
                    _smoke_finish(
                        True,
                        None,
                        extra=_smoke_focus_payload(
                            awaiting_click=False,
                            pending_source="",
                            click_target=None,
                        ),
                    )
                    return

                source = str(focus_sequence_sources[int(focus_step_index)])
                if focus_step_phase == "open_close":
                    if source == "viewport" and (not bool(focus_viewport_baseline_ok)):
                        if focus_viewport_baseline_started_t is None:
                            focus_viewport_baseline_started_t = float(now)
                        baseline_shot = _smoke_capture_main_window_shot(label_suffix="baseline")
                        baseline_sig = baseline_shot.get("main_signature")
                        focus_viewport_baseline_main_shot = baseline_shot
                        if not isinstance(baseline_sig, dict) or ("error" in baseline_sig):
                            _smoke_finish(
                                False,
                                "smoke: baseline main screenshot signature capture failed before viewport close cycle",
                                extra=_smoke_focus_payload(
                                    awaiting_click=False,
                                    pending_source=str(getattr(win, "_focus_probe_pending_source", "")),
                                    click_target=focus_click_target,
                                ),
                            )
                            return
                        try:
                            baseline_luma = float(baseline_sig.get("mean_luma", 0.0))
                        except Exception:
                            baseline_luma = 0.0
                        if baseline_luma < float(focus_viewport_min_luma):
                            elapsed_s = float(now) - float(focus_viewport_baseline_started_t)
                            if elapsed_s < float(focus_viewport_capture_grace_s):
                                return
                            _smoke_finish(
                                False,
                                (
                                    "smoke: baseline main screenshot remained dark after grace "
                                    f"(mean_luma={baseline_luma:.3f})"
                                ),
                                extra=_smoke_focus_payload(
                                    awaiting_click=False,
                                    pending_source=str(getattr(win, "_focus_probe_pending_source", "")),
                                    click_target=focus_click_target,
                                ),
                            )
                            return
                        focus_viewport_baseline_ok = True
                    try:
                        _smoke_focus_open_window(source)
                    except Exception as e:
                        _smoke_finish(False, f"smoke: opening {source} window failed: {type(e).__name__}: {e}")
                        return
                    focus_step_phase = "await_open"
                    focus_step_started_t = now
                    return

                if focus_step_phase == "await_open":
                    if _smoke_focus_window_obj(source) is None:
                        if now - focus_step_started_t > 3.0:
                            _smoke_finish(False, f"smoke: {source} window did not become available after open")
                        return
                    open_confirm_t = focus_open_confirmed_t_by_source.get(source)
                    if open_confirm_t is None:
                        open_confirm_t = float(now)
                        focus_open_confirmed_t_by_source[str(source)] = float(open_confirm_t)
                    if float(now) - float(open_confirm_t) < float(focus_open_dwell_s):
                        return
                    if focus_close_by_click:
                        target_obj = _smoke_focus_window_obj(source)
                        if target_obj is None:
                            if now - focus_step_started_t > 3.0:
                                _smoke_finish(False, f"smoke: {source} window did not become available after open")
                            return
                        target_has_focus = False
                        try:
                            target_has_focus = bool(window_has_key_focus(target_obj))
                        except Exception:
                            target_has_focus = False
                        if not target_has_focus:
                            try:
                                handoff_window_focus(target_obj)
                            except Exception:
                                pass
                            if now - focus_step_started_t > 5.0:
                                _smoke_finish(
                                    False,
                                    f"smoke: close-button target never gained key focus for {source}",
                                    extra=_smoke_focus_payload(
                                        awaiting_click=False,
                                        pending_source=str(getattr(win, "_focus_probe_pending_source", "")),
                                        click_target=focus_click_target,
                                    ),
                                )
                            return
                        try:
                            handoff_window_focus(target_obj)
                        except Exception:
                            pass
                        close_xy = _smoke_close_button_target(target_obj)
                        if close_xy is None:
                            _smoke_finish(
                                False,
                                f"smoke: unable to compute close-button click target for {source}",
                                extra=_smoke_focus_payload(
                                    awaiting_click=False,
                                    pending_source=str(getattr(win, "_focus_probe_pending_source", "")),
                                    click_target=focus_click_target,
                                ),
                            )
                            return
                        # Ask the harness to click the native close button (red traffic-light).
                        focus_click_target = close_xy
                        close_path = "os_close_button"
                        focus_close_path_by_source[str(source)] = close_path
                        focus_close_intent_by_source[str(source)] = close_path
                        focus_close_requested_t_by_source[str(source)] = float(now)
                        focus_step_phase = "await_close_click"
                        focus_step_started_t = now
                        return
                    try:
                        close_path = str(_smoke_focus_close_window(source))
                        focus_close_path_by_source[str(source)] = close_path
                        focus_close_intent_by_source[str(source)] = close_path
                        focus_close_requested_t_by_source[str(source)] = float(now)
                        win._record_tool_close_request_path(source=str(source), path=close_path)
                    except Exception as e:
                        _smoke_finish(False, f"smoke: closing {source} window failed: {type(e).__name__}: {e}")
                        return
                    if (not focus_close_by_click) and source in {"palette", "debug", "param"} and not str(
                        focus_close_path_by_source.get(source, "")
                    ).startswith("child_"):
                        _smoke_finish(
                            False,
                            f"smoke: {source} did not close via child-window close path",
                            extra=_smoke_focus_payload(
                                awaiting_click=False,
                                pending_source=str(getattr(win, "_focus_probe_pending_source", "")),
                                click_target=focus_click_target,
                            ),
                        )
                        return
                    focus_step_phase = "await_arm"
                    focus_step_started_t = now
                    return

                if focus_step_phase == "await_close_click":
                    # Request the OS-injected click (the harness drives the click); we only
                    # advance after the window actually closes.
                    _smoke_write_payload(
                        {
                            "ok": False,
                            "error": None,
                            **_smoke_focus_payload(
                                awaiting_click=True,
                                pending_source=str(source),
                                click_target=focus_click_target,
                                click_kind="close_button",
                            ),
                        }
                    )
                    try:
                        win._prune_viewport_windows()
                    except Exception:
                        pass
                    if _smoke_focus_window_obj(source) is None:
                        focus_step_phase = "await_arm"
                        focus_step_started_t = now
                        focus_click_target = None
                        return
                    if now - focus_step_started_t > 7.0:
                        _smoke_finish(
                            False,
                            f"smoke: close-button click did not close {source} window",
                            extra=_smoke_focus_payload(
                                awaiting_click=True,
                                pending_source=str(source),
                                click_target=focus_click_target,
                                click_kind="close_button",
                            ),
                        )
                        return
                    return

                if focus_step_phase == "await_arm":
                    arm_elapsed_s = max(0.0, float(now) - float(focus_step_started_t))
                    pending = str(getattr(win, "_focus_probe_pending_source", "") or "").strip().lower()

                    if pending != source:
                        if arm_elapsed_s > float(_focus_probe_arm_timeout_s(source)):
                            _smoke_finish(
                                False,
                                f"smoke: focus probe did not arm after closing {source} window",
                                extra=_smoke_focus_payload(
                                    awaiting_click=False,
                                    pending_source=pending,
                                    click_target=focus_click_target,
                                ),
                            )
                        return

                    if (not bool(window_has_key_focus(win))) and (float(now) - float(focus_last_nudge_t) > 0.50):
                        try:
                            handoff_window_focus(win)
                        except Exception:
                            pass
                        focus_last_nudge_t = float(now)

                    key_focus_by_source = _smoke_focus_key_by_source()
                    if bool(window_has_key_focus(win)):
                        win._focus_key_mark(source, diag=window_key_focus_diagnostics(win))
                        key_focus_by_source = _smoke_focus_key_by_source()
                    key_hits_for_source = int(key_focus_by_source.get(source, 0))
                    if key_hits_for_source < 1:
                        if now - focus_step_started_t > 5.0:
                            _smoke_finish(
                                False,
                                f"smoke: key focus not restored after closing {source} window",
                                extra=_smoke_focus_payload(
                                    awaiting_click=False,
                                    pending_source=pending,
                                    click_target=focus_click_target,
                                ),
                            )
                        return
                    observed_close_path = str(
                        getattr(win, "_focus_close_request_path_by_source", {}).get(source, "")
                    ).strip()
                    if observed_close_path:
                        focus_close_observed_by_source[str(source)] = observed_close_path
                    if source == "viewport" and int(focus_step_index) not in focus_viewport_close_capture_steps:
                        close_idx = int(len(focus_viewport_close_main_shots) + 1)
                        close_shot = _smoke_capture_main_window_shot(label_suffix=f"close{int(close_idx)}")
                        close_shot["cycle_index"] = int(close_idx)
                        close_sig = close_shot.get("main_signature")
                        if not isinstance(close_sig, dict) or ("error" in close_sig):
                            _smoke_finish(
                                False,
                                f"smoke: post-close main screenshot signature capture failed for viewport cycle {int(close_idx)}",
                                extra=_smoke_focus_payload(
                                    awaiting_click=False,
                                    pending_source=pending,
                                    click_target=focus_click_target,
                                ),
                            )
                            return
                        try:
                            close_luma = float(close_sig.get("mean_luma", 0.0))
                        except Exception:
                            close_luma = 0.0
                        if close_luma < float(focus_viewport_min_luma):
                            started_t = focus_viewport_close_capture_started_t_by_step.get(int(focus_step_index))
                            if started_t is None:
                                focus_viewport_close_capture_started_t_by_step[int(focus_step_index)] = float(now)
                                started_t = float(now)
                            elapsed_s = float(now) - float(started_t)
                            if elapsed_s < float(focus_viewport_capture_grace_s):
                                return
                            _smoke_finish(
                                False,
                                (
                                    "smoke: post-close main screenshot remained dark after grace "
                                    f"(cycle={int(close_idx)}, mean_luma={close_luma:.3f})"
                                ),
                                extra=_smoke_focus_payload(
                                    awaiting_click=False,
                                    pending_source=pending,
                                    click_target=focus_click_target,
                                ),
                            )
                            return
                        baseline_sig = None
                        if isinstance(focus_viewport_baseline_main_shot, dict):
                            baseline_sig = focus_viewport_baseline_main_shot.get("main_signature")
                        if isinstance(baseline_sig, dict):
                            close_shot["comparison_vs_baseline"] = _smoke_signature_compare(baseline_sig, close_sig)
                        if focus_viewport_close_main_shots:
                            prev_sig = focus_viewport_close_main_shots[-1].get("main_signature")
                            if isinstance(prev_sig, dict):
                                close_shot["comparison_vs_prev_close"] = _smoke_signature_compare(prev_sig, close_sig)
                        focus_viewport_close_main_shots.append(close_shot)
                        focus_viewport_close_capture_steps.add(int(focus_step_index))
                    if source not in focus_validated_sources:
                        focus_validated_sources.append(source)
                    if not focus_require_click:
                        # Gate-mode probe: validate arm and continue without
                        # waiting for injected OS-level clicks.
                        win._focus_probe_pending_source = ""
                        focus_step_index += 1
                        focus_step_phase = "open_close"
                        focus_step_started_t = now
                        focus_click_target = None
                        return
                    if arm_elapsed_s < float(focus_click_settle_s):
                        return
                    focus_click_target = _smoke_compute_focus_click_target()
                    if focus_click_target is None:
                        _smoke_finish(False, "smoke: unable to compute main-window click target")
                        return
                    focus_step_phase = "await_click"
                    focus_step_started_t = now
                    return

                pending = str(getattr(win, "_focus_probe_pending_source", "") or "").strip().lower()
                if focus_step_phase != "await_click":
                    _smoke_finish(False, f"smoke: invalid focus state {focus_step_phase!r}")
                    return

                hits = _smoke_focus_hits_by_source()
                _smoke_write_payload(
                    {
                        "ok": False,
                        "error": None,
                        **_smoke_focus_payload(
                            awaiting_click=True,
                            pending_source=pending,
                            click_target=focus_click_target,
                            click_kind="main_probe",
                        ),
                    }
                )
                if int(hits.get(source, 0)) >= 1:
                    if source == "viewport" and int(focus_step_index) not in focus_viewport_post_main_probe_capture_steps:
                        probe_idx = int(len(focus_viewport_post_main_probe_shots) + 1)
                        probe_shot = _smoke_capture_main_window_shot(label_suffix=f"postprobe{int(probe_idx)}")
                        probe_shot["cycle_index"] = int(probe_idx)
                        probe_sig = probe_shot.get("main_signature")
                        if not isinstance(probe_sig, dict) or ("error" in probe_sig):
                            _smoke_finish(
                                False,
                                (
                                    "smoke: post-main-probe main screenshot signature capture failed "
                                    f"for viewport cycle {int(probe_idx)}"
                                ),
                                extra=_smoke_focus_payload(
                                    awaiting_click=True,
                                    pending_source=pending,
                                    click_target=focus_click_target,
                                    click_kind="main_probe",
                                ),
                            )
                            return
                        baseline_sig = None
                        if isinstance(focus_viewport_baseline_main_shot, dict):
                            baseline_sig = focus_viewport_baseline_main_shot.get("main_signature")
                        if isinstance(baseline_sig, dict):
                            probe_shot["comparison_vs_baseline"] = _smoke_signature_compare(baseline_sig, probe_sig)
                        if len(focus_viewport_close_main_shots) >= int(probe_idx):
                            cycle_close_sig = focus_viewport_close_main_shots[int(probe_idx) - 1].get("main_signature")
                            if isinstance(cycle_close_sig, dict):
                                probe_shot["comparison_vs_cycle_close"] = _smoke_signature_compare(cycle_close_sig, probe_sig)
                        if focus_viewport_post_main_probe_shots:
                            prev_probe_sig = focus_viewport_post_main_probe_shots[-1].get("main_signature")
                            if isinstance(prev_probe_sig, dict):
                                probe_shot["comparison_vs_prev_probe"] = _smoke_signature_compare(prev_probe_sig, probe_sig)
                        focus_viewport_post_main_probe_shots.append(probe_shot)
                        focus_viewport_post_main_probe_capture_steps.add(int(focus_step_index))
                    if source not in focus_validated_sources:
                        focus_validated_sources.append(source)
                    focus_step_index += 1
                    focus_step_phase = "open_close"
                    focus_step_started_t = now
                    focus_click_target = None
                    return
                if now - focus_step_started_t > 5.0:
                    _smoke_finish(
                        False,
                        f"smoke: first post-close click not delivered to main window for {source}",
                        extra=_smoke_focus_payload(
                            awaiting_click=True,
                            pending_source=pending,
                            click_target=focus_click_target,
                        ),
                    )
                    return

                return

            def _suite_begin_step(step: str, *, now: float) -> None:
                nonlocal pressed, saw_rez
                nonlocal opened_second_view, second_fx_info, second_fx_capture_started_t, second_fx_capture_attempts
                nonlocal build_os_initialized, build_os_action_index, build_os_action_started_t
                nonlocal build_os_last_nudge_t, build_os_undo_start_len, build_os_before_positions
                nonlocal build_os_expected_places, build_os_expected_removes, build_os_click_targets_by_name, build_os_actions
                nonlocal keys_os_initialized, keys_os_step_index, keys_os_event_index, keys_os_step_started_t
                nonlocal keys_os_expected, keys_os_step_states
                nonlocal focus_preopen_initialized, focus_preopen_index, focus_preopen_started_t, focus_preopen_open_t_by_source
                nonlocal focus_step_index, focus_step_phase, focus_step_started_t, focus_click_target
                nonlocal focus_validated_sources, focus_close_path_by_source, focus_close_intent_by_source, focus_close_observed_by_source
                nonlocal focus_last_nudge_t, focus_open_confirmed_t_by_source, focus_close_requested_t_by_source
                nonlocal focus_viewport_baseline_main_shot, focus_viewport_baseline_started_t, focus_viewport_baseline_ok
                nonlocal focus_viewport_close_main_shots, focus_viewport_close_capture_steps, focus_viewport_close_capture_started_t_by_step
                nonlocal focus_viewport_post_main_probe_shots, focus_viewport_post_main_probe_capture_steps
                nonlocal suite_step_started_t
                nonlocal frame_cap_prev_hz, frame_cap_initialized, frame_cap_started_t

                suite_step_started_t = float(now)
                step_key = str(step or "").strip().lower()

                if step_key != "second_viewport_fx":
                    try:
                        if _smoke_get_second_view_window() is not None:
                            win._toggle_second_viewport_window()
                    except Exception:
                        pass

                if step_key == "expand_once":
                    pressed = False
                    saw_rez = False
                    pyglet.clock.schedule_once(_smoke_press_expand, 0.25)
                    return
                if step_key == "second_viewport_fx":
                    opened_second_view = False
                    second_fx_info = None
                    second_fx_capture_started_t = None
                    second_fx_capture_attempts = 0
                    pyglet.clock.schedule_once(_smoke_open_second_view, 0.25)
                    return
                if step_key == "frame_cap_present_stability":
                    frame_cap_initialized = False
                    frame_cap_prev_hz = None
                    frame_cap_started_t = float(now)
                    return
                if step_key == "focus_handoff":
                    focus_preopen_initialized = not bool(focus_preopen_sources)
                    focus_preopen_index = 0
                    focus_preopen_started_t = float(now)
                    focus_preopen_open_t_by_source = {}
                    focus_step_index = 0
                    focus_step_phase = "open_close"
                    focus_step_started_t = float(now)
                    focus_click_target = None
                    focus_validated_sources = []
                    focus_close_path_by_source = {}
                    focus_close_intent_by_source = {}
                    focus_close_observed_by_source = {}
                    focus_last_nudge_t = 0.0
                    focus_open_confirmed_t_by_source = {}
                    focus_close_requested_t_by_source = {}
                    focus_viewport_baseline_main_shot = None
                    focus_viewport_baseline_started_t = None
                    focus_viewport_baseline_ok = False
                    focus_viewport_close_main_shots = []
                    focus_viewport_close_capture_steps = set()
                    focus_viewport_close_capture_started_t_by_step = {}
                    focus_viewport_post_main_probe_shots = []
                    focus_viewport_post_main_probe_capture_steps = set()
                    return
                if step_key == "real_window_keys":
                    keys_os_initialized = False
                    keys_os_step_index = 0
                    keys_os_event_index = 0
                    keys_os_step_states = []
                    keys_os_expected = []
                    keys_os_step_started_t = float(now)
                    try:
                        setattr(win, "_smoke_key_events", [])
                    except Exception:
                        pass
                    return
                if step_key == "real_window_build_edits":
                    build_os_initialized = False
                    build_os_action_index = 0
                    build_os_action_started_t = float(now)
                    build_os_last_nudge_t = 0.0
                    build_os_undo_start_len = 0
                    build_os_before_positions = set()
                    build_os_expected_places = []
                    build_os_expected_removes = []
                    build_os_click_targets_by_name = {}
                    build_os_actions = []
                    return

            def _smoke_poll_suite(_dt: float) -> None:
                if done:
                    return
                if not suite_steps:
                    _smoke_finish(False, "smoke: suite has no enabled steps")
                    return
                if int(suite_step_index) >= len(suite_steps):
                    return

                step = str(suite_steps[int(suite_step_index)])
                if step == "expand_once":
                    _smoke_poll_expand(_dt)
                elif step == "second_viewport_fx":
                    _smoke_poll_second_view_fx(_dt)
                elif step == "frame_cap_present_stability":
                    _smoke_poll_frame_cap_present_stability(_dt)
                elif step == "focus_handoff":
                    _smoke_poll_focus_handoff(_dt)
                elif step == "real_window_keys":
                    _smoke_poll_real_window_keys(_dt)
                elif step == "real_window_build_edits":
                    _smoke_poll_real_window_build_edits(_dt)
                elif step == "build_edits":
                    _smoke_poll_build_edits(_dt)
                else:
                    _smoke_finish(False, f"smoke: unknown suite step {step!r}")

            if smoke_suite_enabled:
                if suite_steps:
                    _suite_begin_step(str(suite_steps[0]), now=time.monotonic())
                pyglet.clock.schedule_interval(_smoke_poll_suite, 0.05)
            elif smoke_expand_enabled:
                pyglet.clock.schedule_once(_smoke_press_expand, 0.25)
                pyglet.clock.schedule_interval(_smoke_poll_expand, 0.05)
            elif smoke_second_viewport_fx_enabled:
                pyglet.clock.schedule_once(_smoke_open_second_view, 0.25)
                pyglet.clock.schedule_interval(_smoke_poll_second_view_fx, 0.05)
            elif smoke_build_edits_enabled:
                pyglet.clock.schedule_interval(_smoke_poll_build_edits, 0.05)
            elif smoke_real_window_build_edits_enabled:
                pyglet.clock.schedule_interval(_smoke_poll_real_window_build_edits, 0.05)
            elif smoke_real_window_keys_enabled:
                pyglet.clock.schedule_interval(_smoke_poll_real_window_keys, 0.05)
            else:
                pyglet.clock.schedule_interval(_smoke_poll_focus_handoff, 0.05)

        if not smoke_enabled:
            print(
                "Controls: scroll zoom, ⌥+left-drag rotate, ⌥+middle-drag pan, R reset, Tab hide UI, D debug panel, C toggle 2nd viewport, Shift+C add viewport, Esc exits walk/search/rez"
            )
            print("Extra: E env, F frame view, O ortho, U export USDZ, N export NBT, P open folder")
            print("UI: K kValue, V ender vision, ? help")
            print("Build: B toggle, 1-9/0 hotbar, I palette, LMB break / RMB place / MMB pick (hold ⌥ for camera)")
            print("List: Up/Down, PageUp/PageDown, Home/End, / filter")

        pyglet.app.run()

        if smoke_enabled and smoke_result is not None and not bool(smoke_result.get("ok")):
            err = smoke_result.get("error") or f"smoke: failed (see {smoke_out_path})"
            raise SystemExit(str(err))
    finally:
        if zip_file is not None:
            zip_file.close()
        if texture_source is not None:
            texture_source.close()
