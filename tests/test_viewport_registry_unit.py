from __future__ import annotations

import pytest

from enderterm.viewport_registry import ViewportRegistry


class _FakeWindow:
    def __init__(self) -> None:
        self._closing = False
        self.has_exit = False
        self.context = object()
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self._closing = True
        self.context = None


def _attach_allocated(reg: ViewportRegistry, window: _FakeWindow) -> int:
    viewport_id = reg.allocate_id()
    reg.attach(viewport_id, window)
    return viewport_id


def test_viewport_registry_supports_more_than_two_active_viewports() -> None:
    reg = ViewportRegistry()
    windows = [_FakeWindow(), _FakeWindow(), _FakeWindow()]

    ids: list[int] = []
    for win in windows:
        ids.append(_attach_allocated(reg, win))

    assert ids == [1, 2, 3]
    assert reg.count() == 3
    assert reg.ids() == (1, 2, 3)

    reg.close(2)
    assert windows[1].close_calls == 1
    assert reg.count() == 2
    assert reg.ids() == (1, 3)

    viewport_id = _attach_allocated(reg, _FakeWindow())
    assert viewport_id == 4
    assert reg.count() == 3
    assert reg.ids() == (1, 3, 4)


def test_viewport_registry_prune_and_close_all_lifecycle() -> None:
    reg = ViewportRegistry()
    win1 = _FakeWindow()
    win2 = _FakeWindow()
    win3 = _FakeWindow()
    _attach_allocated(reg, win1)
    _attach_allocated(reg, win2)
    _attach_allocated(reg, win3)

    win2.has_exit = True
    win3.context = None

    removed = reg.prune()
    assert removed == (2, 3)
    assert reg.ids() == (1,)

    closed_ids = reg.close_all()
    assert closed_ids == (1,)
    assert win1.close_calls == 1
    assert reg.count() == 0


def test_viewport_registry_lookup_and_remove_normalize_viewport_ids() -> None:
    reg = ViewportRegistry()
    win = _FakeWindow()
    viewport_id = _attach_allocated(reg, win)

    assert reg.get(viewport_id) is win
    assert reg.get(str(viewport_id)) is win  # type: ignore[arg-type]

    removed = reg.remove(str(viewport_id))  # type: ignore[arg-type]
    assert removed is win
    assert reg.get(viewport_id) is None
    assert reg.remove(viewport_id) is None


def test_viewport_registry_close_normalizes_string_viewport_id() -> None:
    reg = ViewportRegistry()
    win = _FakeWindow()
    viewport_id = reg.allocate_id()
    reg.attach(viewport_id, win)

    reg.close(str(viewport_id))  # type: ignore[arg-type]

    assert win.close_calls == 1
    assert reg.count() == 0


def test_viewport_registry_attach_fails_fast_on_duplicate_ids() -> None:
    reg = ViewportRegistry()
    win1 = _FakeWindow()
    win2 = _FakeWindow()

    viewport_id = _attach_allocated(reg, win1)

    with pytest.raises(ValueError, match="already registered"):
        reg.attach(viewport_id, win2)
    assert reg.get(viewport_id) is win1


@pytest.mark.parametrize("bad_id", [0, -1, " ", "abc"])
def test_viewport_registry_rejects_invalid_viewport_ids(bad_id: object) -> None:
    reg = ViewportRegistry()

    with pytest.raises(ValueError, match="Invalid viewport id"):
        reg.get(bad_id)
    with pytest.raises(ValueError, match="Invalid viewport id"):
        reg.remove(bad_id)
    with pytest.raises(ValueError, match="Invalid viewport id"):
        reg.close(bad_id)
    with pytest.raises(ValueError, match="Invalid viewport id"):
        reg.attach(bad_id, _FakeWindow())


def test_viewport_registry_rejects_none_window_attach() -> None:
    reg = ViewportRegistry()
    viewport_id = reg.allocate_id()

    with pytest.raises(ValueError, match="Window cannot be None"):
        reg.attach(viewport_id, None)


def test_viewport_registry_prune_requires_callable_predicate() -> None:
    reg = ViewportRegistry()
    _attach_allocated(reg, _FakeWindow())

    with pytest.raises(TypeError, match="must be callable"):
        reg.prune(is_alive=object())  # type: ignore[arg-type]


def test_viewport_registry_close_ignores_non_callable_close_attribute() -> None:
    class _WindowWithNonCallableClose:
        def __init__(self) -> None:
            self.close = "not-callable"

    reg = ViewportRegistry()
    viewport_id = reg.allocate_id()
    reg.attach(viewport_id, _WindowWithNonCallableClose())

    reg.close(viewport_id)

    assert reg.count() == 0


def test_viewport_registry_close_swallows_close_exceptions() -> None:
    class _WindowWithFailingClose:
        def __init__(self) -> None:
            self.calls = 0

        def close(self) -> None:
            self.calls += 1
            raise RuntimeError("boom")

    reg = ViewportRegistry()
    window = _WindowWithFailingClose()
    viewport_id = reg.allocate_id()
    reg.attach(viewport_id, window)

    reg.close(viewport_id)

    assert window.calls == 1
    assert reg.count() == 0
