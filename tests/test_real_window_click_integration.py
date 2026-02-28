"""Optional macOS real-window click integration smoke.

Prerequisites:
- macOS desktop session (not headless).
- Accessibility permission granted to the Python interpreter running pytest.
- `MINECRAFT_JAR` points to an existing client jar.

Run:
  MINECRAFT_JAR=/path/to/client.jar \\
  /Users/qarl/tmp/venv/enderterm311/bin/python -m pytest -q --run-optional tests/test_real_window_click_integration.py
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
_KCG_EVENT_FLAG_MASK_ALTERNATE = 0x00080000


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
    # nbttool's click targets come from NSWindow.convertPointToScreen_ (AppKit coordinates,
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


def _post_mouse_event(pid: int, *, x: int, y: int, event_type: int, flags: int = 0) -> None:
    app = _application_services()
    app.CGEventSourceCreate.argtypes = [ctypes.c_uint32]
    app.CGEventSourceCreate.restype = ctypes.c_void_p
    app.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, _CGPoint, ctypes.c_uint32]
    app.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    app.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    app.CGEventSetFlags.restype = None
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
    if flags:
        app.CGEventSetFlags(event, int(flags))
    # Use global HID injection for realism (cursor movement + real focus routing).
    app.CGEventPost(_KCG_HID_EVENT_TAP, event)
    app.CFRelease(event)
    app.CFRelease(source)


def _post_left_click(pid: int, *, x: int, y: int, option_modified: bool) -> None:
    x, y = _to_quartz_xy(x=x, y=y)
    flags = _KCG_EVENT_FLAG_MASK_ALTERNATE if option_modified else 0
    _animate_mouse_to(x=x, y=y)
    _post_mouse_event(pid, x=x, y=y, event_type=_KCG_EVENT_MOUSE_MOVED, flags=flags)
    time.sleep(0.03)
    _post_mouse_event(pid, x=x, y=y, event_type=_KCG_EVENT_LEFT_MOUSE_DOWN, flags=flags)
    time.sleep(0.03)
    _post_mouse_event(pid, x=x, y=y, event_type=_KCG_EVENT_LEFT_MOUSE_UP, flags=flags)


def _wait_for_click_targets(path: Path, *, timeout_s: float) -> dict[str, object]:
    deadline = time.monotonic() + float(timeout_s)
    while time.monotonic() < deadline:
        payload = _load_json_if_ready(path)
        if isinstance(payload, dict):
            if bool(payload.get("awaiting_os_clicks")) and _has_required_click_targets(payload):
                return payload
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for click targets in {path}")


def _click_targets_dict(payload: dict[str, object]) -> dict[str, object] | None:
    targets = payload.get("click_targets")
    if not isinstance(targets, dict):
        return None
    return targets


def _has_required_click_targets(payload: dict[str, object]) -> bool:
    targets = _click_targets_dict(payload)
    if targets is None:
        return False
    return isinstance(targets.get("build"), list) and isinstance(targets.get("orbit"), list)


def _target_xy(targets: dict[str, object], key: str) -> tuple[int, int]:
    raw = targets.get(key)
    assert isinstance(raw, list) and len(raw) == 2
    return (int(raw[0]), int(raw[1]))


def _pending_click_source(
    *,
    payload: dict[str, object],
    expected_sources: list[str],
    clicked_set: set[str],
) -> str | None:
    if not bool(payload.get("awaiting_os_clicks")):
        return None
    source = str(payload.get("pending_source", "")).strip().lower()
    if source not in expected_sources or source in clicked_set:
        return None
    return source


def test_click_targets_dict_returns_payload_targets_dict() -> None:
    payload: dict[str, object] = {"click_targets": {"build": [11, 22], "orbit": [33, 44]}}
    assert _click_targets_dict(payload) == {"build": [11, 22], "orbit": [33, 44]}


def test_click_targets_dict_rejects_missing_or_non_dict_values() -> None:
    assert _click_targets_dict({}) is None
    assert _click_targets_dict({"click_targets": ["not", "a", "dict"]}) is None


def test_has_required_click_targets_requires_build_and_orbit_lists() -> None:
    assert _has_required_click_targets({"click_targets": {"build": [1, 2], "orbit": [3, 4]}})
    assert not _has_required_click_targets({"click_targets": {"build": [1, 2]}})
    assert not _has_required_click_targets({"click_targets": {"build": [1, 2], "orbit": "bad"}})
    assert not _has_required_click_targets({"click_targets": "bad"})


def test_pending_click_source_returns_normalized_expected_source() -> None:
    assert _pending_click_source(
        payload={"awaiting_os_clicks": True, "pending_source": " Debug "},
        expected_sources=["palette", "debug"],
        clicked_set=set(),
    ) == "debug"


def test_pending_click_source_filters_non_pending_or_duplicate_sources() -> None:
    assert (
        _pending_click_source(
            payload={"awaiting_os_clicks": False, "pending_source": "debug"},
            expected_sources=["debug"],
            clicked_set=set(),
        )
        is None
    )
    assert (
        _pending_click_source(
            payload={"awaiting_os_clicks": True, "pending_source": "debug"},
            expected_sources=["debug"],
            clicked_set={"debug"},
        )
        is None
    )
    assert (
        _pending_click_source(
            payload={"awaiting_os_clicks": True, "pending_source": "viewport"},
            expected_sources=["debug"],
            clicked_set=set(),
        )
        is None
    )


@pytest.mark.optional
def test_real_window_click_routing_with_tool_windows(nbttool: ModuleType, tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("real-window click smoke requires macOS")
    if not _is_accessibility_trusted():
        pytest.fail(
            "macOS Accessibility permission is required for OS-level click injection. "
            "Enable it for the Python interpreter running pytest (System Settings -> Privacy & Security -> Accessibility)."
        )

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for real-window click smoke.")

    repo_root = Path(__file__).resolve().parents[1]
    smoke_out = tmp_path / "smoke_real_window_click.json"
    cmd = [
        sys.executable,
        "-m",
        "nbttool",
        "datapack-view",
        str(jar_path),
        "--select",
        label,
        "--smoke-real-window-click",
        "--smoke-timeout",
        "45",
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
    clicked_sources: list[str] = []
    expected_sources = ["palette", "debug", "param", "viewport"]
    clicked_set: set[str] = set()
    try:
        _wait_for_click_targets(smoke_out, timeout_s=30.0)
        deadline = time.monotonic() + 95.0
        while time.monotonic() < deadline:
            payload = _load_json_if_ready(smoke_out)
            if isinstance(payload, dict):
                if bool(payload.get("ok")):
                    break
                source = _pending_click_source(
                    payload=payload,
                    expected_sources=expected_sources,
                    clicked_set=clicked_set,
                )
                if source is not None:
                    targets = _click_targets_dict(payload)
                    assert targets is not None, payload
                    build_x, build_y = _target_xy(targets, "build")
                    _post_left_click(proc.pid, x=build_x, y=build_y, option_modified=False)
                    clicked_set.add(source)
                    clicked_sources.append(source)
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
    assert payload.get("smoke_mode") == "real_window_click", payload
    assert clicked_sources == expected_sources, payload

    tool_windows = payload.get("tool_windows")
    assert isinstance(tool_windows, dict), payload
    assert "palette" in tool_windows, payload
    assert "debug" in tool_windows, payload
    assert "param" in tool_windows, payload

    counts = payload.get("click_counts")
    assert isinstance(counts, dict), payload
    assert int(counts.get("focus_probe_hits", 0)) >= 4, payload
    hits_by_source = counts.get("hits_by_source")
    assert isinstance(hits_by_source, dict), payload
    for source in expected_sources:
        assert int(hits_by_source.get(source, 0)) >= 1, payload

    validated = payload.get("validated_sources")
    assert isinstance(validated, list), payload
    assert [str(x) for x in validated] == expected_sources, payload
