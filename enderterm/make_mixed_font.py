#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import functools
import re
from pathlib import Path
from typing import Any, Iterable

from fontTools.ttLib import TTCollection, TTFont
from fontTools.pens.ttGlyphPen import TTGlyphPen

_UNICODE_BMP_MAX = 0xFFFF
_DEFAULT_ASCII_RANGE = (0x20, 0x7E)


def _get_name(font: TTFont, name_id: int) -> str | None:
    name_table = font["name"]
    preferred = [
        (3, 1, 1033),
        (1, 0, 0),
        (0, 0, 0),
    ]
    for platform_id, encoding_id, lang_id in preferred:
        rec = name_table.getName(name_id, platform_id, encoding_id, lang_id)
        if rec is None:
            continue
        try:
            return rec.toUnicode()
        except Exception:
            continue
    for rec in name_table.names:
        if rec.nameID != name_id:
            continue
        try:
            return rec.toUnicode()
        except Exception:
            continue
    return None


def _set_name(font: TTFont, name_id: int, value: str) -> None:
    name_table = font["name"]
    for rec in name_table.names:
        if rec.nameID != name_id:
            continue
        try:
            rec.string = value.encode(rec.getEncoding(), errors="replace")
        except Exception:
            rec.string = value.encode("utf-8", errors="replace")


def _make_postscript_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:63] if cleaned else "MixedFont"


def _set_family(font: TTFont, *, family: str, subfamily: str) -> None:
    full_name = f"{family} {subfamily}".strip()
    postscript_name = _make_postscript_name(f"{family}-{subfamily}")
    unique_id = full_name
    _set_name(font, 1, family)  # family
    _set_name(font, 2, subfamily)  # subfamily
    _set_name(font, 3, unique_id)  # unique id
    _set_name(font, 4, full_name)  # full name
    _set_name(font, 6, postscript_name)  # postscript name


@functools.lru_cache(maxsize=16)
def _ttc_subfamily_index_map(ttc_path_str: str) -> dict[str, int]:
    coll = TTCollection(ttc_path_str)
    out: dict[str, int] = {}
    for idx, font in enumerate(coll.fonts):
        subfamily = (_get_name(font, 2) or "").strip().lower()
        if subfamily and subfamily not in out:
            out[subfamily] = idx
    return out


def _find_ttc_font_number(ttc_path: Path, *, want_subfamily: str) -> int:
    want = want_subfamily.strip().lower()
    idx = _ttc_subfamily_index_map(str(ttc_path)).get(want)
    if idx is not None:
        return idx
    raise SystemExit(f"Could not find subfamily '{want_subfamily}' in {ttc_path}")


def _validate_source_fonts(*, english: TTFont, ender: TTFont) -> None:
    if "glyf" not in english:
        raise SystemExit("Expected TrueType 'glyf' table in English font")
    if "cmap" not in english or "cmap" not in ender:
        raise SystemExit("Expected 'cmap' tables in both English and Ender fonts")
    if "hmtx" not in english:
        raise SystemExit("Expected 'hmtx' table in English font")


@functools.lru_cache(maxsize=1)
def _cu2qu_pen_class() -> Any | None:
    try:
        from fontTools.pens.cu2quPen import Cu2QuPen

        return Cu2QuPen
    except Exception:
        return None


def _build_ender_glyph(
    *,
    ender: TTFont,
    ender_glyph_name: str,
    ender_has_glyf: bool,
    ender_glyph_set: Any,
    base_glyph_set: Any,
    cu2qu_pen_class: Any | None,
) -> Any:
    if ender_has_glyf:
        return copy.deepcopy(ender["glyf"][ender_glyph_name])

    # Ender is a CFF flavor; convert outlines to quadratic TrueType.
    pen = TTGlyphPen(base_glyph_set)
    if cu2qu_pen_class is not None:
        try:
            qpen = cu2qu_pen_class(pen, maxErr=1.0, reverse_direction=False)
            ender_glyph_set[ender_glyph_name].draw(qpen)
            return pen.glyph()
        except Exception:
            pass

    ender_glyph_set[ender_glyph_name].draw(pen)
    return pen.glyph()


def _metric_for_new_glyph(*, hmtx: Any, base_glyph_name: str) -> tuple[int, int]:
    if base_glyph_name in hmtx.metrics:
        return hmtx.metrics[base_glyph_name]
    return (hmtx.metrics.get(".notdef", (0, 0))[0], 0)


def _iter_writable_unicode_cmap_tables(font: TTFont):
    for table in font["cmap"].tables:
        platform_ok = table.platformID == 0 or (table.platformID == 3 and table.platEncID in (1, 10))
        if not platform_ok:
            continue
        if not hasattr(table, "cmap") or table.cmap is None:
            continue
        yield table


def _iter_ascii_codepoints(copy_ascii_from: tuple[int, int]) -> Iterable[int]:
    ascii_lo, ascii_hi = copy_ascii_from
    return range(int(ascii_lo), int(ascii_hi) + 1)


def _add_unicode_mapping(*, writable_cmap_tables: Iterable[Any], codepoint_to_glyph_name: dict[int, str]) -> None:
    if not codepoint_to_glyph_name:
        return

    for table in writable_cmap_tables:
        cmap = getattr(table, "cmap", None)
        if cmap is None:
            continue
        try:
            cmap.update(codepoint_to_glyph_name)
            continue
        except Exception:
            pass

        for codepoint, glyph_name in codepoint_to_glyph_name.items():
            try:
                cmap[codepoint] = glyph_name
            except Exception:
                continue


def _load_source_fonts(term_ttc: Path) -> tuple[TTFont, TTFont]:
    english_idx = _find_ttc_font_number(term_ttc, want_subfamily="English")
    ender_idx = _find_ttc_font_number(term_ttc, want_subfamily="Ender")
    english = TTFont(str(term_ttc), fontNumber=english_idx)
    ender = TTFont(str(term_ttc), fontNumber=ender_idx)
    _validate_source_fonts(english=english, ender=ender)
    return english, ender


def _add_pua_ender_variant_for_codepoint(
    *,
    cp: int,
    pua_base: int,
    ender: TTFont,
    english_cmap: dict[int, str],
    ender_cmap: dict[int, str],
    glyf: Any,
    hmtx: Any,
    glyph_order: list[str],
    ender_has_glyf: bool,
    ender_glyph_set: Any,
    base_glyph_set: Any,
    cu2qu_pen_class: Any | None,
    new_unicode_mappings: dict[int, str],
) -> bool:
    base_glyph_name = english_cmap.get(cp)
    ender_glyph_name = ender_cmap.get(cp)
    if not base_glyph_name or not ender_glyph_name:
        return False

    new_cp = pua_base + cp
    if not (0 <= new_cp <= _UNICODE_BMP_MAX):
        return False

    new_glyph_name = f"uni{new_cp:04X}"
    if new_glyph_name in glyf.glyphs:
        return False

    glyf.glyphs[new_glyph_name] = _build_ender_glyph(
        ender=ender,
        ender_glyph_name=ender_glyph_name,
        ender_has_glyf=ender_has_glyf,
        ender_glyph_set=ender_glyph_set,
        base_glyph_set=base_glyph_set,
        cu2qu_pen_class=cu2qu_pen_class,
    )
    glyph_order.append(new_glyph_name)
    hmtx.metrics[new_glyph_name] = _metric_for_new_glyph(hmtx=hmtx, base_glyph_name=base_glyph_name)
    new_unicode_mappings[new_cp] = new_glyph_name
    return True


def _finalize_mixed_font(*, english: TTFont, glyph_order: list[str], added_glyphs: int) -> None:
    if int(added_glyphs) > 0:
        english.setGlyphOrder(glyph_order)
        if "maxp" in english:
            english["maxp"].numGlyphs = len(glyph_order)
    _set_family(english, family="terminal", subfamily="Mixed")


def _default_term_ttc_path(script_dir: Path) -> Path:
    workspace_ttc = (script_dir / ".." / "font" / "term.ttc").resolve()
    if workspace_ttc.is_file():
        return workspace_ttc
    return (script_dir / "assets" / "fonts" / "term.ttc").resolve()


def _build_arg_parser(*, script_dir: Path, default_ttc: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a single 'terminal Mixed' font with Ender variants in PUA.")
    parser.add_argument("--term-ttc", type=Path, default=default_ttc, help="Input term.ttc path")
    parser.add_argument(
        "--out",
        type=Path,
        default=(script_dir / "assets" / "fonts" / "term_mixed.ttf"),
        help="Output .ttf path",
    )
    parser.add_argument("--pua-base", type=lambda s: int(s, 0), default=0xE000, help="PUA base (hex ok)")
    return parser


def make_mixed_font(
    term_ttc: Path,
    output_ttf: Path,
    *,
    pua_base: int = 0xE000,
    copy_ascii_from: tuple[int, int] = _DEFAULT_ASCII_RANGE,
) -> None:
    english, ender = _load_source_fonts(term_ttc)

    english_cmap = english.getBestCmap()
    ender_cmap = ender.getBestCmap()
    glyph_order = list(english.getGlyphOrder())
    glyf = english["glyf"]
    hmtx = english["hmtx"]

    ender_has_glyf = "glyf" in ender
    cu2qu_pen_class = None if ender_has_glyf else _cu2qu_pen_class()
    ender_glyph_set = ender.getGlyphSet()
    base_glyph_set = english.getGlyphSet()
    writable_cmap_tables = tuple(_iter_writable_unicode_cmap_tables(english))
    new_unicode_mappings: dict[int, str] = {}

    pua_base = int(pua_base)
    added = 0
    for cp in _iter_ascii_codepoints(copy_ascii_from):
        added_variant = _add_pua_ender_variant_for_codepoint(
            cp=cp,
            pua_base=pua_base,
            ender=ender,
            english_cmap=english_cmap,
            ender_cmap=ender_cmap,
            glyf=glyf,
            hmtx=hmtx,
            glyph_order=glyph_order,
            ender_has_glyf=ender_has_glyf,
            ender_glyph_set=ender_glyph_set,
            base_glyph_set=base_glyph_set,
            cu2qu_pen_class=cu2qu_pen_class,
            new_unicode_mappings=new_unicode_mappings,
        )
        if added_variant:
            added += 1

    _add_unicode_mapping(writable_cmap_tables=writable_cmap_tables, codepoint_to_glyph_name=new_unicode_mappings)

    _finalize_mixed_font(english=english, glyph_order=glyph_order, added_glyphs=added)

    output_ttf.parent.mkdir(parents=True, exist_ok=True)
    english.save(str(output_ttf))
    print(f"Wrote {output_ttf} (+{added} Ender glyphs into PUA)")


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    default_ttc = _default_term_ttc_path(script_dir)
    args = _build_arg_parser(script_dir=script_dir, default_ttc=default_ttc).parse_args()

    make_mixed_font(args.term_ttc, args.out, pua_base=args.pua_base)


if __name__ == "__main__":
    main()
