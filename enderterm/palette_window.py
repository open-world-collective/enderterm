from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from enderterm.params import ParamStore


class TerminalThemeLike(Protocol):
    bg: tuple[int, int, int, int]
    fg: tuple[int, int, int, int]
    muted: tuple[int, int, int, int]
    sel_bg: tuple[int, int, int, int]
    sel_fg: tuple[int, int, int, int]
    box_fg: tuple[int, int, int, int]
    accent: tuple[int, int, int, int]


def create_palette_window(
    *,
    pyglet: object,
    store: ParamStore,
    font_name: str | None,
    entries: Sequence[object],
    load_tex: Callable[[str], object | None],
    initial_selected_idx: int = 0,
    initial_block_id: str = "",
    on_pick_entry: Callable[[int], None],
    on_select_hotbar_slot: Callable[[int], str | None] | None = None,
    on_closed: Callable[[], None],
    theme_from_store: Callable[[object], TerminalThemeLike],
) -> object:
    # NOTE: Keep pyglet import-time effects out of this module. The caller passes
    # the pyglet module object when constructing the window.

    class PaletteWindow(pyglet.window.Window):
        def __init__(self) -> None:
            from enderterm.termui import (
                TermMouseCapture,
                TermScroll,
                TermScrollbar,
                TerminalRenderer,
                TerminalSurface,
                route_term_scrollbar_drag,
                route_term_scrollbar_press,
                route_term_scrollbar_release,
            )

            self._store = store
            self._font_name = font_name
            self._entries = list(entries)
            self._load_tex = load_tex
            self._on_pick_entry = on_pick_entry
            self._on_select_hotbar_slot = on_select_hotbar_slot
            self._on_closed = on_closed
            self._close_request_path = ""

            self._search_active = False
            self._query = ""
            self._filtered: list[int] = list(range(len(self._entries)))
            self._selected = max(0, min(len(self._entries) - 1, int(initial_selected_idx)))
            self._scroll = TermScroll()
            self._grid_cols_last = 1
            self._grid_rows_last = 1
            self._scrollbar = TermScrollbar()
            self._scrollbar_capture = TermMouseCapture()
            self._scrollbar_context = "palette:list"
            self._scrollbar_target = "scrollbar"
            self._route_term_scrollbar_press = route_term_scrollbar_press
            self._route_term_scrollbar_drag = route_term_scrollbar_drag
            self._route_term_scrollbar_release = route_term_scrollbar_release

            self._renderer = TerminalRenderer()
            self._term_font: object | None = None
            self._surface = TerminalSurface(1, 1, default_fg=(18, 14, 22, 255), default_bg=(232, 229, 235, 255))
            self._ui_scale_last = -1.0
            self._ratio_last = -1.0

            self._tile_cells = 4
            self._icon_cells = 2

            self._apply_filter()
            self._sync_initial_selection(initial_block_id)

            desired_vsync = True
            try:
                desired_vsync = bool(self._store.get_int("render.vsync"))
            except Exception:
                desired_vsync = True
            super().__init__(width=620, height=760, resizable=True, caption="EnderTerm: palette", vsync=desired_vsync)

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

        def _apply_filter(self) -> None:
            q = self._query.strip().lower()
            if not q:
                self._filtered = list(range(len(self._entries)))
            else:
                tokens = [t for t in q.split() if t]
                out: list[int] = []
                for i, e in enumerate(self._entries):
                    label = str(getattr(e, "label", "") or "")
                    bid = str(getattr(e, "block_id", "") or "")
                    hay = f"{label} {bid}".lower()
                    if all(t in hay for t in tokens):
                        out.append(int(i))
                self._filtered = out

            if not self._filtered:
                self._selected = 0
                self._scroll.pos_f = 0.0
                return
            if self._selected < 0 or self._selected >= len(self._entries):
                self._selected = int(self._filtered[0])
            if int(self._selected) not in set(self._filtered):
                self._selected = int(self._filtered[0])
            self._scroll.pos_f = 0.0
            self._scroll.follow_selection = True

        def _search_ui_visible(self) -> bool:
            return bool(self._search_active or self._query)

        def _sync_initial_selection(self, block_id: str) -> None:
            want = str(block_id or "").strip()
            if not want:
                return
            for i, e in enumerate(self._entries):
                if str(getattr(e, "block_id", "") or "") == want:
                    self._selected = int(i)
                    return
            for i, e in enumerate(self._entries):
                if str(getattr(e, "label", "") or "") == want:
                    self._selected = int(i)
                    return

        def _select_block_id_if_visible(self, block_id: str) -> None:
            want = str(block_id or "").strip()
            if not want:
                return
            sel: int | None = None
            for i, e in enumerate(self._entries):
                if str(getattr(e, "block_id", "") or "") == want:
                    sel = int(i)
                    break
            if sel is None:
                return
            if sel not in set(self._filtered):
                return
            self._selected = int(sel)
            self._scroll.follow_selection = True

        def _grid_metrics(self, *, cols: int, rows: int) -> tuple[int, int, int, int, int, int, int]:
            inner_w = max(0, int(cols) - 2)
            inner_h = max(0, int(rows) - 2)
            tile = max(2, int(self._tile_cells))
            icon = max(1, min(int(self._icon_cells), int(tile)))

            # Reserve last column for the scrollbar.
            content_w = max(0, int(inner_w) - 1)

            show_search = bool(self._search_ui_visible() and int(inner_h) >= 2)
            grid_h = max(0, int(inner_h) - (1 if show_search else 0))
            grid_cols = max(1, int(content_w) // int(tile)) if content_w > 0 else 1
            grid_rows = max(1, int(grid_h) // int(tile)) if grid_h > 0 else 1
            total_rows = max(1, (len(self._filtered) + int(grid_cols) - 1) // int(grid_cols)) if self._filtered else 1
            max_scroll = max(0, int(total_rows) - int(grid_rows))
            return (tile, icon, content_w, inner_w, inner_h, grid_cols, max_scroll)

        def _selected_pos(self) -> int | None:
            if not self._filtered:
                return None
            try:
                return int(self._filtered.index(int(self._selected)))
            except Exception:
                return None

        def _ensure_selection_visible(self, *, grid_cols: int, grid_rows: int, max_scroll: int) -> None:
            if not self._filtered:
                self._scroll.pos_f = 0.0
                return
            pos = self._selected_pos()
            if pos is None:
                self._scroll.pos_f = 0.0
                return
            sel_row = int(pos) // max(1, int(grid_cols))
            self._scroll.ensure_visible(int(sel_row), visible=int(grid_rows), max_scroll=int(max_scroll))

        def _move_selection(self, delta: int) -> None:
            if not self._filtered:
                return
            pos = self._selected_pos()
            if pos is None:
                self._selected = int(self._filtered[0])
                return
            pos = max(0, min(len(self._filtered) - 1, int(pos) + int(delta)))
            self._selected = int(self._filtered[int(pos)])
            self._scroll.follow_selection = True

        def _mouse_cell(self, x: int, y: int) -> tuple[int, int] | None:
            if self._term_font is None:
                return None
            try:
                ratio = float(self.get_pixel_ratio())
            except Exception:
                ratio = 1.0
            if ratio <= 0.0 or not math.isfinite(ratio):
                ratio = 1.0
            try:
                vp_w_px, vp_h_px = self.get_viewport_size()
            except Exception:
                vp_w_px, vp_h_px = (int(self.width), int(self.height))

            px = int(round(float(x) * ratio))
            py = int(round(float(y) * ratio))
            cell_w = max(1, int(getattr(self._term_font, "cell_w", 8)))
            cell_h = max(1, int(getattr(self._term_font, "cell_h", 14)))
            col = int(px // cell_w)
            row_from_bottom = int(py // cell_h)
            rows = max(1, int(vp_h_px // cell_h))
            row = int(rows - 1 - row_from_bottom)
            return (col, row)

        def on_key_press(self, symbol: int, modifiers: int) -> None:
            cmd_mod = getattr(pyglet.window.key, "MOD_COMMAND", 0) | getattr(pyglet.window.key, "MOD_ACCEL", 0)
            if symbol == pyglet.window.key.ESCAPE:
                if self._search_ui_visible():
                    self._search_active = False
                    self._query = ""
                    self._apply_filter()
                    return
                self.request_close(trigger="key_escape")
                return
            if symbol in {pyglet.window.key.I, pyglet.window.key.Q}:
                close_key = "key_i" if symbol == pyglet.window.key.I else "key_q"
                self.request_close(trigger=close_key)
                return
            if (modifiers & cmd_mod) and symbol == pyglet.window.key.W:
                self.request_close(trigger="key_cmd_w")
                return

            if self._search_active:
                if symbol in {pyglet.window.key.ENTER, pyglet.window.key.RETURN}:
                    self._search_active = False
                    return
                if symbol == pyglet.window.key.BACKSPACE:
                    if self._query:
                        self._query = self._query[:-1]
                        self._apply_filter()
                    else:
                        self._search_active = False
                    return

            if symbol == pyglet.window.key.SLASH and not (modifiers & pyglet.window.key.MOD_SHIFT):
                self._search_active = True
                return

            if symbol in {pyglet.window.key.ENTER, pyglet.window.key.RETURN}:
                try:
                    self._on_pick_entry(int(self._selected))
                except Exception:
                    pass
                self.request_close(trigger="key_enter")
                return

            grid_cols = int(getattr(self, "_grid_cols_last", 1) or 1)
            grid_rows = int(getattr(self, "_grid_rows_last", 1) or 1)

            if not self._search_active and self._on_select_hotbar_slot is not None and not (modifiers & cmd_mod):
                hotbar_map = {
                    pyglet.window.key._1: 0,
                    pyglet.window.key._2: 1,
                    pyglet.window.key._3: 2,
                    pyglet.window.key._4: 3,
                    pyglet.window.key._5: 4,
                    pyglet.window.key._6: 5,
                    pyglet.window.key._7: 6,
                    pyglet.window.key._8: 7,
                    pyglet.window.key._9: 8,
                    pyglet.window.key._0: 9,
                }
                alt = {
                    getattr(pyglet.window.key, "NUM_1", -1): 0,
                    getattr(pyglet.window.key, "NUM_2", -1): 1,
                    getattr(pyglet.window.key, "NUM_3", -1): 2,
                    getattr(pyglet.window.key, "NUM_4", -1): 3,
                    getattr(pyglet.window.key, "NUM_5", -1): 4,
                    getattr(pyglet.window.key, "NUM_6", -1): 5,
                    getattr(pyglet.window.key, "NUM_7", -1): 6,
                    getattr(pyglet.window.key, "NUM_8", -1): 7,
                    getattr(pyglet.window.key, "NUM_9", -1): 8,
                    getattr(pyglet.window.key, "NUM_0", -1): 9,
                }
                if symbol in hotbar_map:
                    block_id = self._on_select_hotbar_slot(int(hotbar_map[symbol]))
                    if block_id:
                        self._select_block_id_if_visible(str(block_id))
                    return
                if symbol in alt and alt[symbol] >= 0:
                    block_id = self._on_select_hotbar_slot(int(alt[symbol]))
                    if block_id:
                        self._select_block_id_if_visible(str(block_id))
                    return

            if symbol == pyglet.window.key.LEFT:
                self._move_selection(-1)
                return
            if symbol == pyglet.window.key.RIGHT:
                self._move_selection(1)
                return
            if symbol == pyglet.window.key.UP:
                self._move_selection(-grid_cols)
                return
            if symbol == pyglet.window.key.DOWN:
                self._move_selection(grid_cols)
                return
            if symbol == pyglet.window.key.PAGEUP:
                self._move_selection(-grid_cols * grid_rows)
                return
            if symbol == pyglet.window.key.PAGEDOWN:
                self._move_selection(grid_cols * grid_rows)
                return
            if symbol == pyglet.window.key.HOME:
                if self._filtered:
                    self._selected = int(self._filtered[0])
                    self._scroll.follow_selection = True
                return
            if symbol == pyglet.window.key.END:
                if self._filtered:
                    self._selected = int(self._filtered[-1])
                    self._scroll.follow_selection = True
                return

        def on_text(self, text: str) -> None:
            if not self._search_active:
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
                self._query += ch
                changed = True
            if changed:
                self._apply_filter()

        def on_mouse_scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
            if not self._filtered:
                return
            cols = int(getattr(self._surface, "cols", 1))
            rows = int(getattr(self._surface, "rows", 1))
            _tile, _icon, _content_w, _inner_w, inner_h, grid_cols, max_scroll = self._grid_metrics(cols=cols, rows=rows)
            # Scrollbar spans the whole inner box; scroll is in tile rows.
            grid_h = max(0, int(inner_h) - 1)
            grid_rows = max(1, int(grid_h) // max(1, int(_tile))) if grid_h > 0 else 1
            self._scroll.scroll_wheel(float(scroll_y), max_scroll=int(max_scroll))
            self._grid_cols_last = max(1, int(grid_cols))
            self._grid_rows_last = max(1, int(grid_rows))

        def on_mouse_press(self, x: int, y: int, button: int, modifiers: int) -> None:
            if button != pyglet.window.mouse.LEFT:
                return
            cell = self._mouse_cell(x, y)
            if cell is None:
                return
            col, row = cell

            cols = int(getattr(self._surface, "cols", 1))
            rows = int(getattr(self._surface, "rows", 1))
            tile, _icon, content_w, _inner_w, inner_h, grid_cols, max_scroll = self._grid_metrics(cols=cols, rows=rows)
            show_search = bool(self._search_ui_visible() and int(inner_h) >= 2)
            grid_h = max(0, int(inner_h) - (1 if show_search else 0))
            grid_rows = max(1, int(grid_h) // max(1, int(tile))) if grid_h > 0 else 1
            scroll_row = int(self._scroll.clamp(int(max_scroll)))
            total_rows = max(1, (len(self._filtered) + int(grid_cols) - 1) // int(grid_cols)) if self._filtered else 1
            inner_x = 1
            inner_y = 1
            sb_x = int(inner_x) + int(content_w)
            self._scrollbar.update(
                track_top=int(inner_y),
                track_rows=int(inner_h),
                visible_rows=int(grid_rows),
                total_rows=int(total_rows),
                scroll_top=int(scroll_row),
            )

            if (
                int(col) == int(sb_x)
                and int(inner_h) > 0
                and int(inner_y) <= int(row) < int(inner_y + inner_h)
            ):
                route = self._route_term_scrollbar_press(
                    capture=self._scrollbar_capture,
                    context_id=str(self._scrollbar_context),
                    target_id=str(self._scrollbar_target),
                    scrollbar=self._scrollbar,
                    row=int(row),
                    current_scroll=float(self._scroll.pos_f),
                    consume_thumb_press=True,
                )
                if route.new_scroll is not None:
                    self._scroll.follow_selection = False
                    self._scroll.pos_f = float(route.new_scroll)
                    self._scroll.clamp(int(max_scroll))
                if route.consumed:
                    return

            hs = self._surface.hotspot_at(int(col), int(row))
            if hs is None:
                return
            if hs.kind == "palette.entry":
                try:
                    entry_idx = int(hs.payload) if hs.payload is not None else -1
                except Exception:
                    entry_idx = -1
                if 0 <= entry_idx < len(self._entries):
                    self._selected = int(entry_idx)
                    try:
                        self._on_pick_entry(int(entry_idx))
                    except Exception:
                        pass
                return
            if hs.kind == "palette.clear":
                self._search_active = False
                self._query = ""
                self._apply_filter()
                return

        def on_mouse_drag(self, x: int, y: int, dx: int, dy: int, buttons: int, modifiers: int) -> None:
            if (not bool(getattr(self._scrollbar, "drag_active", False))) and (
                not bool(getattr(self._scrollbar_capture, "active", False))
            ):
                return

            cols = int(getattr(self._surface, "cols", 1))
            rows = int(getattr(self._surface, "rows", 1))
            tile, _icon, content_w, _inner_w, inner_h, grid_cols, max_scroll = self._grid_metrics(cols=cols, rows=rows)
            show_search = bool(self._search_ui_visible() and int(inner_h) >= 2)
            grid_h = max(0, int(inner_h) - (1 if show_search else 0))
            grid_rows = max(1, int(grid_h) // max(1, int(tile))) if grid_h > 0 else 1
            scroll_row = int(self._scroll.clamp(int(max_scroll)))
            total_rows = max(1, (len(self._filtered) + int(grid_cols) - 1) // int(grid_cols)) if self._filtered else 1
            self._scrollbar.update(
                track_top=1,
                track_rows=int(inner_h),
                visible_rows=int(grid_rows),
                total_rows=int(total_rows),
                scroll_top=int(scroll_row),
            )

            cell = self._mouse_cell(x, y)
            row = int(cell[1]) if cell is not None else int(getattr(self._scrollbar, "thumb_top", 0))
            route = self._route_term_scrollbar_drag(
                capture=self._scrollbar_capture,
                context_id=str(self._scrollbar_context),
                target_id=str(self._scrollbar_target),
                scrollbar=self._scrollbar,
                row=int(row),
                left_button_down=bool(buttons & pyglet.window.mouse.LEFT),
            )
            if route.new_scroll is not None:
                self._scroll.follow_selection = False
                self._scroll.pos_f = float(route.new_scroll)
                self._scroll.clamp(int(max_scroll))
            if route.consumed:
                return

        def on_mouse_release(self, x: int, y: int, button: int, modifiers: int) -> None:
            if button != pyglet.window.mouse.LEFT:
                return
            self._route_term_scrollbar_release(
                capture=self._scrollbar_capture,
                context_id=str(self._scrollbar_context),
                target_id=str(self._scrollbar_target),
                scrollbar=self._scrollbar,
            )

        def on_draw(self) -> None:
            from pyglet import gl as gl_

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
            self._term_font = term_font

            theme = theme_from_store(self._store)
            bg = theme.bg
            fg = theme.fg
            muted = theme.muted
            box_fg = theme.box_fg
            sel_bg = theme.sel_bg
            sel_fg = theme.sel_fg
            accent = theme.accent

            surface.default_bg = bg
            surface.default_fg = fg
            surface.clear()

            title = "Palette (I/Esc to close)"
            surface.draw_box(0, 0, cols, rows, fg=box_fg, bg=bg, title=title)

            inner_x = 1
            inner_y = 1
            inner_w = max(0, cols - 2)
            inner_h = max(0, rows - 2)
            if inner_w <= 0 or inner_h <= 0:
                return

            show_search = bool(self._search_ui_visible() and int(inner_h) >= 2)

            # Grid.
            tile, icon, content_w, _inner_w, inner_h, grid_cols, max_scroll = self._grid_metrics(cols=cols, rows=rows)
            grid_h = max(0, int(inner_h) - (1 if show_search else 0))
            grid_rows = max(1, int(grid_h) // max(1, int(tile))) if grid_h > 0 else 1
            self._grid_cols_last = max(1, int(grid_cols))
            self._grid_rows_last = max(1, int(grid_rows))

            scroll_row = int(self._scroll.clamp(int(max_scroll)))
            if self._scroll.follow_selection:
                self._ensure_selection_visible(grid_cols=int(grid_cols), grid_rows=int(grid_rows), max_scroll=int(max_scroll))
                scroll_row = int(self._scroll.clamp(int(max_scroll)))

            start = int(scroll_row) * int(grid_cols)
            visible = int(grid_cols) * int(grid_rows)
            grid_y0 = int(inner_y)
            for i in range(int(visible)):
                pos = start + i
                if pos >= len(self._filtered):
                    break
                entry_idx = int(self._filtered[pos])
                e = self._entries[entry_idx]

                tcol = int(i) % int(grid_cols)
                trow = int(i) // int(grid_cols)
                tx = int(inner_x) + int(tcol) * int(tile)
                ty = int(grid_y0) + int(trow) * int(tile)
                if ty >= int(inner_y) + int(inner_h):
                    break

                is_sel = int(entry_idx) == int(self._selected)
                surface.add_hotspot(x=int(tx), y=int(ty), w=int(tile), h=int(tile), kind="palette.entry", payload=int(entry_idx))
                if is_sel:
                    surface.fill_rect(int(tx), int(ty), int(tile), int(tile), bg=sel_bg, fg=sel_fg)
                    surface.draw_box(int(tx), int(ty), int(tile), int(tile), fg=accent, bg=sel_bg, title=None)

                jar_rel = str(getattr(e, "jar_rel_tex", "") or "")
                tex = self._load_tex(jar_rel) if jar_rel else None
                if tex is not None:
                    try:
                        tex_w = max(1, int(getattr(tex, "width", 1)))
                        tex_h = max(1, int(getattr(tex, "height", 1)))
                        dim = max(1, max(int(tex_w), int(tex_h)))
                        draw_w = max(1, int(round(float(icon) * float(tex_w) / float(dim))))
                        draw_h = max(1, int(round(float(icon) * float(tex_h) / float(dim))))
                        ix = int(tx + max(0, (int(tile) - int(draw_w)) // 2))
                        iy = int(ty + max(0, (int(tile) - int(draw_h)) // 2))
                        surface.add_sprite(
                            x=int(ix),
                            y=int(iy),
                            w=int(draw_w),
                            h=int(draw_h),
                            target=int(getattr(tex, "target", gl_.GL_TEXTURE_2D)),
                            tex_id=int(getattr(tex, "id", 0)),
                            tex_coords=tuple(getattr(tex, "tex_coords", ())),
                            tint=(255, 255, 255, 255),
                        )
                    except Exception:
                        pass

            # Search bar (bottom row inside box).
            if show_search:
                total = len(self._entries)
                matches = len(self._filtered)
                cursor_on = bool(self._search_active and (int(time.monotonic() * 2.2) % 2 == 0))
                cursor = "▌" if cursor_on else ""
                q = self._query
                left = "/" + q if (q or self._search_active) else "/"
                right = f"{matches}/{total}" if total else f"{matches}"
                cancel = "[X]" if self._search_ui_visible() else ""

                usable = max(0, int(content_w))
                tail = ""
                if cancel and right:
                    tail = f"{cancel} {right}"
                elif cancel:
                    tail = cancel
                else:
                    tail = right
                if len(tail) >= usable:
                    tail = tail[-usable:]
                left_w = max(0, usable - len(tail))
                show_left = left
                if len(show_left) > left_w:
                    show_left = show_left[: max(0, left_w - 1)] + "…"

                bar_y = int(inner_y) + max(0, int(inner_h) - 1)
                surface.fill_rect(inner_x, bar_y, int(content_w), 1, bg=bg, fg=fg)
                surface.put(inner_x, bar_y, show_left[:left_w], fg=fg, bg=bg)
                if cursor and len(show_left) < left_w:
                    cx = inner_x + len(show_left)
                    if inner_x <= cx < inner_x + int(content_w):
                        surface.put(cx, bar_y, cursor, fg=accent, bg=bg)
                if tail:
                    tail_x = inner_x + max(0, usable - len(tail))
                    surface.put(tail_x, bar_y, tail, fg=muted, bg=bg)
                    if cancel:
                        cancel_idx = tail.find(cancel)
                        if cancel_idx >= 0:
                            surface.add_hotspot(x=int(tail_x + cancel_idx), y=int(bar_y), w=len(cancel), h=1, kind="palette.clear")

            # Scrollbar.
            sb_x = int(inner_x) + int(content_w)
            if int(inner_h) > 0 and int(content_w) > 0 and sb_x < cols - 1:
                total_rows = max(1, (len(self._filtered) + int(grid_cols) - 1) // int(grid_cols)) if self._filtered else 1
                self._scrollbar.update(
                    track_top=int(inner_y),
                    track_rows=int(inner_h),
                    visible_rows=int(grid_rows),
                    total_rows=int(total_rows),
                    scroll_top=int(scroll_row),
                )
                for yy in range(int(inner_h)):
                    surface.put(sb_x, int(inner_y) + int(yy), "░", fg=box_fg, bg=bg)
                thumb_top = int(getattr(self._scrollbar, "thumb_top", int(inner_y)))
                thumb_rows = max(1, int(getattr(self._scrollbar, "thumb_rows", 1)))
                for yy in range(int(thumb_rows)):
                    surface.put(sb_x, int(thumb_top) + int(yy), "█", fg=accent, bg=bg)

            gl_.glViewport(0, 0, int(vp_w_px), int(vp_h_px))
            self._renderer.draw(surface=surface, font=term_font, vp_w_px=int(vp_w_px), vp_h_px=int(vp_h_px), param_store=self._store, rez_active=False)

        def on_close(self) -> None:
            if not str(getattr(self, "_close_request_path", "") or "").strip():
                self._close_request_path = "native_titlebar_or_system_close"
            try:
                self._on_closed()
            finally:
                super().on_close()

    return PaletteWindow()
