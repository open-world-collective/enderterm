"""Optional monolithic GUI integration smoke.

Runs the EnderTerm viewer once with `--smoke-suite` and drives any required
OS-level injection (mouse + keyboard) via the smoke JSON protocol.

Prerequisites:
- macOS desktop session (not headless).
- Accessibility permission granted to the Python interpreter running pytest.
- `MINECRAFT_JAR` points to an existing client jar.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType

import pytest

from conftest import (
    optional_smoke_find_label as _find_smoke_label,
    optional_smoke_is_accessibility_trusted as _is_accessibility_trusted,
    optional_smoke_load_json_if_ready as _load_json_if_ready,
)


_KCG_EVENT_SOURCE_STATE_HID_SYSTEM_STATE = 1
_KCG_HID_EVENT_TAP = 0

_KCG_EVENT_LEFT_MOUSE_DOWN = 1
_KCG_EVENT_LEFT_MOUSE_UP = 2
_KCG_EVENT_RIGHT_MOUSE_DOWN = 3
_KCG_EVENT_RIGHT_MOUSE_UP = 4
_KCG_EVENT_MOUSE_MOVED = 5

_KCG_MOUSE_BUTTON_LEFT = 0
_KCG_MOUSE_BUTTON_RIGHT = 1


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


_MAIN_DISPLAY_W: int | None = None
_MAIN_DISPLAY_H: int | None = None


# macOS virtual keycodes (US keyboard)
_VK_TAB = 48
_VK_DOWN_ARROW = 125
_VK_V = 9

_KEYCODES: dict[str, int] = {
    "tab": _VK_TAB,
    "down": _VK_DOWN_ARROW,
    "v": _VK_V,
}


def _application_services() -> ctypes.CDLL:
    return ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")


def _main_display_height() -> int:
    global _MAIN_DISPLAY_W, _MAIN_DISPLAY_H
    if _MAIN_DISPLAY_H is not None:
        return int(_MAIN_DISPLAY_H)
    app = _application_services()
    app.CGMainDisplayID.argtypes = []
    app.CGMainDisplayID.restype = ctypes.c_uint32
    app.CGDisplayBounds.argtypes = [ctypes.c_uint32]
    app.CGDisplayBounds.restype = _CGRect
    did = app.CGMainDisplayID()
    bounds = app.CGDisplayBounds(did)
    _MAIN_DISPLAY_W = int(round(float(bounds.w)))
    _MAIN_DISPLAY_H = int(round(float(bounds.h)))
    return int(_MAIN_DISPLAY_H)


def _to_quartz_xy(*, x: int, y: int) -> tuple[int, int]:
    # nbttool smoke targets are in AppKit screen coordinates (origin bottom-left).
    # Quartz HID events use a y-flipped global space.
    h = _main_display_height()
    return (int(x), int(round(float(h) - float(y))))


def _get_mouse_location() -> tuple[int, int] | None:
    if sys.platform != "darwin":
        return None
    app = _application_services()
    app.CGEventCreate.argtypes = [ctypes.c_void_p]
    app.CGEventCreate.restype = ctypes.c_void_p
    app.CGEventGetLocation.argtypes = [ctypes.c_void_p]
    app.CGEventGetLocation.restype = _CGPoint
    app.CFRelease.argtypes = [ctypes.c_void_p]
    app.CFRelease.restype = None

    ev = app.CGEventCreate(None)
    if not ev:
        return None
    try:
        pt = app.CGEventGetLocation(ev)
        return (int(round(float(pt.x))), int(round(float(pt.y))))
    finally:
        try:
            app.CFRelease(ev)
        except Exception:
            pass


def _warp_mouse(*, x: int, y: int) -> None:
    if sys.platform != "darwin":
        return
    app = _application_services()
    app.CGWarpMouseCursorPosition.argtypes = [_CGPoint]
    app.CGWarpMouseCursorPosition.restype = None
    app.CGAssociateMouseAndMouseCursorPosition.argtypes = [ctypes.c_bool]
    app.CGAssociateMouseAndMouseCursorPosition.restype = ctypes.c_int
    try:
        app.CGAssociateMouseAndMouseCursorPosition(True)
    except Exception:
        pass
    app.CGWarpMouseCursorPosition(_CGPoint(float(x), float(y)))


_MOUSE_STAGE_MARGIN = 50
_MOUSE_STAGE_MIN_DIST2 = 160 * 160  # px^2; below this, hop away first so motion is visible.
_CLOSE_CLICK_MOVE_AWAY_DX = 120
_CLOSE_CLICK_MOVE_AWAY_DY = 24
_CLOSE_CLICK_MOVE_AWAY_STEPS = 30
_CLOSE_CLICK_MOVE_AWAY_STEP_SLEEP_S = 0.012


def _mouse_stage_point(*, target_x: int, target_y: int) -> tuple[int, int]:
    # Pick an opposite corner relative to the target so motion is obvious on-screen.
    h = _main_display_height()
    assert _MAIN_DISPLAY_W is not None
    w = int(_MAIN_DISPLAY_W)
    sx = int(_MOUSE_STAGE_MARGIN if int(target_x) > (int(w) // 2) else max(0, int(w) - int(_MOUSE_STAGE_MARGIN)))
    sy = int(_MOUSE_STAGE_MARGIN if int(target_y) > (int(h) // 2) else max(0, int(h) - int(_MOUSE_STAGE_MARGIN)))
    return (sx, sy)


def _animate_mouse_to(
    *,
    x: int,
    y: int,
    steps: int = 25,
    step_sleep_s: float = 0.02,
    ensure_visible: bool = True,
) -> None:
    cur = _get_mouse_location()
    if cur is None:
        _warp_mouse(x=x, y=y)
        return
    x0, y0 = cur

    if ensure_visible:
        dx = int(x) - int(x0)
        dy = int(y) - int(y0)
        sx, sy = _mouse_stage_point(target_x=int(x), target_y=int(y))
        if dx * dx + dy * dy < _MOUSE_STAGE_MIN_DIST2 and (int(x), int(y)) != (int(sx), int(sy)):
            _animate_mouse_to(x=sx, y=sy, steps=15, step_sleep_s=0.012, ensure_visible=False)
            cur2 = _get_mouse_location()
            if cur2 is not None:
                x0, y0 = cur2

    sx = max(1, int(steps))
    for i in range(1, sx + 1):
        t = float(i) / float(sx)
        xi = int(round(float(x0) + (float(x) - float(x0)) * t))
        yi = int(round(float(y0) + (float(y) - float(y0)) * t))
        _warp_mouse(x=xi, y=yi)
        time.sleep(float(step_sleep_s))


def _close_click_move_away_target(*, x: int, y: int) -> tuple[int, int]:
    """Compute a bounded, visible post-close destination near the click target."""
    h = _main_display_height()
    w = int(_MAIN_DISPLAY_W or 0)
    max_x = max(0, int(w) - 5) if w > 0 else int(x + _CLOSE_CLICK_MOVE_AWAY_DX)
    max_y = max(0, int(h) - 5)
    away_x = min(max(0, int(x) + int(_CLOSE_CLICK_MOVE_AWAY_DX)), int(max_x))
    away_y = min(max(0, int(y) + int(_CLOSE_CLICK_MOVE_AWAY_DY)), int(max_y))
    return (int(away_x), int(away_y))


def _post_mouse_event(*, x: int, y: int, event_type: int, button: int) -> None:
    app = _application_services()
    app.CGEventSourceCreate.argtypes = [ctypes.c_uint32]
    app.CGEventSourceCreate.restype = ctypes.c_void_p
    app.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, _CGPoint, ctypes.c_uint32]
    app.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    app.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    app.CGEventPost.restype = None
    app.CFRelease.argtypes = [ctypes.c_void_p]
    app.CFRelease.restype = None

    source = app.CGEventSourceCreate(_KCG_EVENT_SOURCE_STATE_HID_SYSTEM_STATE)
    if not source:
        raise RuntimeError("CGEventSourceCreate failed")
    event = app.CGEventCreateMouseEvent(
        source,
        int(event_type),
        _CGPoint(float(x), float(y)),
        int(button),
    )
    if not event:
        app.CFRelease(source)
        raise RuntimeError("CGEventCreateMouseEvent failed")
    app.CGEventPost(_KCG_HID_EVENT_TAP, event)
    app.CFRelease(event)
    app.CFRelease(source)


def _post_left_click(*, x: int, y: int, immediate: bool = False, move_away_after: bool = False) -> None:
    if bool(move_away_after):
        # Close-button path must remain immediate at arrival -> down.
        immediate = True
    x, y = _to_quartz_xy(x=x, y=y)
    _animate_mouse_to(x=x, y=y)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_MOUSE_MOVED, button=_KCG_MOUSE_BUTTON_LEFT)
    if not bool(immediate):
        time.sleep(0.03)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_LEFT_MOUSE_DOWN, button=_KCG_MOUSE_BUTTON_LEFT)
    time.sleep(0.03)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_LEFT_MOUSE_UP, button=_KCG_MOUSE_BUTTON_LEFT)
    if bool(move_away_after):
        away_x, away_y = _close_click_move_away_target(x=int(x), y=int(y))
        _animate_mouse_to(
            x=int(away_x),
            y=int(away_y),
            steps=int(_CLOSE_CLICK_MOVE_AWAY_STEPS),
            step_sleep_s=float(_CLOSE_CLICK_MOVE_AWAY_STEP_SLEEP_S),
            ensure_visible=False,
        )


def _post_right_click(*, x: int, y: int) -> None:
    x, y = _to_quartz_xy(x=x, y=y)
    _animate_mouse_to(x=x, y=y)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_MOUSE_MOVED, button=_KCG_MOUSE_BUTTON_RIGHT)
    time.sleep(0.03)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_RIGHT_MOUSE_DOWN, button=_KCG_MOUSE_BUTTON_RIGHT)
    time.sleep(0.03)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_RIGHT_MOUSE_UP, button=_KCG_MOUSE_BUTTON_RIGHT)


def _post_key_event(*, keycode: int, is_down: bool) -> None:
    app = _application_services()
    app.CGEventSourceCreate.argtypes = [ctypes.c_uint32]
    app.CGEventSourceCreate.restype = ctypes.c_void_p
    app.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    app.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    app.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    app.CGEventPost.restype = None
    app.CFRelease.argtypes = [ctypes.c_void_p]
    app.CFRelease.restype = None

    source = app.CGEventSourceCreate(_KCG_EVENT_SOURCE_STATE_HID_SYSTEM_STATE)
    if not source:
        raise RuntimeError("CGEventSourceCreate failed")
    event = app.CGEventCreateKeyboardEvent(source, int(keycode) & 0xFFFF, bool(is_down))
    if not event:
        app.CFRelease(source)
        raise RuntimeError("CGEventCreateKeyboardEvent failed")
    app.CGEventPost(_KCG_HID_EVENT_TAP, event)
    app.CFRelease(event)
    app.CFRelease(source)


def _post_key_press(*, keycode: int) -> None:
    _post_key_event(keycode=keycode, is_down=True)
    time.sleep(0.02)
    _post_key_event(keycode=keycode, is_down=False)


def test_post_left_click_immediate_skips_pre_down_dwell(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[int] = []
    sleeps: list[float] = []

    monkeypatch.setattr(
        sys.modules[__name__],
        "_to_quartz_xy",
        lambda *, x, y: (int(x), int(y)),
    )
    monkeypatch.setattr(sys.modules[__name__], "_animate_mouse_to", lambda **_kwargs: None)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_post_mouse_event",
        lambda *, x, y, event_type, button: events.append(int(event_type)),
    )
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(float(s)))

    _post_left_click(x=10, y=20, immediate=True)

    assert events == [_KCG_EVENT_MOUSE_MOVED, _KCG_EVENT_LEFT_MOUSE_DOWN, _KCG_EVENT_LEFT_MOUSE_UP]
    assert sleeps == [0.03]


def test_post_left_click_default_keeps_pre_down_dwell(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[int] = []
    sleeps: list[float] = []

    monkeypatch.setattr(
        sys.modules[__name__],
        "_to_quartz_xy",
        lambda *, x, y: (int(x), int(y)),
    )
    monkeypatch.setattr(sys.modules[__name__], "_animate_mouse_to", lambda **_kwargs: None)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_post_mouse_event",
        lambda *, x, y, event_type, button: events.append(int(event_type)),
    )
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(float(s)))

    _post_left_click(x=10, y=20)

    assert events == [_KCG_EVENT_MOUSE_MOVED, _KCG_EVENT_LEFT_MOUSE_DOWN, _KCG_EVENT_LEFT_MOUSE_UP]
    assert sleeps == [0.03, 0.03]


def test_post_left_click_close_button_uses_visible_slower_animation(monkeypatch: pytest.MonkeyPatch) -> None:
    timeline: list[tuple[object, ...]] = []
    sleeps: list[float] = []

    def _fake_animate_mouse_to(
        *,
        x: int,
        y: int,
        steps: int = 25,
        step_sleep_s: float = 0.02,
        ensure_visible: bool = True,
    ) -> None:
        timeline.append(("move", int(x), int(y), int(steps), float(step_sleep_s), bool(ensure_visible)))

    monkeypatch.setattr(
        sys.modules[__name__],
        "_to_quartz_xy",
        lambda *, x, y: (int(x), int(y)),
    )
    monkeypatch.setattr(sys.modules[__name__], "_animate_mouse_to", _fake_animate_mouse_to)
    monkeypatch.setattr(
        sys.modules[__name__],
        "_close_click_move_away_target",
        lambda *, x, y: (90, 80),
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "_post_mouse_event",
        lambda *, x, y, event_type, button: timeline.append(("event", int(event_type))),
    )
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(float(s)))

    _post_left_click(x=10, y=20, move_away_after=True)

    assert _CLOSE_CLICK_MOVE_AWAY_STEPS >= 20
    assert _CLOSE_CLICK_MOVE_AWAY_STEP_SLEEP_S >= 0.01
    assert timeline == [
        ("move", 10, 20, 25, 0.02, True),
        ("event", _KCG_EVENT_MOUSE_MOVED),
        ("event", _KCG_EVENT_LEFT_MOUSE_DOWN),
        ("event", _KCG_EVENT_LEFT_MOUSE_UP),
        (
            "move",
            90,
            80,
            int(_CLOSE_CLICK_MOVE_AWAY_STEPS),
            float(_CLOSE_CLICK_MOVE_AWAY_STEP_SLEEP_S),
            False,
        ),
    ]
    assert int(timeline[-1][3]) > 1
    assert sleeps == [0.03]


def _assert_dhash(sig: dict[str, object]) -> None:
    dhash = sig.get("dhash64")
    assert isinstance(dhash, str) and len(dhash) == 16, sig


def _assert_not_mostly_white(signature: dict[str, object], *, label: str) -> None:
    mean_luma = float(signature.get("mean_luma", 0.0))
    assert 0.5 <= mean_luma <= 235.0, f"{label}: suspicious mean_luma={mean_luma:.3f} sig={signature!r}"


def _assert_toggle_round_trip(
    *,
    states: list[object],
    baseline_index: int,
    toggled_index: int,
    restored_index: int,
    field: str,
) -> None:
    baseline = bool(states[baseline_index].get(field))
    toggled = bool(states[toggled_index].get(field))
    restored = bool(states[restored_index].get(field))
    assert toggled != baseline, states
    assert restored == baseline, states


_MONOLITHIC_SUITE_STEPS = (
    "expand_once",
    "second_viewport_fx",
    "frame_cap_present_stability",
    "focus_handoff",
    "real_window_keys",
    "real_window_build_edits",
)


@pytest.fixture(scope="module")
def monolithic_smoke_harness(nbttool: ModuleType, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tmp_path = tmp_path_factory.mktemp("optional-monolithic")
    if sys.platform != "darwin":
        pytest.skip("monolithic optional GUI smoke suite requires macOS")
    if not _is_accessibility_trusted():
        pytest.fail(
            "macOS Accessibility permission is required for OS-level mouse/keyboard injection. "
            "Enable it for the Python interpreter running pytest (System Settings -> Privacy & Security -> Accessibility)."
        )

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for monolithic GUI smoke suite.")

    try:
        smoke_timeout_s = int(os.environ.get("ENDERTERM_OPTIONAL_SMOKE_TIMEOUT_S", "120") or "120")
    except Exception:
        smoke_timeout_s = 120
    smoke_timeout_s = max(30, min(600, int(smoke_timeout_s)))

    repo_root = Path(__file__).resolve().parents[1]
    smoke_out = tmp_path / "smoke_monolithic_suite.json"
    test_home = tmp_path / "home"
    params_path = test_home / ".config" / "enderterm" / "params.json"
    params_path.parent.mkdir(parents=True, exist_ok=True)
    params_path.write_text(
        json.dumps(
            {
                "effects.master_enabled": 0,
                "build.hover_pick.enabled": 0,
                "input.macos.gestures.enabled": 0,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    cmd = [
        sys.executable,
        "-m",
        "nbttool",
        "datapack-view",
        str(jar_path),
        "--select",
        label,
        "--smoke-suite",
        "--smoke-timeout",
        str(smoke_timeout_s),
        "--smoke-out",
        str(smoke_out),
        "--test-banner",
        "AUTOMATED TESTING DO NOT TOUCH",
    ]
    env = os.environ.copy()
    env["HOME"] = str(test_home)
    env["MINECRAFT_JAR"] = str(jar_path)
    env["PYTHONPATH"] = str(repo_root)
    env["ENDERTERM_SMOKE_FOCUS_REQUIRE_CLICK"] = "1"
    env["ENDERTERM_SMOKE_FOCUS_CLOSE_BY_CLICK"] = "1"
    env["ENDERTERM_SMOKE_FOCUS_SOURCES"] = "palette,debug,param,viewport"
    env["ENDERTERM_SMOKE_VIEWPORT_CYCLES"] = "1"
    env["ENDERTERM_SMOKE_SUITE_STEPS"] = ",".join(_MONOLITHIC_SUITE_STEPS)

    proc = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    out = ""
    clicked_focus: set[tuple[str, str, str]] = set()
    clicked_build: set[tuple[str, str]] = set()
    sent_keys: set[tuple[str, int, str]] = set()
    did_focus_click = False
    try:
        deadline = time.monotonic() + float(smoke_timeout_s + 60)
        while time.monotonic() < deadline:
            payload = _load_json_if_ready(smoke_out)
            if isinstance(payload, dict):
                if bool(payload.get("ok")):
                    break
                if payload.get("error") and not bool(payload.get("suite_transition")):
                    break
                suite_step = str(payload.get("suite_step", "")).strip().lower()

                if bool(payload.get("awaiting_os_click")):
                    raw_target = payload.get("click_target")
                    assert isinstance(raw_target, list) and len(raw_target) == 2, payload
                    x = int(raw_target[0])
                    y = int(raw_target[1])
                    pending_action = str(payload.get("pending_action", "")).strip()
                    if pending_action:
                        button = str(payload.get("click_button", "")).strip().lower()
                        key = (suite_step, pending_action)
                        if key not in clicked_build:
                            if button == "right":
                                _post_right_click(x=x, y=y)
                            elif button == "left":
                                _post_left_click(x=x, y=y)
                            else:
                                raise AssertionError(f"unknown click_button={button!r} payload={payload}")
                            clicked_build.add(key)
                    else:
                        source = str(payload.get("pending_source", "")).strip().lower()
                        kind = str(payload.get("click_kind", "")).strip().lower()
                        key = (suite_step, source, kind)
                        if source and kind and key not in clicked_focus:
                            is_close_button = kind == "close_button"
                            _post_left_click(
                                x=x,
                                y=y,
                                immediate=is_close_button,
                                move_away_after=is_close_button,
                            )
                            clicked_focus.add(key)

                if bool(payload.get("awaiting_os_key")):
                    step = int(payload.get("key_step_index", -1))
                    key = str(payload.get("pending_key", "")).strip().lower()
                    if step >= 0 and key and (suite_step, step, key) not in sent_keys:
                        code = _KEYCODES.get(key)
                        assert isinstance(code, int), {"key": key, "payload": payload}
                        focus_xy = payload.get("focus_click_target")
                        key_focus = bool(payload.get("key_focus_current", True))
                        if isinstance(focus_xy, list) and len(focus_xy) == 2:
                            fx, fy = int(focus_xy[0]), int(focus_xy[1])
                            if (not did_focus_click) or (not key_focus):
                                _post_left_click(x=fx, y=fy)
                                did_focus_click = True
                                time.sleep(0.06)
                        _post_key_press(keycode=code)
                        sent_keys.add((suite_step, step, key))

            if proc.poll() is not None:
                break
            time.sleep(0.05)

        out, _ = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                extra, _ = proc.communicate(timeout=5)
                out = (out or "") + extra
            except Exception:
                pass

    assert proc.returncode == 0, (out or "")[-4000:]
    payload = _load_json_if_ready(smoke_out)
    assert isinstance(payload, dict), f"missing smoke payload at {smoke_out}"
    assert payload.get("ok") is True, payload
    assert payload.get("smoke_mode") == "suite", payload
    return {
        "payload": payload,
        "clicked_build": clicked_build,
    }


def _suite_payload(monolithic_smoke_harness: dict[str, object]) -> dict[str, object]:
    payload = monolithic_smoke_harness.get("payload")
    assert isinstance(payload, dict), monolithic_smoke_harness
    return payload


def _suite_dict(monolithic_smoke_harness: dict[str, object]) -> dict[str, object]:
    payload = _suite_payload(monolithic_smoke_harness)
    suite = payload.get("suite")
    assert isinstance(suite, dict), payload
    return suite


def _suite_step(monolithic_smoke_harness: dict[str, object], *, step: str) -> dict[str, object]:
    suite = _suite_dict(monolithic_smoke_harness)
    step_payload = suite.get(step)
    assert isinstance(step_payload, dict), {"missing": step, "suite_keys": sorted(suite.keys())}
    return step_payload


@pytest.mark.optional
def test_optional_monolithic_suite_reports_expected_steps(monolithic_smoke_harness: dict[str, object]) -> None:
    suite = _suite_dict(monolithic_smoke_harness)
    for step in _MONOLITHIC_SUITE_STEPS:
        assert isinstance(suite.get(step), dict), {"missing": step, "suite_keys": sorted(suite.keys())}


@pytest.mark.optional
def test_optional_monolithic_expand_once_step(monolithic_smoke_harness: dict[str, object]) -> None:
    expand = _suite_step(monolithic_smoke_harness, step="expand_once")
    assert expand.get("ok") is True, expand
    shot = expand.get("expand_screenshot")
    assert isinstance(shot, dict), expand
    sig = shot.get("main_signature")
    assert isinstance(sig, dict), shot
    assert "error" not in sig, sig
    _assert_dhash(sig)


@pytest.mark.optional
def test_optional_monolithic_second_viewport_fx_step(monolithic_smoke_harness: dict[str, object]) -> None:
    second = _suite_step(monolithic_smoke_harness, step="second_viewport_fx")
    assert second.get("ok") is True, second
    second_fx = second.get("second_fx")
    assert isinstance(second_fx, dict), second
    shots = second_fx.get("viewport_screenshots")
    assert isinstance(shots, dict), second
    main_sig = shots.get("main_signature")
    second_sig = shots.get("second_signature")
    assert isinstance(main_sig, dict) and isinstance(second_sig, dict), shots
    assert "error" not in main_sig and "error" not in second_sig, shots
    _assert_dhash(main_sig)
    _assert_dhash(second_sig)


@pytest.mark.optional
def test_optional_monolithic_frame_cap_present_stability_step(monolithic_smoke_harness: dict[str, object]) -> None:
    frame_cap = _suite_step(monolithic_smoke_harness, step="frame_cap_present_stability")
    assert frame_cap.get("ok") is True, frame_cap
    cap = frame_cap.get("frame_cap_present_stability")
    assert isinstance(cap, dict), frame_cap
    assert int(cap.get("draw_skip_cap_count", 0)) >= 40, cap
    skip_count = int(cap.get("draw_skip_cap_count", 0))
    cache_count = int(cap.get("draw_cache_present_count", 0))
    assert cache_count >= int(skip_count * 0.95), cap


@pytest.mark.optional
def test_optional_monolithic_focus_handoff_step(monolithic_smoke_harness: dict[str, object]) -> None:
    focus = _suite_step(monolithic_smoke_harness, step="focus_handoff")
    assert focus.get("ok") is True, focus
    baseline = focus.get("viewport_baseline_main_shot")
    assert isinstance(baseline, dict), focus
    baseline_sig = baseline.get("main_signature")
    assert isinstance(baseline_sig, dict) and "error" not in baseline_sig, baseline
    _assert_dhash(baseline_sig)
    _assert_not_mostly_white(baseline_sig, label="viewport_baseline_main_shot")

    close_shots = focus.get("viewport_close_main_shots")
    assert isinstance(close_shots, list) and close_shots, focus
    probe_shots = focus.get("viewport_post_main_probe_shots")
    assert isinstance(probe_shots, list) and probe_shots, focus
    close_sig = close_shots[0].get("main_signature") if isinstance(close_shots[0], dict) else None
    probe_sig = probe_shots[0].get("main_signature") if isinstance(probe_shots[0], dict) else None
    assert isinstance(close_sig, dict) and "error" not in close_sig, close_shots[0]
    assert isinstance(probe_sig, dict) and "error" not in probe_sig, probe_shots[0]
    _assert_dhash(close_sig)
    _assert_dhash(probe_sig)
    _assert_not_mostly_white(close_sig, label="viewport_close_main_shots[0]")
    _assert_not_mostly_white(probe_sig, label="viewport_post_main_probe_shots[0]")


@pytest.mark.optional
def test_optional_monolithic_real_window_keys_step(monolithic_smoke_harness: dict[str, object]) -> None:
    keys = _suite_step(monolithic_smoke_harness, step="real_window_keys")
    assert keys.get("ok") is True, keys
    keys_info = keys.get("keys")
    assert isinstance(keys_info, dict), keys
    expected = keys_info.get("expected")
    states = keys_info.get("states")
    assert isinstance(expected, list) and expected, keys_info
    assert isinstance(states, list) and len(states) == len(expected), keys_info

    # Down x3 should advance by exactly one position per press.
    s0 = states[0]
    s1 = states[1]
    s2 = states[2]
    assert isinstance(s0, dict) and isinstance(s1, dict) and isinstance(s2, dict), states
    down_positions = [int(s0.get("selected_pos") or 0), int(s1.get("selected_pos") or 0), int(s2.get("selected_pos") or 0)]
    assert down_positions == [1, 2, 3], states

    # Tab toggles UI hidden, then toggles it back.
    _assert_toggle_round_trip(
        states=states,
        baseline_index=2,
        toggled_index=3,
        restored_index=4,
        field="ui_hidden",
    )

    # V toggles Ender Vision, then toggles it back.
    _assert_toggle_round_trip(
        states=states,
        baseline_index=4,
        toggled_index=5,
        restored_index=6,
        field="ender_vision",
    )


@pytest.mark.optional
def test_optional_monolithic_real_window_build_edits_step(monolithic_smoke_harness: dict[str, object]) -> None:
    build = _suite_step(monolithic_smoke_harness, step="real_window_build_edits")
    assert build.get("ok") is True, build
    build_info = build.get("build_edits")
    assert isinstance(build_info, dict), build
    actions = build_info.get("actions")
    assert isinstance(actions, list) and actions, build_info
    expected_actions = {str(a.get("name", "")) for a in actions if isinstance(a, dict) and str(a.get("name", "")).strip()}
    clicked_build = monolithic_smoke_harness.get("clicked_build")
    assert isinstance(clicked_build, set), monolithic_smoke_harness
    clicked = {action for step, action in clicked_build if step == "real_window_build_edits"}
    assert clicked == expected_actions, {"clicked": sorted(clicked), "expected": sorted(expected_actions)}

    placed_ok = build_info.get("placed_ok")
    removed_ok = build_info.get("removed_ok")
    assert isinstance(placed_ok, list) and len(placed_ok) == 2, build_info
    assert isinstance(removed_ok, list) and len(removed_ok) == 2, build_info
