#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render a PNG specimen for terminal Mixed (Ender variants in PUA).")
    parser.add_argument("--font", type=Path, default=_default_mixed_font(), help="Path to term_mixed.ttf")
    parser.add_argument("--out", type=Path, default=Path("~/tmp/term_mixed_specimen.png").expanduser())
    parser.add_argument("--pua-base", type=lambda s: int(s, 0), default=0xE000, help="PUA base (hex ok)")
    parser.add_argument("--size", type=int, default=18, help="Font size")
    parser.add_argument("--width", type=int, default=1280, help="Window width (points)")
    parser.add_argument("--height", type=int, default=900, help="Window height (points)")
    parser.add_argument("--open", action="store_true", help="Open the output image with the OS default viewer")
    return parser


def _default_mixed_font() -> Path:
    here = Path(__file__).resolve()
    workspace = here.parents[2] / "font" / "term_mixed.ttf"
    if workspace.is_file():
        return workspace
    bundled = here.parent / "assets" / "fonts" / "term_mixed.ttf"
    if bundled.is_file():
        return bundled
    # Fallback: allow running even if the font isn't built yet.
    return workspace


def _to_pua_variant(text: str, *, pua_base: int = 0xE000) -> str:
    out: list[str] = []
    for ch in text:
        o = ord(ch)
        if 0x20 <= o <= 0x7E:
            out.append(chr(int(pua_base) + o))
        else:
            out.append(ch)
    return "".join(out)


def _build_specimen_text(pua_base: int) -> str:
    examples = [
        ("Upper", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        ("Lower", "abcdefghijklmnopqrstuvwxyz"),
        ("Digits", "0123456789"),
        ("Punct", "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"),
        ("Pangram", "The quick brown fox jumps over the lazy dog."),
        ("Mixed", "Sphinx of black quartz, judge my vow. 0123456789"),
        ("All ASCII", "".join(chr(i) for i in range(0x20, 0x7F))),
    ]

    lines: list[str] = []
    lines.append("terminal Mixed — specimen (English normal / Ender PUA)")
    lines.append("PUA mapping: U+E000 + ASCII codepoint (0x20..0x7E)")
    lines.append("")

    for name, s in examples:
        lines.append(f"{name}:")
        lines.append(f"  EN  {s}")
        lines.append(f"  END {_to_pua_variant(s, pua_base=pua_base)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _open_output_image(out_path: Path) -> None:
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["open", "-a", "Preview", str(out_path)])
        except Exception:
            subprocess.Popen(["open", str(out_path)])
    elif os.name == "nt":
        os.startfile(str(out_path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(out_path)])


def _import_pyglet():
    try:
        import pyglet
        from pyglet import gl
    except Exception as e:
        raise SystemExit(f"Requires pyglet (pip install pyglet). Import error: {e}") from e
    return pyglet, gl


def _register_font_file_if_present(pyglet: object, font_path: Path) -> None:
    if font_path.is_file():
        try:
            pyglet.font.add_file(str(font_path))
        except Exception:
            pass


def _ensure_font_available(pyglet: object, *, font_name: str, font_path: Path) -> None:
    try:
        if not pyglet.font.have_font(font_name):
            raise SystemExit(f"Font '{font_name}' not available. Tried: {font_path}")
    except Exception as e:
        raise SystemExit(f"Font check failed: {e}") from e


def _create_specimen_window(pyglet: object, *, width: int, height: int):
    # On macOS (Quartz), reading the default framebuffer from an *invisible*
    # window often returns black. Easiest fix: create a real window, shove it
    # off-screen, render one frame, capture, then quit.
    window = pyglet.window.Window(width=width, height=height, caption="term_mixed specimen", visible=True)
    try:
        window.set_location(-10000, -10000)
    except Exception:
        pass
    return window


def _capture_window_to_path(pyglet: object, *, window: object, out_path: Path) -> None:
    try:
        window.switch_to()
    except Exception:
        pass
    try:
        window.dispatch_event("on_draw")
        window.flip()
    except Exception:
        pass
    buf = pyglet.image.get_buffer_manager().get_color_buffer()
    buf.save(str(out_path))


def main() -> None:
    args = _build_arg_parser().parse_args()
    pyglet, gl = _import_pyglet()

    font_path = args.font.expanduser().resolve()
    _register_font_file_if_present(pyglet, font_path)

    font_name = "terminal Mixed"
    _ensure_font_available(pyglet, font_name=font_name, font_path=font_path)

    text = _build_specimen_text(args.pua_base)

    pyglet.options["debug_gl"] = False
    window = _create_specimen_window(pyglet, width=args.width, height=args.height)

    batch = pyglet.graphics.Batch()
    label = pyglet.text.Label(
        text,
        x=24,
        y=window.height - 24,
        anchor_x="left",
        anchor_y="top",
        font_name=font_name,
        font_size=args.size,
        color=(255, 210, 255, 255),
        multiline=True,
        width=max(1, window.width - 48),
        batch=batch,
    )

    out_path = args.out.expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    @window.event
    def on_draw() -> None:
        gl.glClearColor(0.03, 0.02, 0.04, 1.0)
        window.clear()
        batch.draw()

    def _capture(_: float) -> None:
        _capture_window_to_path(pyglet, window=window, out_path=out_path)
        pyglet.app.exit()

    pyglet.clock.schedule_once(_capture, 0.25)
    pyglet.app.run()

    if args.open:
        _open_output_image(out_path)

    print(out_path)


if __name__ == "__main__":
    main()
