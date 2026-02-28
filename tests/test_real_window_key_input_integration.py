"""Optional macOS real-window key input integration smoke.

Validates that keypresses are delivered to the app via OS-level keyboard
injection (not via pyglet event dispatch), and that key-driven behaviors flip
as expected.

Prerequisites:
- macOS desktop session (not headless).
- Accessibility permission granted to the Python interpreter running pytest.
- `MINECRAFT_JAR` points to an existing client jar.

Run:
  MINECRAFT_JAR=/path/to/client.jar \\
  /Users/qarl/tmp/venv/enderterm311/bin/python -m pytest -q --run-optional \\
    tests/test_real_window_key_input_integration.py
"""

from __future__ import annotations

import ctypes
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


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


_MAIN_DISPLAY_W: int | None = None
_MAIN_DISPLAY_H: int | None = None


_KCG_EVENT_LEFT_MOUSE_DOWN = 1
_KCG_EVENT_LEFT_MOUSE_UP = 2
_KCG_EVENT_MOUSE_MOVED = 5
_KCG_MOUSE_BUTTON_LEFT = 0

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


def _main_display_width() -> int:
    _ = _main_display_height()
    assert _MAIN_DISPLAY_W is not None
    return int(_MAIN_DISPLAY_W)


def _to_quartz_xy(*, x: int, y: int) -> tuple[int, int]:
    # EnderTerm click targets come from NSWindow.convertPointToScreen_ (AppKit coordinates,
    # origin bottom-left). Quartz events use a y-flipped global space, so convert.
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
    # Ensure the cursor is associated (some automation paths can disassociate it).
    app.CGAssociateMouseAndMouseCursorPosition.argtypes = [ctypes.c_bool]
    app.CGAssociateMouseAndMouseCursorPosition.restype = ctypes.c_int
    try:
        app.CGAssociateMouseAndMouseCursorPosition(True)
    except Exception:
        pass
    app.CGWarpMouseCursorPosition(_CGPoint(float(x), float(y)))


_MOUSE_STAGE_MARGIN = 50
_MOUSE_STAGE_MIN_DIST2 = 160 * 160  # px^2; below this, hop away first so motion is visible.


def _mouse_stage_point(*, target_x: int, target_y: int) -> tuple[int, int]:
    # Pick an opposite corner relative to the target so motion is obvious on-screen.
    w = _main_display_width()
    h = _main_display_height()
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

    # If the cursor already happens to be near the target, the animation can be effectively
    # invisible to a human observer. Hop to a staging point first in that case.
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


def _post_mouse_event(*, x: int, y: int, event_type: int) -> None:
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
        _KCG_MOUSE_BUTTON_LEFT,
    )
    if not event:
        app.CFRelease(source)
        raise RuntimeError("CGEventCreateMouseEvent failed")
    # Use global HID injection for realism (cursor movement + real focus routing).
    app.CGEventPost(_KCG_HID_EVENT_TAP, event)
    app.CFRelease(event)
    app.CFRelease(source)


def _post_left_click(*, x: int, y: int) -> None:
    x, y = _to_quartz_xy(x=x, y=y)
    _animate_mouse_to(x=x, y=y)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_MOUSE_MOVED)
    time.sleep(0.03)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_LEFT_MOUSE_DOWN)
    time.sleep(0.03)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_LEFT_MOUSE_UP)


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


def _inject_pending_key_if_requested(
    *,
    payload: dict[str, object],
    sent_set: set[tuple[int, str]],
    sent_keys: list[str],
    did_focus_click: bool,
) -> bool:
    if not bool(payload.get("awaiting_os_key")):
        return did_focus_click

    step = int(payload.get("key_step_index", -1))
    key = str(payload.get("pending_key", "")).strip().lower()
    if step < 0 or not key or (step, key) in sent_set:
        return did_focus_click

    code = _KEYCODES.get(key)
    assert isinstance(code, int), {"key": key, "payload": payload}

    # Ensure the app is actually key before injecting keyboard events.
    # Click-to-focus is visible and avoids suite-order focus flakiness.
    focus_xy = payload.get("focus_click_target")
    key_focus = bool(payload.get("key_focus_current", True))
    if isinstance(focus_xy, list) and len(focus_xy) == 2 and ((not did_focus_click) or (not key_focus)):
        fx, fy = int(focus_xy[0]), int(focus_xy[1])
        _post_left_click(x=fx, y=fy)
        did_focus_click = True
        time.sleep(0.06)

    _post_key_press(keycode=code)
    sent_set.add((step, key))
    sent_keys.append(key)
    return did_focus_click


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


def test_assert_toggle_round_trip_accepts_toggle_then_restore() -> None:
    states: list[object] = [
        {"ui_hidden": False},
        {"ui_hidden": True},
        {"ui_hidden": False},
    ]
    _assert_toggle_round_trip(
        states=states,
        baseline_index=0,
        toggled_index=1,
        restored_index=2,
        field="ui_hidden",
    )


def test_assert_toggle_round_trip_rejects_no_toggle() -> None:
    states: list[object] = [
        {"ender_vision": True},
        {"ender_vision": True},
        {"ender_vision": True},
    ]
    with pytest.raises(AssertionError):
        _assert_toggle_round_trip(
            states=states,
            baseline_index=0,
            toggled_index=1,
            restored_index=2,
            field="ender_vision",
        )


def test_inject_pending_key_if_requested_ignores_non_pending_payload() -> None:
    sent_set: set[tuple[int, str]] = set()
    sent_keys: list[str] = []
    did_focus_click = _inject_pending_key_if_requested(
        payload={"awaiting_os_key": False, "key_step_index": 1, "pending_key": "tab"},
        sent_set=sent_set,
        sent_keys=sent_keys,
        did_focus_click=False,
    )
    assert did_focus_click is False
    assert sent_set == set()
    assert sent_keys == []


def test_inject_pending_key_if_requested_posts_once_for_new_step(monkeypatch: pytest.MonkeyPatch) -> None:
    clicked: list[tuple[int, int]] = []
    pressed: list[int] = []

    monkeypatch.setattr(
        sys.modules[__name__],
        "_post_left_click",
        lambda *, x, y: clicked.append((int(x), int(y))),
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "_post_key_press",
        lambda *, keycode: pressed.append(int(keycode)),
    )
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    payload: dict[str, object] = {
        "awaiting_os_key": True,
        "key_step_index": "2",
        "pending_key": " TAB ",
        "focus_click_target": ["12", "34"],
        "key_focus_current": False,
    }
    sent_set: set[tuple[int, str]] = set()
    sent_keys: list[str] = []

    did_focus_click = _inject_pending_key_if_requested(
        payload=payload,
        sent_set=sent_set,
        sent_keys=sent_keys,
        did_focus_click=False,
    )
    did_focus_click = _inject_pending_key_if_requested(
        payload=payload,
        sent_set=sent_set,
        sent_keys=sent_keys,
        did_focus_click=did_focus_click,
    )

    assert did_focus_click is True
    assert clicked == [(12, 34)]
    assert pressed == [_VK_TAB]
    assert sent_set == {(2, "tab")}
    assert sent_keys == ["tab"]


@pytest.mark.optional
def test_real_window_key_input_sequence(nbttool: ModuleType, tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("real-window key input smoke requires macOS")
    if not _is_accessibility_trusted():
        pytest.fail(
            "macOS Accessibility permission is required for OS-level key injection. "
            "Enable it for the Python interpreter running pytest (System Settings -> Privacy & Security -> Accessibility)."
        )

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for real-window key input smoke.")

    repo_root = Path(__file__).resolve().parents[1]
    smoke_out = tmp_path / "smoke_real_window_keys.json"
    cmd = [
        sys.executable,
        "-m",
        "nbttool",
        "datapack-view",
        str(jar_path),
        "--select",
        label,
        "--smoke-real-window-keys",
        "--smoke-timeout",
        "70",
        "--smoke-out",
        str(smoke_out),
        "--test-banner",
        "AUTOMATED TESTING DO NOT TOUCH",
    ]
    env = os.environ.copy()
    env["MINECRAFT_JAR"] = str(jar_path)
    proc = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    out = ""
    sent_keys: list[str] = []
    sent_set: set[tuple[int, str]] = set()
    did_focus_click = False
    try:
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            payload = _load_json_if_ready(smoke_out)
            if isinstance(payload, dict):
                if bool(payload.get("ok")):
                    break
                did_focus_click = _inject_pending_key_if_requested(
                    payload=payload,
                    sent_set=sent_set,
                    sent_keys=sent_keys,
                    did_focus_click=did_focus_click,
                )
            if proc.poll() is not None:
                break
            time.sleep(0.05)

        out, _ = proc.communicate(timeout=120)
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
    assert payload.get("smoke_mode") == "real_window_keys", payload

    keys = payload.get("keys")
    assert isinstance(keys, dict), payload
    expected = keys.get("expected")
    states = keys.get("states")
    assert isinstance(expected, list) and expected, keys
    assert isinstance(states, list) and len(states) == len(expected), keys
    assert sent_keys == [str(x) for x in expected], {"sent": sent_keys, "expected": expected}

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
