from __future__ import annotations

from pathlib import Path

import pytest


def test_to_pua_variant_maps_ascii_and_leaves_unicode() -> None:
    from enderterm.font_specimen import _to_pua_variant

    assert _to_pua_variant("A", pua_base=0xE000) == chr(0xE000 + ord("A"))
    assert _to_pua_variant(" hello ", pua_base=0xF000).startswith(chr(0xF000 + ord(" ")))
    assert _to_pua_variant("☃", pua_base=0xE000) == "☃"


def test_build_specimen_text_is_deterministic_and_newline_terminated() -> None:
    from enderterm.font_specimen import _build_specimen_text

    text = _build_specimen_text(0xE000)
    assert text.endswith("\n")
    assert "Upper:" in text
    assert "END" in text


def test_default_mixed_font_prefers_workspace_then_bundled(monkeypatch: pytest.MonkeyPatch) -> None:
    import enderterm.font_specimen as fs

    here = Path(fs.__file__).resolve()
    workspace = here.parents[2] / "font" / "term_mixed.ttf"
    bundled = here.parent / "assets" / "fonts" / "term_mixed.ttf"

    def fake_is_file(path: Path) -> bool:
        if path == workspace:
            return True
        if path == bundled:
            return False
        return False

    monkeypatch.setattr(fs.Path, "is_file", fake_is_file, raising=True)
    assert fs._default_mixed_font() == workspace


def test_default_mixed_font_falls_back_to_workspace_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import enderterm.font_specimen as fs

    monkeypatch.setattr(fs.Path, "is_file", lambda _p: False, raising=True)
    out = fs._default_mixed_font()
    assert str(out).endswith("font/term_mixed.ttf")


def test_font_specimen_main_runs_with_stub_pyglet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import importlib
    import sys
    from types import ModuleType, SimpleNamespace

    import enderterm.font_specimen as fs

    out_path = tmp_path / "specimen.png"
    font_path = tmp_path / "term_mixed.ttf"
    font_path.write_bytes(b"fake")

    class _FakeGL:
        def __getattr__(self, name: str):
            if name.startswith("gl"):
                return lambda *_args, **_kwargs: None
            raise AttributeError(name)

    class _Window:
        def __init__(self, *, width: int, height: int, caption: str, visible: bool) -> None:
            self.width = int(width)
            self.height = int(height)
            self.caption = caption
            self.visible = visible
            self._on_draw = None

        def set_location(self, *_args, **_kwargs) -> None:
            raise RuntimeError("no location")

        def clear(self) -> None:
            return None

        def switch_to(self) -> None:
            raise RuntimeError("no switch")

        def dispatch_event(self, _name: str) -> None:
            if _name == "on_draw" and self._on_draw is not None:
                self._on_draw()

        def flip(self) -> None:
            raise RuntimeError("no flip")

        def event(self, fn):
            self._on_draw = fn
            return fn

    class _Batch:
        def draw(self) -> None:
            return None

    class _Label:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

    class _ColorBuffer:
        def save(self, path: str) -> None:
            Path(path).write_bytes(b"png")

    class _BufferManager:
        def get_color_buffer(self):
            return _ColorBuffer()

    class _Clock:
        @staticmethod
        def schedule_once(fn, _dt: float) -> None:
            fn(0.25)

    class _App:
        @staticmethod
        def run() -> None:
            return None

        @staticmethod
        def exit() -> None:
            return None

    pyglet = ModuleType("pyglet")
    pyglet.options = {}
    pyglet.gl = _FakeGL()
    pyglet.font = SimpleNamespace(add_file=lambda _p: (_ for _ in ()).throw(RuntimeError("no add")), have_font=lambda _name: True)
    pyglet.window = SimpleNamespace(Window=_Window)
    pyglet.graphics = SimpleNamespace(Batch=_Batch)
    pyglet.text = SimpleNamespace(Label=_Label)
    pyglet.image = SimpleNamespace(get_buffer_manager=lambda: _BufferManager())
    pyglet.clock = _Clock()
    pyglet.app = _App()
    monkeypatch.setitem(sys.modules, "pyglet", pyglet)

    calls: list[list[str]] = []

    def fake_popen(argv: list[str], **_kwargs: object) -> object:
        calls.append([str(x) for x in argv])
        if argv[:3] == ["open", "-a", "Preview"]:
            raise OSError("force fallback")
        return object()

    monkeypatch.setattr(fs.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(fs.sys, "platform", "darwin")

    monkeypatch.setattr(
        fs.sys,
        "argv",
        [
            "font_specimen.py",
            "--font",
            str(font_path),
            "--out",
            str(out_path),
            "--open",
        ],
    )
    importlib.reload(fs)
    fs.main()

    assert out_path.is_file()
    assert calls == [["open", "-a", "Preview", str(out_path)], ["open", str(out_path)]]


def test_font_specimen_main_errors_when_pyglet_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import builtins
    import sys

    import enderterm.font_specimen as fs

    font_path = tmp_path / "term_mixed.ttf"
    font_path.write_bytes(b"fake")
    out_path = tmp_path / "specimen.png"

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "pyglet":
            raise ImportError("no pyglet")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(sys, "argv", ["font_specimen.py", "--font", str(font_path), "--out", str(out_path)])
    with pytest.raises(SystemExit, match="Requires pyglet"):
        fs.main()


def test_font_specimen_main_exits_when_font_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import sys
    from types import ModuleType, SimpleNamespace

    import enderterm.font_specimen as fs

    font_path = tmp_path / "term_mixed.ttf"
    font_path.write_bytes(b"fake")
    out_path = tmp_path / "specimen.png"

    pyglet = ModuleType("pyglet")
    pyglet.gl = object()
    pyglet.options = {}
    pyglet.font = SimpleNamespace(add_file=lambda _p: None, have_font=lambda _name: False)
    monkeypatch.setitem(sys.modules, "pyglet", pyglet)
    monkeypatch.setattr(sys, "argv", ["font_specimen.py", "--font", str(font_path), "--out", str(out_path)])
    with pytest.raises(SystemExit, match="not available"):
        fs.main()


def test_font_specimen_main_open_linux_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import importlib
    import sys
    from types import ModuleType, SimpleNamespace

    import enderterm.font_specimen as fs

    out_path = tmp_path / "specimen.png"
    font_path = tmp_path / "term_mixed.ttf"
    font_path.write_bytes(b"fake")

    class _FakeGL:
        def __getattr__(self, name: str):
            if name.startswith("gl"):
                return lambda *_args, **_kwargs: None
            raise AttributeError(name)

    class _Window:
        def __init__(self, *, width: int, height: int, caption: str, visible: bool) -> None:
            self.width = int(width)
            self.height = int(height)
            self._on_draw = None

        def set_location(self, *_args, **_kwargs) -> None:
            return None

        def clear(self) -> None:
            return None

        def switch_to(self) -> None:
            return None

        def dispatch_event(self, _name: str) -> None:
            if _name == "on_draw" and self._on_draw is not None:
                self._on_draw()

        def flip(self) -> None:
            return None

        def event(self, fn):
            self._on_draw = fn
            return fn

    class _Batch:
        def draw(self) -> None:
            return None

    class _Label:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

    class _ColorBuffer:
        def save(self, path: str) -> None:
            Path(path).write_bytes(b"png")

    class _BufferManager:
        def get_color_buffer(self):
            return _ColorBuffer()

    class _Clock:
        @staticmethod
        def schedule_once(fn, _dt: float) -> None:
            fn(0.25)

    class _App:
        @staticmethod
        def run() -> None:
            return None

        @staticmethod
        def exit() -> None:
            return None

    pyglet = ModuleType("pyglet")
    pyglet.options = {}
    pyglet.gl = _FakeGL()
    pyglet.font = SimpleNamespace(add_file=lambda _p: None, have_font=lambda _name: True)
    pyglet.window = SimpleNamespace(Window=_Window)
    pyglet.graphics = SimpleNamespace(Batch=_Batch)
    pyglet.text = SimpleNamespace(Label=_Label)
    pyglet.image = SimpleNamespace(get_buffer_manager=lambda: _BufferManager())
    pyglet.clock = _Clock()
    pyglet.app = _App()
    monkeypatch.setitem(sys.modules, "pyglet", pyglet)

    calls: list[list[str]] = []

    def fake_popen(argv: list[str], **_kwargs: object) -> object:
        calls.append([str(x) for x in argv])
        return object()

    monkeypatch.setattr(fs.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(fs.sys, "platform", "linux")
    monkeypatch.setattr(
        fs.sys,
        "argv",
        [
            "font_specimen.py",
            "--font",
            str(font_path),
            "--out",
            str(out_path),
            "--open",
        ],
    )
    importlib.reload(fs)
    fs.main()
    assert calls == [["xdg-open", str(out_path)]]


def test_font_specimen_main_font_check_failed_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import sys
    from types import ModuleType, SimpleNamespace

    import enderterm.font_specimen as fs

    font_path = tmp_path / "term_mixed.ttf"
    font_path.write_bytes(b"fake")
    out_path = tmp_path / "specimen.png"

    pyglet = ModuleType("pyglet")
    pyglet.gl = object()
    pyglet.options = {}

    def raise_have_font(_name: str) -> bool:
        raise RuntimeError("boom")

    pyglet.font = SimpleNamespace(add_file=lambda _p: None, have_font=raise_have_font)
    monkeypatch.setitem(sys.modules, "pyglet", pyglet)
    monkeypatch.setattr(sys, "argv", ["font_specimen.py", "--font", str(font_path), "--out", str(out_path)])

    with pytest.raises(SystemExit, match="Font check failed"):
        fs.main()


def test_font_specimen_main_open_windows_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import sys
    from types import ModuleType, SimpleNamespace

    import enderterm.font_specimen as fs

    out_path = tmp_path / "specimen.png"
    font_path = tmp_path / "term_mixed.ttf"
    font_path.write_bytes(b"fake")

    class _FakeGL:
        def __getattr__(self, name: str):
            if name.startswith("gl"):
                return lambda *_args, **_kwargs: None
            raise AttributeError(name)

    class _Window:
        def __init__(self, *, width: int, height: int, caption: str, visible: bool) -> None:
            self.width = int(width)
            self.height = int(height)
            self._on_draw = None

        def set_location(self, *_args, **_kwargs) -> None:
            return None

        def clear(self) -> None:
            return None

        def switch_to(self) -> None:
            return None

        def dispatch_event(self, _name: str) -> None:
            if _name == "on_draw" and self._on_draw is not None:
                self._on_draw()

        def flip(self) -> None:
            return None

        def event(self, fn):
            self._on_draw = fn
            return fn

    class _Batch:
        def draw(self) -> None:
            return None

    class _Label:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

    class _ColorBuffer:
        def save(self, path: str) -> None:
            Path(path).write_bytes(b"png")

    class _BufferManager:
        def get_color_buffer(self):
            return _ColorBuffer()

    class _Clock:
        @staticmethod
        def schedule_once(fn, _dt: float) -> None:
            fn(0.25)

    class _App:
        @staticmethod
        def run() -> None:
            return None

        @staticmethod
        def exit() -> None:
            return None

    pyglet = ModuleType("pyglet")
    pyglet.options = {}
    pyglet.gl = _FakeGL()
    pyglet.font = SimpleNamespace(add_file=lambda _p: None, have_font=lambda _name: True)
    pyglet.window = SimpleNamespace(Window=_Window)
    pyglet.graphics = SimpleNamespace(Batch=_Batch)
    pyglet.text = SimpleNamespace(Label=_Label)
    pyglet.image = SimpleNamespace(get_buffer_manager=lambda: _BufferManager())
    pyglet.clock = _Clock()
    pyglet.app = _App()
    monkeypatch.setitem(sys.modules, "pyglet", pyglet)

    opened: list[str] = []
    dummy_os = SimpleNamespace(name="nt", startfile=lambda p: opened.append(str(p)))
    monkeypatch.setattr(fs, "os", dummy_os)
    monkeypatch.setattr(fs.sys, "platform", "win32")

    monkeypatch.setattr(
        fs.sys,
        "argv",
        [
            "font_specimen.py",
            "--font",
            str(font_path),
            "--out",
            str(out_path),
            "--open",
        ],
    )
    fs.main()
    assert opened == [str(out_path)]


def test_font_specimen___main___guard_executes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import runpy
    import sys
    from types import ModuleType, SimpleNamespace

    font_path = tmp_path / "term_mixed.ttf"
    font_path.write_bytes(b"fake")

    pyglet = ModuleType("pyglet")
    pyglet.gl = object()
    pyglet.options = {}
    pyglet.font = SimpleNamespace(add_file=lambda _p: None, have_font=lambda _name: False)
    monkeypatch.setitem(sys.modules, "pyglet", pyglet)
    monkeypatch.setattr(sys, "argv", ["font_specimen.py", "--font", str(font_path)])
    monkeypatch.delitem(sys.modules, "enderterm.font_specimen", raising=False)

    with pytest.raises(SystemExit):
        runpy.run_module("enderterm.font_specimen", run_name="__main__")
