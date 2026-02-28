"""Optional macOS native-close regression matrix smoke.

This smoke reuses focus-handoff close mechanics and runs a repeatable matrix over
native close paths for debug/viewport combinations.

Run:
  MINECRAFT_JAR=/path/to/client.jar \
  PYTHONPATH=. pytest -q --run-optional tests/test_real_window_native_close_integration.py
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Sequence
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


def _normalize_focus_sources(sources: Sequence[object]) -> list[str]:
    return [str(src).strip().lower() for src in sources if str(src).strip()]


def _expected_click_actions(expected_sources: Sequence[str]) -> list[tuple[str, str]]:
    actions: list[tuple[str, str]] = []
    for src in expected_sources:
        actions.append((str(src), "close_button"))
        actions.append((str(src), "main_probe"))
    return actions


def _assert_main_signature(
    *,
    case_name: str,
    shot: object,
    label: str,
) -> dict[str, object]:
    assert isinstance(shot, dict), f"{case_name}: missing {label}: {shot!r}"
    sig = shot.get("main_signature")
    assert isinstance(sig, dict), f"{case_name}: missing {label}.main_signature: {shot!r}"
    assert "error" not in sig, f"{case_name}: signature error in {label}: {shot!r}"
    dhash = sig.get("dhash64")
    assert isinstance(dhash, str) and len(dhash) == 16, f"{case_name}: invalid dhash in {label}: {sig!r}"
    return sig


def _assert_not_mostly_white(
    *,
    case_name: str,
    signature: dict[str, object],
    label: str,
) -> None:
    mean_luma = float(signature.get("mean_luma", 0.0))
    # Native-close/mouse-probe should not collapse the main viewport to a white frame.
    assert 0.5 <= mean_luma <= 235.0, f"{case_name}: suspicious main luma in {label}: {mean_luma:.3f}"


def _run_native_close_case(
    *,
    jar_path: Path,
    label: str,
    repo_root: Path,
    case_name: str,
    focus_sources: list[str],
    preopen_sources: list[str],
    smoke_out: Path,
) -> tuple[dict[str, object], str]:
    expected_sources = _normalize_focus_sources(focus_sources)
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
    env["ENDERTERM_SMOKE_FOCUS_SOURCES"] = ",".join(expected_sources)
    if preopen_sources:
        env["ENDERTERM_SMOKE_FOCUS_PREOPEN_SOURCES"] = ",".join(_normalize_focus_sources(preopen_sources))
    else:
        env.pop("ENDERTERM_SMOKE_FOCUS_PREOPEN_SOURCES", None)
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
    expected_actions = _expected_click_actions(expected_sources)
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
                        assert isinstance(raw_target, list) and len(raw_target) == 2, f"{case_name}: {payload}"
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

    assert proc.returncode == 0, f"{case_name}: {(out or '')[-4000:]}"
    payload = _load_json_if_ready(smoke_out)
    assert isinstance(payload, dict), f"missing smoke payload at {smoke_out}"
    assert payload.get("ok") is True, f"{case_name}: {payload}"
    assert payload.get("smoke_mode") == "focus_handoff", f"{case_name}: {payload}"
    assert clicked_actions == expected_actions, f"{case_name}: {payload}"
    return payload, out


def _assert_native_close_case_payload(
    *,
    case_name: str,
    payload: dict[str, object],
    expected_sources: list[str],
    expected_preopen_sources: list[str],
) -> None:
    focus_sources = payload.get("focus_sources")
    assert isinstance(focus_sources, list), f"{case_name}: {payload}"
    assert [str(x) for x in focus_sources] == expected_sources, f"{case_name}: {payload}"

    preopen = payload.get("focus_preopen_sources")
    assert isinstance(preopen, list), f"{case_name}: {payload}"
    assert [str(x) for x in preopen] == expected_preopen_sources, f"{case_name}: {payload}"

    validated = payload.get("validated_sources")
    assert isinstance(validated, list), f"{case_name}: {payload}"
    assert [str(x) for x in validated] == expected_sources, f"{case_name}: {payload}"

    focus_probe = payload.get("focus_probe")
    assert isinstance(focus_probe, dict), f"{case_name}: {payload}"
    hits_by_source = focus_probe.get("hits_by_source")
    assert isinstance(hits_by_source, dict), f"{case_name}: {payload}"
    for source in expected_sources:
        assert int(hits_by_source.get(source, 0)) >= 1, f"{case_name}: {payload}"

    close_paths = payload.get("close_path_by_source")
    assert isinstance(close_paths, dict), f"{case_name}: {payload}"
    for source in expected_sources:
        assert str(close_paths.get(source, "")).startswith("os_close_button"), f"{case_name}: {payload}"

    child_close_used = payload.get("child_close_path_used_by_source")
    assert isinstance(child_close_used, dict), f"{case_name}: {payload}"
    for source in ("palette", "debug", "param", "viewport"):
        if source not in expected_sources:
            continue
        assert bool(child_close_used.get(source)) is False, f"{case_name}: {payload}"

    window_diag = payload.get("window_diag_by_source")
    assert isinstance(window_diag, dict), f"{case_name}: {payload}"
    main_diag = window_diag.get("main")
    assert isinstance(main_diag, dict), f"{case_name}: {payload}"
    assert "key_focus_diag" in main_diag, f"{case_name}: {payload}"

    viewport_cycle_count = sum(1 for source in expected_sources if str(source) == "viewport")
    if viewport_cycle_count < 1:
        return

    baseline = payload.get("viewport_baseline_main_shot")
    baseline_sig = _assert_main_signature(case_name=case_name, shot=baseline, label="viewport_baseline_main_shot")
    _assert_not_mostly_white(case_name=case_name, signature=baseline_sig, label="viewport_baseline_main_shot")

    close_shots = payload.get("viewport_close_main_shots")
    assert isinstance(close_shots, list), f"{case_name}: {payload}"
    assert len(close_shots) >= int(viewport_cycle_count), f"{case_name}: {payload}"

    post_probe_shots = payload.get("viewport_post_main_probe_shots")
    assert isinstance(post_probe_shots, list), f"{case_name}: {payload}"
    assert len(post_probe_shots) >= int(viewport_cycle_count), f"{case_name}: {payload}"

    for idx in range(int(viewport_cycle_count)):
        cycle = int(idx + 1)
        close_shot = close_shots[idx]
        post_probe_shot = post_probe_shots[idx]

        close_sig = _assert_main_signature(
            case_name=case_name,
            shot=close_shot,
            label=f"viewport_close_main_shots[{idx}]",
        )
        _assert_not_mostly_white(
            case_name=case_name,
            signature=close_sig,
            label=f"viewport_close_main_shots[{idx}]",
        )
        post_probe_sig = _assert_main_signature(
            case_name=case_name,
            shot=post_probe_shot,
            label=f"viewport_post_main_probe_shots[{idx}]",
        )
        _assert_not_mostly_white(
            case_name=case_name,
            signature=post_probe_sig,
            label=f"viewport_post_main_probe_shots[{idx}]",
        )

        assert int(close_shot.get("cycle_index") or 0) == int(cycle), f"{case_name}: {payload}"
        assert int(post_probe_shot.get("cycle_index") or 0) == int(cycle), f"{case_name}: {payload}"

        probe_vs_close = post_probe_shot.get("comparison_vs_cycle_close")
        assert isinstance(probe_vs_close, dict), f"{case_name}: {payload}"
        dhash_hamming = int(probe_vs_close.get("dhash_hamming", -1))
        assert 0 <= dhash_hamming <= 32, f"{case_name}: {payload}"
        ratio_raw = probe_vs_close.get("mean_luma_ratio")
        assert ratio_raw is not None, f"{case_name}: {payload}"
        luma_ratio = float(ratio_raw)
        assert 0.55 <= luma_ratio <= 1.75, f"{case_name}: {payload}"


def test_normalize_focus_sources_strips_lowers_and_drops_empty() -> None:
    assert _normalize_focus_sources([" debug ", "", " ", "VIEWPORT", "Param"]) == ["debug", "viewport", "param"]


def test_expected_click_actions_builds_source_ordered_pairs() -> None:
    assert _expected_click_actions(["debug", "viewport"]) == [
        ("debug", "close_button"),
        ("debug", "main_probe"),
        ("viewport", "close_button"),
        ("viewport", "main_probe"),
    ]


def test_expected_click_actions_preserves_duplicate_sources() -> None:
    assert _expected_click_actions(["debug", "debug"]) == [
        ("debug", "close_button"),
        ("debug", "main_probe"),
        ("debug", "close_button"),
        ("debug", "main_probe"),
    ]


@pytest.mark.optional
def test_native_close_first_click_reliability_matrix(nbttool: ModuleType, tmp_path: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("real-window native-close smoke requires macOS")
    if not _is_accessibility_trusted():
        pytest.fail(
            "macOS Accessibility permission is required for OS-level click injection. "
            "Enable it for the Python interpreter running pytest (System Settings -> Privacy & Security -> Accessibility)."
        )

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for native-close smoke.")

    repo_root = Path(__file__).resolve().parents[1]
    matrix: list[dict[str, object]] = [
        {
            "name": "debug_close_with_viewport_open",
            "focus_sources": ["debug"],
            "preopen_sources": ["viewport"],
            "repeats": 3,
        },
        {
            "name": "viewport_close_with_debug_open",
            "focus_sources": ["viewport"],
            "preopen_sources": ["debug"],
            "repeats": 1,
        },
        {
            "name": "debug_close_with_palette_and_viewport_open",
            "focus_sources": ["debug"],
            "preopen_sources": ["palette", "viewport"],
            "repeats": 1,
        },
    ]

    for case in matrix:
        base_name = str(case["name"])
        focus_sources = [str(src) for src in list(case["focus_sources"])]  # type: ignore[index]
        preopen_sources = [str(src) for src in list(case["preopen_sources"])]  # type: ignore[index]
        repeats = int(case["repeats"])
        for idx in range(repeats):
            case_name = f"{base_name}#{idx + 1}"
            smoke_out = tmp_path / f"smoke_native_close_{base_name}_{idx + 1}.json"
            payload, _out = _run_native_close_case(
                jar_path=jar_path,
                label=label,
                repo_root=repo_root,
                case_name=case_name,
                focus_sources=focus_sources,
                preopen_sources=preopen_sources,
                smoke_out=smoke_out,
            )
            _assert_native_close_case_payload(
                case_name=case_name,
                payload=payload,
                expected_sources=focus_sources,
                expected_preopen_sources=preopen_sources,
            )
