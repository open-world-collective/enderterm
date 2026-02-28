from __future__ import annotations

import math
from collections.abc import Callable
from typing import Protocol

from enderterm.params import ParamStore


class TerminalThemeLike(Protocol):
    bg: tuple[int, int, int, int]
    fg: tuple[int, int, int, int]
    muted: tuple[int, int, int, int]
    sel_bg: tuple[int, int, int, int]
    sel_fg: tuple[int, int, int, int]
    box_fg: tuple[int, int, int, int]
    accent: tuple[int, int, int, int]


def _wrap_debug_line(line: str, width: int) -> list[str]:
    if width <= 1:
        return [line[:width]]
    out: list[str] = []
    s = line.rstrip()
    if not s:
        return [""]
    while len(s) > width:
        cut = s.rfind(" ", 0, width + 1)
        if cut <= 0:
            cut = width
        out.append(s[:cut].rstrip())
        s = s[cut:].lstrip()
    out.append(s)
    return out


def _wrap_debug_lines(raw_lines: list[str], *, width: int) -> list[str]:
    wrapped: list[str] = []
    for line in raw_lines:
        wrapped.extend(_wrap_debug_line(str(line), width))
    if not wrapped:
        return ["(no debug info)"]
    return wrapped


def create_debug_window(
    *,
    pyglet: object,
    get_text: Callable[[], str],
    store: ParamStore,
    font_name: str | None,
    on_closed: Callable[[], None],
    theme_from_store: Callable[[object], TerminalThemeLike],
) -> object:
    # NOTE: Keep pyglet import-time effects out of this module. The caller passes
    # the pyglet module object when constructing the window.

    class DebugWindow(pyglet.window.Window):
        def __init__(
            self,
            *,
            get_text: Callable[[], str],
            store: ParamStore,
            font_name: str | None,
            on_closed: Callable[[], None],
        ) -> None:
            self._ready = False
            self._get_text = get_text
            self._store = store
            self._font_name = font_name
            self._on_closed = on_closed
            self._close_request_path = ""

            from enderterm.termui import TerminalRenderer, TerminalSurface

            self._renderer = TerminalRenderer()
            self._term_font: object | None = None
            self._surface = TerminalSurface(1, 1, default_fg=(18, 18, 18, 255), default_bg=(232, 232, 232, 255))
            self._ui_scale_last = -1.0
            self._ratio_last = -1.0
            self._tick_rate_s = 1.0 / 20.0
            self._tick_hz = 20

            desired_vsync = True
            try:
                desired_vsync = bool(self._store.get_int("render.vsync"))
            except Exception:
                desired_vsync = True
            super().__init__(width=760, height=520, resizable=True, caption="EnderTerm: debug", vsync=desired_vsync)
            self._ready = True
            pyglet.clock.schedule_interval(self._on_tick, self._tick_rate_s)

        def _refresh_tick_rate(self) -> None:
            # Keep debug redraw cadence aligned with render cap when capped,
            # so debug-window refreshes do not drive extra main-window swaps.
            try:
                cap_hz = max(0, int(self._store.get_int("render.frame_cap_hz")))
            except Exception:
                cap_hz = 0
            desired_hz = 20 if cap_hz <= 0 else max(1, min(20, int(cap_hz)))
            if int(desired_hz) == int(self._tick_hz):
                return
            self._tick_hz = int(desired_hz)
            self._tick_rate_s = 1.0 / float(max(1, int(self._tick_hz)))
            try:
                pyglet.clock.unschedule(self._on_tick)
            except Exception:
                pass
            pyglet.clock.schedule_interval(self._on_tick, self._tick_rate_s)

        def request_close(self, *, trigger: str = "api") -> str:
            trig = str(trigger or "api").strip().lower() or "api"
            ns_window = getattr(self, "_nswindow", None)
            perform_close = getattr(ns_window, "performClose_", None)
            if callable(perform_close):
                try:
                    self._close_request_path = f"{trig}:native_perform_close"
                    perform_close(None)
                    return str(self._close_request_path)
                except Exception:
                    pass
            try:
                self._close_request_path = f"{trig}:window_close"
                self.close()
                return str(self._close_request_path)
            except Exception:
                self._close_request_path = f"{trig}:close_failed"
                return str(self._close_request_path)

        def _sync_termui(self, *, force: bool) -> object:
            from enderterm.termui import MinecraftAsciiBitmapFont, TerminalFont, _default_minecraft_ascii_png

            try:
                ui_scale = float(self._store.get("ui.font.scale") or 1.0)
            except Exception:
                ui_scale = 1.0
            if not math.isfinite(ui_scale):
                ui_scale = 1.0
            ui_scale = max(0.5, min(3.0, float(ui_scale)))

            try:
                ratio = float(self.get_pixel_ratio())
            except Exception:
                ratio = 1.0
            if ratio <= 0.0 or not math.isfinite(ratio):
                ratio = 1.0

            if (not force) and abs(ui_scale - float(self._ui_scale_last)) < 1e-6 and abs(ratio - float(self._ratio_last)) < 1e-6:
                if self._term_font is not None:
                    return self._term_font

            self._ui_scale_last = float(ui_scale)
            self._ratio_last = float(ratio)

            font_size_px = max(6, int(round(14.0 * ui_scale * ratio)))
            ascii_png = _default_minecraft_ascii_png()
            if ascii_png is not None:
                self._term_font = MinecraftAsciiBitmapFont(atlas_path=ascii_png, cell_px=font_size_px)
            else:
                self._term_font = TerminalFont(font_name=self._font_name, font_size_px=font_size_px)
            return self._term_font

        def _on_tick(self, _dt: float) -> None:
            if not self._ready:
                return
            self._refresh_tick_rate()
            self.invalid = True

        def _close_trigger_for_key(self, symbol: int, modifiers: int) -> str | None:
            key = pyglet.window.key
            trigger = {
                key.ESCAPE: "key_escape",
                key.D: "key_d",
                key.Q: "key_q",
            }.get(symbol)
            if trigger is not None:
                return str(trigger)
            cmd_mod = getattr(key, "MOD_COMMAND", 0) | getattr(key, "MOD_ACCEL", 0)
            if symbol == key.W and (modifiers & cmd_mod):
                return "key_cmd_w"
            return None

        def on_key_press(self, symbol: int, modifiers: int) -> None:
            trigger = self._close_trigger_for_key(int(symbol), int(modifiers))
            if trigger is not None:
                self.request_close(trigger=trigger)
                return

        def on_resize(self, width: int, height: int) -> None:
            if not self._ready:
                return
            self.invalid = True

        def on_draw(self) -> None:
            from pyglet import gl as gl_
            from enderterm.termui import TerminalSurface

            term_font = self._sync_termui(force=False)
            vp_w_px, vp_h_px = self.get_viewport_size()
            vp_w_px = max(1, int(vp_w_px))
            vp_h_px = max(1, int(vp_h_px))

            cell_w = max(1, int(getattr(term_font, "cell_w", 8)))
            cell_h = max(1, int(getattr(term_font, "cell_h", 14)))
            cols = max(24, int(vp_w_px // cell_w))
            rows = max(10, int(vp_h_px // cell_h))

            surface = self._surface
            surface.resize(cols, rows)

            theme = theme_from_store(self._store)
            bg = theme.bg
            fg = theme.fg
            muted = theme.muted
            box_fg = theme.box_fg
            accent = theme.accent
            surface.default_bg = bg
            surface.default_fg = muted
            surface.clear()

            title = "Debug (D/Esc to close)"
            surface.draw_box(0, 0, cols, rows, fg=box_fg, bg=bg, title=title)

            text = ""
            try:
                text = str(self._get_text() or "")
            except Exception:
                text = ""
            raw_lines = text.splitlines() if text else []

            inner_x = 1
            inner_y = 1
            inner_w = max(0, cols - 2)
            inner_h = max(0, rows - 2)
            if inner_w <= 0 or inner_h <= 0:
                return

            wrapped = _wrap_debug_lines(raw_lines, width=inner_w)

            for i, ln in enumerate(wrapped[:inner_h]):
                is_special = False
                if "err=" in ln:
                    is_special = True
                if ln.startswith(("selection:", "blocks:", "rez:", "filter:", "jigsaw depth:")):
                    is_special = True

                color = accent if i == 0 else (fg if is_special else muted)
                surface.put(inner_x, inner_y + i, ln[:inner_w], fg=color, bg=bg)

            gl_.glViewport(0, 0, int(vp_w_px), int(vp_h_px))
            self._renderer.draw(
                surface=surface,
                font=term_font,
                vp_w_px=int(vp_w_px),
                vp_h_px=int(vp_h_px),
                param_store=self._store,
                rez_active=False,
            )

        def on_close(self) -> None:
            if not str(getattr(self, "_close_request_path", "") or "").strip():
                self._close_request_path = "native_titlebar_or_system_close"
            try:
                pyglet.clock.unschedule(self._on_tick)
            except Exception:
                pass
            try:
                super().on_close()
            finally:
                try:
                    self._on_closed()
                except Exception:
                    pass

    return DebugWindow(get_text=get_text, store=store, font_name=font_name, on_closed=on_closed)
