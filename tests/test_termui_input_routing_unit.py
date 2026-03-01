"""Mouse input routing regression tests.

Run with:
  python -m pytest -q tests/test_termui_input_routing_unit.py
"""

from __future__ import annotations

import enderterm.termui as termui_mod
from enderterm.termui import (
    _mouse_route_matches_capture,
    _mouse_route_release_capture,
    handoff_window_focus,
    _reset_keyboard_repeat_state,
    route_window_focus_keyboard,
    window_key_focus_diagnostics,
    window_has_key_focus,
    _tool_window_click_handler_result,
    _tool_window_click_overlay_mode,
    TermMouseCapture,
    TermScrollbar,
    route_term_scrollbar_drag,
    route_term_scrollbar_press,
    route_term_scrollbar_release,
    route_tool_window_click,
)
from enderterm.debug_window import create_debug_window
from enderterm.kvalue_window import create_term_param_window
from enderterm.palette_window import create_palette_window
from enderterm.datapack_viewer import (
    _adaptive_update_budget_fps,
    _apply_tool_window_close_focus_handoff,
    _close_and_clear_window_attr,
    _close_focus_handoff_window,
    _draw_guard_render_cap,
    _draw_guard_render_retry,
    _render_cap_desired_hz,
    _render_cap_interval_s,
    _render_cap_is_uncapped,
    _render_cap_mark_dirty_state,
    _render_cap_ratio_changed,
    _render_cap_refresh_hz,
    _render_cap_refresh_hz_state,
    _render_cap_schedule_step,
    _render_cap_view_changed,
    _safe_window_gl_cleanup,
    _smoke_hex_hamming_distance,
    _smoke_signature_from_rgba,
    _walk_mode_integrate_xz,
    _walk_mode_key_action,
    _walk_mode_move_direction_xz,
)


def _datapack_viewer_source() -> str:
    source_path = _close_focus_handoff_window.__code__.co_filename
    with open(source_path, "r", encoding="utf-8") as f:
        return f.read()


def _source_method_blocks(source: str, *, signature: str, window: int) -> list[str]:
    blocks: list[str] = []
    start = 0
    while True:
        idx = source.find(signature, start)
        if idx < 0:
            break
        blocks.append(source[idx : idx + int(window)])
        start = idx + len(signature)
    return blocks


def _kvalue_window_source() -> str:
    source_path = create_term_param_window.__code__.co_filename
    with open(source_path, "r", encoding="utf-8") as f:
        return f.read()


def _should_render_frame_blocks() -> list[str]:
    src = _datapack_viewer_source()
    marker = "def _should_render_frame(self, *, now_s: float) -> bool:"
    blocks: list[str] = []
    start = 0
    while True:
        idx = src.find(marker, start)
        if idx < 0:
            break
        # Keep extraction simple and stable: each method body fits well within this
        # window and includes all scheduler state-transition statements.
        blocks.append(src[idx : idx + 2200])
        start = idx + len(marker)
    return blocks


def _make_scrollbar(*, track_top: int = 4, track_rows: int = 14, visible_rows: int = 5, total_rows: int = 40, scroll_top: int = 8) -> TermScrollbar:
    sb = TermScrollbar()
    sb.update(
        track_top=int(track_top),
        track_rows=int(track_rows),
        visible_rows=int(visible_rows),
        total_rows=int(total_rows),
        scroll_top=int(scroll_top),
    )
    return sb


def _source_class_block(source: str, class_signature: str) -> str:
    start = source.find(class_signature)
    assert start >= 0
    next_class = source.find("\n        class ", start + len(class_signature))
    if next_class < 0:
        next_class = len(source)
    return source[start:next_class]


def _source_method_block(class_block: str, method_signature: str) -> str:
    start = class_block.find(method_signature)
    assert start >= 0
    next_method = class_block.find("\n            def ", start + len(method_signature))
    if next_method < 0:
        next_method = len(class_block)
    return class_block[start:next_method]


def _run_activation_cycle_without_keyboard_route(create_window) -> list[bool]:
    focus_route_calls: list[bool] = []
    old_route = termui_mod.route_window_focus_keyboard
    termui_mod.route_window_focus_keyboard = lambda **_kwargs: focus_route_calls.append(True) or True
    try:
        win = create_window()
        win.on_activate()
        win.on_deactivate()
    finally:
        termui_mod.route_window_focus_keyboard = old_route
    return focus_route_calls


def test_render_cap_interval_and_schedule_helpers() -> None:
    assert _render_cap_interval_s(0.0) == 0.0
    assert _render_cap_interval_s(-5.0) == 0.0
    assert _render_cap_interval_s(20.0) == 0.05

    wait_draw, wait_deadline = _render_cap_schedule_step(
        now_s=10.0,
        frame_cap_hz=20.0,
        next_deadline_s=10.20,
        startup_until_s=0.0,
        force_render=False,
    )
    assert wait_draw is False
    assert wait_deadline == 10.20

    due_draw, due_deadline = _render_cap_schedule_step(
        now_s=10.21,
        frame_cap_hz=20.0,
        next_deadline_s=10.20,
        startup_until_s=0.0,
        force_render=False,
    )
    assert due_draw is True
    assert due_deadline > 10.21

    forced_draw, forced_deadline = _render_cap_schedule_step(
        now_s=10.0,
        frame_cap_hz=20.0,
        next_deadline_s=10.50,
        startup_until_s=0.0,
        force_render=True,
    )
    assert forced_draw is True
    assert forced_deadline == 10.05

    startup_draw, startup_deadline = _render_cap_schedule_step(
        now_s=10.0,
        frame_cap_hz=20.0,
        next_deadline_s=10.50,
        startup_until_s=11.0,
        force_render=False,
    )
    assert startup_draw is True
    assert startup_deadline == 10.05


def test_render_cap_schedule_has_idle_quiet_period_after_forced_draw() -> None:
    forced_draw, next_deadline = _render_cap_schedule_step(
        now_s=5.0,
        frame_cap_hz=2.0,
        next_deadline_s=12.0,
        startup_until_s=0.0,
        force_render=True,
    )
    assert forced_draw is True
    assert next_deadline == 5.5

    wait_draw, wait_deadline = _render_cap_schedule_step(
        now_s=5.2,
        frame_cap_hz=2.0,
        next_deadline_s=next_deadline,
        startup_until_s=0.0,
        force_render=False,
    )
    assert wait_draw is False
    assert wait_deadline == 5.5

    due_draw, due_deadline = _render_cap_schedule_step(
        now_s=5.51,
        frame_cap_hz=2.0,
        next_deadline_s=wait_deadline,
        startup_until_s=0.0,
        force_render=False,
    )
    assert due_draw is True
    assert due_deadline > 5.51


def test_render_cap_schedule_respects_low_idle_cadence() -> None:
    # 1 Hz cap should not devolve into a high-frequency redraw storm while idle.
    next_deadline = 0.0
    draws = 0
    step_s = 1.0 / 60.0
    t = 0.0
    while t <= 2.0 + 1e-9:
        should_draw, next_deadline = _render_cap_schedule_step(
            now_s=t,
            frame_cap_hz=1.0,
            next_deadline_s=next_deadline,
            startup_until_s=0.0,
            force_render=False,
        )
        if should_draw:
            draws += 1
        t += step_s
    assert 2 <= draws <= 3


def test_walk_mode_key_action_transitions_and_precedence() -> None:
    toggle = 76
    escape = 27
    cmd_mod = 0x100 | 0x200
    scaffold = {87, 65, 83, 68, 32, 340, 344}

    assert _walk_mode_key_action(
        active=False,
        symbol=toggle,
        modifiers=0,
        toggle_symbol=toggle,
        escape_symbol=escape,
        cmd_mod=cmd_mod,
        scaffold_symbols=set(scaffold),
    ) == "toggle_on"
    assert _walk_mode_key_action(
        active=True,
        symbol=toggle,
        modifiers=0,
        toggle_symbol=toggle,
        escape_symbol=escape,
        cmd_mod=cmd_mod,
        scaffold_symbols=set(scaffold),
    ) == "toggle_off"
    assert _walk_mode_key_action(
        active=True,
        symbol=escape,
        modifiers=0,
        toggle_symbol=toggle,
        escape_symbol=escape,
        cmd_mod=cmd_mod,
        scaffold_symbols=set(scaffold),
    ) == "exit_escape"
    assert _walk_mode_key_action(
        active=True,
        symbol=87,
        modifiers=0,
        toggle_symbol=toggle,
        escape_symbol=escape,
        cmd_mod=cmd_mod,
        scaffold_symbols=set(scaffold),
    ) == "consume_scaffold"
    assert _walk_mode_key_action(
        active=False,
        symbol=87,
        modifiers=0,
        toggle_symbol=toggle,
        escape_symbol=escape,
        cmd_mod=cmd_mod,
        scaffold_symbols=set(scaffold),
    ) == "pass"
    assert _walk_mode_key_action(
        active=False,
        symbol=toggle,
        modifiers=cmd_mod,
        toggle_symbol=toggle,
        escape_symbol=escape,
        cmd_mod=cmd_mod,
        scaffold_symbols=set(scaffold),
    ) == "pass"

    # When the toggle key is also a walk-movement key (W), active walk mode should
    # keep treating it as movement input instead of toggling off.
    toggle_as_move = 87
    assert _walk_mode_key_action(
        active=False,
        symbol=toggle_as_move,
        modifiers=0,
        toggle_symbol=toggle_as_move,
        escape_symbol=escape,
        cmd_mod=cmd_mod,
        scaffold_symbols=set(scaffold),
    ) == "toggle_on"
    assert _walk_mode_key_action(
        active=True,
        symbol=toggle_as_move,
        modifiers=0,
        toggle_symbol=toggle_as_move,
        escape_symbol=escape,
        cmd_mod=cmd_mod,
        scaffold_symbols=set(scaffold),
    ) == "consume_scaffold"


def test_walk_mode_move_direction_xz_follows_yaw_and_normalizes_diagonals() -> None:
    keys = {"w": 87, "a": 65, "s": 83, "d": 68}
    dx, dz = _walk_mode_move_direction_xz(
        pressed_symbols={keys["w"]},
        yaw_deg=0.0,
        key_w=keys["w"],
        key_a=keys["a"],
        key_s=keys["s"],
        key_d=keys["d"],
    )
    assert abs(dx - 0.0) <= 1e-9
    assert abs(dz + 1.0) <= 1e-9

    dx_yaw_90, dz_yaw_90 = _walk_mode_move_direction_xz(
        pressed_symbols={keys["w"]},
        yaw_deg=90.0,
        key_w=keys["w"],
        key_a=keys["a"],
        key_s=keys["s"],
        key_d=keys["d"],
    )
    assert abs(dx_yaw_90 - 1.0) <= 1e-9
    assert abs(dz_yaw_90 - 0.0) <= 1e-9

    diag_x, diag_z = _walk_mode_move_direction_xz(
        pressed_symbols={keys["w"], keys["d"]},
        yaw_deg=0.0,
        key_w=keys["w"],
        key_a=keys["a"],
        key_s=keys["s"],
        key_d=keys["d"],
    )
    diag_mag = 2.0**-0.5
    assert abs(diag_x - diag_mag) <= 1e-9
    assert abs(diag_z + diag_mag) <= 1e-9

    idle_x, idle_z = _walk_mode_move_direction_xz(
        pressed_symbols=set(),
        yaw_deg=15.0,
        key_w=keys["w"],
        key_a=keys["a"],
        key_s=keys["s"],
        key_d=keys["d"],
    )
    assert idle_x == 0.0
    assert idle_z == 0.0


def test_walk_mode_integrate_xz_uses_fixed_step_and_stable_carry() -> None:
    keys = {"w": 87, "a": 65, "s": 83, "d": 68}
    move_dx, move_dz, carry = _walk_mode_integrate_xz(
        pressed_symbols={keys["w"]},
        yaw_deg=0.0,
        frame_dt_s=0.20,
        carry_dt_s=0.0,
        fixed_dt_s=0.05,
        max_steps=2,
        speed_u_per_s=4.0,
        key_w=keys["w"],
        key_a=keys["a"],
        key_s=keys["s"],
        key_d=keys["d"],
    )
    assert abs(move_dx - 0.0) <= 1e-9
    assert abs(move_dz + 0.4) <= 1e-9
    assert abs(carry - 0.0) <= 1e-9

    # No movement input still consumes fixed-step budget (no catch-up burst later).
    idle_dx, idle_dz, idle_carry = _walk_mode_integrate_xz(
        pressed_symbols=set(),
        yaw_deg=0.0,
        frame_dt_s=0.20,
        carry_dt_s=0.0,
        fixed_dt_s=0.05,
        max_steps=2,
        speed_u_per_s=4.0,
        key_w=keys["w"],
        key_a=keys["a"],
        key_s=keys["s"],
        key_d=keys["d"],
    )
    assert idle_dx == 0.0
    assert idle_dz == 0.0
    assert abs(idle_carry - 0.0) <= 1e-9

    dx2, dz2, carry2 = _walk_mode_integrate_xz(
        pressed_symbols={keys["d"]},
        yaw_deg=0.0,
        frame_dt_s=0.03,
        carry_dt_s=0.025,
        fixed_dt_s=0.05,
        max_steps=8,
        speed_u_per_s=4.0,
        key_w=keys["w"],
        key_a=keys["a"],
        key_s=keys["s"],
        key_d=keys["d"],
    )
    assert abs(dx2 - 0.2) <= 1e-9
    assert abs(dz2 - 0.0) <= 1e-9
    assert abs(carry2 - 0.005) <= 1e-9


def test_render_cap_schedule_catchup_step_is_bounded() -> None:
    # Slightly-late frame should clamp catchup to four steps, not burst through all missed intervals.
    should_draw, next_deadline = _render_cap_schedule_step(
        now_s=99.31,
        frame_cap_hz=10.0,
        next_deadline_s=99.0,
        startup_until_s=0.0,
        force_render=False,
    )
    assert should_draw is True
    assert abs(next_deadline - 99.4) < 1e-9

    # Extremely stale deadlines should resynchronize to now + interval.
    should_draw_stale, next_deadline_stale = _render_cap_schedule_step(
        now_s=104.0,
        frame_cap_hz=10.0,
        next_deadline_s=99.0,
        startup_until_s=0.0,
        force_render=False,
    )
    assert should_draw_stale is True
    assert abs(next_deadline_stale - 104.1) < 1e-9


def test_render_cap_jitter_guards_avoid_low_cap_bypass() -> None:
    # +/-1 viewport px oscillation should not force redraws.
    assert _render_cap_view_changed((1600, 900), (1601, 900)) is False
    assert _render_cap_view_changed((1600, 900), (1600, 901)) is False
    assert _render_cap_view_changed((1600, 900), (1602, 900)) is True
    # Tiny pixel-ratio jitter should not force redraws.
    assert _render_cap_ratio_changed(2.0, 2.01) is False
    assert _render_cap_ratio_changed(2.0, 2.06) is True


def test_render_cap_refresh_hz_helper_updates_owner_state_from_param_store() -> None:
    class _Store:
        def get_int(self, key: str) -> int:
            assert key == "render.frame_cap_hz"
            return 60

    owner = type("Owner", (), {"_render_cap_hz": 30, "_render_cap_force_next": False})()
    _render_cap_refresh_hz(owner, param_store=_Store())

    assert owner._render_cap_hz == 60
    # Cap transition should force one draw opportunity.
    assert owner._render_cap_force_next is True


def test_render_cap_refresh_hz_helper_handles_invalid_or_unchanged_values() -> None:
    class _StoreBad:
        def get_int(self, _key: str) -> int:
            raise RuntimeError("boom")

    class _StoreSame:
        def get_int(self, _key: str) -> int:
            return 24

    owner_bad = type("OwnerBad", (), {"_render_cap_hz": 24, "_render_cap_force_next": False})()
    _render_cap_refresh_hz(owner_bad, param_store=_StoreBad())
    assert owner_bad._render_cap_hz == 0
    assert owner_bad._render_cap_force_next is True

    owner_same = type("OwnerSame", (), {"_render_cap_hz": 24, "_render_cap_force_next": True})()
    _render_cap_refresh_hz(owner_same, param_store=_StoreSame())
    assert owner_same._render_cap_hz == 24
    assert owner_same._render_cap_force_next is True


def test_render_cap_desired_hz_helper_clamps_and_falls_back() -> None:
    class _StoreNeg:
        def get_int(self, _key: str) -> int:
            return -8

    class _StoreOk:
        def get_int(self, _key: str) -> int:
            return 90

    class _StoreErr:
        def get_int(self, _key: str) -> int:
            raise ValueError("bad")

    assert _render_cap_desired_hz(_StoreNeg()) == 0
    assert _render_cap_desired_hz(_StoreOk()) == 90
    assert _render_cap_desired_hz(_StoreErr()) == 0


def test_render_cap_is_uncapped_helper() -> None:
    assert _render_cap_is_uncapped(0.0) is True
    assert _render_cap_is_uncapped(-5.0) is True
    assert _render_cap_is_uncapped(0.25) is False
    assert _render_cap_is_uncapped(0.9) is False
    assert _render_cap_is_uncapped(1.0) is False
    assert _render_cap_is_uncapped(60.0) is False


def test_mark_render_dirty_paths_can_share_force_render_transition() -> None:
    class _MainLike:
        def __init__(self) -> None:
            self._render_cap_force_next = False
            self.invalid = False

        def mark_dirty(self) -> None:
            self._render_cap_force_next = _render_cap_mark_dirty_state(force_draw=bool(self._render_cap_force_next))
            self.invalid = True

    class _CompanionLike:
        def __init__(self, *, should_render: bool) -> None:
            self._render_cap_force_next = False
            self.invalid = False
            self._should_render = bool(should_render)
            self.calls = 0

        def _should_render_frame(self, *, now_s: float) -> bool:
            self.calls += 1
            _ = float(now_s)
            return bool(self._should_render)

        def mark_dirty(self) -> None:
            self._render_cap_force_next = _render_cap_mark_dirty_state(force_draw=bool(self._render_cap_force_next))
            if self._should_render_frame(now_s=0.0):
                self.invalid = True

    main = _MainLike()
    main.mark_dirty()
    assert main._render_cap_force_next is True
    assert main.invalid is True

    companion_skip = _CompanionLike(should_render=False)
    companion_skip.mark_dirty()
    assert companion_skip._render_cap_force_next is True
    assert companion_skip.invalid is False
    assert companion_skip.calls == 1

    companion_draw = _CompanionLike(should_render=True)
    companion_draw.mark_dirty()
    assert companion_draw._render_cap_force_next is True
    assert companion_draw.invalid is True
    assert companion_draw.calls == 1


def test_smoke_hex_hamming_distance_handles_valid_and_invalid_inputs() -> None:
    assert _smoke_hex_hamming_distance("0f", "f0") == 8
    assert _smoke_hex_hamming_distance("ff", "ff") == 0
    assert _smoke_hex_hamming_distance("not-hex", "ff") == -1
    assert _smoke_hex_hamming_distance("ff", "also-bad") == -1


def test_smoke_signature_from_rgba_empty_buffer_fallback() -> None:
    sig = _smoke_signature_from_rgba(b"", width=4, height=0)
    assert sig["width"] == 4
    assert sig["height"] == 1
    assert sig["dhash64"] == ("0" * 16)
    assert float(sig["mean_luma"]) == 0.0


def test_smoke_signature_from_rgba_solid_row_and_clamped_dims() -> None:
    # width=-2,height=1 clamps to 1x1; one red pixel.
    sig = _smoke_signature_from_rgba(bytes([255, 0, 0, 255]), width=-2, height=1)
    assert sig["width"] == 1
    assert sig["height"] == 1
    assert sig["dhash64"] == ("0" * 16)
    mean_rgb = sig["mean_rgb"]
    assert isinstance(mean_rgb, list) and len(mean_rgb) == 3
    assert mean_rgb[0] == 255.0
    assert mean_rgb[1] == 0.0
    assert mean_rgb[2] == 0.0
    assert abs(float(sig["mean_luma"]) - 76.245) < 1e-6


def test_smoke_signature_from_rgba_truncated_pixels_fall_back_to_zero_sample() -> None:
    # Deliberately short buffer for a nominal 2x1 image; second sample should
    # safely fall back to zero during grid reads.
    sig = _smoke_signature_from_rgba(bytes([10, 20, 30]), width=2, height=1)
    assert sig["width"] == 2
    assert sig["height"] == 1
    assert isinstance(sig["dhash64"], str) and len(sig["dhash64"]) == 16
    assert float(sig["mean_luma"]) >= 0.0


def test_draw_guards_cover_skip_and_retry_callbacks_behaviorally() -> None:
    class _RenderOwner:
        def __init__(self, *, should_draw: bool) -> None:
            self.should_draw = bool(should_draw)

        def _should_render_frame(self, *, now_s: float) -> bool:
            _ = float(now_s)
            return bool(self.should_draw)

    skipped = {"n": 0}
    assert _draw_guard_render_cap(
        _RenderOwner(should_draw=False),
        now_s=1.0,
        on_skip=lambda: skipped.__setitem__("n", skipped["n"] + 1),
    ) is False
    assert skipped["n"] == 1

    retried = {"n": 0}
    assert _draw_guard_render_retry(
        type("RetryOwner", (), {"_viewer_error_kind": "render", "_viewer_error_retry_after_t": 5.0})(),
        now_s=1.0,
        on_retry=lambda: retried.__setitem__("n", retried["n"] + 1),
    ) is False
    assert retried["n"] == 1


def test_render_cap_dirty_and_refresh_state_helpers() -> None:
    # Dirty-mark transition always forces one upcoming draw.
    assert _render_cap_mark_dirty_state(force_draw=False) is True
    assert _render_cap_mark_dirty_state(force_draw=True) is True

    same_hz, same_force = _render_cap_refresh_hz_state(current_hz=30, desired_hz=30, force_draw=False)
    assert same_hz == 30
    assert same_force is False

    changed_hz, changed_force = _render_cap_refresh_hz_state(current_hz=30, desired_hz=60, force_draw=False)
    assert changed_hz == 60
    assert changed_force is True

    # Existing force-draw state remains set when refreshing with unchanged cap.
    keep_hz, keep_force = _render_cap_refresh_hz_state(current_hz=60, desired_hz=60, force_draw=True)
    assert keep_hz == 60
    assert keep_force is True


def test_adaptive_update_budget_fps_uses_tick_signal() -> None:
    # 60 Hz dt with degraded tick-fps smoothing should scale budgets using the
    # update signal, independent from render FPS.
    fps = _adaptive_update_budget_fps(dt_s=(1.0 / 60.0), tick_fps_smooth=30.0)
    assert fps == 30.0

    # If smoothed tick-fps is unavailable, fallback to the instantaneous tick dt.
    fps_fallback = _adaptive_update_budget_fps(dt_s=(1.0 / 50.0), tick_fps_smooth=0.0)
    assert abs(fps_fallback - 50.0) < 1e-6


def test_draw_guard_render_cap_helper_calls_skip_once() -> None:
    class _Owner:
        def __init__(self, *, should_draw: bool) -> None:
            self.should_draw = bool(should_draw)
            self.calls: list[float] = []

        def _should_render_frame(self, *, now_s: float) -> bool:
            self.calls.append(float(now_s))
            return bool(self.should_draw)

    owner = _Owner(should_draw=True)
    skipped = {"n": 0}
    assert _draw_guard_render_cap(owner, now_s=4.0, on_skip=lambda: skipped.__setitem__("n", skipped["n"] + 1)) is True
    assert owner.calls == [4.0]
    assert skipped["n"] == 0

    owner2 = _Owner(should_draw=False)
    assert _draw_guard_render_cap(owner2, now_s=5.0, on_skip=lambda: skipped.__setitem__("n", skipped["n"] + 1)) is False
    assert owner2.calls == [5.0]
    assert skipped["n"] == 1


def test_draw_guard_render_retry_helper_honors_retry_window() -> None:
    class _Owner:
        def __init__(self, kind: str, retry_after: float) -> None:
            self._viewer_error_kind = kind
            self._viewer_error_retry_after_t = retry_after

    retried = {"n": 0}
    on_retry = lambda: retried.__setitem__("n", retried["n"] + 1)

    assert _draw_guard_render_retry(_Owner("none", 10.0), now_s=1.0, on_retry=on_retry) is True
    assert retried["n"] == 0
    assert _draw_guard_render_retry(_Owner("render", 10.0), now_s=1.0, on_retry=on_retry) is False
    assert retried["n"] == 1
    assert _draw_guard_render_retry(_Owner("render", 1.0), now_s=1.0, on_retry=on_retry) is True
    assert retried["n"] == 1


def test_single_click_thumb_press_keeps_selection_path() -> None:
    capture = TermMouseCapture()
    sb = _make_scrollbar()
    route = route_term_scrollbar_press(
        capture=capture,
        context_id="window-a:list",
        target_id="scrollbar",
        scrollbar=sb,
        row=int(sb.thumb_top),
        current_scroll=8.0,
    )
    selection_would_run = not route.consumed

    assert route.drag_started is True
    assert route.new_scroll is None
    # Thumb presses should not swallow first-click list selection.
    assert selection_would_run is True
    assert capture.matches(context_id="window-a:list", target_id="scrollbar", button=1) is True


def test_track_press_pages_and_consumes() -> None:
    capture = TermMouseCapture()
    sb = _make_scrollbar(scroll_top=10)
    route = route_term_scrollbar_press(
        capture=capture,
        context_id="window-a:list",
        target_id="scrollbar",
        scrollbar=sb,
        row=int(sb.thumb_top + sb.thumb_rows + 1),
        current_scroll=10.0,
    )

    assert route.drag_started is False
    assert route.consumed is True
    assert route.new_scroll is not None
    assert route.new_scroll > 10.0
    assert capture.active is False


def test_thumb_press_can_be_consumed_for_scrollbar_column() -> None:
    capture = TermMouseCapture()
    sb = _make_scrollbar(scroll_top=8)
    route = route_term_scrollbar_press(
        capture=capture,
        context_id="window-a:list",
        target_id="scrollbar",
        scrollbar=sb,
        row=int(sb.thumb_top),
        current_scroll=8.0,
        consume_thumb_press=True,
    )

    assert route.drag_started is True
    assert route.new_scroll is None
    # Sidebar scrollbar presses should not fall through to list-row selection.
    assert route.consumed is True
    assert capture.matches(context_id="window-a:list", target_id="scrollbar", button=1) is True

def test_palette_and_sidebar_scrollbar_routes_are_consistent() -> None:
    capture = TermMouseCapture()
    sidebar = _make_scrollbar(scroll_top=8)
    palette = _make_scrollbar(scroll_top=8)

    sidebar_route = route_term_scrollbar_press(
        capture=capture,
        context_id="sidebar:list",
        target_id="scrollbar",
        scrollbar=sidebar,
        row=int(sidebar.thumb_top + sidebar.thumb_rows + 1),
        current_scroll=8.0,
    )
    palette_route = route_term_scrollbar_press(
        capture=capture,
        context_id="palette:list",
        target_id="scrollbar",
        scrollbar=palette,
        row=int(palette.thumb_top + palette.thumb_rows + 1),
        current_scroll=8.0,
    )

    assert sidebar_route.consumed is True
    assert palette_route.consumed is True
    assert sidebar_route.new_scroll is not None
    assert palette_route.new_scroll is not None
    assert sidebar_route.new_scroll == palette_route.new_scroll


def test_palette_scrollbar_drag_release_shared_path() -> None:
    capture = TermMouseCapture()
    sb = _make_scrollbar(scroll_top=6)
    press = route_term_scrollbar_press(
        capture=capture,
        context_id="palette:list",
        target_id="scrollbar",
        scrollbar=sb,
        row=int(sb.thumb_top),
        current_scroll=6.0,
        consume_thumb_press=True,
    )
    assert press.drag_started is True
    assert press.consumed is True

    drag = route_term_scrollbar_drag(
        capture=capture,
        context_id="palette:list",
        target_id="scrollbar",
        scrollbar=sb,
        row=int(sb.thumb_top + 4),
        left_button_down=True,
    )
    assert drag.consumed is True
    assert drag.new_scroll is not None

    released = route_term_scrollbar_release(
        capture=capture,
        context_id="palette:list",
        target_id="scrollbar",
        scrollbar=sb,
    )
    assert released is True
    assert capture.active is False
def test_drag_requires_matching_context_and_target() -> None:
    capture = TermMouseCapture()
    sb = _make_scrollbar(scroll_top=6)
    press = route_term_scrollbar_press(
        capture=capture,
        context_id="window-a:list",
        target_id="scrollbar",
        scrollbar=sb,
        row=int(sb.thumb_top),
        current_scroll=6.0,
    )
    assert press.drag_started is True

    wrong_context = route_term_scrollbar_drag(
        capture=capture,
        context_id="window-b:list",
        target_id="scrollbar",
        scrollbar=sb,
        row=int(sb.thumb_top + 4),
        left_button_down=True,
    )
    wrong_target = route_term_scrollbar_drag(
        capture=capture,
        context_id="window-a:list",
        target_id="search-box",
        scrollbar=sb,
        row=int(sb.thumb_top + 4),
        left_button_down=True,
    )
    right_target = route_term_scrollbar_drag(
        capture=capture,
        context_id="window-a:list",
        target_id="scrollbar",
        scrollbar=sb,
        row=int(sb.thumb_top + 4),
        left_button_down=True,
    )

    assert wrong_context.consumed is False
    assert wrong_context.new_scroll is None
    assert wrong_target.consumed is False
    assert wrong_target.new_scroll is None
    assert right_target.consumed is True
    assert right_target.new_scroll is not None


def test_release_respects_context_boundaries() -> None:
    capture = TermMouseCapture()
    sb = _make_scrollbar()
    route_term_scrollbar_press(
        capture=capture,
        context_id="window-a:list",
        target_id="scrollbar",
        scrollbar=sb,
        row=int(sb.thumb_top),
        current_scroll=8.0,
    )

    assert route_term_scrollbar_release(
        capture=capture,
        context_id="window-b:list",
        target_id="scrollbar",
        scrollbar=sb,
    ) is False
    assert capture.active is True

    assert route_term_scrollbar_release(
        capture=capture,
        context_id="window-a:list",
        target_id="scrollbar",
        scrollbar=sb,
    ) is True
    assert capture.active is False


def test_shared_capture_separates_two_window_scrollbars() -> None:
    capture = TermMouseCapture()
    sb_a = _make_scrollbar(scroll_top=9)
    sb_b = _make_scrollbar(scroll_top=9)

    route_term_scrollbar_press(
        capture=capture,
        context_id="window-a:list",
        target_id="scrollbar",
        scrollbar=sb_a,
        row=int(sb_a.thumb_top),
        current_scroll=9.0,
    )

    drag_b = route_term_scrollbar_drag(
        capture=capture,
        context_id="window-b:list",
        target_id="scrollbar",
        scrollbar=sb_b,
        row=int(sb_b.thumb_top + 5),
        left_button_down=True,
    )
    drag_a = route_term_scrollbar_drag(
        capture=capture,
        context_id="window-a:list",
        target_id="scrollbar",
        scrollbar=sb_a,
        row=int(sb_a.thumb_top + 5),
        left_button_down=True,
    )

    assert drag_b.consumed is False
    assert drag_b.new_scroll is None
    assert drag_a.consumed is True
    assert drag_a.new_scroll is not None


def test_mouse_route_matches_capture_uses_context_target_and_button_invariants() -> None:
    capture = TermMouseCapture()
    capture.begin(context_id="window-a:list", target_id="scrollbar", button=1)

    assert _mouse_route_matches_capture(
        capture=capture,
        context_id="window-a:list",
        target_id="scrollbar",
        button=1,
    ) is True
    assert _mouse_route_matches_capture(
        capture=capture,
        context_id="window-b:list",
        target_id="scrollbar",
        button=1,
    ) is False
    assert _mouse_route_matches_capture(
        capture=capture,
        context_id="window-a:list",
        target_id="search-box",
        button=1,
    ) is False
    assert _mouse_route_matches_capture(
        capture=capture,
        context_id="window-a:list",
        target_id="scrollbar",
        button=2,
    ) is False


def test_mouse_route_release_capture_ends_drag_and_clears_capture() -> None:
    capture = TermMouseCapture()
    sb = _make_scrollbar()
    capture.begin(context_id="window-a:list", target_id="scrollbar", button=1)
    sb.drag_active = True

    _mouse_route_release_capture(capture=capture, scrollbar=sb)

    assert capture.active is False
    assert sb.drag_active is False


def test_detached_tool_window_does_not_consume_main_click() -> None:
    called = {"n": 0}

    def _handler() -> bool:
        called["n"] += 1
        return True

    consumed = route_tool_window_click(mode="window", click_handler=_handler)

    assert consumed is False
    # Detached windows must not intercept main-window clicks.
    assert called["n"] == 0


def test_overlay_tool_window_uses_click_handler() -> None:
    called = {"n": 0}

    def _handler() -> bool:
        called["n"] += 1
        return True

    consumed = route_tool_window_click(mode="overlay", click_handler=_handler)

    assert consumed is True
    assert called["n"] == 1


def test_overlay_tool_window_route_normalizes_mode() -> None:
    called = {"n": 0}

    def _handler() -> bool:
        called["n"] += 1
        return True

    consumed = route_tool_window_click(mode="  OVERLAY  ", click_handler=_handler)

    assert consumed is True
    assert called["n"] == 1


def test_tool_window_click_overlay_mode_predicate() -> None:
    assert _tool_window_click_overlay_mode("overlay") is True
    assert _tool_window_click_overlay_mode(" OVERLAY ") is True
    assert _tool_window_click_overlay_mode("window") is False
    assert _tool_window_click_overlay_mode("") is False


def test_tool_window_click_handler_result_predicate() -> None:
    assert _tool_window_click_handler_result(None) is False
    assert _tool_window_click_handler_result(lambda: True) is True
    assert _tool_window_click_handler_result(lambda: 1) is True
    assert _tool_window_click_handler_result(lambda: 0) is False

    def _boom() -> bool:
        raise RuntimeError("boom")

    assert _tool_window_click_handler_result(_boom) is False


def test_overlay_tool_window_handler_false_does_not_consume() -> None:
    consumed = route_tool_window_click(mode="overlay", click_handler=lambda: False)
    assert consumed is False


def test_missing_or_bad_handler_does_not_consume() -> None:
    assert route_tool_window_click(mode="overlay", click_handler=None) is False

    def _boom() -> bool:
        raise RuntimeError("boom")

    assert route_tool_window_click(mode="overlay", click_handler=_boom) is False


def test_non_string_tool_window_mode_does_not_consume_or_call_handler() -> None:
    called = {"n": 0}

    def _handler() -> bool:
        called["n"] += 1
        return True

    assert route_tool_window_click(mode=None, click_handler=_handler) is False  # type: ignore[arg-type]
    assert route_tool_window_click(mode=42, click_handler=_handler) is False  # type: ignore[arg-type]
    assert called["n"] == 0


def test_reset_keyboard_repeat_state_resets_known_fields() -> None:
    class _FakeWindow:
        _repeat_symbol = 17
        _repeat_hold_s = 1.25
        _repeat_step_s = 2.5
        untouched = "ok"

    win = _FakeWindow()
    _reset_keyboard_repeat_state(win)

    assert win._repeat_symbol is None
    assert win._repeat_hold_s == 0.0
    assert win._repeat_step_s == 0.0
    assert win.untouched == "ok"


def test_focus_keyboard_resets_repeat_state_on_deactivate() -> None:
    class _FakeWindow:
        _repeat_symbol = 123
        _repeat_hold_s = 1.5
        _repeat_step_s = 2.5

    fake = _FakeWindow()
    primed = route_window_focus_keyboard(window=fake, activated=False)

    assert primed is False
    assert fake._repeat_symbol is None
    assert fake._repeat_hold_s == 0.0
    assert fake._repeat_step_s == 0.0


def test_reset_keyboard_repeat_state_tolerates_partial_setter_failures() -> None:
    class _FakeWindow:
        def __init__(self) -> None:
            self._symbol = 456
            self._repeat_hold_s = 4.5
            self._repeat_step_s = 5.5

        @property
        def _repeat_symbol(self) -> int:
            return self._symbol

        @_repeat_symbol.setter
        def _repeat_symbol(self, _value: object) -> None:
            raise RuntimeError("boom")

    fake = _FakeWindow()
    termui_mod._reset_keyboard_repeat_state(fake)

    assert fake._repeat_symbol == 456
    assert fake._repeat_hold_s == 0.0
    assert fake._repeat_step_s == 0.0


def test_focus_keyboard_primes_first_responder_on_activate() -> None:
    class _FakeNSWindow:
        def __init__(self) -> None:
            self.calls: list[object] = []

        def makeFirstResponder_(self, view: object) -> None:
            self.calls.append(view)

    class _FakeWindow:
        def __init__(self) -> None:
            self._repeat_symbol = 321
            self._repeat_hold_s = 9.0
            self._repeat_step_s = 8.0
            self._nswindow = _FakeNSWindow()
            self._nsview = object()
            self.switch_calls = 0

        def switch_to(self) -> None:
            self.switch_calls += 1

    fake = _FakeWindow()
    primed = route_window_focus_keyboard(window=fake, activated=True)

    assert primed is True
    # Keep native mouse/control routing intact: when native handles exist we
    # should not switch GL context during activation.
    assert fake.switch_calls == 0
    assert fake._nswindow.calls == [fake._nsview]
    assert fake._repeat_symbol is None
    assert fake._repeat_hold_s == 0.0
    assert fake._repeat_step_s == 0.0


def test_focus_keyboard_falls_back_to_switch_to_without_native_handles() -> None:
    class _FakeWindow:
        def __init__(self) -> None:
            self._repeat_symbol = 7
            self._repeat_hold_s = 1.0
            self._repeat_step_s = 1.0
            self.switch_calls = 0

        def switch_to(self) -> None:
            self.switch_calls += 1

    fake = _FakeWindow()
    primed = route_window_focus_keyboard(window=fake, activated=True)

    assert primed is True
    assert fake.switch_calls == 1
    assert fake._repeat_symbol is None
    assert fake._repeat_hold_s == 0.0
    assert fake._repeat_step_s == 0.0


def test_focus_keyboard_handles_missing_or_failing_native_focus() -> None:
    class _NoNative:
        _repeat_symbol = 1
        _repeat_hold_s = 2.0
        _repeat_step_s = 3.0

    class _BadNativeWindow:
        def makeFirstResponder_(self, _view: object) -> None:
            raise RuntimeError("boom")

    class _BadNative:
        def __init__(self) -> None:
            self._repeat_symbol = 1
            self._repeat_hold_s = 2.0
            self._repeat_step_s = 3.0
            self._nswindow = _BadNativeWindow()
            self._nsview = object()

    assert route_window_focus_keyboard(window=_NoNative(), activated=True) is False
    assert route_window_focus_keyboard(window=_BadNative(), activated=True) is False


def test_focus_keyboard_route_delegates_to_shared_helpers() -> None:
    calls: list[tuple[str, object]] = []
    win = object()
    old_reset = termui_mod._reset_keyboard_repeat_state
    old_prime = termui_mod._prime_window_keyboard_focus
    try:
        termui_mod._reset_keyboard_repeat_state = lambda window: calls.append(("reset", window))
        termui_mod._prime_window_keyboard_focus = lambda window: calls.append(("prime", window)) or True
        assert route_window_focus_keyboard(window=win, activated=True) is True
    finally:
        termui_mod._reset_keyboard_repeat_state = old_reset
        termui_mod._prime_window_keyboard_focus = old_prime

    assert calls == [("reset", win), ("prime", win)]


def test_focus_keyboard_route_skips_prime_helper_when_deactivated() -> None:
    calls: list[tuple[str, object]] = []
    win = object()
    old_reset = termui_mod._reset_keyboard_repeat_state
    old_prime = termui_mod._prime_window_keyboard_focus
    try:
        termui_mod._reset_keyboard_repeat_state = lambda window: calls.append(("reset", window))
        termui_mod._prime_window_keyboard_focus = lambda window: calls.append(("prime", window)) or True
        assert route_window_focus_keyboard(window=win, activated=False) is False
    finally:
        termui_mod._reset_keyboard_repeat_state = old_reset
        termui_mod._prime_window_keyboard_focus = old_prime

    assert calls == [("reset", win)]


def test_handoff_window_focus_calls_activate_and_switch_to() -> None:
    class _FakeWindow:
        def __init__(self) -> None:
            self.activate_calls = 0
            self.switch_calls = 0
            self._closing = False
            self.has_exit = False

        def activate(self) -> None:
            self.activate_calls += 1

        def switch_to(self) -> None:
            self.switch_calls += 1

    win = _FakeWindow()
    assert handoff_window_focus(win) is True
    assert win.activate_calls == 1
    assert win.switch_calls == 1


def test_handoff_window_focus_skips_closing_or_exited_windows() -> None:
    class _ClosingWindow:
        _closing = True
        has_exit = False

        def activate(self) -> None:
            raise AssertionError("activate should not run")

    class _ExitedWindow:
        _closing = False
        has_exit = True

        def switch_to(self) -> None:
            raise AssertionError("switch_to should not run")

    assert handoff_window_focus(_ClosingWindow()) is False
    assert handoff_window_focus(_ExitedWindow()) is False


def test_handoff_window_focus_tolerates_activate_or_switch_errors() -> None:
    class _FailActivateWindow:
        def __init__(self) -> None:
            self._closing = False
            self.has_exit = False
            self.switch_calls = 0

        def activate(self) -> None:
            raise RuntimeError("boom")

        def switch_to(self) -> None:
            self.switch_calls += 1

    class _FailSwitchWindow:
        def __init__(self) -> None:
            self._closing = False
            self.has_exit = False
            self.activate_calls = 0

        def activate(self) -> None:
            self.activate_calls += 1

        def switch_to(self) -> None:
            raise RuntimeError("boom")

    a = _FailActivateWindow()
    b = _FailSwitchWindow()

    assert handoff_window_focus(a) is True
    assert a.switch_calls == 1
    assert handoff_window_focus(b) is True
    assert b.activate_calls == 1


def test_handoff_window_focus_returns_false_when_no_focus_methods_exist() -> None:
    class _BareWindow:
        _closing = False
        has_exit = False

    assert handoff_window_focus(_BareWindow()) is False


def test_handoff_window_focus_primes_native_first_responder() -> None:
    class _FakeNSWindow:
        def __init__(self) -> None:
            self.make_key_calls = 0
            self.order_front_calls = 0
            self.first_responder_calls: list[object] = []

        def makeKeyAndOrderFront_(self, _sender: object) -> None:
            self.make_key_calls += 1

        def orderFrontRegardless(self) -> None:
            self.order_front_calls += 1

        def makeFirstResponder_(self, view: object) -> None:
            self.first_responder_calls.append(view)

    class _FakeWindow:
        def __init__(self) -> None:
            self._closing = False
            self.has_exit = False
            self._nswindow = _FakeNSWindow()
            self._nsview = object()

    win = _FakeWindow()
    assert handoff_window_focus(win) is True
    assert win._nswindow.make_key_calls == 1
    assert win._nswindow.order_front_calls == 1
    assert win._nswindow.first_responder_calls == [win._nsview]


def test_handoff_window_focus_uses_native_window_from_context_canvas() -> None:
    class _FakeNSWindow:
        def __init__(self) -> None:
            self.first_responder_calls: list[object] = []

        def makeFirstResponder_(self, view: object) -> None:
            self.first_responder_calls.append(view)

    class _FakeCanvas:
        def __init__(self) -> None:
            self._nswindow = _FakeNSWindow()
            self._nsview = object()

    class _FakeContext:
        def __init__(self) -> None:
            self.canvas = _FakeCanvas()

    class _FakeWindow:
        def __init__(self) -> None:
            self._closing = False
            self.has_exit = False
            self.context = _FakeContext()

    win = _FakeWindow()
    assert handoff_window_focus(win) is True
    assert win.context.canvas._nswindow.first_responder_calls == [win.context.canvas._nsview]


def test_handoff_window_focus_tolerates_native_focus_errors() -> None:
    class _BadNSWindow:
        def makeKeyAndOrderFront_(self, _sender: object) -> None:
            raise RuntimeError("key")

        def orderFrontRegardless(self) -> None:
            raise RuntimeError("front")

        def makeFirstResponder_(self, _view: object) -> None:
            raise RuntimeError("first")

    class _FakeWindow:
        def __init__(self) -> None:
            self._closing = False
            self.has_exit = False
            self._nswindow = _BadNSWindow()
            self._nsview = object()

    # Errors in native focus plumbing should be swallowed.
    old_shared_app = termui_mod._shared_ns_application
    old_running_app = termui_mod._shared_running_application
    try:
        termui_mod._shared_ns_application = lambda: None
        termui_mod._shared_running_application = lambda: None
        assert handoff_window_focus(_FakeWindow()) is False
    finally:
        termui_mod._shared_ns_application = old_shared_app
        termui_mod._shared_running_application = old_running_app


def test_debug_window_close_runs_super_before_focus_callback() -> None:
    events: list[str] = []

    class _FakeWindowBase:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def on_close(self) -> None:
            events.append("super")

    class _FakeClock:
        def schedule_interval(self, _fn: object, _dt: float) -> None:
            pass

        def unschedule(self, _fn: object) -> None:
            events.append("unschedule")

    class _FakeStore:
        def get_int(self, _key: str) -> int:
            return 1

    class _FakeTheme:
        bg = (0, 0, 0, 255)
        fg = (255, 255, 255, 255)
        muted = (128, 128, 128, 255)
        sel_bg = (0, 0, 0, 255)
        sel_fg = (255, 255, 255, 255)
        box_fg = (120, 120, 120, 255)
        accent = (255, 200, 0, 255)

    class _FakePyglet:
        class window:
            Window = _FakeWindowBase

        clock = _FakeClock()

    win = create_debug_window(
        pyglet=_FakePyglet(),
        get_text=lambda: "",
        store=_FakeStore(),  # type: ignore[arg-type]
        font_name=None,
        on_closed=lambda: events.append("callback"),
        theme_from_store=lambda _store: _FakeTheme(),
    )

    win.on_close()

    # Critical ordering: native close completes before focus callback handoff.
    assert events == ["unschedule", "super", "callback"]


def test_debug_window_key_close_uses_native_request_path_and_invokes_callback() -> None:
    events: list[str] = []

    class _FakeWindowBase:
        def __init__(self, **_kwargs: object) -> None:
            self._nswindow = self._FakeNSWindow(self)

        class _FakeNSWindow:
            def __init__(self, owner: object) -> None:
                self._owner = owner

            def performClose_(self, _sender: object) -> None:
                events.append("perform_close")
                self._owner.on_close()

        def close(self) -> None:
            events.append("close")
            self.on_close()

        def on_close(self) -> None:
            events.append("super")

    class _FakeClock:
        def schedule_interval(self, _fn: object, _dt: float) -> None:
            pass

        def unschedule(self, _fn: object) -> None:
            events.append("unschedule")

    class _FakeStore:
        def get_int(self, _key: str) -> int:
            return 1

    class _FakeTheme:
        bg = (0, 0, 0, 255)
        fg = (255, 255, 255, 255)
        muted = (128, 128, 128, 255)
        sel_bg = (0, 0, 0, 255)
        sel_fg = (255, 255, 255, 255)
        box_fg = (120, 120, 120, 255)
        accent = (255, 200, 0, 255)

    class _FakePyglet:
        class window:
            Window = _FakeWindowBase

            class key:
                D = 68
                ESCAPE = 27
                Q = 81
                W = 87
                MOD_COMMAND = 0x100
                MOD_ACCEL = 0

        clock = _FakeClock()

    win = create_debug_window(
        pyglet=_FakePyglet(),
        get_text=lambda: "",
        store=_FakeStore(),  # type: ignore[arg-type]
        font_name=None,
        on_closed=lambda: events.append("callback"),
        theme_from_store=lambda _store: _FakeTheme(),
    )

    win.on_key_press(_FakePyglet.window.key.D, 0)

    assert str(getattr(win, "_close_request_path", "")) == "key_d:native_perform_close"
    assert events == ["perform_close", "unschedule", "super", "callback"]


def test_debug_window_key_shortcuts_map_to_close_triggers() -> None:
    close_triggers: list[str] = []

    class _FakeWindowBase:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class _FakeClock:
        def schedule_interval(self, _fn: object, _dt: float) -> None:
            pass

    class _FakeStore:
        def get_int(self, _key: str) -> int:
            return 1

    class _FakeTheme:
        bg = (0, 0, 0, 255)
        fg = (255, 255, 255, 255)
        muted = (128, 128, 128, 255)
        sel_bg = (0, 0, 0, 255)
        sel_fg = (255, 255, 255, 255)
        box_fg = (120, 120, 120, 255)
        accent = (255, 200, 0, 255)

    class _FakePyglet:
        class window:
            Window = _FakeWindowBase

            class key:
                A = 65
                D = 68
                ESCAPE = 27
                Q = 81
                W = 87
                MOD_COMMAND = 0x100
                MOD_ACCEL = 0x200

        clock = _FakeClock()

    win = create_debug_window(
        pyglet=_FakePyglet(),
        get_text=lambda: "",
        store=_FakeStore(),  # type: ignore[arg-type]
        font_name=None,
        on_closed=lambda: None,
        theme_from_store=lambda _store: _FakeTheme(),
    )
    win.request_close = lambda *, trigger="api": close_triggers.append(str(trigger))  # type: ignore[method-assign]

    key = _FakePyglet.window.key
    win.on_key_press(key.ESCAPE, 0)
    win.on_key_press(key.D, 0)
    win.on_key_press(key.Q, 0)
    win.on_key_press(key.W, key.MOD_COMMAND)
    win.on_key_press(key.W, key.MOD_ACCEL)
    win.on_key_press(key.A, 0)

    assert close_triggers == ["key_escape", "key_d", "key_q", "key_cmd_w", "key_cmd_w"]


def test_debug_window_activation_does_not_route_keyboard_focus() -> None:
    base_events: list[str] = []

    class _FakeWindowBase:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def on_activate(self) -> None:
            base_events.append("activate")

        def on_deactivate(self) -> None:
            base_events.append("deactivate")

    class _FakeClock:
        def schedule_interval(self, _fn: object, _dt: float) -> None:
            pass

    class _FakeStore:
        def get_int(self, _key: str) -> int:
            return 1

    class _FakeTheme:
        bg = (0, 0, 0, 255)
        fg = (255, 255, 255, 255)
        muted = (128, 128, 128, 255)
        sel_bg = (0, 0, 0, 255)
        sel_fg = (255, 255, 255, 255)
        box_fg = (120, 120, 120, 255)
        accent = (255, 200, 0, 255)

    class _FakePyglet:
        class window:
            Window = _FakeWindowBase

        clock = _FakeClock()

    focus_route_calls = _run_activation_cycle_without_keyboard_route(
        lambda: create_debug_window(
            pyglet=_FakePyglet(),
            get_text=lambda: "",
            store=_FakeStore(),  # type: ignore[arg-type]
            font_name=None,
            on_closed=lambda: None,
            theme_from_store=lambda _store: _FakeTheme(),
        )
    )

    assert focus_route_calls == []
    assert base_events == ["activate", "deactivate"]


def test_palette_window_activation_does_not_route_keyboard_focus() -> None:
    base_events: list[str] = []

    class _FakeWindowBase:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def on_activate(self) -> None:
            base_events.append("activate")

        def on_deactivate(self) -> None:
            base_events.append("deactivate")

    class _FakeStore:
        def get_int(self, _key: str) -> int:
            return 1

        def get(self, _key: str) -> object:
            return 1.0

    class _FakeTheme:
        bg = (0, 0, 0, 255)
        fg = (255, 255, 255, 255)
        muted = (128, 128, 128, 255)
        sel_bg = (0, 0, 0, 255)
        sel_fg = (255, 255, 255, 255)
        box_fg = (120, 120, 120, 255)
        accent = (255, 200, 0, 255)

    class _Entry:
        def __init__(self, block_id: str, label: str) -> None:
            self.block_id = block_id
            self.label = label

    class _FakePyglet:
        class window:
            Window = _FakeWindowBase

    focus_route_calls = _run_activation_cycle_without_keyboard_route(
        lambda: create_palette_window(
            pyglet=_FakePyglet(),
            store=_FakeStore(),  # type: ignore[arg-type]
            font_name=None,
            entries=[_Entry("minecraft:stone", "Stone")],
            load_tex=lambda _jar_rel: None,
            initial_selected_idx=0,
            initial_block_id="minecraft:stone",
            on_pick_entry=lambda _idx: None,
            on_closed=lambda: None,
            theme_from_store=lambda _store: _FakeTheme(),
        )
    )

    assert focus_route_calls == []
    assert base_events == ["activate", "deactivate"]


def test_datapack_viewer_opening_tool_windows_does_not_force_activate() -> None:
    source = _datapack_viewer_source()

    assert "self._param_window.activate()" not in source
    assert "self._worldgen_window.activate()" not in source
    assert "self._debug_window.activate()" not in source
    assert "win.activate()" not in source


def test_datapack_viewer_activation_does_not_prime_keyboard_focus() -> None:
    source = _datapack_viewer_source()

    assert "route_window_focus_keyboard(window=self, activated=True)" not in source


def test_kvalue_window_activation_does_not_route_keyboard_focus() -> None:
    source = _kvalue_window_source()

    assert "route_window_focus_keyboard(" not in source
    assert ".activate(" not in source


def test_datapack_viewer_focus_smoke_includes_param_window() -> None:
    source = _datapack_viewer_source()

    assert 'focus_allowed_sources = ("palette", "debug", "param", "viewport")' in source
    assert 'focus_sources = _parse_focus_source_list(os.environ.get("ENDERTERM_SMOKE_FOCUS_SOURCES"))' in source
    assert 'focus_preopen_sources = _parse_focus_source_list(os.environ.get("ENDERTERM_SMOKE_FOCUS_PREOPEN_SOURCES"))' in source
    assert '"param": bool(getattr(win, "_param_window", None) is not None)' in source


def test_datapack_viewer_smoke_capture_region_helper_is_shared() -> None:
    source = _datapack_viewer_source()

    assert "def _smoke_capture_region_px(" in source
    assert "_smoke_capture_region_px(win, trim_sidebar=True)" in source
    assert "_smoke_capture_region_px(second, trim_sidebar=False)" in source


def test_datapack_viewer_tool_close_focus_handoff_remains_enabled() -> None:
    source = _datapack_viewer_source()

    assert "_apply_tool_window_close_focus_handoff(" in source
    assert "handoff_window_focus(self)" in source


def test_datapack_viewer_uses_shared_tool_close_handoff_helpers() -> None:
    source = _datapack_viewer_source()
    compact = "".join(source.split())

    assert "def _request_tool_window_close_handoff(" in source
    assert "def _consume_tool_window_close_request_path(" in source
    assert 'self._request_tool_window_close_handoff(source="debug"' in compact
    assert 'self._request_tool_window_close_handoff(source="palette"' in compact
    assert 'self._consume_tool_window_close_request_path(source="debug"' in compact
    assert 'self._consume_tool_window_close_request_path(source="palette"' in compact


def test_datapack_viewer_walk_mode_scaffold_hooks_present() -> None:
    source = _datapack_viewer_source()

    assert "def _walk_mode_set_active(self, active: bool, *, reason: str) -> None:" in source
    assert "def _walk_mode_force_exit(self, *, reason: str) -> None:" in source
    assert "def _set_walk_mode_capture(self, enabled: bool) -> None:" in source
    assert "walk_action = _walk_mode_key_action(" in source
    assert 'toggle_symbol=int(getattr(pyglet.window.key, "W", -1))' in source
    assert "if walk_action == \"exit_escape\":" in source
    assert "self._walk_mode_set_active(False, reason=\"key_escape\")" in source
    assert "self.walk_mode_label = pyglet.text.Label(" in source
    assert "WALK MODE ACTIVE  (Esc exits)" in source


def test_datapack_viewer_viewport_toggle_keybind_is_c_chord() -> None:
    source = _datapack_viewer_source()
    key_blocks = _source_method_blocks(
        source,
        signature="def on_key_press(self, symbol: int, modifiers: int) -> None:",
        window=12000,
    )
    main_block = next(
        b for b in key_blocks if "_open_additional_viewport_window()" in b and "_toggle_second_viewport_window()" in b
    )
    assert "if symbol == pyglet.window.key.C:" in main_block
    assert "if symbol == pyglet.window.key.W:" not in main_block
    assert "Shift+C add viewport" in source
    assert "W walk mode" in source
    assert "L walk mode" not in source


def test_datapack_viewer_walk_mode_integrator_updates_orbit_xz_only() -> None:
    source = _datapack_viewer_source()
    assert "move_dx, move_dz, move_carry = _walk_mode_integrate_xz(" in source
    assert "self._orbit_target = (float(ox) + float(move_dx), float(oy), float(oz) + float(move_dz))" in source


def test_datapack_viewer_walk_mode_capture_releases_on_focus_and_tool_window_paths() -> None:
    source = _datapack_viewer_source()

    assert "self._walk_mode_force_exit(reason=\"deactivate\")" in source
    assert "self._walk_mode_force_exit(reason=\"window_close\")" in source
    assert source.count("self._walk_mode_force_exit(reason=\"tool_window\")") >= 4


def test_close_focus_handoff_window_prefers_child_close_for_debug_and_palette() -> None:
    events: list[str] = []

    class _ChildWindow:
        def __init__(self, name: str) -> None:
            self._name = name
            self._close_request_path = ""

        def request_close(self, *, trigger: str = "api") -> str:
            path = f"{str(trigger)}:native_perform_close"
            self._close_request_path = path
            events.append(f"{self._name}:request:{path}")
            return path

        def close(self) -> None:
            events.append(f"{self._name}:close")

    palette = _ChildWindow("palette")
    debug = _ChildWindow("debug")

    palette_path = _close_focus_handoff_window(
        source="palette",
        palette_window=palette,
        debug_window=debug,
        viewport_window=None,
        close_palette_fallback=lambda: events.append("palette:toggle"),
        close_debug_fallback=lambda: events.append("debug:toggle"),
        close_viewport_fallback=None,
    )
    debug_path = _close_focus_handoff_window(
        source="debug",
        palette_window=palette,
        debug_window=debug,
        viewport_window=None,
        close_palette_fallback=lambda: events.append("palette:toggle"),
        close_debug_fallback=lambda: events.append("debug:toggle"),
        close_viewport_fallback=None,
    )

    assert palette_path == "child_close:native_perform_close"
    assert debug_path == "child_close:native_perform_close"
    assert events == [
        "palette:request:child_close:native_perform_close",
        "debug:request:child_close:native_perform_close",
    ]


def test_close_focus_handoff_window_falls_back_when_close_missing() -> None:
    events: list[str] = []

    path = _close_focus_handoff_window(
        source="palette",
        palette_window=object(),
        debug_window=None,
        viewport_window=None,
        close_palette_fallback=lambda: events.append("palette:toggle"),
        close_debug_fallback=None,
        close_viewport_fallback=None,
    )

    assert path == "child_close:toggle_fallback"
    assert events == ["palette:toggle"]


def test_close_focus_handoff_window_param_invokes_close_callback_once() -> None:
    events: list[str] = []

    class _ParamWindow:
        def close(self) -> None:
            events.append("param:close")

    param = _ParamWindow()
    seen: list[object] = []
    path = _close_focus_handoff_window(
        source="param",
        palette_window=None,
        debug_window=None,
        param_window=param,
        viewport_window=None,
        close_param_fallback=None,
        on_param_window_close=lambda target: seen.append(target),
    )

    assert path == "child_close:window_close"
    assert events == ["param:close"]
    assert seen == [param]


def test_close_focus_handoff_window_param_uses_toggle_fallback_before_native_path() -> None:
    events: list[str] = []

    class _NsWindow:
        def performClose_(self, _sender: object | None) -> None:
            events.append("param:native_close")

    class _ParamWindow:
        _nswindow = _NsWindow()

    path = _close_focus_handoff_window(
        source="param",
        palette_window=None,
        debug_window=None,
        param_window=_ParamWindow(),
        viewport_window=None,
        close_param_fallback=lambda: events.append("param:toggle"),
    )

    assert path == "child_close:toggle_fallback"
    assert events == ["param:toggle"]


def test_tool_window_close_focus_handoff_is_single_immediate_call() -> None:
    calls: list[str] = []

    class _Owner:
        def __init__(self) -> None:
            self._debug_window = object()

    owner = _Owner()

    count = _apply_tool_window_close_focus_handoff(
        owner=owner,
        source="debug",
        attr_name="_debug_window",
        restore_focus=lambda src: calls.append(str(src)),
    )

    assert count == 1
    assert owner._debug_window is None
    assert calls == ["debug"]


def test_tool_window_close_focus_handoff_ignores_empty_source() -> None:
    calls: list[str] = []

    class _Owner:
        marker = object()

    owner = _Owner()

    count = _apply_tool_window_close_focus_handoff(
        owner=owner,
        source="",
        attr_name=None,
        restore_focus=lambda src: calls.append(str(src)),
    )

    assert count == 0
    assert calls == []


def test_close_and_clear_window_attr_clears_even_when_close_raises() -> None:
    events: list[str] = []

    class _Window:
        def close(self) -> None:
            events.append("close")
            raise RuntimeError("boom")

    class _Owner:
        def __init__(self) -> None:
            self._palette_window = _Window()

    owner = _Owner()
    closed = _close_and_clear_window_attr(owner=owner, attr_name="_palette_window")

    assert closed is True
    assert events == ["close"]
    assert owner._palette_window is None


def test_close_and_clear_window_attr_returns_false_for_missing_or_none() -> None:
    class _Owner:
        def __init__(self) -> None:
            self._debug_window = None

    owner = _Owner()

    assert _close_and_clear_window_attr(owner=owner, attr_name="_debug_window") is False
    assert _close_and_clear_window_attr(owner=owner, attr_name="_missing_window") is False


def test_safe_window_gl_cleanup_requires_make_current() -> None:
    events: list[str] = []

    ran = _safe_window_gl_cleanup(make_current=None, cleanup=lambda: events.append("cleanup"))

    assert ran is False
    assert events == []


def test_safe_window_gl_cleanup_runs_cleanup_after_context_switch() -> None:
    events: list[str] = []

    ran = _safe_window_gl_cleanup(
        make_current=lambda: events.append("switch"),
        cleanup=lambda: events.append("cleanup"),
    )

    assert ran is True
    assert events == ["switch", "cleanup"]


def test_safe_window_gl_cleanup_swallows_cleanup_errors() -> None:
    events: list[str] = []

    def _cleanup() -> None:
        events.append("cleanup")
        raise RuntimeError("boom")

    ran = _safe_window_gl_cleanup(
        make_current=lambda: events.append("switch"),
        cleanup=_cleanup,
    )

    assert ran is True
    assert events == ["switch", "cleanup"]


def test_companion_viewport_on_close_restores_owner_context_after_super_close() -> None:
    src = _datapack_viewer_source()
    companion_cls = _source_class_block(src, "class CompanionViewportWindow(pyglet.window.Window):")
    on_close = _source_method_block(companion_cls, "def on_close(self) -> None:")

    assert "_safe_window_gl_cleanup(" in on_close
    assert "make_current=getattr(self, \"switch_to\", None)" in on_close
    assert "cleanup=self._cleanup_gl_resources" in on_close
    assert "owner_switch_to = getattr(self._owner, \"switch_to\", None)" in on_close
    super_idx = on_close.find("super().on_close()")
    restore_idx = on_close.find("owner_switch_to()")
    assert super_idx >= 0
    assert restore_idx > super_idx


def test_datapack_viewer_uses_shared_window_close_bookkeeping_helper() -> None:
    source = _datapack_viewer_source()
    assert "if _close_and_clear_window_attr(owner=self, attr_name=\"_param_window\"):" in source
    assert "if _close_and_clear_window_attr(owner=self, attr_name=\"_worldgen_window\"):" in source
    assert "palette_was_open = _close_and_clear_window_attr(owner=self, attr_name=\"_palette_window\")" in source


def test_window_has_key_focus_requires_active_app_and_key_window() -> None:
    class _KeyNSWindow:
        def isKeyWindow(self) -> bool:
            return True

    class _FakeApp:
        def __init__(self, *, active: bool, key_window: object | None) -> None:
            self._active = bool(active)
            self._key_window = key_window

        def isActive(self) -> bool:
            return bool(self._active)

        def keyWindow(self) -> object | None:
            return self._key_window

    class _FakeWindow:
        def __init__(self) -> None:
            self._closing = False
            self.has_exit = False
            self._nswindow = _KeyNSWindow()
            self._nsview = object()

    win = _FakeWindow()
    old_shared_app = termui_mod._shared_ns_application
    old_running_app = termui_mod._shared_running_application
    try:
        termui_mod._shared_ns_application = lambda: _FakeApp(active=True, key_window=win._nswindow)
        termui_mod._shared_running_application = lambda: None
        diag = window_key_focus_diagnostics(win)
        assert diag["app_active"] is True
        assert diag["is_key_window"] is True
        assert diag["key_window_match"] is True
        assert diag["strict"] is True
        assert window_has_key_focus(win) is True
    finally:
        termui_mod._shared_ns_application = old_shared_app
        termui_mod._shared_running_application = old_running_app


def test_window_has_key_focus_fails_when_app_is_inactive() -> None:
    class _KeyNSWindow:
        def isKeyWindow(self) -> bool:
            return True

    class _FakeApp:
        def __init__(self, *, active: bool, key_window: object | None) -> None:
            self._active = bool(active)
            self._key_window = key_window

        def isActive(self) -> bool:
            return bool(self._active)

        def keyWindow(self) -> object | None:
            return self._key_window

    class _FakeWindow:
        def __init__(self) -> None:
            self._closing = False
            self.has_exit = False
            self._nswindow = _KeyNSWindow()
            self._nsview = object()

    win = _FakeWindow()
    old_shared_app = termui_mod._shared_ns_application
    old_running_app = termui_mod._shared_running_application
    try:
        termui_mod._shared_ns_application = lambda: _FakeApp(active=False, key_window=win._nswindow)
        termui_mod._shared_running_application = lambda: None
        diag = window_key_focus_diagnostics(win)
        assert diag["app_active"] is False
        assert diag["is_key_window"] is True
        assert diag["strict"] is False
        assert window_has_key_focus(win) is False
    finally:
        termui_mod._shared_ns_application = old_shared_app
        termui_mod._shared_running_application = old_running_app


def test_window_has_key_focus_does_not_accept_main_window_alone() -> None:
    class _PlainNSWindow:
        def isKeyWindow(self) -> bool:
            return False

        def isMainWindow(self) -> bool:
            return True

    class _FakeApp:
        def __init__(self, *, active: bool, key_window: object | None) -> None:
            self._active = bool(active)
            self._key_window = key_window

        def isActive(self) -> bool:
            return bool(self._active)

        def keyWindow(self) -> object | None:
            return self._key_window

    class _FakeWindow:
        def __init__(self) -> None:
            self._closing = False
            self.has_exit = False
            self._nswindow = _PlainNSWindow()
            self._nsview = object()

    win = _FakeWindow()
    old_shared_app = termui_mod._shared_ns_application
    old_running_app = termui_mod._shared_running_application
    try:
        termui_mod._shared_ns_application = lambda: _FakeApp(active=True, key_window=object())
        termui_mod._shared_running_application = lambda: None
        diag = window_key_focus_diagnostics(win)
        assert diag["app_active"] is True
        assert diag["is_key_window"] is False
        assert diag["key_window_match"] is False
        assert diag["strict"] is False
        assert window_has_key_focus(win) is False
    finally:
        termui_mod._shared_ns_application = old_shared_app
        termui_mod._shared_running_application = old_running_app
