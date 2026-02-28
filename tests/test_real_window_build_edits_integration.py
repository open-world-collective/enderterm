"""Optional macOS real-window build-edits integration smoke.

Validates that build place/remove work when driven via OS-level mouse injection
(real cursor movement + real click routing), not via internal method calls.

Prerequisites:
- macOS desktop session (not headless).
- Accessibility permission granted to the Python interpreter running pytest.
- `MINECRAFT_JAR` points to an existing client jar.

Run:
  MINECRAFT_JAR=/path/to/client.jar \\
  /Users/qarl/tmp/venv/enderterm311/bin/python -m pytest -q --run-optional \\
    tests/test_real_window_build_edits_integration.py
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile
from types import ModuleType

import pytest


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("w", ctypes.c_double), ("h", ctypes.c_double)]


_MAIN_DISPLAY_W: int | None = None
_MAIN_DISPLAY_H: int | None = None


_KCG_EVENT_SOURCE_STATE_HID_SYSTEM_STATE = 1
_KCG_EVENT_LEFT_MOUSE_DOWN = 1
_KCG_EVENT_LEFT_MOUSE_UP = 2
_KCG_EVENT_RIGHT_MOUSE_DOWN = 3
_KCG_EVENT_RIGHT_MOUSE_UP = 4
_KCG_EVENT_MOUSE_MOVED = 5
_KCG_MOUSE_BUTTON_LEFT = 0
_KCG_MOUSE_BUTTON_RIGHT = 1
_KCG_HID_EVENT_TAP = 0


def _find_smoke_label(nbttool: ModuleType, *, jar_path: Path, tmp_path: Path) -> str | None:
    template_id: str | None = None
    with zipfile.ZipFile(jar_path, "r") as zf:
        dp_source = nbttool.DatapackSource(jar_path, zf)
        stack = nbttool.PackStack(work_dir=tmp_path / "work-pack", vendors=[dp_source])
        index = nbttool.JigsawDatapackIndex(stack.source)

        jigsaw_structures = nbttool.list_worldgen_jigsaw_structures(stack)
        if not jigsaw_structures:
            return None

        for structure_id in jigsaw_structures[:50]:
            obj = stack.source.read_json(nbttool.canonical_worldgen_structure_json(structure_id)) or {}
            start_pool = obj.get("start_pool")
            if not isinstance(start_pool, str) or not start_pool:
                continue
            pool_def = index.load_pool(start_pool)
            for elem in pool_def.elements[:50]:
                tmpl = index.load_template(elem.location_id)
                if tmpl is None:
                    continue
                open_conns = [
                    c for c in tmpl.connectors if c.pool not in {"", "minecraft:empty"} and c.target not in {"", "minecraft:empty"}
                ]
                if open_conns:
                    template_id = tmpl.template_id
                    break
            if template_id is not None:
                break

    if template_id is None:
        return None
    # nbttool datapack-view lists NBT structures as `namespace/path/...`, not `namespace:path/...`.
    return template_id.replace(":", "/", 1)


def _application_services() -> ctypes.CDLL:
    return ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")


def _is_accessibility_trusted() -> bool:
    if sys.platform != "darwin":
        return False
    app = _application_services()
    app.AXIsProcessTrusted.argtypes = []
    app.AXIsProcessTrusted.restype = ctypes.c_bool
    return bool(app.AXIsProcessTrusted())


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


def _post_left_click(*, x: int, y: int) -> None:
    x, y = _to_quartz_xy(x=x, y=y)
    _animate_mouse_to(x=x, y=y)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_MOUSE_MOVED, button=_KCG_MOUSE_BUTTON_LEFT)
    time.sleep(0.03)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_LEFT_MOUSE_DOWN, button=_KCG_MOUSE_BUTTON_LEFT)
    time.sleep(0.03)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_LEFT_MOUSE_UP, button=_KCG_MOUSE_BUTTON_LEFT)


def _post_right_click(*, x: int, y: int) -> None:
    x, y = _to_quartz_xy(x=x, y=y)
    _animate_mouse_to(x=x, y=y)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_MOUSE_MOVED, button=_KCG_MOUSE_BUTTON_RIGHT)
    time.sleep(0.03)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_RIGHT_MOUSE_DOWN, button=_KCG_MOUSE_BUTTON_RIGHT)
    time.sleep(0.03)
    _post_mouse_event(x=x, y=y, event_type=_KCG_EVENT_RIGHT_MOUSE_UP, button=_KCG_MOUSE_BUTTON_RIGHT)


def _load_json_if_ready(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@pytest.mark.optional
def test_real_window_build_edits_via_os_injection(nbttool: ModuleType, tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("real-window build-edits smoke requires macOS")
    if not _is_accessibility_trusted():
        pytest.fail(
            "macOS Accessibility permission is required for OS-level click injection. "
            "Enable it for the Python interpreter running pytest (System Settings -> Privacy & Security -> Accessibility)."
        )

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for real-window build-edits smoke.")

    repo_root = Path(__file__).resolve().parents[1]
    smoke_out = tmp_path / "smoke_real_window_build_edits.json"
    cmd = [
        sys.executable,
        "-m",
        "nbttool",
        "datapack-view",
        str(jar_path),
        "--select",
        label,
        "--smoke-real-window-build-edits",
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
    clicked_actions: list[str] = []
    clicked_set: set[str] = set()
    try:
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            payload = _load_json_if_ready(smoke_out)
            if isinstance(payload, dict):
                if bool(payload.get("ok")):
                    break
                if bool(payload.get("awaiting_os_click")):
                    action = str(payload.get("pending_action", "")).strip()
                    button = str(payload.get("click_button", "")).strip().lower()
                    raw_target = payload.get("click_target")
                    if action and action not in clicked_set:
                        assert isinstance(raw_target, list) and len(raw_target) == 2, payload
                        x = int(raw_target[0])
                        y = int(raw_target[1])
                        if button == "right":
                            _post_right_click(x=x, y=y)
                        elif button == "left":
                            _post_left_click(x=x, y=y)
                        else:
                            raise AssertionError(f"unknown click_button={button!r} payload={payload}")
                        clicked_set.add(action)
                        clicked_actions.append(action)
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
    assert payload.get("smoke_mode") == "real_window_build_edits", payload

    info = payload.get("build_edits")
    assert isinstance(info, dict), payload
    actions = info.get("actions")
    assert isinstance(actions, list) and actions, info
    expected_actions = [str(a.get("name", "")) for a in actions if isinstance(a, dict)]
    assert clicked_actions == expected_actions, {"clicked": clicked_actions, "expected": expected_actions}

    placed_ok = info.get("placed_ok")
    removed_ok = info.get("removed_ok")
    assert isinstance(placed_ok, list) and len(placed_ok) == 2, info
    assert isinstance(removed_ok, list) and len(removed_ok) == 2, info
