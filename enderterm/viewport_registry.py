from __future__ import annotations

from collections.abc import Callable
from typing import TypeAlias

ViewportId: TypeAlias = int


def _normalize_viewport_id(viewport_id: object) -> ViewportId:
    """Convert int-like ids and reject invalid or non-positive values."""
    try:
        normalized = int(viewport_id)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid viewport id: {viewport_id!r}") from exc
    if normalized <= 0:
        raise ValueError(f"Invalid viewport id: {viewport_id!r}")
    return normalized


def is_window_alive(window: object) -> bool:
    """Best-effort liveness check for auxiliary viewport windows."""
    if window is None:
        return False
    if bool(getattr(window, "_closing", False)):
        return False
    if bool(getattr(window, "has_exit", False)):
        return False
    return getattr(window, "context", None) is not None


def _window_close_callback(window: object) -> Callable[[], object] | None:
    close = getattr(window, "close", None)
    if not callable(close):
        return None
    return close


class ViewportRegistry:
    """Track companion viewport windows by id with safe close/prune helpers."""

    def __init__(self) -> None:
        self._next_id: ViewportId = 1
        self._windows: dict[ViewportId, object] = {}

    def allocate_id(self) -> ViewportId:
        viewport_id = self._next_id
        self._next_id += 1
        return viewport_id

    def attach(self, viewport_id: object, window: object) -> None:
        viewport_key = _normalize_viewport_id(viewport_id)
        if window is None:
            raise ValueError("Window cannot be None")
        if viewport_key in self._windows:
            raise ValueError(f"Viewport id already registered: {viewport_key}")
        self._windows[viewport_key] = window

    def _window_for(self, viewport_id: object) -> object | None:
        """Lookup helper that centralizes viewport id normalization."""
        return self._windows.get(_normalize_viewport_id(viewport_id))

    def get(self, viewport_id: object) -> object | None:
        return self._window_for(viewport_id)

    def _pop_window(self, viewport_id: object) -> object | None:
        """Normalize a viewport id and pop its window entry if present."""
        viewport_key = _normalize_viewport_id(viewport_id)
        return self._windows.pop(viewport_key, None)

    def remove(self, viewport_id: object) -> object | None:
        return self._pop_window(viewport_id)

    def count(self) -> int:
        return len(self._windows)

    def ids(self) -> tuple[ViewportId, ...]:
        return tuple(sorted(self._windows))

    def prune(self, *, is_alive: Callable[[object], bool] = is_window_alive) -> tuple[int, ...]:
        if not callable(is_alive):
            raise TypeError("is_alive must be callable")
        removed: list[int] = []
        for viewport_id, window in list(self._windows.items()):
            alive = False
            try:
                alive = bool(is_alive(window))
            except Exception:
                alive = False
            if not alive:
                self._windows.pop(viewport_id, None)
                removed.append(viewport_id)
        return tuple(sorted(removed))

    def close(self, viewport_id: object) -> None:
        window = self._pop_window(viewport_id)
        if window is None:
            return
        close = _window_close_callback(window)
        if close is None:
            return
        try:
            close()
        except Exception:
            pass

    def close_all(self) -> tuple[ViewportId, ...]:
        closed_ids = self.ids()
        for viewport_id in closed_ids:
            self.close(viewport_id)
        return closed_ids
