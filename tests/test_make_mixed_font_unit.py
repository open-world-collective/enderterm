from __future__ import annotations

from pathlib import Path

import pytest


def test_make_postscript_name_sanitizes_and_truncates() -> None:
    from enderterm.make_mixed_font import _make_postscript_name

    assert _make_postscript_name(" terminal Mixed ") == "terminal_Mixed"
    assert _make_postscript_name("a/b:c") == "a_b_c"
    assert len(_make_postscript_name("x" * 999)) == 63
    assert _make_postscript_name("   ") == "MixedFont"


def test_find_ttc_font_number_finds_english_and_ender() -> None:
    from enderterm.make_mixed_font import _find_ttc_font_number

    here = Path(__file__).resolve()
    term_ttc = here.parents[1] / "enderterm" / "assets" / "fonts" / "term.ttc"
    assert term_ttc.is_file()

    assert isinstance(_find_ttc_font_number(term_ttc, want_subfamily="English"), int)
    assert isinstance(_find_ttc_font_number(term_ttc, want_subfamily="Ender"), int)

    with pytest.raises(SystemExit):
        _find_ttc_font_number(term_ttc, want_subfamily="DefinitelyNotARealSubfamily")


def test_find_ttc_font_number_reuses_cached_ttc_scan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import enderterm.make_mixed_font as mm

    class _FakeFont:
        def __init__(self, subfamily: str) -> None:
            self.subfamily = subfamily

    class _FakeCollection:
        def __init__(self) -> None:
            self.fonts = [_FakeFont("English"), _FakeFont("Ender")]

    scans = {"count": 0}

    def fake_ttcollection(_path: str) -> _FakeCollection:
        scans["count"] += 1
        return _FakeCollection()

    def fake_get_name(font: _FakeFont, name_id: int) -> str | None:
        if name_id != 2:
            return None
        return font.subfamily

    mm._ttc_subfamily_index_map.cache_clear()
    monkeypatch.setattr(mm, "TTCollection", fake_ttcollection)
    monkeypatch.setattr(mm, "_get_name", fake_get_name)

    ttc_path = tmp_path / "fake.ttc"
    assert mm._find_ttc_font_number(ttc_path, want_subfamily="English") == 0
    assert mm._find_ttc_font_number(ttc_path, want_subfamily="Ender") == 1
    assert scans["count"] == 1

    mm._ttc_subfamily_index_map.cache_clear()


def test_make_mixed_font_writes_ttf(tmp_path: Path) -> None:
    from fontTools.ttLib import TTFont
    from enderterm.make_mixed_font import make_mixed_font

    repo_root = Path(__file__).resolve().parents[1]
    term_ttc = repo_root / "enderterm" / "assets" / "fonts" / "term.ttc"
    out_ttf = tmp_path / "term_mixed_test.ttf"

    make_mixed_font(
        term_ttc,
        out_ttf,
        pua_base=0xE000,
        copy_ascii_from=(0x41, 0x43),  # keep it fast: only A..C
    )
    assert out_ttf.is_file()
    assert out_ttf.stat().st_size > 0

    loaded = TTFont(str(out_ttf))
    assert loaded["maxp"].numGlyphs == len(loaded.getGlyphOrder())


def test_make_mixed_font_scans_writable_cmap_tables_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import enderterm.make_mixed_font as mm

    repo_root = Path(__file__).resolve().parents[1]
    term_ttc = repo_root / "enderterm" / "assets" / "fonts" / "term.ttc"
    assert term_ttc.is_file()

    calls = {"count": 0}
    original = mm._iter_writable_unicode_cmap_tables

    def wrapped(font: object):
        calls["count"] += 1
        yield from original(font)

    monkeypatch.setattr(mm, "_iter_writable_unicode_cmap_tables", wrapped)

    out_ttf = tmp_path / "term_mixed_scan_once.ttf"
    mm.make_mixed_font(term_ttc, out_ttf, pua_base=0xE000, copy_ascii_from=(0x41, 0x43))
    assert out_ttf.is_file()
    assert calls["count"] == 1


def test_make_mixed_font_batches_unicode_cmap_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import enderterm.make_mixed_font as mm

    repo_root = Path(__file__).resolve().parents[1]
    term_ttc = repo_root / "enderterm" / "assets" / "fonts" / "term.ttc"
    assert term_ttc.is_file()

    calls = {"count": 0, "sizes": []}
    original = mm._add_unicode_mapping

    def wrapped(*, writable_cmap_tables: object, codepoint_to_glyph_name: dict[int, str]) -> None:
        calls["count"] += 1
        calls["sizes"].append(len(codepoint_to_glyph_name))
        original(writable_cmap_tables=writable_cmap_tables, codepoint_to_glyph_name=codepoint_to_glyph_name)

    monkeypatch.setattr(mm, "_add_unicode_mapping", wrapped)

    out_ttf = tmp_path / "term_mixed_batched_writes.ttf"
    mm.make_mixed_font(term_ttc, out_ttf, pua_base=0xE000, copy_ascii_from=(0x41, 0x43))
    assert out_ttf.is_file()
    assert calls["count"] == 1
    assert calls["sizes"] == [3]


def test_cu2qu_pen_class_lookup_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    import enderterm.make_mixed_font as mm

    calls = {"count": 0}
    real_import = builtins.__import__

    def wrapped_import(name: str, globals: object = None, locals: object = None, fromlist: tuple[str, ...] = (), level: int = 0):
        if name == "fontTools.pens.cu2quPen":
            calls["count"] += 1
        return real_import(name, globals, locals, fromlist, level)

    cache_clear = getattr(mm._cu2qu_pen_class, "cache_clear")
    cache_clear()
    monkeypatch.setattr(builtins, "__import__", wrapped_import)

    assert mm._cu2qu_pen_class() is not None
    assert mm._cu2qu_pen_class() is not None
    assert calls["count"] == 1

    cache_clear()


def test_make_mixed_font_resolves_cu2qu_pen_class_once_per_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import enderterm.make_mixed_font as mm

    repo_root = Path(__file__).resolve().parents[1]
    term_ttc = repo_root / "enderterm" / "assets" / "fonts" / "term.ttc"
    assert term_ttc.is_file()

    calls = {"count": 0}
    original = mm._cu2qu_pen_class
    cache_clear = getattr(original, "cache_clear")
    cache_clear()

    def wrapped() -> object:
        calls["count"] += 1
        return original()

    monkeypatch.setattr(mm, "_cu2qu_pen_class", wrapped)

    out_ttf = tmp_path / "term_mixed_cu2qu_once.ttf"
    mm.make_mixed_font(term_ttc, out_ttf, pua_base=0xE000, copy_ascii_from=(0x41, 0x43))
    assert out_ttf.is_file()
    assert calls["count"] == 1

    cache_clear()


def test_finalize_mixed_font_skips_recalc_when_no_added_glyphs(monkeypatch: pytest.MonkeyPatch) -> None:
    import enderterm.make_mixed_font as mm

    calls = {"set_order": 0, "recalc": 0, "set_family": 0}

    class _Maxp:
        def __init__(self) -> None:
            self.numGlyphs = 0

        def recalc(self, _font: object) -> None:
            calls["recalc"] += 1

    class _Font(dict):
        def __init__(self) -> None:
            super().__init__()
            self["maxp"] = _Maxp()

        def __contains__(self, key: object) -> bool:
            return key == "maxp"

        def setGlyphOrder(self, _glyph_order: list[str]) -> None:
            calls["set_order"] += 1

    def fake_set_family(*_args: object, **_kwargs: object) -> None:
        calls["set_family"] += 1

    monkeypatch.setattr(mm, "_set_family", fake_set_family)

    font = _Font()
    mm._finalize_mixed_font(english=font, glyph_order=[".notdef", "uniE041"], added_glyphs=0)
    assert calls["set_order"] == 0
    assert calls["recalc"] == 0
    assert calls["set_family"] == 1

    mm._finalize_mixed_font(english=font, glyph_order=[".notdef", "uniE041"], added_glyphs=1)
    assert calls["set_order"] == 1
    assert calls["recalc"] == 0
    assert calls["set_family"] == 2
    assert font["maxp"].numGlyphs == 2


def test_get_name_and_set_name_handle_fallbacks_and_encoding_errors() -> None:
    from enderterm.make_mixed_font import _get_name, _set_name

    class _Rec:
        def __init__(self, *, name_id: int, value: str, raise_unicode: bool = False, raise_encoding: bool = False) -> None:
            self.nameID = int(name_id)
            self._value = value
            self._raise_unicode = raise_unicode
            self._raise_encoding = raise_encoding
            self.string = b""

        def toUnicode(self) -> str:
            if self._raise_unicode:
                raise ValueError("bad unicode")
            return self._value

        def getEncoding(self) -> str:
            if self._raise_encoding:
                raise LookupError("bad encoding")
            return "ascii"

    class _NameTable:
        def __init__(self) -> None:
            self.names = [
                _Rec(name_id=2, value="ignored", raise_unicode=True),
                _Rec(name_id=2, value="FallbackOK"),
                _Rec(name_id=4, value="Full Name"),
            ]
            self._calls = 0

        def getName(self, _name_id: int, _platform_id: int, _encoding_id: int, _lang_id: int):
            self._calls += 1
            if self._calls == 1:
                return _Rec(name_id=2, value="bad", raise_unicode=True)
            return None

    class _Font(dict):
        def __init__(self) -> None:
            super().__init__()
            self["name"] = _NameTable()

    font = _Font()
    assert _get_name(font, 2) == "FallbackOK"
    assert _get_name(font, 999) is None

    bad_enc = _Rec(name_id=1, value="x", raise_encoding=True)
    font["name"].names.append(bad_enc)
    _set_name(font, 1, "terminal")
    assert bad_enc.string


def test_make_mixed_font_main_parses_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import sys

    import enderterm.make_mixed_font as mm

    calls: list[tuple[Path, Path, int]] = []

    def fake_make(term_ttc: Path, out_ttf: Path, *, pua_base: int = 0xE000, **_kw: object) -> None:
        calls.append((Path(term_ttc), Path(out_ttf), int(pua_base)))

    monkeypatch.setattr(mm, "make_mixed_font", fake_make)

    ttc = tmp_path / "term.ttc"
    ttc.write_bytes(b"fake")
    out = tmp_path / "out.ttf"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_mixed_font.py",
            "--term-ttc",
            str(ttc),
            "--out",
            str(out),
            "--pua-base",
            "0xE100",
        ],
    )
    mm.main()
    assert calls == [(ttc, out, 0xE100)]


def test_make_mixed_font_exits_when_missing_required_tables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import enderterm.make_mixed_font as mm

    class _Font:
        def __init__(self, present_tables: set[str]) -> None:
            self._present_tables = set(present_tables)

        def __contains__(self, key: object) -> bool:
            return bool(isinstance(key, str) and key in self._present_tables)

    def fake_find(_ttc_path: Path, *, want_subfamily: str) -> int:
        return 0 if want_subfamily == "English" else 1

    def fake_ttfont(_path: str, *, fontNumber: int = 0, **_kw: object) -> _Font:
        if fontNumber == 0:
            return _Font(set())  # missing 'glyf'
        return _Font({"cmap"})

    monkeypatch.setattr(mm, "_find_ttc_font_number", fake_find)
    monkeypatch.setattr(mm, "TTFont", fake_ttfont)

    with pytest.raises(SystemExit, match="Expected TrueType 'glyf' table"):
        mm.make_mixed_font(tmp_path / "in.ttc", tmp_path / "out.ttf")

    def fake_ttfont_missing_cmap(_path: str, *, fontNumber: int = 0, **_kw: object) -> _Font:
        return _Font({"glyf"}) if fontNumber == 0 else _Font(set())

    monkeypatch.setattr(mm, "TTFont", fake_ttfont_missing_cmap)
    with pytest.raises(SystemExit, match="Expected 'cmap' tables"):
        mm.make_mixed_font(tmp_path / "in.ttc", tmp_path / "out.ttf")

    def fake_ttfont_missing_hmtx(_path: str, *, fontNumber: int = 0, **_kw: object) -> _Font:
        return _Font({"glyf", "cmap"}) if fontNumber == 0 else _Font({"cmap"})

    monkeypatch.setattr(mm, "TTFont", fake_ttfont_missing_hmtx)
    with pytest.raises(SystemExit, match="Expected 'hmtx' table"):
        mm.make_mixed_font(tmp_path / "in.ttc", tmp_path / "out.ttf")


def test_make_mixed_font_handles_skips_and_cmap_write_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import enderterm.make_mixed_font as mm

    class _Glyf:
        def __init__(self, glyphs: dict[str, object]) -> None:
            self.glyphs = dict(glyphs)

        def __getitem__(self, key: str) -> object:
            return self.glyphs[key]

    class _Hmtx:
        def __init__(self, metrics: dict[str, tuple[int, int]]) -> None:
            self.metrics = dict(metrics)

    class _RaisingMap(dict):
        def __setitem__(self, _k: object, _v: object) -> None:
            raise ValueError("nope")

    class _BadPlatformTable:
        platformID = 1
        platEncID = 0
        cmap: dict[int, str] = {}

    class _NoCmapTable:
        platformID = 0
        platEncID = 1
        cmap = None

    class _RaisingTable:
        platformID = 0
        platEncID = 1

        def __init__(self) -> None:
            self.cmap = _RaisingMap()

    class _Cmap:
        def __init__(self) -> None:
            self.tables = [_BadPlatformTable(), _NoCmapTable(), _RaisingTable()]

    class _Font:
        def __init__(self, *, glyf: _Glyf | None, cmap: dict[int, str]) -> None:
            self._glyf = glyf
            self._cmap = dict(cmap)
            self._hmtx = _Hmtx({".notdef": (123, 0)})
            self._cmap_table = _Cmap()
            self._glyph_order: list[str] = [".notdef"]

        def __contains__(self, key: object) -> bool:
            if key == "glyf":
                return self._glyf is not None
            return key in {"cmap", "hmtx"}

        def __getitem__(self, key: str) -> object:
            if key == "glyf":
                if self._glyf is None:
                    raise KeyError(key)
                return self._glyf
            if key == "hmtx":
                return self._hmtx
            if key == "cmap":
                return self._cmap_table
            raise KeyError(key)

        def getBestCmap(self) -> dict[int, str]:
            return dict(self._cmap)

        def getGlyphOrder(self) -> list[str]:
            return list(self._glyph_order)

        def setGlyphOrder(self, glyph_order: list[str]) -> None:
            self._glyph_order = list(glyph_order)

        def getGlyphSet(self) -> dict[str, object]:
            return {}

        def save(self, path: str) -> None:
            Path(path).write_bytes(b"ttf")

    english_glyf = _Glyf({"uniE041": object()})
    ender_glyf = _Glyf({"EA": object(), "EB": object()})

    english = _Font(glyf=english_glyf, cmap={0x41: "A", 0x42: "B", 0x43: "C"})
    ender = _Font(glyf=ender_glyf, cmap={0x41: "EA", 0x42: "EB"})  # missing 0x43 -> skip

    def fake_find(_ttc_path: Path, *, want_subfamily: str) -> int:
        return 0 if want_subfamily == "English" else 1

    def fake_ttfont(_path: str, *, fontNumber: int = 0, **_kw: object) -> _Font:
        return english if fontNumber == 0 else ender

    monkeypatch.setattr(mm, "_find_ttc_font_number", fake_find)
    monkeypatch.setattr(mm, "TTFont", fake_ttfont)
    monkeypatch.setattr(mm, "_set_family", lambda *_a, **_kw: None)

    out_ttf = tmp_path / "out.ttf"
    mm.make_mixed_font(tmp_path / "in.ttc", out_ttf, pua_base=0xE000, copy_ascii_from=(0x41, 0x43))
    assert out_ttf.is_file()
    assert "uniE042" in english_glyf.glyphs


def test_make_mixed_font_skips_out_of_range_pua_codepoints(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import enderterm.make_mixed_font as mm

    class _Glyf:
        def __init__(self) -> None:
            self.glyphs: dict[str, object] = {}

    class _Hmtx:
        def __init__(self) -> None:
            self.metrics = {".notdef": (123, 0)}

    class _Cmap:
        def __init__(self) -> None:
            self.tables: list[object] = []

    class _Font:
        def __init__(self) -> None:
            self._glyph_order: list[str] = [".notdef"]
            self._glyf = _Glyf()
            self._hmtx = _Hmtx()
            self._cmap = _Cmap()

        def __contains__(self, key: object) -> bool:
            return key in {"glyf", "cmap", "hmtx"}

        def __getitem__(self, key: str) -> object:
            if key == "glyf":
                return self._glyf
            if key == "hmtx":
                return self._hmtx
            if key == "cmap":
                return self._cmap
            raise KeyError(key)

        def getBestCmap(self) -> dict[int, str]:
            return {0x41: "A"}

        def getGlyphOrder(self) -> list[str]:
            return list(self._glyph_order)

        def setGlyphOrder(self, glyph_order: list[str]) -> None:
            self._glyph_order = list(glyph_order)

        def getGlyphSet(self) -> dict[str, object]:
            return {}

        def save(self, path: str) -> None:
            Path(path).write_bytes(b"ttf")

    english = _Font()
    ender = _Font()

    monkeypatch.setattr(mm, "_find_ttc_font_number", lambda *_a, **_kw: 0)
    monkeypatch.setattr(mm, "TTFont", lambda *_a, **_kw: english if _kw.get("fontNumber", 0) == 0 else ender)
    monkeypatch.setattr(mm, "_set_family", lambda *_a, **_kw: None)

    out_ttf = tmp_path / "out.ttf"
    mm.make_mixed_font(tmp_path / "in.ttc", out_ttf, pua_base=0x1_0000, copy_ascii_from=(0x41, 0x41))
    assert out_ttf.is_file()
    assert english["glyf"].glyphs == {}


def test_make_mixed_font_handles_descending_ascii_range_as_noop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import enderterm.make_mixed_font as mm

    class _Glyf:
        def __init__(self) -> None:
            self.glyphs: dict[str, object] = {}

    class _Hmtx:
        def __init__(self) -> None:
            self.metrics = {".notdef": (123, 0)}

    class _Cmap:
        def __init__(self) -> None:
            self.tables: list[object] = []

    class _Font:
        def __init__(self) -> None:
            self._glyph_order: list[str] = [".notdef"]
            self._glyf = _Glyf()
            self._hmtx = _Hmtx()
            self._cmap = _Cmap()

        def __contains__(self, key: object) -> bool:
            return key in {"glyf", "cmap", "hmtx"}

        def __getitem__(self, key: str) -> object:
            if key == "glyf":
                return self._glyf
            if key == "hmtx":
                return self._hmtx
            if key == "cmap":
                return self._cmap
            raise KeyError(key)

        def getBestCmap(self) -> dict[int, str]:
            return {0x41: "A", 0x42: "B", 0x43: "C"}

        def getGlyphOrder(self) -> list[str]:
            return list(self._glyph_order)

        def setGlyphOrder(self, glyph_order: list[str]) -> None:
            self._glyph_order = list(glyph_order)

        def getGlyphSet(self) -> dict[str, object]:
            return {}

        def save(self, path: str) -> None:
            Path(path).write_bytes(b"ttf")

    english = _Font()
    ender = _Font()

    monkeypatch.setattr(mm, "_find_ttc_font_number", lambda *_a, **_kw: 0)
    monkeypatch.setattr(mm, "TTFont", lambda *_a, **_kw: english if _kw.get("fontNumber", 0) == 0 else ender)
    monkeypatch.setattr(mm, "_set_family", lambda *_a, **_kw: None)

    out_ttf = tmp_path / "out.ttf"
    mm.make_mixed_font(tmp_path / "in.ttc", out_ttf, pua_base=0xE000, copy_ascii_from=(0x43, 0x41))
    assert out_ttf.is_file()
    assert english["glyf"].glyphs == {}


def test_make_mixed_font_converts_non_glyf_ender_via_pen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fontTools.pens.cu2quPen as cu2qu
    import enderterm.make_mixed_font as mm

    class _Glyf:
        def __init__(self) -> None:
            self.glyphs: dict[str, object] = {}

    class _Hmtx:
        def __init__(self) -> None:
            self.metrics = {".notdef": (123, 0)}

    class _Cmap:
        def __init__(self) -> None:
            self.tables: list[object] = []

    class _Glyph:
        def __init__(self) -> None:
            self.called = False

        def draw(self, _pen: object) -> None:
            self.called = True

    class _Pen:
        def __init__(self, _glyph_set: object) -> None:
            self._out = object()

        def glyph(self) -> object:
            return self._out

    class _EnglishFont:
        def __init__(self) -> None:
            self._glyph_order: list[str] = [".notdef"]
            self._glyf = _Glyf()
            self._hmtx = _Hmtx()
            self._cmap = _Cmap()

        def __contains__(self, key: object) -> bool:
            return key in {"glyf", "cmap", "hmtx"}

        def __getitem__(self, key: str) -> object:
            if key == "glyf":
                return self._glyf
            if key == "hmtx":
                return self._hmtx
            if key == "cmap":
                return self._cmap
            raise KeyError(key)

        def getBestCmap(self) -> dict[int, str]:
            return {0x41: "A"}

        def getGlyphOrder(self) -> list[str]:
            return list(self._glyph_order)

        def setGlyphOrder(self, glyph_order: list[str]) -> None:
            self._glyph_order = list(glyph_order)

        def getGlyphSet(self) -> dict[str, object]:
            return {}

        def save(self, path: str) -> None:
            Path(path).write_bytes(b"ttf")

    class _EnderFont:
        def __init__(self) -> None:
            self.glyph = _Glyph()

        def __contains__(self, key: object) -> bool:
            return key == "cmap"

        def __getitem__(self, key: str) -> object:
            raise KeyError(key)

        def getBestCmap(self) -> dict[int, str]:
            return {0x41: "EA"}

        def getGlyphSet(self) -> dict[str, _Glyph]:
            return {"EA": self.glyph}

    english = _EnglishFont()
    ender = _EnderFont()

    monkeypatch.setattr(mm, "_find_ttc_font_number", lambda *_a, **kw: 0 if kw.get("want_subfamily") == "English" else 1)
    monkeypatch.setattr(mm, "TTFont", lambda *_a, **kw: english if kw.get("fontNumber", 0) == 0 else ender)
    monkeypatch.setattr(mm, "_set_family", lambda *_a, **_kw: None)
    monkeypatch.setattr(mm, "TTGlyphPen", _Pen)
    cache_clear = getattr(mm._cu2qu_pen_class, "cache_clear")
    cache_clear()
    monkeypatch.setattr(cu2qu, "Cu2QuPen", lambda pen, **_kw: pen)

    out_ttf = tmp_path / "out.ttf"
    mm.make_mixed_font(tmp_path / "in.ttc", out_ttf, pua_base=0xE000, copy_ascii_from=(0x41, 0x41))
    assert out_ttf.is_file()
    assert ender.glyph.called
    cache_clear()
