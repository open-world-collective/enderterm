from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from types import SimpleNamespace

from enderterm import fx as fx_mod
from enderterm.debug_window import _wrap_debug_line, _wrap_debug_lines
from enderterm.termui import (
    MinecraftAsciiBitmapFont,
    TermSprite,
    TerminalFont,
    TerminalRenderer,
    TerminalSurface,
    _default_minecraft_ascii_png,
    _quad_tex_coords,
)


class _FakeGLNoop:
    """Fallback OpenGL stub: unknown gl* calls no-op; unknown GL_* constants -> 0."""

    _GL_CONSTANTS: dict[str, int] = {}

    def __getattr__(self, name: str):
        if name.startswith("GL_"):
            return int(self._GL_CONSTANTS.get(name, 0))
        if name.startswith("gl"):
            return lambda *_args, **_kwargs: None
        raise AttributeError(name)


def _install_fake_pyglet_gl(monkeypatch: pytest.MonkeyPatch, *, constants: dict[str, int]) -> ModuleType:
    class _FakeGL(_FakeGLNoop):
        _GL_CONSTANTS = dict(constants)

    pyglet = ModuleType("pyglet")
    pyglet.gl = _FakeGL()
    monkeypatch.setitem(sys.modules, "pyglet", pyglet)
    return pyglet


_TERM_RENDERER_GL_CONSTANTS = {
    "GL_TEXTURE_2D": 3553,
    "GL_TEXTURE_MIN_FILTER": 10241,
    "GL_TEXTURE_MAG_FILTER": 10240,
    "GL_NEAREST": 9728,
    "GL_COLOR_BUFFER_BIT": 0x4000,
    "GL_DEPTH_TEST": 0x0B71,
    "GL_LIGHTING": 0x0B50,
    "GL_BLEND": 0x0BE2,
    "GL_SRC_ALPHA": 0x0302,
    "GL_ONE_MINUS_SRC_ALPHA": 0x0303,
    "GL_PROJECTION": 0x1701,
    "GL_MODELVIEW": 0x1700,
    "GL_QUADS": 0x0007,
}


def test_debug_window_wrap_helpers_preserve_wrapping_behavior() -> None:
    assert _wrap_debug_line("abc", 1) == ["a"]
    assert _wrap_debug_line("abc", 0) == [""]
    assert _wrap_debug_line("   ", 10) == [""]
    assert _wrap_debug_line("alpha beta gamma", 5) == ["alpha", "beta", "gamma"]
    assert _wrap_debug_line("abcdefghij", 4) == ["abcd", "efgh", "ij"]

    assert _wrap_debug_lines([], width=8) == ["(no debug info)"]
    assert _wrap_debug_lines(["alpha beta", "gamma"], width=5) == ["alpha", "beta", "gamma"]


def test_terminal_surface_resize_clear_and_bounds() -> None:
    s = TerminalSurface(cols=2, rows=2, default_fg=(1, 2, 3, 4), default_bg=(0, 0, 0, 0))
    s.put(0, 0, "xyz")  # clips to width
    assert s.cell(0, 0) is not None and s.cell(0, 0).ch == "x"

    s.resize(2, 2)  # no-op resize
    assert s.cell(0, 0) is not None and s.cell(0, 0).ch == "x"

    s.resize(3, 1)
    assert s.cell(-1, 0) is None
    assert s.cell(999, 0) is None

    s.clear()
    assert s.cell(0, 0) is not None and s.cell(0, 0).ch == " "

    s.put(0, -1, "nope")
    s.put(0, 0, "")
    s.fill_rect(0, 0, 0, 10, ch="x")
    s.draw_box(0, 0, 1, 1)
    s.draw_box(0, 0, 6, 2, title="TITLE_TOO_LONG")


def test_start_perf_timer_uses_perf_counter_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _perf_counter() -> float:
        calls["n"] += 1
        return 12.34

    monkeypatch.setattr(fx_mod.time, "perf_counter", _perf_counter)

    assert fx_mod._start_perf_timer(False) == 0.0
    assert calls["n"] == 0
    assert fx_mod._start_perf_timer(True) == pytest.approx(12.34)
    assert calls["n"] == 1


def test_update_perf_counter_ms_writes_elapsed_ms_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    target = SimpleNamespace()
    monkeypatch.setattr(fx_mod.time, "perf_counter", lambda: 10.25)

    fx_mod._update_perf_counter_ms(
        target,
        enabled=True,
        start_t=10.0,
        attr_name="_perf_last_world_ms",
    )

    assert target._perf_last_world_ms == pytest.approx(250.0)


def test_update_perf_counter_ms_skips_update_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    target = SimpleNamespace(_perf_last_world_ms=123.0)
    calls = {"n": 0}

    def _perf_counter() -> float:
        calls["n"] += 1
        return 99.0

    monkeypatch.setattr(fx_mod.time, "perf_counter", _perf_counter)

    fx_mod._update_perf_counter_ms(
        target,
        enabled=False,
        start_t=42.0,
        attr_name="_perf_last_world_ms",
    )

    assert target._perf_last_world_ms == pytest.approx(123.0)
    assert calls["n"] == 0


def test_terminal_font_glyph_caches(nbttool: ModuleType) -> None:
    class _DummyFontObj:
        ascent = 10
        descent = -2

        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_glyphs(self, s: str):
            self.calls.append(s)
            return [object()]

    tf = TerminalFont.__new__(TerminalFont)
    tf._font_name = "dummy"
    tf._font_size_px = 12
    tf._font = _DummyFontObj()
    tf._glyphs = {}
    tf.ascent = 10.0
    tf.descent = -2.0

    g1 = tf.glyph("A")
    g2 = tf.glyph("A")
    assert g1 is g2
    assert tf._font.calls.count("A") == 1
    assert tf.font_name == "dummy"
    assert tf.font_size_px == 12


def test_ascii_bitmap_font_glyph_index_cp437_fallback() -> None:
    f = MinecraftAsciiBitmapFont.__new__(MinecraftAsciiBitmapFont)
    assert f._glyph_index("A") == ord("A")
    assert f._glyph_index("") == ord(" ")
    assert f._glyph_index("\x00") == ord(" ")
    assert f._glyph_index("🐍") == 0x3F


def test_quad_tex_coords_normalizes_supported_inputs() -> None:
    tc = (0.0, 1.0, 99.0, 2.0, 3.0, 98.0, 4.0, 5.0, 97.0, 6.0, 7.0, 96.0)
    assert _quad_tex_coords(tc) == (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)
    assert _quad_tex_coords([0, 1, 0, 2, 3, 0, 4, 5, 0, 6, 7]) == (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0)


def test_quad_tex_coords_rejects_missing_or_short_values() -> None:
    assert _quad_tex_coords(None) is None
    assert _quad_tex_coords(()) is None
    assert _quad_tex_coords((0.0, 1.0, 2.0)) is None


def test_quad_tex_coords_tuple_cache_hits_for_repeat_input() -> None:
    import enderterm.termui as termui_mod

    termui_mod._quad_tex_coords_tuple_cached.cache_clear()
    before = termui_mod._quad_tex_coords_tuple_cached.cache_info()
    tc = (0.0, 1.0, 99.0, 2.0, 3.0, 98.0, 4.0, 5.0, 97.0, 6.0, 7.0, 96.0)
    a = _quad_tex_coords(tc)
    mid = termui_mod._quad_tex_coords_tuple_cached.cache_info()
    b = _quad_tex_coords(tc)
    after = termui_mod._quad_tex_coords_tuple_cached.cache_info()

    assert a == b
    assert mid.misses == before.misses + 1
    assert after.hits == mid.hits + 1


def test_default_minecraft_ascii_png_prefers_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    p = tmp_path / "ascii.png"
    p.write_bytes(b"not a png, just needs to exist")
    monkeypatch.setenv("NBTTOOL_ASCII_PNG", str(p))
    assert _default_minecraft_ascii_png() == p

    # Cover the env-path exception handler, and the workspace fallback path.
    monkeypatch.setenv("NBTTOOL_ASCII_PNG", "~/definitely/not/a/real/path.png")
    monkeypatch.setattr(Path, "expanduser", lambda _self: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(Path, "is_file", lambda _self: False)
    assert _default_minecraft_ascii_png() is None
    monkeypatch.delenv("NBTTOOL_ASCII_PNG", raising=False)

    def fake_is_file(path: Path) -> bool:
        return str(path).endswith("font/pics/ascii.png")

    monkeypatch.setattr(Path, "is_file", fake_is_file)
    assert _default_minecraft_ascii_png() is not None

    # Cover the bundled fallback (used in packaged apps).
    def fake_is_file_bundled(path: Path) -> bool:
        s = str(path)
        if s.endswith("font/pics/ascii.png"):
            return False
        return s.endswith("enderterm/assets/fonts/ascii.png")

    monkeypatch.setattr(Path, "is_file", fake_is_file_bundled)
    p2 = _default_minecraft_ascii_png()
    assert p2 is not None
    assert str(p2).endswith("enderterm/assets/fonts/ascii.png")

    # Cover the workspace-fallback exception handler.
    monkeypatch.setattr(Path, "resolve", lambda _self: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _default_minecraft_ascii_png() is None


def test_terminal_renderer_draw_runs_without_real_opengl(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_pyglet_gl(monkeypatch, constants=_TERM_RENDERER_GL_CONSTANTS)

    surf = TerminalSurface(cols=2, rows=1, default_fg=(255, 255, 255, 255), default_bg=(0, 0, 0, 0))
    surf.put(0, 0, "A")

    class _Glyph:
        id = 1
        target = 3553
        vertices = (0.0, 0.0, 8.0, 8.0)
        tex_coords = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0)

    class _Font:
        cell_w = 8
        cell_h = 8
        descent = 0.0

        def glyph(self, _ch: str):
            return _Glyph()

    renderer = TerminalRenderer()
    renderer.draw(surface=surf, font=_Font(), vp_w_px=0, vp_h_px=0)
    renderer.draw(surface=surf, font=_Font(), vp_w_px=32, vp_h_px=16)


def test_terminal_renderer_reuses_uv_for_repeated_glyph_object(monkeypatch: pytest.MonkeyPatch) -> None:
    import enderterm.termui as termui_mod

    _install_fake_pyglet_gl(monkeypatch, constants=_TERM_RENDERER_GL_CONSTANTS)

    surf = TerminalSurface(cols=8, rows=1, default_fg=(255, 255, 255, 255), default_bg=(0, 0, 0, 0))
    surf.put(0, 0, "AAAAAAAA")

    class _Glyph:
        id = 1
        target = 3553
        vertices = (0.0, 0.0, 8.0, 8.0)
        tex_coords = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0)

    shared_glyph = _Glyph()

    class _Font:
        cell_w = 8
        cell_h = 8
        descent = 0.0

        def glyph(self, _ch: str):
            return shared_glyph

    calls = {"n": 0}
    real_quad_tex_coords = termui_mod._quad_tex_coords

    def _counting_quad_tex_coords(tc):
        calls["n"] += 1
        return real_quad_tex_coords(tc)

    monkeypatch.setattr(termui_mod, "_quad_tex_coords", _counting_quad_tex_coords)

    renderer = TerminalRenderer()
    renderer.draw(surface=surf, font=_Font(), vp_w_px=64, vp_h_px=16)
    assert calls["n"] == 1


def test_terminal_renderer_sprite_buckets_group_by_target_and_texture() -> None:
    renderer = TerminalRenderer()
    surface = TerminalSurface(cols=12, rows=8, default_fg=(255, 255, 255, 255), default_bg=(0, 0, 0, 0))
    tex = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0)

    sprite_a = TermSprite(x=1, y=1, w=2, h=1, target=3553, tex_id=10, tex_coords=tex)
    sprite_b = TermSprite(x=4, y=2, w=1, h=2, target=3553, tex_id=10, tex_coords=tex)
    sprite_c = TermSprite(x=0, y=0, w=1, h=1, target=3553, tex_id=11, tex_coords=tex)
    sprite_d = TermSprite(x=2, y=3, w=1, h=1, target=34067, tex_id=5, tex_coords=tex)
    surface.sprites = [sprite_a, sprite_b, sprite_c, sprite_d]

    buckets = renderer._build_sprite_buckets(surface=surface, cols=12, rows=8, cell_w=8, cell_h=16, h=128)

    assert set(buckets) == {(3553, 10), (3553, 11), (34067, 5)}
    assert len(buckets[(3553, 10)]) == 2
    assert buckets[(3553, 10)][0][0] is sprite_a
    assert buckets[(3553, 10)][0][1:] == (8.0, 96.0, 24.0, 112.0)


def test_terminal_renderer_sprite_bucket_clipping_invariants() -> None:
    renderer = TerminalRenderer()
    surface = TerminalSurface(cols=6, rows=4, default_fg=(255, 255, 255, 255), default_bg=(0, 0, 0, 0))
    tex = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0)

    sprite_partial_left = TermSprite(x=-1, y=1, w=2, h=1, target=3553, tex_id=7, tex_coords=tex)
    sprite_partial_top = TermSprite(x=2, y=-1, w=1, h=2, target=3553, tex_id=7, tex_coords=tex)
    sprite_inside = TermSprite(x=3, y=2, w=2, h=2, target=3553, tex_id=7, tex_coords=tex)
    surface.sprites = [
        TermSprite(x=-3, y=1, w=2, h=1, target=3553, tex_id=7, tex_coords=tex),  # fully left
        TermSprite(x=6, y=1, w=1, h=1, target=3553, tex_id=7, tex_coords=tex),  # fully right
        TermSprite(x=2, y=4, w=1, h=1, target=3553, tex_id=7, tex_coords=tex),  # fully below
        TermSprite(x=2, y=-4, w=1, h=2, target=3553, tex_id=7, tex_coords=tex),  # fully above
        TermSprite(x=1, y=1, w=0, h=1, target=3553, tex_id=7, tex_coords=tex),  # zero width
        TermSprite(x=1, y=1, w=1, h=0, target=3553, tex_id=7, tex_coords=tex),  # zero height
        TermSprite(x=1, y=1, w=1, h=1, target=3553, tex_id=0, tex_coords=tex),  # invalid texture id
        sprite_partial_left,
        sprite_partial_top,
        sprite_inside,
    ]

    buckets = renderer._build_sprite_buckets(surface=surface, cols=6, rows=4, cell_w=10, cell_h=20, h=80)

    assert set(buckets) == {(3553, 7)}
    quads = buckets[(3553, 7)]
    assert len(quads) == 3
    quad_by_sprite_id = {id(spr): (x0, y0, x1, y1) for spr, x0, y0, x1, y1 in quads}
    assert quad_by_sprite_id[id(sprite_partial_left)] == (-10.0, 40.0, 10.0, 60.0)
    assert quad_by_sprite_id[id(sprite_partial_top)] == (20.0, 60.0, 30.0, 100.0)
    assert quad_by_sprite_id[id(sprite_inside)] == (30.0, 0.0, 50.0, 40.0)


def test_termui_font_and_ascii_bitmap_init_without_real_pyglet(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    class _DummyGlyph:
        def __init__(self) -> None:
            self.advance = 8
            self.width = 8

    class _DummyFontObj:
        ascent = 10
        descent = -2

        def get_glyphs(self, _s: str):
            return [_DummyGlyph()]

    class _Region:
        tex_coords = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0)
        id = 7
        target = 3553

    class _Texture:
        width = 128
        height = 128
        id = 7
        target = 3553
        tex_coords = (0.0,) * 12
        mag_filter = 0
        min_filter = 0

        def get_region(self, *_args, **_kwargs):
            return _Region()

    class _Image:
        def get_texture(self):
            return _Texture()

    pyglet = _install_fake_pyglet_gl(
        monkeypatch,
        constants={
            "GL_NEAREST": 9728,
            "GL_TEXTURE_2D": 3553,
            "GL_TEXTURE_MIN_FILTER": 10241,
            "GL_TEXTURE_MAG_FILTER": 10240,
        },
    )
    pyglet.font = SimpleNamespace(load=lambda *_args, **_kwargs: _DummyFontObj())
    pyglet.image = SimpleNamespace(load=lambda _path: _Image())

    tf = TerminalFont(font_name="dummy", font_size_px=12)
    assert tf.cell_w > 0 and tf.cell_h > 0

    atlas = tmp_path / "ascii.png"
    atlas.write_bytes(b"fake")
    mf = MinecraftAsciiBitmapFont(atlas_path=atlas, cell_px=8)
    g1 = mf.glyph("A")
    g2 = mf.glyph("A")
    assert g1 is g2

    # Exercise the exception fallback path in glyph(): no region, use texture-level attrs.
    mf2 = MinecraftAsciiBitmapFont(atlas_path=atlas, cell_px=8)
    mf2._tex.get_region = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no region"))
    g3 = mf2.glyph("B")
    assert g3.id == 7
    assert g3.target == 3553

    # atlas_path=None triggers lookup + error when not found.
    import enderterm.termui as termui

    monkeypatch.setattr(termui, "_default_minecraft_ascii_png", lambda: None)
    with pytest.raises(FileNotFoundError):
        MinecraftAsciiBitmapFont(atlas_path=None, cell_px=8)

    # Exercise the try/except when setting texture filtering.
    class _BadTexture(_Texture):
        def __setattr__(self, name: str, value: object) -> None:
            if name in {"mag_filter", "min_filter"}:
                raise RuntimeError("nope")
            super().__setattr__(name, value)

    class _BadImage:
        def get_texture(self):
            return _BadTexture()

    pyglet.image = SimpleNamespace(load=lambda _path: _BadImage())
    MinecraftAsciiBitmapFont(atlas_path=atlas, cell_px=8)


def test_terminal_renderer_draw_covers_additional_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_pyglet_gl(monkeypatch, constants=_TERM_RENDERER_GL_CONSTANTS)

    class _GlyphOk:
        id = 1
        target = 3553
        vertices = (0.0, 0.0, 8.0, 8.0)
        tex_coords = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0)

    class _GlyphBadTex:
        id = 1
        target = 3553
        vertices = (0.0, 0.0, 8.0, 8.0)
        tex_coords = ()

    class _GlyphNoTexId:
        id = 0
        target = 3553
        vertices = (0.0, 0.0, 8.0, 8.0)
        tex_coords = _GlyphOk.tex_coords

    class _Font:
        cell_w = 8
        cell_h = 8
        descent = 0.0

        def glyph(self, ch: str):
            if ch == "A":
                return _GlyphBadTex()
            if ch == "D":
                return _GlyphNoTexId()
            return _GlyphOk()

    renderer = TerminalRenderer()
    renderer._ensure_nearest(3553, 1)
    renderer._ensure_nearest(3553, 1)  # cached early return

    surf = TerminalSurface(cols=2, rows=2, default_fg=(255, 255, 255, 255), default_bg=(0, 0, 0, 0))
    surf.put(0, 0, "A", bg=(1, 2, 3, 4))
    surf.put(1, 0, "D")
    surf.put(0, 1, "C")

    # Viewport only shows the first cell, so we hit x/y out-of-range continues.
    renderer.draw(surface=surf, font=_Font(), vp_w_px=8, vp_h_px=8)
    # Viewport includes the second cell to cover `tex_id <= 0` early-continue.
    renderer.draw(surface=surf, font=_Font(), vp_w_px=16, vp_h_px=8)


class _DrawSceneGL:
    GL_COLOR_BUFFER_BIT = 0x4000
    GL_DEPTH_BUFFER_BIT = 0x0100

    def __getattr__(self, name: str):
        if name.startswith("GL_"):
            return 0
        if name.startswith("gl"):
            return lambda *_args, **_kwargs: None
        raise AttributeError(name)


class _DrawSceneViewer:
    def __init__(self, *, perf_enabled: bool) -> None:
        self._perf_enabled = bool(perf_enabled)
        self._effects_enabled = False
        self._fps_frames = 0
        self._fps_last_t = 0.0
        self._fps_value = 0.0
        self._perf_last_world_ms = 111.0
        self._perf_last_ui_ms = 222.0
        self._perf_last_draw_ms = 333.0
        self.clear_calls = 0
        self.hover_updates = 0
        self.overlay_updates = 0

    def _env_clear_rgb(self) -> tuple[float, float, float]:
        return (0.0, 0.0, 0.0)

    def clear(self) -> None:
        self.clear_calls += 1

    def _update_ender_vision_hover(self) -> None:
        self.hover_updates += 1

    def _update_ender_vision_overlay(self) -> None:
        self.overlay_updates += 1

    def get_viewport_size(self) -> tuple[int, int]:
        return (640, 360)


def test_draw_scene_updates_perf_counters_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    viewer = _DrawSceneViewer(perf_enabled=True)
    gl = _DrawSceneGL()
    calls = {"world": 0, "ui": 0}

    monkeypatch.setattr(
        fx_mod.time,
        "perf_counter",
        iter([10.0, 11.0, 11.2, 12.0, 12.4, 13.0]).__next__,
    )
    monkeypatch.setattr(fx_mod.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(
        fx_mod,
        "draw_world",
        lambda *_args, **_kwargs: calls.__setitem__("world", calls["world"] + 1),
    )
    monkeypatch.setattr(
        fx_mod,
        "draw_ui",
        lambda *_args, **_kwargs: calls.__setitem__("ui", calls["ui"] + 1),
    )
    monkeypatch.setattr(fx_mod, "draw_post_fx_overlay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fx_mod, "apply_copy_glitch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fx_mod, "apply_ender_vignette", lambda *_args, **_kwargs: None)

    fx_mod.draw_scene(viewer, gl=gl, param_store=SimpleNamespace())

    assert calls["world"] == 1
    assert calls["ui"] == 1
    assert viewer._perf_last_world_ms == pytest.approx(200.0)
    assert viewer._perf_last_ui_ms == pytest.approx(400.0)
    assert viewer._perf_last_draw_ms == pytest.approx(3000.0)


def test_draw_scene_does_not_update_perf_counters_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    viewer = _DrawSceneViewer(perf_enabled=False)
    gl = _DrawSceneGL()
    calls = {"world": 0, "ui": 0, "perf_counter": 0}

    monkeypatch.setattr(fx_mod.time, "monotonic", lambda: 1.0)

    def _perf_counter() -> float:
        calls["perf_counter"] += 1
        return 99.0

    monkeypatch.setattr(fx_mod.time, "perf_counter", _perf_counter)
    monkeypatch.setattr(
        fx_mod,
        "draw_world",
        lambda *_args, **_kwargs: calls.__setitem__("world", calls["world"] + 1),
    )
    monkeypatch.setattr(
        fx_mod,
        "draw_ui",
        lambda *_args, **_kwargs: calls.__setitem__("ui", calls["ui"] + 1),
    )
    monkeypatch.setattr(fx_mod, "draw_post_fx_overlay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fx_mod, "apply_copy_glitch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fx_mod, "apply_ender_vignette", lambda *_args, **_kwargs: None)

    fx_mod.draw_scene(viewer, gl=gl, param_store=SimpleNamespace())

    assert calls["world"] == 1
    assert calls["ui"] == 1
    assert calls["perf_counter"] == 0
    assert viewer._perf_last_world_ms == pytest.approx(111.0)
    assert viewer._perf_last_ui_ms == pytest.approx(222.0)
    assert viewer._perf_last_draw_ms == pytest.approx(333.0)


def test_app_macos_try_configure_minecraft_jar_from_arg_filters_non_jar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import enderterm.app_macos as app_macos

    validate_calls: list[Path] = []
    monkeypatch.setattr(app_macos, "validate_minecraft_client_jar", lambda p: validate_calls.append(Path(p)) or None)

    assert app_macos._try_configure_minecraft_jar_from_arg("") is False
    assert app_macos._try_configure_minecraft_jar_from_arg("--flag") is False
    assert app_macos._try_configure_minecraft_jar_from_arg(tmp_path / "readme.txt") is False
    assert validate_calls == []


def test_app_macos_try_configure_minecraft_jar_from_arg_returns_false_when_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import enderterm.app_macos as app_macos

    jar = tmp_path / "client.jar"
    saved: list[Path] = []
    monkeypatch.setattr(app_macos, "validate_minecraft_client_jar", lambda _p: "invalid")
    monkeypatch.setattr(app_macos, "save_configured_minecraft_jar_path", lambda p: saved.append(Path(p)))
    monkeypatch.delenv("MINECRAFT_JAR", raising=False)

    assert app_macos._try_configure_minecraft_jar_from_arg(jar) is False
    assert saved == []
    assert "MINECRAFT_JAR" not in app_macos.os.environ


def test_app_macos_try_configure_minecraft_jar_from_arg_saves_and_sets_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import enderterm.app_macos as app_macos

    jar = tmp_path / "client.jar"
    saved: list[Path] = []
    monkeypatch.setattr(app_macos, "validate_minecraft_client_jar", lambda _p: None)
    monkeypatch.setattr(app_macos, "save_configured_minecraft_jar_path", lambda p: saved.append(Path(p)))
    monkeypatch.delenv("MINECRAFT_JAR", raising=False)

    assert app_macos._try_configure_minecraft_jar_from_arg(jar) is True
    assert saved == [jar]
    assert app_macos.os.environ["MINECRAFT_JAR"] == str(jar)
