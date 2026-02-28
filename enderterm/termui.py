from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import secrets
from typing import Any, Callable, Iterable


RGBA = tuple[int, int, int, int]


@dataclass(slots=True)
class TermCell:
    ch: str = " "
    fg: RGBA = (255, 255, 255, 255)
    bg: RGBA = (0, 0, 0, 0)


@dataclass(slots=True)
class TermSprite:
    """A textured quad aligned to the terminal cell grid (top-left origin)."""

    x: int
    y: int
    w: int
    h: int
    target: int
    tex_id: int
    tex_coords: tuple[float, ...]
    tint: RGBA = (255, 255, 255, 255)


@dataclass(slots=True)
class TermHotspot:
    """A cell-rect hit target for clicks/hover in terminal-space."""

    x: int
    y: int
    w: int
    h: int
    kind: str
    payload: Any | None = None


@dataclass(slots=True)
class TermScroll:
    """Shared scroll state for TermUI lists/grids.

    Stores scroll as a float to allow fractional trackpad deltas; callers can
    snap to integer rows/entries via int(pos_f).
    """

    pos_f: float = 0.0
    follow_selection: bool = True

    def clamp(self, max_scroll: int) -> int:
        max_scroll = max(0, int(max_scroll))
        if max_scroll <= 0:
            self.pos_f = 0.0
            return 0
        clamped = min(max(float(self.pos_f), 0.0), float(max_scroll))
        self.pos_f = clamped
        return int(clamped)

    def scroll_wheel(self, scroll_y: float, *, max_scroll: int) -> int:
        self.follow_selection = False
        try:
            dy = float(scroll_y)
        except Exception:
            dy = 0.0
        self.pos_f = float(self.pos_f) - dy
        return self.clamp(int(max_scroll))

    def ensure_visible(self, sel_pos: int | None, *, visible: int, max_scroll: int) -> int:
        max_scroll = max(0, int(max_scroll))
        visible = max(1, int(visible))
        if sel_pos is None:
            self.pos_f = 0.0
            return 0
        pos = int(sel_pos)
        top = int(self.clamp(max_scroll))
        if pos < top:
            self.pos_f = float(pos)
        elif pos >= top + visible:
            self.pos_f = float(pos - visible + 1)
        return self.clamp(max_scroll)


@dataclass(slots=True)
class TermScrollbar:
    """Row-oriented scrollbar model used by terminal lists."""

    track_top: int = 0
    track_rows: int = 0
    thumb_top: int = 0
    thumb_rows: int = 0
    visible_rows: int = 1
    total_rows: int = 0
    max_scroll: int = 0
    drag_active: bool = False
    _drag_offset_rows: int = 0

    def update(
        self,
        *,
        track_top: int,
        track_rows: int,
        visible_rows: int,
        total_rows: int,
        scroll_top: int,
    ) -> None:
        self.track_top = int(track_top)
        self.track_rows = max(0, int(track_rows))
        self.visible_rows = max(1, int(visible_rows))
        self.total_rows = max(0, int(total_rows))
        self.max_scroll = max(0, int(self.total_rows) - int(self.visible_rows))
        if self.track_rows <= 0:
            self.thumb_rows = 0
            self.thumb_top = int(self.track_top)
            self.drag_active = False
            self._drag_offset_rows = 0
            return

        top = max(0, min(int(self.max_scroll), int(scroll_top)))
        total_e = max(1, int(self.total_rows))
        thumb_rows = max(1, int(round(float(self.visible_rows) / float(total_e) * float(self.track_rows))))
        if thumb_rows > self.track_rows:
            thumb_rows = int(self.track_rows)
        self.thumb_rows = int(thumb_rows)

        travel = max(0, int(self.track_rows) - int(self.thumb_rows))
        thumb_rel = 0
        if self.max_scroll > 0 and travel > 0:
            thumb_rel = int(round(float(top) / float(self.max_scroll) * float(travel)))
        self.thumb_top = int(self.track_top) + int(thumb_rel)

    def hit_test(self, *, row: int) -> str | None:
        if self.track_rows <= 0:
            return None
        ry = int(row)
        if ry < int(self.track_top) or ry >= int(self.track_top) + int(self.track_rows):
            return None
        if ry < int(self.thumb_top):
            return "track_before"
        if ry >= int(self.thumb_top) + int(self.thumb_rows):
            return "track_after"
        return "thumb"

    def begin_drag(self, *, row: int) -> bool:
        if self.hit_test(row=int(row)) != "thumb":
            return False
        self.drag_active = True
        self._drag_offset_rows = max(0, int(row) - int(self.thumb_top))
        return True

    def drag_to(self, *, row: int) -> float | None:
        if not self.drag_active:
            return None
        if self.max_scroll <= 0 or self.track_rows <= 0:
            return 0.0
        travel = max(1, int(self.track_rows) - int(self.thumb_rows))
        thumb_rel = int(row) - int(self.track_top) - int(self._drag_offset_rows)
        if thumb_rel < 0:
            thumb_rel = 0
        if thumb_rel > travel:
            thumb_rel = travel
        return float(thumb_rel) * float(self.max_scroll) / float(travel)

    def end_drag(self) -> None:
        self.drag_active = False
        self._drag_offset_rows = 0

    def _clamp_scroll_for_page_click(self, current_scroll: float) -> float:
        """Normalize page-click starting scroll to the scrollbar's legal range."""
        if self.max_scroll <= 0:
            return 0.0
        cur = float(current_scroll)
        if cur < 0.0:
            return 0.0
        max_scroll_f = float(self.max_scroll)
        if cur > max_scroll_f:
            return max_scroll_f
        return cur

    def press(self, *, row: int, current_scroll: float) -> tuple[bool, float | None, bool]:
        """Handle a left-button press on the scrollbar track.

        Returns ``(drag_started, new_scroll, consumed)``.
        Thumb presses arm dragging but do not consume the click so list-row
        single-click selection still runs on first press.
        """
        hit = self.hit_test(row=int(row))
        if hit is None:
            return (False, None, False)
        if hit == "thumb":
            return (self.begin_drag(row=int(row)), None, False)
        return (False, self.track_click(row=int(row), current_scroll=float(current_scroll)), True)

    def track_click(self, *, row: int, current_scroll: float) -> float | None:
        hit = self.hit_test(row=int(row))
        if hit is None or hit == "thumb":
            return None
        cur = self._clamp_scroll_for_page_click(current_scroll)
        page = max(1, int(self.visible_rows))
        if hit == "track_before":
            return max(0.0, cur - float(page))
        return min(float(self.max_scroll), cur + float(page))


@dataclass(slots=True)
class TermMouseCapture:
    """Pointer capture state for deterministic mouse routing across contexts."""

    context_id: str = ""
    target_id: str = ""
    button: int = 0
    active: bool = False

    def begin(self, *, context_id: str, target_id: str, button: int) -> None:
        self.context_id = str(context_id)
        self.target_id = str(target_id)
        self.button = int(button)
        self.active = True

    def clear(self) -> None:
        self.context_id = ""
        self.target_id = ""
        self.button = 0
        self.active = False

    def matches(self, *, context_id: str, target_id: str, button: int) -> bool:
        if not self.active:
            return False
        return (
            str(self.context_id) == str(context_id)
            and str(self.target_id) == str(target_id)
            and int(self.button) == int(button)
        )


@dataclass(frozen=True, slots=True)
class TermMouseRouteResult:
    drag_started: bool
    new_scroll: float | None
    consumed: bool


def route_term_scrollbar_press(
    *,
    capture: TermMouseCapture,
    context_id: str,
    target_id: str,
    scrollbar: TermScrollbar,
    row: int,
    current_scroll: float,
    button: int = 1,
    consume_thumb_press: bool = False,
) -> TermMouseRouteResult:
    drag_started, new_scroll, consumed = scrollbar.press(row=int(row), current_scroll=float(current_scroll))
    if drag_started and bool(consume_thumb_press):
        consumed = True
    if drag_started:
        capture.begin(context_id=str(context_id), target_id=str(target_id), button=int(button))
    return TermMouseRouteResult(bool(drag_started), new_scroll, bool(consumed))


def _mouse_route_matches_capture(
    *,
    capture: TermMouseCapture,
    context_id: str,
    target_id: str,
    button: int,
) -> bool:
    return capture.matches(context_id=str(context_id), target_id=str(target_id), button=int(button))


def _mouse_route_release_capture(*, capture: TermMouseCapture, scrollbar: TermScrollbar) -> None:
    scrollbar.end_drag()
    capture.clear()


def route_term_scrollbar_drag(
    *,
    capture: TermMouseCapture,
    context_id: str,
    target_id: str,
    scrollbar: TermScrollbar,
    row: int,
    left_button_down: bool,
    button: int = 1,
) -> TermMouseRouteResult:
    if not _mouse_route_matches_capture(
        capture=capture,
        context_id=str(context_id),
        target_id=str(target_id),
        button=int(button),
    ):
        return TermMouseRouteResult(False, None, False)
    if not bool(left_button_down):
        _mouse_route_release_capture(capture=capture, scrollbar=scrollbar)
        return TermMouseRouteResult(False, None, True)
    return TermMouseRouteResult(False, scrollbar.drag_to(row=int(row)), True)


def route_term_scrollbar_release(
    *,
    capture: TermMouseCapture,
    context_id: str,
    target_id: str,
    scrollbar: TermScrollbar,
    button: int = 1,
) -> bool:
    if not _mouse_route_matches_capture(
        capture=capture,
        context_id=str(context_id),
        target_id=str(target_id),
        button=int(button),
    ):
        return False
    _mouse_route_release_capture(capture=capture, scrollbar=scrollbar)
    return True


def _normalize_tool_window_click_mode(mode: object) -> str:
    return str(mode or "").strip().lower()


def _tool_window_click_overlay_mode(mode: str) -> bool:
    """Return whether mouse-click routing should run overlay handler logic."""
    return _normalize_tool_window_click_mode(mode) == "overlay"


def _tool_window_click_handler_result(click_handler: Callable[[], bool] | None) -> bool:
    """Evaluate click handler in a fail-closed way for routing decisions."""
    if click_handler is None:
        return False
    try:
        return bool(click_handler())
    except Exception:
        return False


def route_tool_window_click(
    *,
    mode: str,
    click_handler: Callable[[], bool] | None = None,
) -> bool:
    """Route optional tool-window click handling for main-window events.

    ``mode="window"`` means the tool UI is detached in a separate native
    window, so it must never consume clicks from the main viewport.
    ``mode="overlay"`` preserves the old in-window overlay semantics where a
    handler can consume the click.
    """
    if not _tool_window_click_overlay_mode(mode):
        return False
    return _tool_window_click_handler_result(click_handler)


def _resolve_ns_window_view(window: object) -> tuple[object | None, object | None]:
    ns_window = getattr(window, "_nswindow", None)
    ns_view = getattr(window, "_nsview", None)
    if ns_window is not None and ns_view is not None:
        return (ns_window, ns_view)
    try:
        ctx = getattr(window, "context", None)
        canvas = getattr(ctx, "canvas", None) if ctx is not None else None
        if ns_window is None:
            ns_window = getattr(canvas, "_nswindow", None) if canvas is not None else None
        if ns_view is None:
            ns_view = getattr(canvas, "_nsview", None) if canvas is not None else None
    except Exception:
        pass
    return (ns_window, ns_view)


def _shared_ns_application() -> object | None:
    try:
        from pyglet.libs.darwin import cocoapy

        return cocoapy.NSApplication.sharedApplication()
    except Exception:
        return None


def _shared_running_application() -> object | None:
    try:
        from pyglet.libs.darwin import cocoapy

        app_cls = cocoapy.ObjCClass("NSRunningApplication")
        current_method = getattr(app_cls, "currentApplication", None)
        if callable(current_method):
            return current_method()
    except Exception:
        pass
    return None


def _objc_objects_match(a: object | None, b: object | None) -> bool:
    if a is None or b is None:
        return False
    if a == b:
        return True
    for accessor in (lambda x: int(x), lambda x: int(getattr(x, "value")), lambda x: int(getattr(x, "ptr"))):
        try:
            if accessor(a) == accessor(b):
                return True
        except Exception:
            continue
    return False


_FOCUS_HANDOFF_WINDOW_CALLS: tuple[tuple[str, tuple[object, ...]], ...] = (
    ("activate", ()),
    ("switch_to", ()),
)

# NSApplicationActivateAllWindows | NSApplicationActivateIgnoringOtherApps
_FOCUS_HANDOFF_RUNNING_APP_CALLS: tuple[tuple[str, tuple[object, ...]], ...] = (
    ("activateWithOptions_", (3,)),
)

_FOCUS_HANDOFF_APP_CALLS: tuple[tuple[str, tuple[object, ...]], ...] = (
    # NSApplicationActivationPolicyRegular (0) ensures activate can promote a
    # key window for this process.
    ("setActivationPolicy_", (0,)),
    ("unhide_", (None,)),
    ("activateIgnoringOtherApps_", (True,)),
)

_FOCUS_HANDOFF_NS_WINDOW_CALLS: tuple[tuple[str, tuple[object, ...]], ...] = (
    ("orderFront_", (None,)),
    ("makeKeyAndOrderFront_", (None,)),
    ("makeMainWindow", ()),
    ("makeKeyWindow", ()),
    ("orderFrontRegardless", ()),
)


def _focus_window_unavailable(window: object | None) -> bool:
    if window is None:
        return True
    if bool(getattr(window, "_closing", False)):
        return True
    if bool(getattr(window, "has_exit", False)):
        return True
    return False


def _try_call_method(target: object | None, method_name: str, *args: object) -> bool:
    if target is None:
        return False
    fn = getattr(target, str(method_name), None)
    if not callable(fn):
        return False
    try:
        fn(*args)
        return True
    except Exception:
        return False


def _try_call_methods(target: object | None, calls: tuple[tuple[str, tuple[object, ...]], ...]) -> bool:
    called = False
    for method_name, args in calls:
        called = _try_call_method(target, str(method_name), *tuple(args)) or called
    return called


def window_key_focus_diagnostics(window: object) -> dict[str, bool]:
    """Strict key-focus diagnostics for a window.

    Strict success requires:
    - NSApp active
    - this window is key (isKeyWindow OR NSApp.keyWindow matches)
    """
    diag: dict[str, bool] = {
        "app_active": False,
        "is_key_window": False,
        "key_window_match": False,
        "strict": False,
    }
    if _focus_window_unavailable(window):
        return diag

    ns_window, _ns_view = _resolve_ns_window_view(window)
    if ns_window is None:
        return diag

    is_key = getattr(ns_window, "isKeyWindow", None)
    if callable(is_key):
        try:
            diag["is_key_window"] = bool(is_key())
        except Exception:
            pass

    app = _shared_ns_application()
    if app is not None:
        app_is_active = getattr(app, "isActive", None)
        if callable(app_is_active):
            try:
                diag["app_active"] = bool(app_is_active())
            except Exception:
                pass
        key_window_method = getattr(app, "keyWindow", None)
        if callable(key_window_method):
            try:
                key_window = key_window_method()
                diag["key_window_match"] = _objc_objects_match(key_window, ns_window)
            except Exception:
                pass

    if not diag["app_active"]:
        running_app = _shared_running_application()
        if running_app is not None:
            running_is_active = getattr(running_app, "isActive", None)
            if callable(running_is_active):
                try:
                    diag["app_active"] = bool(running_is_active())
                except Exception:
                    pass

    diag["strict"] = bool(diag["app_active"] and (diag["is_key_window"] or diag["key_window_match"]))
    return diag


def window_has_key_focus(window: object) -> bool:
    """Return whether a window currently has strict key focus semantics."""
    return bool(window_key_focus_diagnostics(window).get("strict", False))


def handoff_window_focus(window: object) -> bool:
    """Best-effort window focus/context handoff after auxiliary window close."""
    if _focus_window_unavailable(window):
        return False

    focused = _try_call_methods(window, _FOCUS_HANDOFF_WINDOW_CALLS)

    ns_window, ns_view = _resolve_ns_window_view(window)
    if ns_window is not None:
        running_app = _shared_running_application()
        focused = _try_call_methods(running_app, _FOCUS_HANDOFF_RUNNING_APP_CALLS) or focused
        app = _shared_ns_application()
        if app is not None:
            focused = _try_call_methods(app, _FOCUS_HANDOFF_APP_CALLS) or focused
        focused = _try_call_methods(ns_window, _FOCUS_HANDOFF_NS_WINDOW_CALLS) or focused
        if ns_view is not None:
            focused = _try_call_method(ns_window, "makeFirstResponder_", ns_view) or focused
    return bool(focused or window_has_key_focus(window))


def _reset_keyboard_repeat_state(window: object) -> None:
    """Reset keyboard repeat fields during focus transitions.

    Focus changes can strand repeat-tracking fields if key-up events are
    consumed by another window. Keep this best-effort and tolerant of partial
    window stubs used in tests.
    """
    for attr, value in (
        ("_repeat_symbol", None),
        ("_repeat_hold_s", 0.0),
        ("_repeat_step_s", 0.0),
    ):
        if not hasattr(window, attr):
            continue
        try:
            setattr(window, attr, value)
        except Exception:
            pass


def route_window_focus_keyboard(
    *,
    window: object,
    activated: bool,
) -> bool:
    """Normalize keyboard routing around focus changes.

    Focus transitions can miss key-up events for held keys. Reset repeat state
    on any transition and, when activating, re-prime first-responder so the
    first keypress is not dropped.
    """
    _reset_keyboard_repeat_state(window)

    if not bool(activated):
        return False

    return _prime_window_keyboard_focus(window)


def _prime_window_keyboard_focus(window: object) -> bool:
    ns_window, ns_view = _resolve_ns_window_view(window)
    if ns_window is not None and ns_view is not None:
        try:
            ns_window.makeFirstResponder_(ns_view)
            return True
        except Exception:
            pass

    switch_to = getattr(window, "switch_to", None)
    if callable(switch_to):
        try:
            switch_to()
            return True
        except Exception:
            pass
    return False


class TerminalSurface:
    """A simple retained terminal-style grid (top-left origin)."""

    def __init__(self, cols: int, rows: int, *, default_fg: RGBA, default_bg: RGBA) -> None:
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))
        self.default_fg = tuple(int(v) for v in default_fg)
        self.default_bg = tuple(int(v) for v in default_bg)
        self._cells: list[TermCell] = [TermCell(" ", self.default_fg, self.default_bg) for _ in range(self.cols * self.rows)]
        self.sprites: list[TermSprite] = []
        self.hotspots: list[TermHotspot] = []
        self._hotspot_by_cell: list[int] = [-1] * (self.cols * self.rows)

    def resize(self, cols: int, rows: int) -> None:
        cols = max(1, int(cols))
        rows = max(1, int(rows))
        if cols == self.cols and rows == self.rows:
            return
        self.cols = cols
        self.rows = rows
        self._cells = [TermCell(" ", self.default_fg, self.default_bg) for _ in range(self.cols * self.rows)]
        self.sprites.clear()
        self.hotspots.clear()
        self._hotspot_by_cell = [-1] * (self.cols * self.rows)

    def clear_overlays(self) -> None:
        self.sprites.clear()
        self.hotspots.clear()
        self._hotspot_by_cell = [-1] * (self.cols * self.rows)

    def clear(self) -> None:
        fg = self.default_fg
        bg = self.default_bg
        for i in range(len(self._cells)):
            self._cells[i] = TermCell(" ", fg, bg)
        self.clear_overlays()

    def _resolve_colors(self, *, fg: RGBA | None, bg: RGBA | None) -> tuple[RGBA, RGBA]:
        fg2 = self.default_fg if fg is None else tuple(int(v) for v in fg)
        bg2 = self.default_bg if bg is None else tuple(int(v) for v in bg)
        return fg2, bg2

    def cell(self, x: int, y: int) -> TermCell | None:
        if x < 0 or y < 0 or x >= self.cols or y >= self.rows:
            return None
        return self._cells[y * self.cols + x]

    def _clip_rect_to_surface(self, *, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int] | None:
        if int(w) <= 0 or int(h) <= 0:
            return None
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(self.cols, int(x) + int(w))
        y1 = min(self.rows, int(y) + int(h))
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1, y1)

    def put(self, x: int, y: int, text: str, *, fg: RGBA | None = None, bg: RGBA | None = None) -> None:
        if not isinstance(text, str) or not text:
            return
        if y < 0 or y >= self.rows:
            return
        fg2, bg2 = self._resolve_colors(fg=fg, bg=bg)
        cx = int(x)
        for ch in text:
            if cx >= self.cols:
                break
            if cx >= 0:
                self._cells[y * self.cols + cx] = TermCell(ch, fg2, bg2)
            cx += 1

    def fill_rect(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        *,
        ch: str | None = None,
        fg: RGBA | None = None,
        bg: RGBA | None = None,
    ) -> None:
        clip = self._clip_rect_to_surface(x=int(x), y=int(y), w=int(w), h=int(h))
        if clip is None:
            return
        fg2, bg2 = self._resolve_colors(fg=fg, bg=bg)
        ch2 = " " if ch is None else (ch[0] if ch else " ")
        x0, y0, x1, y1 = clip
        for yy in range(y0, y1):
            row = yy * self.cols
            for xx in range(x0, x1):
                self._cells[row + xx] = TermCell(ch2, fg2, bg2)

    def draw_box(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        *,
        fg: RGBA | None = None,
        bg: RGBA | None = None,
        title: str | None = None,
    ) -> None:
        if w < 2 or h < 2:
            return
        fg2, bg2 = self._resolve_colors(fg=fg, bg=bg)
        x0 = int(x)
        y0 = int(y)
        x1 = int(x) + int(w) - 1
        y1 = int(y) + int(h) - 1

        def _put(xx: int, yy: int, ch: str) -> None:
            if 0 <= xx < self.cols and 0 <= yy < self.rows:
                self._cells[yy * self.cols + xx] = TermCell(ch, fg2, bg2)

        # Corners
        _put(x0, y0, "┌")
        _put(x1, y0, "┐")
        _put(x0, y1, "└")
        _put(x1, y1, "┘")
        # Edges
        for xx in range(x0 + 1, x1):
            _put(xx, y0, "─")
            _put(xx, y1, "─")
        for yy in range(y0 + 1, y1):
            _put(x0, yy, "│")
            _put(x1, yy, "│")

        if title:
            t = str(title)
            max_t = max(0, (x1 - x0 - 3))
            if max_t > 0:
                t = t[:max_t]
                self.put(x0 + 2, y0, t, fg=fg2, bg=bg2)

    def add_sprite(
        self,
        *,
        x: int,
        y: int,
        w: int,
        h: int,
        target: int,
        tex_id: int,
        tex_coords: tuple[float, ...],
        tint: RGBA | None = None,
    ) -> None:
        if w <= 0 or h <= 0:
            return
        if int(tex_id) <= 0:
            return
        self.sprites.append(
            TermSprite(
                x=int(x),
                y=int(y),
                w=int(w),
                h=int(h),
                target=int(target),
                tex_id=int(tex_id),
                tex_coords=tuple(float(v) for v in tex_coords),
                tint=(255, 255, 255, 255) if tint is None else tuple(int(v) for v in tint),
            )
        )

    def add_hotspot(self, *, x: int, y: int, w: int, h: int, kind: str, payload: Any | None = None) -> None:
        clip = self._clip_rect_to_surface(x=int(x), y=int(y), w=int(w), h=int(h))
        if clip is None:
            return
        x0, y0, x1, y1 = clip
        idx = len(self.hotspots)
        self.hotspots.append(
            TermHotspot(x=int(x0), y=int(y0), w=int(x1 - x0), h=int(y1 - y0), kind=str(kind), payload=payload)
        )
        for yy in range(y0, y1):
            base = yy * self.cols
            for xx in range(x0, x1):
                self._hotspot_by_cell[base + xx] = int(idx)

    def hotspot_at(self, x: int, y: int) -> TermHotspot | None:
        if x < 0 or y < 0 or x >= self.cols or y >= self.rows:
            return None
        idx = self._hotspot_by_cell[y * self.cols + x]
        if idx < 0:
            return None
        if idx >= len(self.hotspots):
            return None
        return self.hotspots[int(idx)]

    def iter_cells(self) -> Iterable[tuple[int, int, TermCell]]:
        for y in range(self.rows):
            row = y * self.cols
            for x in range(self.cols):
                yield x, y, self._cells[row + x]


class TerminalFont:
    def __init__(self, *, font_name: str | None, font_size_px: int) -> None:
        import pyglet

        self._font_name = font_name
        self._font_size_px = max(6, int(font_size_px))
        self._font = pyglet.font.load(font_name or "", self._font_size_px)
        g = self._font.get_glyphs("M")[0]
        self.cell_w = int(round(float(getattr(g, "advance", g.width))))
        self.cell_h = int(round(float(self._font.ascent - self._font.descent)))
        self._glyphs: dict[str, object] = {}
        self.ascent = float(self._font.ascent)
        self.descent = float(self._font.descent)

    @property
    def font_name(self) -> str | None:
        return self._font_name

    @property
    def font_size_px(self) -> int:
        return int(self._font_size_px)

    def glyph(self, ch: str) -> object:
        g = self._glyphs.get(ch)
        if g is not None:
            return g
        gs = self._font.get_glyphs(ch if ch else " ")
        g = gs[0] if gs else self._font.get_glyphs(" ")[0]
        self._glyphs[ch] = g
        return g


@dataclass(slots=True)
class AtlasGlyph:
    id: int
    target: int
    vertices: tuple[float, float, float, float]
    tex_coords: tuple[float, ...]


def _default_minecraft_ascii_png() -> Path | None:
    env = os.environ.get("ENDERTERM_ASCII_PNG") or os.environ.get("NBTTOOL_ASCII_PNG") or os.environ.get("MINECRAFT_ASCII_PNG")
    if env:
        try:
            p = Path(env).expanduser()
            if p.is_file():
                return p
        except Exception:
            pass
    try:
        # termui.py lives at: .../minecraft/worker/enderterm/termui.py
        # We want:            .../minecraft/font/pics/ascii.png
        root = Path(__file__).resolve().parents[2]
        p = root / "font" / "pics" / "ascii.png"
        if p.is_file():
            return p
    except Exception:
        pass
    try:
        bundled = Path(__file__).resolve().parent / "assets" / "fonts" / "ascii.png"
        if bundled.is_file():
            return bundled
    except Exception:
        pass
    return None


class MinecraftAsciiBitmapFont:
    """Minecraft-style 8x8 glyph atlas (ascii.png) with CP437 mapping."""

    def __init__(self, *, atlas_path: str | Path | None, cell_px: int) -> None:
        import pyglet
        from pyglet import gl

        if atlas_path is None:
            atlas_path = _default_minecraft_ascii_png()
        if atlas_path is None:
            raise FileNotFoundError("minecraft ascii.png not found (set $ENDERTERM_ASCII_PNG or $NBTTOOL_ASCII_PNG)")
        self._atlas_path = Path(atlas_path)
        self._cell_px = max(6, int(cell_px))

        img = pyglet.image.load(str(self._atlas_path))
        tex = img.get_texture()
        try:
            tex.mag_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
            tex.min_filter = gl.GL_NEAREST  # type: ignore[attr-defined]
        except Exception:
            pass
        self._tex = tex
        self._src_cell_w = max(1, int(getattr(tex, "width", 128)) // 16)
        self._src_cell_h = max(1, int(getattr(tex, "height", 128)) // 16)

        self.cell_w = int(self._cell_px)
        self.cell_h = int(self._cell_px)
        self.ascent = float(self._cell_px)
        self.descent = 0.0
        self._glyphs: dict[str, AtlasGlyph] = {}

    def _glyph_index(self, ch: str) -> int:
        if not ch or ch == "\x00":
            ch = " "
        try:
            b = ch.encode("cp437", errors="strict")
            if b:
                return int(b[0])
        except Exception:
            pass
        return 0x3F  # '?'

    def glyph(self, ch: str) -> AtlasGlyph:
        g = self._glyphs.get(ch)
        if g is not None:
            return g
        idx = self._glyph_index(ch)
        col = int(idx) & 15
        row = int(idx) >> 4  # row 0 at top in ascii.png
        x = int(col) * int(self._src_cell_w)
        y = int(getattr(self._tex, "height", 128)) - (int(row) + 1) * int(self._src_cell_h)
        try:
            region = self._tex.get_region(x, y, self._src_cell_w, self._src_cell_h)
            tex_coords = tuple(float(v) for v in getattr(region, "tex_coords", ()))
            gid = int(getattr(region, "id", getattr(self._tex, "id", 0)))
            target = int(getattr(region, "target", getattr(self._tex, "target", 0)))
        except Exception:
            tex_coords = tuple(float(v) for v in getattr(self._tex, "tex_coords", ()))
            gid = int(getattr(self._tex, "id", 0))
            target = int(getattr(self._tex, "target", 0))
        g = AtlasGlyph(
            id=gid,
            target=target,
            vertices=(0.0, 0.0, float(self.cell_w), float(self.cell_h)),
            tex_coords=tex_coords,
        )
        self._glyphs[ch] = g
        return g


@lru_cache(maxsize=1024)
def _quad_tex_coords_tuple_cached(tc: tuple[Any, ...]) -> tuple[float, float, float, float, float, float, float, float] | None:
    if len(tc) < 11:
        return None
    return (
        float(tc[0]),
        float(tc[1]),
        float(tc[3]),
        float(tc[4]),
        float(tc[6]),
        float(tc[7]),
        float(tc[9]),
        float(tc[10]),
    )


def _quad_tex_coords(tc: object) -> tuple[float, float, float, float, float, float, float, float] | None:
    """Normalize a pyglet-style tex-coord tuple into four quad UV pairs."""
    if not tc:
        return None
    try:
        if isinstance(tc, tuple):
            return _quad_tex_coords_tuple_cached(tc)
        if len(tc) < 11:  # type: ignore[arg-type]
            return None
        return (
            float(tc[0]),  # type: ignore[index]
            float(tc[1]),  # type: ignore[index]
            float(tc[3]),  # type: ignore[index]
            float(tc[4]),  # type: ignore[index]
            float(tc[6]),  # type: ignore[index]
            float(tc[7]),  # type: ignore[index]
            float(tc[9]),  # type: ignore[index]
            float(tc[10]),  # type: ignore[index]
        )
    except Exception:
        return None


class TerminalRenderer:
    def __init__(self) -> None:
        self._nearest_tex_ids: set[int] = set()
        self._fx_seed = secrets.randbits(32)
        self._fx_proxy: _TerminalFxProxy | None = None

    def _ensure_fx_proxy(self) -> "_TerminalFxProxy":
        fx = self._fx_proxy
        if fx is None:
            fx = _TerminalFxProxy(seed=int(self._fx_seed))
            self._fx_proxy = fx
        return fx

    def _ensure_nearest(self, target: int, tex_id: int) -> None:
        if tex_id in self._nearest_tex_ids:
            return
        from pyglet import gl

        gl.glBindTexture(int(target), int(tex_id))
        gl.glTexParameteri(int(target), gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(int(target), gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
        self._nearest_tex_ids.add(int(tex_id))

    def _resolve_draw_grid(
        self,
        *,
        surface: TerminalSurface,
        font: TerminalFont,
        w: int,
        h: int,
    ) -> tuple[int, int, int, int]:
        cell_w = max(1, int(font.cell_w))
        cell_h = max(1, int(font.cell_h))
        cols = min(int(surface.cols), max(1, int(w // cell_w)))
        rows = min(int(surface.rows), max(1, int(h // cell_h)))
        return cell_w, cell_h, cols, rows

    def _configure_draw_state(self, *, gl: Any, w: int, h: int, bg: RGBA, clear: bool) -> None:
        if clear:
            # Clear to the default background once.
            gl.glClearColor(bg[0] / 255.0, bg[1] / 255.0, bg[2] / 255.0, bg[3] / 255.0)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)

        gl.glDisable(gl.GL_DEPTH_TEST)
        gl.glDisable(gl.GL_LIGHTING)
        gl.glEnable(gl.GL_BLEND)
        gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
        gl.glMatrixMode(gl.GL_PROJECTION)
        gl.glLoadIdentity()
        gl.glOrtho(0.0, float(w), 0.0, float(h), -1.0, 1.0)
        gl.glMatrixMode(gl.GL_MODELVIEW)
        gl.glLoadIdentity()

    def _draw_background_cells(
        self,
        *,
        gl: Any,
        surface: TerminalSurface,
        cols: int,
        rows: int,
        cell_w: int,
        cell_h: int,
        h: int,
        bg: RGBA,
    ) -> None:
        # Background cells (only those differing from the default bg).
        gl.glDisable(gl.GL_TEXTURE_2D)
        gl.glBegin(gl.GL_QUADS)
        for x, y, c in surface.iter_cells():
            if x >= cols or y >= rows:
                continue
            if c.bg == bg:
                continue
            x0 = float(x * cell_w)
            x1 = float((x + 1) * cell_w)
            y0 = float(h - (y + 1) * cell_h)
            y1 = float(h - y * cell_h)
            r, g, b, a = c.bg
            gl.glColor4ub(int(r), int(g), int(b), int(a))
            gl.glVertex2f(x0, y0)
            gl.glVertex2f(x1, y0)
            gl.glVertex2f(x1, y1)
            gl.glVertex2f(x0, y1)
        gl.glEnd()

    def _build_sprite_buckets(
        self,
        *,
        surface: TerminalSurface,
        cols: int,
        rows: int,
        cell_w: int,
        cell_h: int,
        h: int,
    ) -> dict[tuple[int, int], list[tuple[TermSprite, float, float, float, float]]]:
        sprite_buckets: dict[tuple[int, int], list[tuple[TermSprite, float, float, float, float]]] = {}
        sprites = getattr(surface, "sprites", None)
        if not isinstance(sprites, list) or not sprites:
            return sprite_buckets
        for spr in sprites:
            try:
                target = int(spr.target)
                tex_id = int(spr.tex_id)
                if tex_id <= 0:
                    continue
                sx0 = int(spr.x)
                sy0 = int(spr.y)
                sw = int(spr.w)
                sh = int(spr.h)
            except Exception:
                continue
            if sw <= 0 or sh <= 0:
                continue
            sx1 = sx0 + sw
            sy1 = sy0 + sh
            if sx1 <= 0 or sy1 <= 0:
                continue
            if sx0 >= cols or sy0 >= rows:
                continue
            x0 = float(sx0 * cell_w)
            x1 = float(sx1 * cell_w)
            y0 = float(h - sy1 * cell_h)
            y1 = float(h - sy0 * cell_h)
            sprite_buckets.setdefault((target, tex_id), []).append((spr, x0, y0, x1, y1))
        return sprite_buckets

    def _draw_sprite_buckets(
        self,
        *,
        gl: Any,
        sprite_buckets: dict[tuple[int, int], list[tuple[TermSprite, float, float, float, float]]],
    ) -> None:
        if not sprite_buckets:
            return
        # TermSprites (textured quads aligned to the cell grid).
        gl.glEnable(gl.GL_TEXTURE_2D)
        for (target, tex_id), quads in sprite_buckets.items():
            self._ensure_nearest(int(target), int(tex_id))
            gl.glBindTexture(int(target), int(tex_id))
            gl.glBegin(gl.GL_QUADS)
            for spr, x0, y0, x1, y1 in quads:
                try:
                    r, g, b, a = spr.tint
                except Exception:
                    r, g, b, a = (255, 255, 255, 255)
                gl.glColor4ub(int(r), int(g), int(b), int(a))
                uv = _quad_tex_coords(getattr(spr, "tex_coords", None))
                if uv is None:
                    continue
                u0, v0, u1, v1, u2, v2, u3, v3 = uv
                gl.glTexCoord2f(u0, v0)
                gl.glVertex2f(x0, y0)
                gl.glTexCoord2f(u1, v1)
                gl.glVertex2f(x1, y0)
                gl.glTexCoord2f(u2, v2)
                gl.glVertex2f(x1, y1)
                gl.glTexCoord2f(u3, v3)
                gl.glVertex2f(x0, y1)
            gl.glEnd()

    def _build_glyph_buckets(
        self,
        *,
        surface: TerminalSurface,
        font: TerminalFont,
        cols: int,
        rows: int,
        cell_w: int,
        cell_h: int,
        h: int,
        texture_2d_target: int,
    ) -> dict[tuple[int, int], list[tuple[object, float, float, RGBA]]]:
        # Group glyph draws by (target, tex_id) to avoid excessive binds.
        buckets: dict[tuple[int, int], list[tuple[object, float, float, RGBA]]] = {}
        baseline_off = float(-font.descent)
        for x, y, c in surface.iter_cells():
            if x >= cols or y >= rows:
                continue
            if not c.ch or c.ch == " ":
                continue
            gobj = font.glyph(c.ch)
            tex_id = int(getattr(gobj, "id", 0))
            target = int(getattr(gobj, "target", texture_2d_target))
            if tex_id <= 0:
                continue
            pen_x = float(x * cell_w)
            row_bottom = float(h - (y + 1) * cell_h)
            baseline_y = row_bottom + baseline_off
            buckets.setdefault((target, tex_id), []).append((gobj, pen_x, baseline_y, c.fg))
        return buckets

    def _draw_glyph_buckets(
        self,
        *,
        gl: Any,
        buckets: dict[tuple[int, int], list[tuple[object, float, float, RGBA]]],
        cell_w: int,
        cell_h: int,
    ) -> None:
        gl.glEnable(gl.GL_TEXTURE_2D)
        glyph_uv_cache: dict[int, tuple[float, float, float, float, float, float, float, float] | None] = {}
        for (target, tex_id), quads in buckets.items():
            self._ensure_nearest(int(target), int(tex_id))
            gl.glBindTexture(int(target), int(tex_id))
            gl.glBegin(gl.GL_QUADS)
            for gobj, pen_x, baseline_y, fg in quads:
                r, g, b, a = fg
                gl.glColor4ub(int(r), int(g), int(b), int(a))
                vx0, vy0, vx1, vy1 = getattr(gobj, "vertices", (0.0, 0.0, float(cell_w), float(cell_h)))
                x0 = pen_x + float(vx0)
                y0 = baseline_y + float(vy0)
                x1 = pen_x + float(vx1)
                y1 = baseline_y + float(vy1)
                gid = id(gobj)
                if gid in glyph_uv_cache:
                    uv = glyph_uv_cache[gid]
                else:
                    uv = _quad_tex_coords(getattr(gobj, "tex_coords", None))
                    glyph_uv_cache[gid] = uv
                if uv is None:
                    continue
                u0, v0, u1, v1, u2, v2, u3, v3 = uv
                gl.glTexCoord2f(u0, v0)
                gl.glVertex2f(x0, y0)
                gl.glTexCoord2f(u1, v1)
                gl.glVertex2f(x1, y0)
                gl.glTexCoord2f(u2, v2)
                gl.glVertex2f(x1, y1)
                gl.glTexCoord2f(u3, v3)
                gl.glVertex2f(x0, y1)
            gl.glEnd()
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        gl.glColor4ub(255, 255, 255, 255)

    def _draw_post_fx_overlay(
        self,
        *,
        gl: Any,
        param_store: Any | None,
        w: int,
        h: int,
        rez_active: bool,
    ) -> None:
        # Post-fx overlay (scanlines / glitch / vignette) for TermUI panels.
        # We reuse the main viewer's post-fx shaderless path by drawing it in
        # the current viewport after glyph rendering.
        if param_store is None:
            return
        try:
            from enderterm import fx as fx_mod

            fx = self._ensure_fx_proxy()
            fx.width = float(w)
            fx.height = float(h)
            fx.sidebar_width = 0.0
            fx._rez_active = bool(rez_active)
            pushed = False
            try:
                gl.glPushAttrib(gl.GL_ENABLE_BIT | gl.GL_COLOR_BUFFER_BIT | gl.GL_LINE_BIT)
                pushed = True
            except Exception:
                pushed = False
            try:
                fx_mod.draw_post_fx_overlay(fx, gl=gl, param_store=param_store)
            finally:
                if pushed:
                    try:
                        gl.glPopAttrib()
                    except Exception:
                        pass
        except Exception:
            pass

    def draw(
        self,
        *,
        surface: TerminalSurface,
        font: TerminalFont,
        vp_w_px: int,
        vp_h_px: int,
        param_store: Any | None = None,
        rez_active: bool = False,
        clear: bool = True,
    ) -> None:
        from pyglet import gl

        w = int(vp_w_px)
        h = int(vp_h_px)
        if w <= 0 or h <= 0:
            return

        cell_w, cell_h, cols, rows = self._resolve_draw_grid(surface=surface, font=font, w=w, h=h)

        bg = surface.default_bg
        self._configure_draw_state(gl=gl, w=w, h=h, bg=bg, clear=clear)
        self._draw_background_cells(
            gl=gl,
            surface=surface,
            cols=cols,
            rows=rows,
            cell_w=cell_w,
            cell_h=cell_h,
            h=h,
            bg=bg,
        )
        sprite_buckets = self._build_sprite_buckets(
            surface=surface,
            cols=cols,
            rows=rows,
            cell_w=cell_w,
            cell_h=cell_h,
            h=h,
        )
        self._draw_sprite_buckets(gl=gl, sprite_buckets=sprite_buckets)
        glyph_buckets = self._build_glyph_buckets(
            surface=surface,
            font=font,
            cols=cols,
            rows=rows,
            cell_w=cell_w,
            cell_h=cell_h,
            h=h,
            texture_2d_target=int(gl.GL_TEXTURE_2D),
        )
        self._draw_glyph_buckets(gl=gl, buckets=glyph_buckets, cell_w=cell_w, cell_h=cell_h)
        self._draw_post_fx_overlay(gl=gl, param_store=param_store, w=w, h=h, rez_active=rez_active)


class _TerminalFxZeroUint:
    __slots__ = ()

    value = 0


class _TerminalFxProxy:
    __slots__ = ("width", "height", "sidebar_width", "_fx_seed", "_rez_active", "_ender_vignette_prog")

    def __init__(self, *, seed: int) -> None:
        self.width = 0.0
        self.height = 0.0
        self.sidebar_width = 0.0
        self._fx_seed = int(seed) & 0xFFFFFFFF
        self._rez_active = False
        # TermUI doesn't set up the vignette GLSL; force the fallback path.
        self._ender_vignette_prog = _TerminalFxZeroUint()

    def get_pixel_ratio(self) -> float:
        # TermUI renders directly in device pixels (vp_w_px / vp_h_px).
        return 1.0
