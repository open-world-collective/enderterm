from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Protocol

from enderterm.params import DEFAULT_PARAM_HELP, ParamDef, ParamStore


class TerminalThemeLike(Protocol):
    bg: tuple[int, int, int, int]
    fg: tuple[int, int, int, int]
    muted: tuple[int, int, int, int]
    sel_bg: tuple[int, int, int, int]
    sel_fg: tuple[int, int, int, int]
    box_fg: tuple[int, int, int, int]
    accent: tuple[int, int, int, int]


def create_term_param_window(
    *,
    pyglet: object,
    store: ParamStore,
    font_name: str | None,
    is_rezzing: Callable[[], bool],
    on_closed: Callable[[], None],
    theme_from_store: Callable[[object], TerminalThemeLike],
) -> object:
    # NOTE: Keep pyglet import-time effects out of this module. The caller passes
    # the pyglet module object when constructing the window.

    class TermParamWindow(pyglet.window.Window):
        def __init__(
            self,
            *,
            store: ParamStore,
            font_name: str | None,
            is_rezzing: Callable[[], bool],
            on_closed: Callable[[], None],
        ) -> None:
            from enderterm.termui import TerminalFont, TerminalRenderer, TerminalSurface

            self._store = store
            self._font_name = font_name
            self._is_rezzing = is_rezzing
            self._on_closed = on_closed

            # Model state (Bubble Tea-ish).
            self._filter_active = False
            self._filter_query = ""
            self._all_defs = sorted(store.defs(), key=lambda d: d.key)
            self._all_keys = [d.key for d in self._all_defs]
            self._filtered_keys: list[str] = list(self._all_keys)
            self._selected = 0
            self._scroll_entry = 0  # top entry index (each entry uses 2 rows)
            self._drag_key: str | None = None

            self._renderer = TerminalRenderer()
            self._term_font: TerminalFont | None = None
            self._surface: TerminalSurface | None = None
            self._ui_scale_last = -1.0
            self._ratio_last = -1.0

            desired_vsync = True
            try:
                desired_vsync = bool(store.get_int("render.vsync"))
            except Exception:
                desired_vsync = True
            super().__init__(width=760, height=760, resizable=True, caption="EnderTerm: kValue", vsync=desired_vsync)
            self._sync_font(force=True)
            self._apply_filter(reset_scroll=True)

        def _sync_font(self, *, force: bool) -> None:
            from enderterm.termui import MinecraftAsciiBitmapFont, TerminalFont, TerminalSurface, _default_minecraft_ascii_png

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
                return
            self._ui_scale_last = float(ui_scale)
            self._ratio_last = float(ratio)

            # Use ui.font.scale to keep the app's typography consistent.
            # Multiply by the pixel ratio so our grid is pixel-perfect on
            # retina displays (we render in device pixels).
            font_size_px = max(6, int(round(14.0 * ui_scale * ratio)))
            ascii_png = _default_minecraft_ascii_png()
            if ascii_png is not None:
                self._term_font = MinecraftAsciiBitmapFont(atlas_path=ascii_png, cell_px=font_size_px)
            else:
                self._term_font = TerminalFont(font_name=self._font_name, font_size_px=font_size_px)

            # Ensure the surface exists (size adjusted at draw-time).
            if self._surface is None:
                self._surface = TerminalSurface(1, 1, default_fg=(18, 14, 22, 255), default_bg=(232, 229, 235, 255))

        def _apply_filter(self, *, reset_scroll: bool) -> None:
            q = self._filter_query.strip().lower()
            if not q:
                self._filtered_keys = list(self._all_keys)
            else:
                self._filtered_keys = [k for k in self._all_keys if q in k.lower()]
            if reset_scroll:
                self._scroll_entry = 0
            if self._selected >= len(self._filtered_keys):
                self._selected = max(0, len(self._filtered_keys) - 1)

        def _active_key(self) -> str | None:
            if not self._filtered_keys:
                return None
            i = int(self._selected)
            if i < 0 or i >= len(self._filtered_keys):
                return None
            return self._filtered_keys[i]

        def _move_selection(self, delta: int) -> None:
            if not self._filtered_keys:
                self._selected = 0
                self._scroll_entry = 0
                return
            self._selected = max(0, min(len(self._filtered_keys) - 1, int(self._selected) + int(delta)))

        def _slider_step(self, d: ParamDef, modifiers: int) -> float:
            span = float(d.max_value - d.min_value)
            if d.is_int:
                step = 1.0
            else:
                step = span / 200.0 if span > 1e-9 else 0.01
            if modifiers & pyglet.window.key.MOD_SHIFT:
                step *= 10.0
            # ⌥ is usually MOD_OPTION on macOS; keep best-effort.
            if modifiers & getattr(pyglet.window.key, "MOD_OPTION", 0):
                step *= 0.1
            return float(step)

        def _adjust_selected(self, delta: float, modifiers: int) -> None:
            key = self._active_key()
            if not key:
                return
            d = self._store.def_for_key(key)
            if d is None:
                return
            step = self._slider_step(d, modifiers)
            self._store.set(key, float(self._store.get(key)) + float(delta) * float(step))

        def _mouse_cell(self, x: int, y: int) -> tuple[int, int] | None:
            if self._term_font is None:
                return None
            try:
                ratio = float(self.get_pixel_ratio())
            except Exception:
                ratio = 1.0
            if ratio <= 0.0:
                ratio = 1.0
            try:
                vp_w_px, vp_h_px = self.get_viewport_size()
            except Exception:
                vp_w_px, vp_h_px = (int(self.width), int(self.height))
            px = int(round(float(x) * ratio))
            py = int(round(float(y) * ratio))
            cell_w = max(1, int(self._term_font.cell_w))
            cell_h = max(1, int(self._term_font.cell_h))
            if cell_w <= 0 or cell_h <= 0:
                return None
            col = int(px // cell_w)
            row_from_bottom = int(py // cell_h)
            rows = max(1, int(vp_h_px // cell_h))
            row = int(rows - 1 - row_from_bottom)
            return (col, row)

        def on_mouse_press(self, x: int, y: int, button: int, modifiers: int) -> None:
            if button != pyglet.window.mouse.LEFT:
                return
            cell = self._mouse_cell(x, y)
            if cell is None:
                return
            col, row = cell
            if self._surface is None:
                return
            cols, rows = int(self._surface.cols), int(self._surface.rows)
            if col < 0 or row < 0 or col >= cols or row >= rows:
                return

            # Layout (must match draw).
            header_h = 1
            help_h = max(8, rows // 4)
            list_box_y = header_h
            list_box_h = max(2, rows - header_h - help_h)
            help_box_y = rows - help_h
            if row < header_h:
                return
            if help_box_y <= row:
                return
            if row < list_box_y or row >= list_box_y + list_box_h:
                return

            inner_x = 1
            inner_y = list_box_y + 1
            inner_w = max(0, cols - 2)
            inner_h = max(0, list_box_h - 2)
            if inner_w <= 1 or inner_h <= 0:
                return
            text_w = max(1, inner_w - 1)  # last col reserved for scrollbar
            if col < inner_x or col >= inner_x + text_w:
                return
            rel_y = int(row - inner_y)
            if rel_y < 0 or rel_y >= inner_h:
                return

            show_search = inner_h >= 3
            entry_h = max(0, int(inner_h) - (1 if show_search else 0))
            visible_entries = max(1, int(entry_h) // 2)
            if show_search and row == inner_y + int(entry_h):
                # Search bar (inside list box).
                if self._filter_active or self._filter_query:
                    total = len(self._all_keys)
                    matches = len(self._filtered_keys)
                    right = f"{matches}/{total}"
                    cancel = "[X]"
                    tail = f"{cancel} {right}" if right else cancel
                    if len(tail) >= text_w:
                        tail = tail[-text_w:]
                    tail_start = int(inner_x + max(0, text_w - len(tail)))
                    cancel_idx = tail.find(cancel)
                    if cancel_idx >= 0:
                        cancel_start = int(tail_start + cancel_idx)
                        cancel_end = int(cancel_start + len(cancel) - 1)
                    else:
                        cancel_start = -1
                        cancel_end = -1
                    if cancel_start <= col <= cancel_end:
                        self._filter_active = False
                        self._filter_query = ""
                        self._apply_filter(reset_scroll=True)
                        return
                self._filter_active = True
                return
            if show_search and rel_y >= entry_h:
                return
            if rel_y >= visible_entries * 2:
                return

            entry_in_view = int(rel_y // 2)
            entry_idx = int(self._scroll_entry) + entry_in_view
            if 0 <= entry_idx < len(self._filtered_keys):
                self._selected = entry_idx
                # If clicking on the slider row, set value based on x.
                if (rel_y % 2) == 1:
                    key = self._active_key()
                    if key:
                        d = self._store.def_for_key(key)
                        if d is not None:
                            value_w = 12
                            slider_w = max(8, text_w - value_w - 1)
                            sx0 = inner_x
                            if col < sx0 + slider_w:
                                t = float(col - sx0) / float(max(1, slider_w - 1))
                                t = max(0.0, min(1.0, t))
                                self._store.set(key, float(d.min_value) + t * float(d.max_value - d.min_value))
                                self._drag_key = key

        def on_mouse_drag(self, x: int, y: int, dx: int, dy: int, buttons: int, modifiers: int) -> None:
            if not (buttons & pyglet.window.mouse.LEFT):
                return
            if not self._drag_key:
                return
            key = self._drag_key
            d = self._store.def_for_key(key)
            if d is None:
                return
            cell = self._mouse_cell(x, y)
            if cell is None or self._surface is None:
                return
            col, row = cell
            cols, rows = int(self._surface.cols), int(self._surface.rows)
            header_h = 1
            help_h = max(8, rows // 4)
            list_box_y = header_h
            list_box_h = max(2, rows - header_h - help_h)
            inner_x = 1
            inner_w = max(0, cols - 2)
            text_w = max(1, inner_w - 1)
            value_w = 12
            slider_w = max(8, text_w - value_w - 1)
            sx0 = inner_x
            if col < sx0:
                col = sx0
            if col > sx0 + slider_w - 1:
                col = sx0 + slider_w - 1
            t = float(col - sx0) / float(max(1, slider_w - 1))
            t = max(0.0, min(1.0, t))
            self._store.set(key, float(d.min_value) + t * float(d.max_value - d.min_value))

        def on_mouse_release(self, x: int, y: int, button: int, modifiers: int) -> None:
            if button == pyglet.window.mouse.LEFT:
                self._drag_key = None

        def on_mouse_scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
            if not self._filtered_keys:
                return
            # Scroll by entries (2-row items).
            self._scroll_entry = int(self._scroll_entry) - int(scroll_y)
            if self._scroll_entry < 0:
                self._scroll_entry = 0

        def on_key_press(self, symbol: int, modifiers: int) -> None:
            if symbol == pyglet.window.key.K and (not self._filter_active):
                self.close()
                return
            if symbol == pyglet.window.key.ESCAPE:
                if self._filter_active or self._filter_query:
                    self._filter_active = False
                    self._filter_query = ""
                    self._apply_filter(reset_scroll=True)
                    return
                self.close()
                return
            if symbol == pyglet.window.key.SLASH and not (modifiers & pyglet.window.key.MOD_SHIFT):
                self._filter_active = True
                return
            if symbol == pyglet.window.key.BACKSPACE and self._filter_active:
                if self._filter_query:
                    self._filter_query = self._filter_query[:-1]
                    self._apply_filter(reset_scroll=True)
                else:
                    self._filter_active = False
                return
            if symbol == pyglet.window.key.UP:
                self._move_selection(-1)
                return
            if symbol == pyglet.window.key.DOWN:
                self._move_selection(1)
                return
            if symbol == pyglet.window.key.PAGEUP:
                self._move_selection(-8)
                return
            if symbol == pyglet.window.key.PAGEDOWN:
                self._move_selection(8)
                return
            if symbol == pyglet.window.key.LEFT and (not self._filter_active):
                self._adjust_selected(-1.0, modifiers)
                return
            if symbol == pyglet.window.key.RIGHT and (not self._filter_active):
                self._adjust_selected(1.0, modifiers)
                return
            if symbol in {pyglet.window.key.ENTER, pyglet.window.key.RETURN}:
                self._filter_active = False
                return

        def on_text(self, text: str) -> None:
            if not self._filter_active:
                return
            if not isinstance(text, str) or not text:
                return
            changed = False
            for ch in text:
                if ch in {"\r", "\n"}:
                    continue
                if ord(ch) < 32:
                    continue
                if ch == "/":
                    continue
                self._filter_query += ch
                changed = True
            if changed:
                self._apply_filter(reset_scroll=True)

        def on_resize(self, width: int, height: int) -> None:
            self._sync_font(force=False)

        def on_draw(self) -> None:
            from enderterm.termui import TerminalSurface

            self._sync_font(force=False)
            if self._term_font is None or self._renderer is None:
                return
            try:
                vp_w_px, vp_h_px = self.get_viewport_size()
            except Exception:
                vp_w_px, vp_h_px = (int(self.width), int(self.height))

            cell_w = max(1, int(self._term_font.cell_w))
            cell_h = max(1, int(self._term_font.cell_h))
            cols = max(1, int(vp_w_px // cell_w))
            rows = max(1, int(vp_h_px // cell_h))

            if self._surface is None:
                self._surface = TerminalSurface(cols, rows, default_fg=(235, 220, 245, 255), default_bg=(8, 8, 10, 255))
            else:
                self._surface.resize(cols, rows)

            theme = theme_from_store(self._store)
            bg = theme.bg
            fg = theme.fg
            muted = theme.muted
            sel_bg = theme.sel_bg
            sel_fg = theme.sel_fg
            box_fg = theme.box_fg
            accent = theme.accent

            self._surface.default_bg = bg
            self._surface.default_fg = fg
            self._surface.clear()

            # Header.
            title = "kValue"
            hint = "K close   / search   ←/→ adjust   Shift coarse   ⌥ fine"
            self._surface.put(0, 0, title, fg=fg, bg=bg)
            if cols > len(title) + 1:
                self._surface.put(min(cols - 1, len(title) + 2), 0, hint[: max(0, cols - len(title) - 3)], fg=muted, bg=bg)

            # Layout boxes.
            header_h = 1
            help_h = max(8, rows // 4)
            list_box_y = header_h
            list_box_h = max(2, rows - header_h - help_h)
            help_box_y = rows - help_h

            self._surface.draw_box(0, list_box_y, cols, list_box_h, fg=box_fg, bg=bg, title="kValues")
            self._surface.draw_box(0, help_box_y, cols, help_h, fg=box_fg, bg=bg, title="Help")

            # List contents.
            inner_x = 1
            inner_y = list_box_y + 1
            inner_w = max(0, cols - 2)
            inner_h = max(0, list_box_h - 2)
            text_w = max(1, inner_w - 1)
            show_search = inner_h >= 3
            entry_h = max(0, int(inner_h) - (1 if show_search else 0))
            visible_entries = max(1, entry_h // 2)

            if self._selected < self._scroll_entry:
                self._scroll_entry = int(self._selected)
            if self._selected >= self._scroll_entry + visible_entries:
                self._scroll_entry = int(self._selected) - visible_entries + 1
            max_scroll = max(0, len(self._filtered_keys) - visible_entries)
            if self._scroll_entry > max_scroll:
                self._scroll_entry = max_scroll
            if self._scroll_entry < 0:
                self._scroll_entry = 0

            for i in range(visible_entries):
                entry_idx = int(self._scroll_entry) + int(i)
                if entry_idx >= len(self._filtered_keys):
                    break
                key = self._filtered_keys[entry_idx]
                y0 = inner_y + i * 2
                y1 = y0 + 1
                selected = entry_idx == int(self._selected)
                if selected:
                    self._surface.fill_rect(inner_x, y0, text_w, 2, bg=sel_bg, fg=sel_fg)
                # Row 1: key
                self._surface.put(inner_x, y0, key[:text_w], fg=sel_fg if selected else muted, bg=sel_bg if selected else bg)
                # Row 2: slider + value
                d = self._store.def_for_key(key)
                if d is None:
                    continue
                try:
                    val = float(self._store.get(key))
                except Exception:
                    val = float(d.default)
                try:
                    vstr = str(d.fmt).format(val)
                except Exception:
                    vstr = f"{val:.3g}"
                value_w = 12
                if len(vstr) > value_w:
                    vstr = vstr[:value_w]
                slider_w = max(8, text_w - value_w - 1)
                span = float(d.max_value - d.min_value)
                t = 0.0 if span <= 1e-9 else (val - float(d.min_value)) / span
                t = max(0.0, min(1.0, t))
                knob = int(round(t * float(max(1, slider_w - 1))))
                bar_chars: list[str] = []
                for j in range(slider_w):
                    if j < knob:
                        bar_chars.append("█")
                    elif j == knob:
                        bar_chars.append("▌")
                    else:
                        bar_chars.append("░")
                bar = "".join(bar_chars)
                row_bg = sel_bg if selected else bg
                self._surface.put(inner_x, y1, bar[:slider_w], fg=muted, bg=row_bg)
                if 0 <= knob < slider_w:
                    self._surface.put(inner_x + knob, y1, "▌", fg=accent, bg=row_bg)
                if len(vstr) < value_w:
                    vstr = " " * (value_w - len(vstr)) + vstr
                self._surface.put(inner_x + slider_w + 1, y1, vstr[:value_w], fg=sel_fg if selected else fg, bg=row_bg)

            # Search bar (inside list box, bottom row).
            if show_search:
                total = len(self._all_keys)
                matches = len(self._filtered_keys)
                cursor_on = bool(self._filter_active and (int(time.monotonic() * 2.2) % 2 == 0))
                cursor = "▌" if cursor_on else ""
                q = self._filter_query
                left = "/" + q if (q or self._filter_active) else "/"
                right = f"{matches}/{total}"
                cancel = "[X]" if (self._filter_active or q) else ""
                tail = f"{cancel} {right}" if cancel else right
                if len(tail) >= text_w:
                    tail = tail[-text_w:]
                left_w = max(0, int(text_w) - len(tail))
                show_left = left
                if len(show_left) > left_w:
                    show_left = show_left[: max(0, left_w - 1)] + "…"
                bar_y = inner_y + int(entry_h)
                self._surface.fill_rect(inner_x, bar_y, text_w, 1, bg=bg, fg=fg)
                self._surface.put(inner_x, bar_y, show_left[:left_w], fg=fg, bg=bg)
                if cursor and len(show_left) < left_w:
                    cx = inner_x + len(show_left)
                    if inner_x <= cx < inner_x + text_w:
                        self._surface.put(cx, bar_y, cursor, fg=accent, bg=bg)
                if tail:
                    self._surface.put(inner_x + max(0, text_w - len(tail)), bar_y, tail, fg=muted, bg=bg)

            # Scrollbar (inside list box).
            sb_x = inner_x + text_w
            if inner_h > 0 and text_w > 0 and sb_x < cols - 1:
                for yy in range(inner_h):
                    self._surface.put(sb_x, inner_y + yy, "░", fg=box_fg, bg=bg)
                total_e = max(1, len(self._filtered_keys))
                thumb_h = max(1, int(round(float(visible_entries) / float(total_e) * float(inner_h))))
                if thumb_h > inner_h:
                    thumb_h = inner_h
                thumb_y = 0
                if max_scroll > 0:
                    thumb_y = int(round(float(self._scroll_entry) / float(max_scroll) * float(max(0, inner_h - thumb_h))))
                for yy in range(thumb_h):
                    self._surface.put(sb_x, inner_y + thumb_y + yy, "█", fg=accent, bg=bg)

            # Help panel.
            key = self._active_key()
            help_lines: list[str] = []
            if key:
                d = self._store.def_for_key(key)
                if d is not None:
                    help_lines.append(f"{key}")
                    help_src = ""
                    if getattr(d, "help", ""):
                        help_src = str(getattr(d, "help", ""))
                    elif key in DEFAULT_PARAM_HELP:
                        help_src = DEFAULT_PARAM_HELP[key]
                    else:
                        help_src = str(getattr(d, "label", ""))
                    for ln in str(help_src).splitlines():
                        help_lines.append(ln)
            else:
                help_lines = ["(no kValues match this filter)"]

            help_inner_x = 1
            help_inner_y = help_box_y + 1
            help_inner_w = max(0, cols - 2)
            help_inner_h = max(0, help_h - 2)

            def _wrap(text: str, width: int) -> list[str]:
                if width <= 1:
                    return [text[:width]]
                out: list[str] = []
                for raw in text.splitlines():
                    s = raw.rstrip()
                    while len(s) > width:
                        cut = s.rfind(" ", 0, width + 1)
                        if cut <= 0:
                            cut = width
                        out.append(s[:cut].rstrip())
                        s = s[cut:].lstrip()
                    out.append(s)
                return out

            wrapped: list[str] = []
            for ln in help_lines:
                wrapped.extend(_wrap(ln, help_inner_w))
            for i, ln in enumerate(wrapped[:help_inner_h]):
                self._surface.put(help_inner_x, help_inner_y + i, ln[:help_inner_w], fg=fg, bg=bg)

            # Save/tick after rendering to keep IO off the hot path.
            self._store.tick()

            rez_active = False
            try:
                rez_active = bool(self._is_rezzing())
            except Exception:
                rez_active = False

            self._renderer.draw(
                surface=self._surface,
                font=self._term_font,
                vp_w_px=int(vp_w_px),
                vp_h_px=int(vp_h_px),
                param_store=self._store,
                rez_active=rez_active,
            )

        def on_close(self) -> None:
            try:
                self._store.save()
            finally:
                try:
                    self._on_closed()
                finally:
                    super().on_close()

    return TermParamWindow(store=store, font_name=font_name, is_rezzing=is_rezzing, on_closed=on_closed)
