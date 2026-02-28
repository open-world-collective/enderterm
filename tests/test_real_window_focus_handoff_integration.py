"""Optional macOS real-window focus-handoff smoke.

Prerequisites:
- macOS desktop session (not headless).
- Accessibility permission granted to the Python interpreter running pytest.
- `MINECRAFT_JAR` points to an existing client jar.

Run:
  MINECRAFT_JAR=/path/to/client.jar \
  /Users/qarl/tmp/venv/enderterm311/bin/python -m pytest -q --run-optional tests/test_real_window_focus_handoff_integration.py
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


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


_MAIN_DISPLAY_W: int | None = None
_MAIN_DISPLAY_H: int | None = None


_KCG_EVENT_SOURCE_STATE_HID_SYSTEM_STATE = 1
_KCG_EVENT_LEFT_MOUSE_DOWN = 1
_KCG_EVENT_LEFT_MOUSE_UP = 2
_KCG_EVENT_MOUSE_MOVED = 5
_KCG_MOUSE_BUTTON_LEFT = 0
_KCG_HID_EVENT_TAP = 0


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


def _post_mouse_event(pid: int, *, x: int, y: int, event_type: int) -> None:
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


def _post_left_click(pid: int, *, x: int, y: int) -> None:
    x, y = _to_quartz_xy(x=x, y=y)
    _animate_mouse_to(x=x, y=y)
    _post_mouse_event(pid, x=x, y=y, event_type=_KCG_EVENT_MOUSE_MOVED)
    time.sleep(0.03)
    _post_mouse_event(pid, x=x, y=y, event_type=_KCG_EVENT_LEFT_MOUSE_DOWN)
    time.sleep(0.03)
    _post_mouse_event(pid, x=x, y=y, event_type=_KCG_EVENT_LEFT_MOUSE_UP)


_FOCUS_ACTION_SEQUENCE = ("close_button", "main_probe")


def _expected_focus_actions(sources: list[str]) -> list[tuple[str, str]]:
    return [(source, action) for source in sources for action in _FOCUS_ACTION_SEQUENCE]


def _assert_min_float_per_source(
    values: dict[str, object],
    sources: list[str],
    *,
    minimum: float,
    payload: dict[str, object],
) -> None:
    for source in sources:
        assert float(values.get(source, 0.0)) >= float(minimum), payload


def _assert_min_int_per_source(
    values: dict[str, object],
    sources: list[str],
    *,
    minimum: int,
    payload: dict[str, object],
) -> None:
    for source in sources:
        assert int(values.get(source, 0)) >= int(minimum), payload


def test_expected_focus_actions_preserves_source_order() -> None:
    assert _expected_focus_actions(["palette", "debug"]) == [
        ("palette", "close_button"),
        ("palette", "main_probe"),
        ("debug", "close_button"),
        ("debug", "main_probe"),
    ]


def test_expected_focus_actions_empty_sources() -> None:
    assert _expected_focus_actions([]) == []


def test_assert_min_float_per_source_accepts_threshold() -> None:
    _assert_min_float_per_source(
        {"palette": "0.95", "debug": 1.0},
        ["palette", "debug"],
        minimum=0.95,
        payload={},
    )


def test_assert_min_float_per_source_rejects_below_threshold() -> None:
    with pytest.raises(AssertionError):
        _assert_min_float_per_source(
            {"palette": 0.94},
            ["palette"],
            minimum=0.95,
            payload={},
        )


def test_assert_min_int_per_source_accepts_threshold() -> None:
    _assert_min_int_per_source(
        {"palette": "1", "debug": 2},
        ["palette", "debug"],
        minimum=1,
        payload={},
    )


def test_assert_min_int_per_source_rejects_below_threshold() -> None:
    with pytest.raises(AssertionError):
        _assert_min_int_per_source(
            {"palette": 0},
            ["palette"],
            minimum=1,
            payload={},
        )


@pytest.mark.optional
def test_real_window_focus_handoff_after_subwindow_close(nbttool: ModuleType, tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("real-window focus smoke requires macOS")
    if not _is_accessibility_trusted():
        pytest.fail(
            "macOS Accessibility permission is required for OS-level click injection. "
            "Enable it for the Python interpreter running pytest (System Settings -> Privacy & Security -> Accessibility)."
        )

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for focus-handoff smoke.")

    repo_root = Path(__file__).resolve().parents[1]
    smoke_out = tmp_path / "smoke_focus_handoff.json"
    cmd = [
        sys.executable,
        "-m",
        "nbttool",
        "datapack-view",
        str(jar_path),
        "--select",
        label,
        "--smoke-focus-handoff",
        "--smoke-timeout",
        "55",
        "--smoke-out",
        str(smoke_out),
        "--test-banner",
        "AUTOMATED TESTING DO NOT TOUCH",
    ]
    env = os.environ.copy()
    env["MINECRAFT_JAR"] = str(jar_path)
    env["ENDERTERM_SMOKE_FOCUS_REQUIRE_CLICK"] = "1"
    env["ENDERTERM_SMOKE_FOCUS_CLOSE_BY_CLICK"] = "1"
    proc = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    out = ""
    clicked_actions: list[tuple[str, str]] = []
    expected_sources = ["palette", "debug", "param", "viewport"]
    expected_actions = _expected_focus_actions(expected_sources)
    clicked_set: set[tuple[str, str]] = set()

    try:
        deadline = time.monotonic() + 95.0
        while time.monotonic() < deadline:
            payload = _load_json_if_ready(smoke_out)
            if isinstance(payload, dict):
                if bool(payload.get("ok")):
                    break
                if bool(payload.get("awaiting_os_click")):
                    source = str(payload.get("pending_source", "")).strip().lower()
                    kind = str(payload.get("click_kind", "")).strip().lower()
                    raw_target = payload.get("click_target")
                    if (
                        source in expected_sources
                        and kind in {"close_button", "main_probe"}
                        and (source, kind) not in clicked_set
                    ):
                        assert isinstance(raw_target, list) and len(raw_target) == 2, payload
                        x = int(raw_target[0])
                        y = int(raw_target[1])
                        _post_left_click(proc.pid, x=x, y=y)
                        clicked_set.add((source, kind))
                        clicked_actions.append((source, kind))
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
    assert payload.get("smoke_mode") == "focus_handoff", payload
    assert clicked_actions == expected_actions, payload

    validated = payload.get("validated_sources")
    assert isinstance(validated, list), payload
    assert [str(x) for x in validated] == expected_sources, payload

    dwell = payload.get("dwell_before_close_s_by_source")
    assert isinstance(dwell, dict), payload
    _assert_min_float_per_source(dwell, expected_sources, minimum=0.95, payload=payload)

    close_paths = payload.get("close_path_by_source")
    assert isinstance(close_paths, dict), payload
    assert str(close_paths.get("palette", "")).startswith("os_close_button"), payload
    assert str(close_paths.get("debug", "")).startswith("os_close_button"), payload
    assert str(close_paths.get("param", "")).startswith("os_close_button"), payload
    assert str(close_paths.get("viewport", "")).startswith("os_close_button"), payload

    child_close_used = payload.get("child_close_path_used_by_source")
    assert isinstance(child_close_used, dict), payload
    assert bool(child_close_used.get("palette")) is False, payload
    assert bool(child_close_used.get("debug")) is False, payload
    assert bool(child_close_used.get("param")) is False, payload

    focus_probe = payload.get("focus_probe")
    assert isinstance(focus_probe, dict), payload
    hits_by_source = focus_probe.get("hits_by_source")
    assert isinstance(hits_by_source, dict), payload
    _assert_min_int_per_source(hits_by_source, expected_sources, minimum=1, payload=payload)
