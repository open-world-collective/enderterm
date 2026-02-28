from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from typing import Literal

from enderterm.datapack import PackStack, canonical_template_pool_json, list_processor_lists, list_structure_templates, list_template_pools
from enderterm.params import ParamStore


def create_jigsaw_editor_window(
    *,
    pyglet: object,
    store: ParamStore,
    get_stack: Callable[[], PackStack],
    on_regrow: Callable[[], None],
    on_closed: Callable[[], None],
    font_name: str | None,
) -> object:
    # NOTE: Keep pyglet import-time effects out of this module. The caller passes
    # the pyglet module object when constructing the window.

    class WorldgenEditorWindow(pyglet.window.Window):
        def __init__(
            self,
            *,
            get_stack: Callable[[], PackStack],
            on_regrow: Callable[[], None],
            on_closed: Callable[[], None],
            font_name: str | None,
        ) -> None:
            # NOTE: pyglet calls `on_resize` during `Window.__init__` (via
            # `set_visible(True)`), so we must guard layout until the UI is
            # fully constructed.
            self._ready = False
            self._get_stack = get_stack
            self._on_regrow = on_regrow
            self._on_closed = on_closed
            self._font_name = font_name

            self._batch = pyglet.graphics.Batch()
            self._group_bg = pyglet.graphics.OrderedGroup(0)
            self._group_widgets = pyglet.graphics.OrderedGroup(1)
            self._group_text = pyglet.graphics.OrderedGroup(2)

            try:
                ui_scale = float(store.get("ui.font.scale") or 1.0)
            except Exception:
                ui_scale = 1.0
            if not math.isfinite(ui_scale):
                ui_scale = 1.0
            self._ui_scale = max(0.5, min(3.0, float(ui_scale)))

            self._sidebar_w = 420
            self._header_h = max(48, int(round(62.0 * float(self._ui_scale))))
            self._row_h = max(12, int(round(18.0 * float(self._ui_scale))))
            self._scroll_px = 0.0
            self._scroll_max_px = 0.0
            self._selected = 0
            self._items: list[tuple[str, str]] = []  # (pool_id, owner_label)
            self._filtered: list[int] = []
            self._search_active = False
            self._search_query = ""
            self._list_labels: list[pyglet.text.Label] = []

            # Editor state.
            self._tab: Literal["form", "raw"] = "form"
            self._active_pool_id: str | None = None
            self._active_pool_owner: str = ""
            self._active_pool_canon: str | None = None
            self._pool_obj: dict[str, object] | None = None
            self._pool_dirty = False
            self._pool_error: str | None = None

            # Elements list (Form tab).
            self._elements_selected = 0
            self._elements_scroll_px = 0.0
            self._elements_scroll_max_px = 0.0
            self._element_labels: list[pyglet.text.Label] = []

            # Picker overlay (for dropdowns/search-pickers).
            self._picker_active = False
            self._picker_title = ""
            self._picker_query = ""
            self._picker_items: list[str] = []
            self._picker_filtered: list[int] = []
            self._picker_selected = 0
            self._picker_on_choose: Callable[[str], None] | None = None

            # Raw JSON (Raw tab).
            self._raw_active = False
            self._raw_modified = False
            self._raw_doc = pyglet.text.document.UnformattedDocument("")
            self._raw_layout: pyglet.text.layout.IncrementalTextLayout | None = None
            self._raw_caret: pyglet.text.caret.Caret | None = None

            desired_vsync = True
            try:
                desired_vsync = bool(store.get_int("render.vsync"))
            except Exception:
                desired_vsync = True
            super().__init__(
                width=1180, height=760, resizable=True, caption="EnderTerm: jigsaw editor", vsync=desired_vsync
            )

            self._raw_layout = pyglet.text.layout.IncrementalTextLayout(
                self._raw_doc,
                10,
                10,
                multiline=True,
                wrap_lines=True,
                batch=self._batch,
                group=self._group_text,
            )
            # Caret defaults to `layout.batch`, but some pyglet/macOS paths
            # construct layouts with `batch=None` until after the first
            # draw. Use our window batch explicitly.
            self._raw_caret = pyglet.text.caret.Caret(self._raw_layout, batch=self._batch, color=(235, 235, 245))
            self._raw_caret.visible = False

            self._bg = pyglet.shapes.Rectangle(
                0, 0, self.width, self.height, color=(8, 8, 10), batch=self._batch, group=self._group_bg
            )
            self._bg.opacity = 245
            self._sidebar_bg = pyglet.shapes.Rectangle(
                0, 0, self._sidebar_w, self.height, color=(14, 14, 18), batch=self._batch, group=self._group_bg
            )
            self._sidebar_bg.opacity = 245
            self._divider = pyglet.shapes.Rectangle(
                self._sidebar_w - 1,
                0,
                1,
                self.height,
                color=(80, 70, 95),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._divider.opacity = 200

            self._title = pyglet.text.Label(
                "Jigsaw Editor",
                x=14,
                y=self.height - 14,
                anchor_x="left",
                anchor_y="top",
                font_name=self._font_name,
                font_size=max(6, int(round(14.0 * float(self._ui_scale)))),
                color=(255, 245, 255, 255),
                batch=self._batch,
                group=self._group_text,
            )
            self._subtitle = pyglet.text.Label(
                "Pools (template_pool)  —  / filter   G close",
                x=14,
                y=self.height - 34,
                anchor_x="left",
                anchor_y="top",
                font_name=self._font_name,
                font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                color=(190, 170, 210, 255),
                batch=self._batch,
                group=self._group_text,
            )

            self._search_bg = pyglet.shapes.Rectangle(
                14,
                self.height - 58,
                max(1, self._sidebar_w - 28),
                max(1, int(round(18.0 * float(self._ui_scale)))),
                color=(18, 16, 22),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._search_bg.opacity = 210
            self._search_label = pyglet.text.Label(
                "/",
                x=22,
                y=self.height - 49,
                anchor_x="left",
                anchor_y="center",
                font_name=self._font_name,
                font_size=max(6, int(round(11.0 * float(self._ui_scale)))),
                color=(220, 210, 235, 255),
                batch=self._batch,
                group=self._group_text,
            )

            self._status = pyglet.text.Label(
                "",
                x=self._sidebar_w + 14,
                y=self.height - 14,
                anchor_x="left",
                anchor_y="top",
                font_name=self._font_name,
                font_size=max(6, int(round(12.0 * float(self._ui_scale)))),
                color=(255, 235, 255, 235),
                batch=self._batch,
                group=self._group_text,
            )
            self._hint = pyglet.text.Label(
                "Form / Raw editor   Click fields to pick",
                x=self._sidebar_w + 14,
                y=self.height - 36,
                anchor_x="left",
                anchor_y="top",
                font_name=self._font_name,
                font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                color=(190, 170, 210, 200),
                batch=self._batch,
                group=self._group_text,
            )

            # Right panel UI.
            self._tab_form_bg = pyglet.shapes.Rectangle(
                self._sidebar_w + 14,
                self.height - 86,
                72,
                22,
                color=(55, 35, 70),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._tab_form_bg.opacity = 220
            self._tab_raw_bg = pyglet.shapes.Rectangle(
                self._sidebar_w + 92,
                self.height - 86,
                72,
                22,
                color=(20, 18, 26),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._tab_raw_bg.opacity = 220
            self._tab_form_label = pyglet.text.Label(
                "Form",
                x=self._sidebar_w + 14 + 36,
                y=self.height - 75,
                anchor_x="center",
                anchor_y="center",
                font_name=self._font_name,
                font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                color=(255, 240, 255, 255),
                batch=self._batch,
                group=self._group_text,
            )
            self._tab_raw_label = pyglet.text.Label(
                "Raw",
                x=self._sidebar_w + 92 + 36,
                y=self.height - 75,
                anchor_x="center",
                anchor_y="center",
                font_name=self._font_name,
                font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                color=(220, 210, 235, 255),
                batch=self._batch,
                group=self._group_text,
            )

            self._btn_fork_bg = pyglet.shapes.Rectangle(
                self.width - 14 - 92 - 92 - 92,
                self.height - 86,
                86,
                22,
                color=(60, 50, 75),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._btn_fork_bg.opacity = 220
            self._btn_fork_label = pyglet.text.Label(
                "Fork",
                x=self._btn_fork_bg.x + self._btn_fork_bg.width / 2,
                y=self._btn_fork_bg.y + self._btn_fork_bg.height / 2,
                anchor_x="center",
                anchor_y="center",
                font_name=self._font_name,
                font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                color=(240, 235, 255, 255),
                batch=self._batch,
                group=self._group_text,
            )

            self._btn_save_bg = pyglet.shapes.Rectangle(
                self.width - 14 - 92 - 92,
                self.height - 86,
                86,
                22,
                color=(55, 35, 70),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._btn_save_bg.opacity = 220
            self._btn_save_label = pyglet.text.Label(
                "Save",
                x=self._btn_save_bg.x + self._btn_save_bg.width / 2,
                y=self._btn_save_bg.y + self._btn_save_bg.height / 2,
                anchor_x="center",
                anchor_y="center",
                font_name=self._font_name,
                font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                color=(255, 240, 255, 255),
                batch=self._batch,
                group=self._group_text,
            )

            self._btn_regrow_bg = pyglet.shapes.Rectangle(
                self.width - 14 - 92,
                self.height - 86,
                86,
                22,
                color=(25, 23, 31),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._btn_regrow_bg.opacity = 220
            self._btn_regrow_label = pyglet.text.Label(
                "Regrow",
                x=self._btn_regrow_bg.x + self._btn_regrow_bg.width / 2,
                y=self._btn_regrow_bg.y + self._btn_regrow_bg.height / 2,
                anchor_x="center",
                anchor_y="center",
                font_name=self._font_name,
                font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                color=(220, 210, 235, 255),
                batch=self._batch,
                group=self._group_text,
            )

            # Form widgets.
            self._form_title = pyglet.text.Label(
                "",
                x=self._sidebar_w + 14,
                y=self.height - 110,
                anchor_x="left",
                anchor_y="top",
                font_name=self._font_name,
                font_size=max(6, int(round(12.0 * float(self._ui_scale)))),
                color=(255, 240, 255, 255),
                batch=self._batch,
                group=self._group_text,
            )
            self._form_fallback_label = pyglet.text.Label(
                "fallback",
                x=self._sidebar_w + 14,
                y=self.height - 134,
                anchor_x="left",
                anchor_y="top",
                font_name=self._font_name,
                font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                color=(190, 170, 210, 220),
                batch=self._batch,
                group=self._group_text,
            )
            self._form_fallback_bg = pyglet.shapes.Rectangle(
                self._sidebar_w + 14,
                self.height - 156,
                420,
                20,
                color=(18, 16, 22),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._form_fallback_bg.opacity = 210
            self._form_fallback_value = pyglet.text.Label(
                "",
                x=self._sidebar_w + 22,
                y=self.height - 146,
                anchor_x="left",
                anchor_y="center",
                font_name=self._font_name,
                font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                color=(235, 235, 245, 255),
                batch=self._batch,
                group=self._group_text,
            )

            self._form_elements_label = pyglet.text.Label(
                "elements",
                x=self._sidebar_w + 14,
                y=self.height - 176,
                anchor_x="left",
                anchor_y="top",
                font_name=self._font_name,
                font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                color=(190, 170, 210, 220),
                batch=self._batch,
                group=self._group_text,
            )
            self._form_elements_bg = pyglet.shapes.Rectangle(
                self._sidebar_w + 14,
                14,
                420,
                max(1, self.height - 210),
                color=(12, 10, 14),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._form_elements_bg.opacity = 200
            self._form_elements_sel = pyglet.shapes.Rectangle(
                self._sidebar_w + 14,
                14,
                420,
                self._row_h,
                color=(80, 45, 105),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._form_elements_sel.opacity = 80

            self._form_el_btns: list[tuple[str, pyglet.shapes.Rectangle, pyglet.text.Label]] = []
            for label in ("+", "-", "dup", "↑", "↓"):
                rect = pyglet.shapes.Rectangle(
                    self._sidebar_w + 14,
                    self.height - 200,
                    44,
                    18,
                    color=(20, 18, 26),
                    batch=self._batch,
                    group=self._group_widgets,
                )
                rect.opacity = 220
                lbl = pyglet.text.Label(
                    label,
                    x=rect.x + rect.width / 2,
                    y=rect.y + rect.height / 2,
                    anchor_x="center",
                    anchor_y="center",
                    font_name=self._font_name,
                    font_size=max(6, int(round(9.0 * float(self._ui_scale)))),
                    color=(220, 210, 235, 255),
                    batch=self._batch,
                    group=self._group_text,
                )
                self._form_el_btns.append((label, rect, lbl))

            # Element detail editor (right column).
            self._elem_edit_title = pyglet.text.Label(
                "",
                x=self._sidebar_w + 14 + 420 + 20,
                y=self.height - 176,
                anchor_x="left",
                anchor_y="top",
                font_name=self._font_name,
                font_size=max(6, int(round(11.0 * float(self._ui_scale)))),
                color=(255, 240, 255, 255),
                batch=self._batch,
                group=self._group_text,
            )
            self._elem_field_labels: dict[str, pyglet.text.Label] = {}
            self._elem_field_bg: dict[str, pyglet.shapes.Rectangle] = {}
            self._elem_field_value: dict[str, pyglet.text.Label] = {}

            for key in ("type", "location", "processors", "projection", "weight"):
                lbl = pyglet.text.Label(
                    key,
                    x=self._elem_edit_title.x,
                    y=self._elem_edit_title.y - 18,
                    anchor_x="left",
                    anchor_y="top",
                    font_name=self._font_name,
                    font_size=max(6, int(round(9.0 * float(self._ui_scale)))),
                    color=(190, 170, 210, 220),
                    batch=self._batch,
                    group=self._group_text,
                )
                bg = pyglet.shapes.Rectangle(
                    self._elem_edit_title.x,
                    self._elem_edit_title.y - 38,
                    max(1, self.width - int(self._elem_edit_title.x) - 14),
                    18,
                    color=(18, 16, 22),
                    batch=self._batch,
                    group=self._group_widgets,
                )
                bg.opacity = 210
                val = pyglet.text.Label(
                    "",
                    x=bg.x + 8,
                    y=bg.y + bg.height / 2,
                    anchor_x="left",
                    anchor_y="center",
                    font_name=self._font_name,
                    font_size=max(6, int(round(9.0 * float(self._ui_scale)))),
                    color=(235, 235, 245, 255),
                    batch=self._batch,
                    group=self._group_text,
                )
                self._elem_field_labels[key] = lbl
                self._elem_field_bg[key] = bg
                self._elem_field_value[key] = val

            # Picker overlay.
            self._picker_bg = pyglet.shapes.Rectangle(
                self._sidebar_w + 60,
                self.height - 420,
                520,
                320,
                color=(10, 9, 12),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._picker_bg.opacity = 240
            self._picker_title_label = pyglet.text.Label(
                "",
                x=int(self._picker_bg.x) + 12,
                y=int(self._picker_bg.y + self._picker_bg.height) - 10,
                anchor_x="left",
                anchor_y="top",
                font_name=self._font_name,
                font_size=max(6, int(round(11.0 * float(self._ui_scale)))),
                color=(255, 240, 255, 255),
                batch=self._batch,
                group=self._group_text,
            )
            self._picker_query_label = pyglet.text.Label(
                "",
                x=int(self._picker_bg.x) + 12,
                y=int(self._picker_bg.y + self._picker_bg.height) - 32,
                anchor_x="left",
                anchor_y="top",
                font_name=self._font_name,
                font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                color=(220, 210, 235, 255),
                batch=self._batch,
                group=self._group_text,
            )
            self._picker_labels: list[pyglet.text.Label] = []
            for _ in range(16):
                self._picker_labels.append(
                    pyglet.text.Label(
                        "",
                        x=int(self._picker_bg.x) + 12,
                        y=int(self._picker_bg.y) + int(self._picker_bg.height) - 58,
                        anchor_x="left",
                        anchor_y="top",
                        font_name=self._font_name,
                        font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                        color=(235, 235, 245, 220),
                        batch=self._batch,
                        group=self._group_text,
                    )
                )
            self._set_picker_visible(False)

            # Raw background.
            self._raw_bg = pyglet.shapes.Rectangle(
                self._sidebar_w + 14,
                14,
                max(1, self.width - self._sidebar_w - 28),
                max(1, self.height - 120),
                color=(12, 10, 14),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._raw_bg.opacity = 200
            self._raw_border = pyglet.shapes.Rectangle(
                self._sidebar_w + 14,
                14,
                max(1, self.width - self._sidebar_w - 28),
                max(1, self.height - 120),
                color=(80, 70, 95),
                batch=self._batch,
                group=self._group_widgets,
            )
            self._raw_border.opacity = 40
            self._raw_border.visible = False
            self._raw_bg.visible = False

            self._refresh_items()
            self._layout()
            self._ready = True

        def _set_picker_visible(self, visible: bool) -> None:
            self._picker_active = bool(visible)
            self._picker_bg.visible = bool(visible)
            self._picker_title_label.visible = bool(visible)
            self._picker_query_label.visible = bool(visible)
            for lbl in self._picker_labels:
                lbl.visible = bool(visible)

        def _refresh_items(self) -> None:
            try:
                stack = self._get_stack()
                self._items = list_template_pools(stack)
            except Exception:
                self._items = []
            self._apply_filter(reset_scroll=True)

        def _apply_filter(self, *, reset_scroll: bool) -> None:
            q = self._search_query.strip().lower()
            if not q:
                self._filtered = list(range(len(self._items)))
            else:
                self._filtered = [i for i, (pid, _owner) in enumerate(self._items) if q in pid.lower()]
            if reset_scroll:
                self._scroll_px = 0.0
            self._selected = max(0, min(len(self._filtered) - 1, int(self._selected))) if self._filtered else 0

        def _apply_raw_style(self) -> None:
            try:
                self._raw_doc.set_style(
                    0,
                    len(self._raw_doc.text),
                    {
                        "font_name": self._font_name,
                        "font_size": max(6, int(round(10.0 * float(self._ui_scale)))),
                        "color": (235, 235, 245, 255),
                    },
                )
            except Exception:
                pass

        def _sync_raw_from_obj(self) -> None:
            if self._pool_obj is None:
                self._raw_doc.text = ""
                return
            try:
                self._raw_doc.text = json.dumps(self._pool_obj, indent=2) + "\n"
            except Exception:
                self._raw_doc.text = "{}\n"
            self._apply_raw_style()
            if self._raw_layout is not None:
                try:
                    # Defensive: pyglet stores the document via a weakref.
                    # If anything goes wrong during init (or a previous
                    # failed open left a half-constructed layout), the
                    # document may appear detached.
                    if self._raw_layout.document is None:
                        self._raw_layout.document = self._raw_doc
                except Exception:
                    pass
                try:
                    self._raw_layout.view_y = 0
                except Exception:
                    pass

        def _ensure_active_pool(self, pool_id: str, owner_label: str) -> None:
            if not pool_id:
                self._active_pool_id = None
                self._active_pool_owner = ""
                self._active_pool_canon = None
                self._pool_obj = None
                self._pool_dirty = False
                self._pool_error = None
                self._raw_active = False
                self._raw_modified = False
                return
            if pool_id == self._active_pool_id and owner_label == self._active_pool_owner:
                return
            self._active_pool_id = pool_id
            self._active_pool_owner = owner_label
            self._active_pool_canon = canonical_template_pool_json(pool_id)
            self._pool_dirty = False
            self._pool_error = None
            self._raw_active = False
            self._raw_modified = False
            self._elements_selected = 0
            self._elements_scroll_px = 0.0

            canon = self._active_pool_canon
            obj: dict[str, object] = {}
            if canon is None:
                obj = {"fallback": "minecraft:empty", "elements": []}
            else:
                try:
                    stack = self._get_stack()
                    data = stack.source.read(canon)
                    parsed = json.loads(data.decode("utf-8"))
                    if isinstance(parsed, dict):
                        obj = dict(parsed)
                    else:
                        obj = {}
                except Exception as e:
                    self._pool_error = f"{type(e).__name__}: {e}"
                    obj = {}
            if "fallback" not in obj:
                obj["fallback"] = "minecraft:empty"
            if "elements" not in obj:
                obj["elements"] = []
            self._pool_obj = obj
            self._sync_raw_from_obj()

        def _active_pool_elements(self) -> list[dict[str, object]]:
            obj = self._pool_obj
            if not isinstance(obj, dict):
                return []
            elems_obj = obj.get("elements")
            if not isinstance(elems_obj, list):
                return []
            out: list[dict[str, object]] = []
            for e in elems_obj:
                if isinstance(e, dict):
                    out.append(e)
            return out

        def _active_pool_fallback(self) -> str:
            obj = self._pool_obj
            if isinstance(obj, dict):
                fb = obj.get("fallback")
                if isinstance(fb, str) and fb:
                    return fb
            return "minecraft:empty"

        def _can_edit_pool(self) -> bool:
            if self._active_pool_id is None:
                return False
            if self._active_pool_owner != "work":
                return False
            return self._active_pool_canon is not None

        def _set_tab(self, tab: Literal["form", "raw"]) -> None:
            if tab == self._tab:
                return
            if self._tab == "raw" and self._raw_modified:
                parsed = self._parse_raw_json()
                if parsed is None:
                    self.invalid = True
                    return
                if "fallback" not in parsed:
                    parsed["fallback"] = "minecraft:empty"
                if "elements" not in parsed:
                    parsed["elements"] = []
                self._pool_obj = parsed
                self._pool_dirty = True
                self._raw_modified = False
            if tab == "raw":
                self._sync_raw_from_obj()
            self._raw_active = False
            self._tab = tab
            self.invalid = True

        def _open_picker(self, *, title: str, items: list[str], on_choose: Callable[[str], None]) -> None:
            self._picker_title = title
            self._picker_query = ""
            self._picker_items = list(items)
            self._picker_on_choose = on_choose
            self._picker_selected = 0
            self._apply_picker_filter(reset_selection=True)
            self._set_picker_visible(True)
            self.invalid = True

        def _apply_picker_filter(self, *, reset_selection: bool) -> None:
            q = self._picker_query.strip().lower()
            if not q:
                self._picker_filtered = list(range(len(self._picker_items)))
            else:
                self._picker_filtered = [i for i, s in enumerate(self._picker_items) if q in s.lower()]
            if reset_selection:
                self._picker_selected = 0
            self._picker_selected = max(0, min(len(self._picker_filtered) - 1, int(self._picker_selected))) if self._picker_filtered else 0

        def _close_picker(self) -> None:
            self._picker_on_choose = None
            self._picker_items = []
            self._picker_filtered = []
            self._picker_query = ""
            self._picker_selected = 0
            self._set_picker_visible(False)

        def _fork_active_pool(self) -> None:
            if self._active_pool_id is None:
                return
            canon = self._active_pool_canon
            if canon is None:
                return
            try:
                stack = self._get_stack()
                stack.fork_into_work(canon)
            except Exception:
                return
            try:
                self._refresh_items()
            except Exception:
                pass
            self._active_pool_owner = "work"
            self._ensure_active_pool(self._active_pool_id, "work")
            self.invalid = True

        def _parse_raw_json(self) -> dict[str, object] | None:
            try:
                text = self._raw_doc.text
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else None
            except Exception as e:
                self._pool_error = f"Raw JSON: {type(e).__name__}: {e}"
                return None

        def _save_active_pool(self) -> None:
            if not self._can_edit_pool():
                return
            canon = self._active_pool_canon
            if canon is None:
                return
            if self._tab == "raw" or self._raw_modified:
                parsed = self._parse_raw_json()
                if parsed is None:
                    self.invalid = True
                    return
                if "fallback" not in parsed:
                    parsed["fallback"] = "minecraft:empty"
                if "elements" not in parsed:
                    parsed["elements"] = []
                self._pool_obj = parsed
                self._raw_modified = False

            obj = self._pool_obj or {"fallback": "minecraft:empty", "elements": []}
            try:
                data = (json.dumps(obj, indent=2) + "\n").encode("utf-8")
            except Exception:
                data = b"{}\n"
            try:
                stack = self._get_stack()
                stack.work.write(canon, data)
            except Exception as e:
                self._pool_error = f"Save: {type(e).__name__}: {e}"
                self.invalid = True
                return
            self._pool_dirty = False
            self._pool_error = None
            try:
                self._refresh_items()
            except Exception:
                pass
            self.invalid = True

        def _regrow(self) -> None:
            try:
                self._on_regrow()
            except Exception:
                pass

        def _ensure_element_labels(self) -> None:
            h = float(self._form_elements_bg.height)
            want = max(1, int(h / float(self._row_h)) + 2)
            while len(self._element_labels) < want:
                self._element_labels.append(
                    pyglet.text.Label(
                        "",
                        x=self._form_elements_bg.x + 8,
                        y=self._form_elements_bg.y + self._form_elements_bg.height - 8,
                        anchor_x="left",
                        anchor_y="top",
                        font_name=self._font_name,
                        font_size=max(6, int(round(9.0 * float(self._ui_scale)))),
                        color=(220, 220, 230, 220),
                        batch=self._batch,
                        group=self._group_text,
                    )
                )
            while len(self._element_labels) > want:
                lbl = self._element_labels.pop()
                try:
                    lbl.delete()
                except Exception:
                    pass

        def _element_summary(self, idx: int, entry: dict[str, object]) -> str:
            w = entry.get("weight", 1)
            try:
                weight = int(w)
            except Exception:
                weight = 1
            elt = entry.get("element")
            elt_type = ""
            loc = ""
            proc = ""
            proj = ""
            if isinstance(elt, dict):
                elt_type = str(elt.get("element_type") or "")
                loc = str(elt.get("location") or "")
                proc = str(elt.get("processors") or "")
                proj = str(elt.get("projection") or "")
            short_type = "?"
            if "empty_pool_element" in elt_type:
                short_type = "empty"
            elif "single_pool_element" in elt_type:
                short_type = "single"
            elif elt_type:
                short_type = elt_type.split(":", 1)[-1]
            def _short(s: str, n: int = 22) -> str:
                if len(s) <= n:
                    return s
                return "…" + s[-(n - 1) :]
            parts = [
                f"{idx+1:>3}",
                f"w{weight}",
                _short(short_type, 10),
            ]
            if loc:
                parts.append(_short(loc, 22))
            if proj:
                parts.append(_short(proj, 12))
            if proc:
                parts.append(_short(proc, 16))
            return "  ".join(parts)

        def _layout_right_panel(self) -> None:
            s = float(self._ui_scale)

            def _u(px: float) -> float:
                return float(px) * s

            panel_x0 = float(self._sidebar_w + 14)
            panel_x1 = float(self.width - 14)
            panel_w = max(1.0, panel_x1 - panel_x0)
            col_w = min(520.0, max(320.0, panel_w * 0.58))
            editor_x0 = panel_x0 + col_w + 20.0
            editor_w = max(1.0, panel_x1 - editor_x0)

            tab_y = float(self.height) - _u(86.0)
            tab_h = max(14.0, _u(22.0))
            tab_w = 72.0
            tab_gap = max(2.0, _u(6.0))
            self._tab_form_bg.x = panel_x0
            self._tab_form_bg.y = tab_y
            self._tab_form_bg.width = tab_w
            self._tab_form_bg.height = tab_h
            self._tab_raw_bg.x = panel_x0 + tab_w + tab_gap
            self._tab_raw_bg.y = tab_y
            self._tab_raw_bg.width = tab_w
            self._tab_raw_bg.height = tab_h
            self._tab_form_label.x = self._tab_form_bg.x + tab_w / 2
            self._tab_form_label.y = tab_y + tab_h / 2
            self._tab_raw_label.x = self._tab_raw_bg.x + tab_w / 2
            self._tab_raw_label.y = tab_y + tab_h / 2

            btn_w = 86.0
            btn_h = max(14.0, _u(22.0))
            btn_gap = max(4.0, _u(8.0))
            regrow_x = panel_x1 - btn_w
            save_x = regrow_x - btn_gap - btn_w
            fork_x = save_x - btn_gap - btn_w
            self._btn_regrow_bg.x = regrow_x
            self._btn_regrow_bg.y = tab_y
            self._btn_regrow_bg.width = btn_w
            self._btn_regrow_bg.height = btn_h
            self._btn_regrow_label.x = self._btn_regrow_bg.x + btn_w / 2
            self._btn_regrow_label.y = tab_y + btn_h / 2

            self._btn_save_bg.x = save_x
            self._btn_save_bg.y = tab_y
            self._btn_save_bg.width = btn_w
            self._btn_save_bg.height = btn_h
            self._btn_save_label.x = self._btn_save_bg.x + btn_w / 2
            self._btn_save_label.y = tab_y + btn_h / 2

            self._btn_fork_bg.x = fork_x
            self._btn_fork_bg.y = tab_y
            self._btn_fork_bg.width = btn_w
            self._btn_fork_bg.height = btn_h
            self._btn_fork_label.x = self._btn_fork_bg.x + btn_w / 2
            self._btn_fork_label.y = tab_y + btn_h / 2

            # Tabs visual state.
            if self._tab == "form":
                self._tab_form_bg.color = (55, 35, 70)
                self._tab_raw_bg.color = (20, 18, 26)
                self._tab_form_label.color = (255, 240, 255, 255)
                self._tab_raw_label.color = (220, 210, 235, 255)
            else:
                self._tab_form_bg.color = (20, 18, 26)
                self._tab_raw_bg.color = (55, 35, 70)
                self._tab_form_label.color = (220, 210, 235, 255)
                self._tab_raw_label.color = (255, 240, 255, 255)

            pool_selected = self._active_pool_id is not None
            can_edit = self._can_edit_pool()
            show_fork = bool(pool_selected and (not can_edit) and self._active_pool_canon is not None)
            self._btn_fork_bg.visible = show_fork
            self._btn_fork_label.visible = show_fork
            self._btn_save_bg.visible = bool(pool_selected)
            self._btn_save_label.visible = bool(pool_selected)
            self._btn_regrow_bg.visible = bool(pool_selected)
            self._btn_regrow_label.visible = bool(pool_selected)

            if self._pool_error:
                self._hint.text = self._pool_error
            elif pool_selected and (not can_edit) and self._active_pool_canon is not None:
                self._hint.text = "Vendor pack: read-only. Click Fork to edit."
            elif pool_selected and self._pool_dirty:
                self._hint.text = "Unsaved changes (*)"
            else:
                self._hint.text = "Form / Raw editor   Click fields to pick"

            if can_edit:
                self._btn_save_bg.color = (55, 35, 70)
                self._btn_save_label.color = (255, 240, 255, 255)
            else:
                self._btn_save_bg.color = (25, 23, 31)
                self._btn_save_label.color = (190, 170, 210, 200)

            # Content visibility.
            form_vis = bool(pool_selected and self._tab == "form")
            raw_vis = bool(pool_selected and self._tab == "raw")
            self._raw_bg.visible = raw_vis
            self._raw_border.visible = raw_vis
            if self._raw_caret is not None:
                self._raw_caret.visible = bool(raw_vis and self._raw_active)

            for w in (
                self._form_title,
                self._form_fallback_label,
                self._form_fallback_bg,
                self._form_fallback_value,
                self._form_elements_label,
                self._form_elements_bg,
                self._form_elements_sel,
                self._elem_edit_title,
            ):
                w.visible = form_vis
            for _, rect, lbl in self._form_el_btns:
                rect.visible = form_vis
                lbl.visible = form_vis
            for key in self._elem_field_labels:
                self._elem_field_labels[key].visible = form_vis
                self._elem_field_bg[key].visible = form_vis
                self._elem_field_value[key].visible = form_vis
            for lbl in self._element_labels:
                lbl.visible = form_vis
                if not form_vis:
                    lbl.text = ""

            if not pool_selected:
                return

            # Layout raw area.
            raw_x = panel_x0
            raw_y = _u(14.0)
            raw_w = panel_w
            raw_h = max(1.0, float(self.height) - _u(120.0))
            self._raw_bg.x = raw_x
            self._raw_bg.y = raw_y
            self._raw_bg.width = raw_w
            self._raw_bg.height = raw_h
            self._raw_border.x = raw_x
            self._raw_border.y = raw_y
            self._raw_border.width = raw_w
            self._raw_border.height = raw_h
            if self._raw_layout is not None:
                # NOTE: do not toggle `IncrementalTextLayout.visible`. In pyglet 1.5,
                # hiding it calls `delete()`, which detaches the document/batch.
                if raw_vis:
                    self._raw_layout.x = int(raw_x + 10)
                    self._raw_layout.y = int(raw_y + 10)
                    self._raw_layout.width = int(max(1.0, raw_w - 20))
                    self._raw_layout.height = int(max(1.0, raw_h - 20))
                else:
                    self._raw_layout.x = -10000
                    self._raw_layout.y = -10000
                    self._raw_layout.width = 1
                    self._raw_layout.height = 1

            if not form_vis:
                self._layout_picker()
                return

            # Layout form.
            title_y = float(self.height) - _u(110.0)
            self._form_title.x = panel_x0
            self._form_title.y = title_y
            pool_id = self._active_pool_id or ""
            owner = self._active_pool_owner or "-"
            self._form_title.text = f"{pool_id}  [{owner}]"
            self._form_fallback_label.x = panel_x0
            self._form_fallback_label.y = title_y - _u(24.0)
            self._form_fallback_bg.x = panel_x0
            self._form_fallback_bg.y = title_y - _u(46.0)
            self._form_fallback_bg.width = col_w
            self._form_fallback_value.x = panel_x0 + 8
            self._form_fallback_value.y = self._form_fallback_bg.y + self._form_fallback_bg.height / 2
            self._form_fallback_value.text = self._active_pool_fallback()

            self._form_elements_label.x = panel_x0
            self._form_elements_label.y = title_y - _u(66.0)
            btn_y = title_y - _u(86.0)
            btn_total_w = len(self._form_el_btns) * 44 + (len(self._form_el_btns) - 1) * 6
            btn_x = panel_x0 + col_w - float(btn_total_w)
            for i, (_name, rect, lbl) in enumerate(self._form_el_btns):
                rect.x = int(btn_x + float(i) * (44.0 + 6.0))
                rect.y = int(btn_y)
                rect.width = 44
                rect.height = int(max(12.0, _u(18.0)))
                lbl.x = rect.x + rect.width / 2
                lbl.y = rect.y + rect.height / 2
                if can_edit:
                    rect.color = (20, 18, 26)
                    lbl.color = (220, 210, 235, 255)
                else:
                    rect.color = (14, 13, 18)
                    lbl.color = (160, 145, 180, 180)

            elements_top = float(btn_y) - _u(10.0)
            self._form_elements_bg.x = panel_x0
            self._form_elements_bg.y = _u(14.0)
            self._form_elements_bg.width = col_w
            self._form_elements_bg.height = max(1.0, elements_top - _u(14.0))
            self._form_elements_sel.x = panel_x0
            self._form_elements_sel.width = col_w

            elems = self._active_pool_elements()
            total_rows = len(elems)
            self._ensure_element_labels()
            list_h = float(self._form_elements_bg.height)
            self._elements_scroll_max_px = max(0.0, float(total_rows * self._row_h) - list_h)
            self._elements_scroll_px = max(0.0, min(float(self._elements_scroll_max_px), float(self._elements_scroll_px)))
            self._elements_selected = max(0, min(total_rows - 1, int(self._elements_selected))) if total_rows else 0
            first_row = int(self._elements_scroll_px // float(self._row_h))
            frac = float(self._elements_scroll_px) - float(first_row) * float(self._row_h)
            top_y = float(self._form_elements_bg.y + self._form_elements_bg.height - 6)

            for i, lbl in enumerate(self._element_labels):
                row = first_row + i
                y = top_y - float(i) * float(self._row_h) + frac
                if row < 0 or row >= total_rows:
                    lbl.text = ""
                    continue
                entry = elems[row]
                lbl.x = self._form_elements_bg.x + 8
                lbl.y = y
                lbl.text = self._element_summary(row, entry)
                if row == self._elements_selected:
                    lbl.color = (255, 190, 255, 255)
                else:
                    lbl.color = (220, 220, 230, 220)

            if total_rows:
                sel_top = top_y - float(self._elements_selected - first_row) * float(self._row_h) + frac
                self._form_elements_sel.y = sel_top - float(self._row_h)
                self._form_elements_sel.height = float(self._row_h)
                self._form_elements_sel.visible = True
            else:
                self._form_elements_sel.visible = False

            # Element detail editor layout & values.
            self._elem_edit_title.x = editor_x0
            self._elem_edit_title.y = title_y - _u(66.0)
            if total_rows:
                self._elem_edit_title.text = f"Element {self._elements_selected+1}/{total_rows}"
            else:
                self._elem_edit_title.text = "Element (none)"

            field_y = float(self._elem_edit_title.y) - _u(20.0)
            field_order = ("type", "weight", "location", "processors", "projection")
            active_entry = elems[self._elements_selected] if 0 <= self._elements_selected < total_rows else {}
            elt = active_entry.get("element") if isinstance(active_entry, dict) else {}
            elt_dict = elt if isinstance(elt, dict) else {}
            values: dict[str, str] = {
                "type": str(elt_dict.get("element_type") or ""),
                "location": str(elt_dict.get("location") or ""),
                "processors": str(elt_dict.get("processors") or ""),
                "projection": str(elt_dict.get("projection") or ""),
                "weight": str(active_entry.get("weight") if isinstance(active_entry, dict) else ""),
            }
            for key in field_order:
                lbl = self._elem_field_labels[key]
                bg = self._elem_field_bg[key]
                val = self._elem_field_value[key]
                lbl.x = editor_x0
                lbl.y = field_y
                bg.x = editor_x0
                bg.y = field_y - _u(18.0)
                bg.width = editor_w
                val.x = bg.x + 8
                val.y = bg.y + bg.height / 2
                text = values.get(key, "")
                if len(text) > 40:
                    text = "…" + text[-39:]
                val.text = text
                field_y -= _u(44.0)

            self._layout_picker()

        def _layout_picker(self) -> None:
            if not self._picker_active:
                return
            s = float(self._ui_scale)

            def _u(px: float) -> int:
                return int(round(float(px) * s))

            px = float(self._picker_bg.x)
            py = float(self._picker_bg.y)
            pw = float(self._picker_bg.width)
            ph = float(self._picker_bg.height)
            self._picker_title_label.x = int(px) + 12
            self._picker_title_label.y = int(py + ph) - _u(10.0)
            cursor = "▌" if int(time.monotonic() * 2.2) % 2 == 0 else ""
            q = self._picker_query
            render_q = q
            if len(render_q) > 52:
                render_q = "…" + render_q[-51:]
            self._picker_query_label.text = f"{render_q}{cursor}" if (render_q or cursor) else ""
            self._picker_query_label.x = int(px) + 12
            self._picker_query_label.y = int(py + ph) - _u(32.0)
            self._picker_title_label.text = self._picker_title

            top_y = float(py + ph - _u(58.0))
            for i, lbl in enumerate(self._picker_labels):
                row = i
                y = top_y - float(i) * float(self._row_h)
                if row < 0 or row >= len(self._picker_filtered):
                    lbl.text = ""
                    continue
                item_idx = self._picker_filtered[row]
                text = self._picker_items[item_idx]
                if len(text) > 60:
                    text = "…" + text[-59:]
                lbl.x = int(px) + 12
                lbl.y = y
                lbl.text = text
                if row == self._picker_selected:
                    lbl.color = (255, 190, 255, 255)
                else:
                    lbl.color = (235, 235, 245, 220)

        def _list_area(self) -> tuple[float, float, float, float]:
            x0 = 0.0
            y0 = 0.0
            w = float(self._sidebar_w)
            h = float(max(1, self.height - self._header_h))
            return (x0, y0, w, h)

        def _ensure_list_labels(self) -> None:
            _, _, _, h = self._list_area()
            want = max(1, int(h / float(self._row_h)) + 2)
            while len(self._list_labels) < want:
                lbl = pyglet.text.Label(
                    "",
                    x=int(round(14.0 * float(self._ui_scale))),
                    y=0,
                    anchor_x="left",
                    anchor_y="top",
                    font_name=self._font_name,
                    font_size=max(6, int(round(10.0 * float(self._ui_scale)))),
                    color=(220, 220, 230, 220),
                    batch=self._batch,
                    group=self._group_text,
                )
                self._list_labels.append(lbl)
            while len(self._list_labels) > want:
                lbl = self._list_labels.pop()
                try:
                    lbl.delete()
                except Exception:
                    pass

        def _layout(self) -> None:
            s = float(self._ui_scale)

            def _u(px: float) -> int:
                return int(round(float(px) * s))

            self._bg.width = self.width
            self._bg.height = self.height
            self._sidebar_bg.height = self.height
            self._divider.x = self._sidebar_w - 1
            self._divider.height = self.height
            try:
                self._title.y = self.height - _u(14.0)
                self._subtitle.y = self.height - _u(34.0)
            except Exception:
                pass
            try:
                self._search_bg.y = self.height - _u(58.0)
                self._search_bg.width = max(1, self._sidebar_w - 28)
                self._search_label.y = self.height - _u(49.0)
            except Exception:
                pass
            try:
                self._status.x = self._sidebar_w + 14
                self._status.y = self.height - _u(14.0)
                self._hint.x = self._sidebar_w + 14
                self._hint.y = self.height - _u(36.0)
            except Exception:
                pass

            self._ensure_list_labels()
            x0, y0, w, h = self._list_area()
            top_y = y0 + h
            total_rows = len(self._filtered)
            self._scroll_max_px = max(0.0, float(total_rows * self._row_h) - float(h))
            self._scroll_px = max(0.0, min(float(self._scroll_max_px), float(self._scroll_px)))
            first_row = int(self._scroll_px // float(self._row_h))
            frac = float(self._scroll_px) - float(first_row) * float(self._row_h)

            sel_idx = self._selected
            sel_pool = ""
            sel_owner = ""
            if 0 <= sel_idx < len(self._filtered):
                src_i = self._filtered[sel_idx]
                if 0 <= src_i < len(self._items):
                    sel_pool, sel_owner = self._items[src_i]
            self._ensure_active_pool(sel_pool, sel_owner)
            dirty = ""
            if self._pool_dirty or self._raw_modified:
                dirty = " *"
            owner_hint = sel_owner or "-"
            if self._pool_error:
                owner_hint = "error"
            self._status.text = f"selected: {sel_pool or '(none)'}  [{owner_hint}]{dirty}"
            self._layout_right_panel()

            cursor = ""
            if self._search_active:
                cursor = "▌" if int(time.monotonic() * 2.2) % 2 == 0 else ""
            q = self._search_query
            render_q = q
            if len(render_q) > 52:
                render_q = "…" + render_q[-51:]
            self._search_label.text = f"/{render_q}{cursor}" if render_q or cursor else "/"

            for i, lbl in enumerate(self._list_labels):
                row = first_row + i
                y = top_y - float(i) * float(self._row_h) + frac
                if row < 0 or row >= total_rows:
                    lbl.text = ""
                    continue
                item_idx = self._filtered[row]
                pid, owner = self._items[item_idx]
                lbl.x = 14
                lbl.y = y
                prefix = "W " if owner == "work" else "V "
                lbl.text = prefix + pid
                if row == sel_idx:
                    lbl.color = (255, 190, 255, 255)
                else:
                    lbl.color = (220, 220, 230, 220)

        def open_pool(self, pool_id: str, *, fork: bool) -> None:
            stack = self._get_stack()
            canon = canonical_template_pool_json(pool_id)
            if fork and canon is not None:
                try:
                    stack.fork_into_work(canon)
                except Exception:
                    pass
                try:
                    self._refresh_items()
                except Exception:
                    pass

            try:
                idx = next((i for i, (pid, _o) in enumerate(self._items) if pid == pool_id), None)
            except Exception:
                idx = None
            if idx is not None:
                try:
                    pos = self._filtered.index(int(idx))
                except Exception:
                    pos = None
                if pos is not None:
                    self._selected = int(pos)
                    self._layout()
                    self.invalid = True
            try:
                self.activate()
            except Exception:
                pass

        def on_draw(self) -> None:
            from pyglet import gl as gl_

            if not getattr(self, "_ready", False):
                return
            self.clear()
            gl_.glClear(gl_.GL_COLOR_BUFFER_BIT)
            gl_.glDisable(gl_.GL_DEPTH_TEST)
            gl_.glDisable(gl_.GL_LIGHTING)
            gl_.glEnable(gl_.GL_BLEND)
            gl_.glBlendFunc(gl_.GL_SRC_ALPHA, gl_.GL_ONE_MINUS_SRC_ALPHA)
            gl_.glMatrixMode(gl_.GL_PROJECTION)
            gl_.glLoadIdentity()
            gl_.glOrtho(0.0, float(self.width), 0.0, float(self.height), -1.0, 1.0)
            gl_.glMatrixMode(gl_.GL_MODELVIEW)
            gl_.glLoadIdentity()
            self._layout()
            self._batch.draw()

        def on_resize(self, width: int, height: int) -> None:
            if not getattr(self, "_ready", False):
                return
            self._layout()
            self.invalid = True

        def on_mouse_scroll(self, x: int, y: int, scroll_x: int, scroll_y: int) -> None:
            if self._picker_active:
                if scroll_y:
                    self._picker_selected = max(
                        0, min(max(0, len(self._picker_filtered) - 1), int(self._picker_selected) - int(scroll_y))
                    )
                    self.invalid = True
                return

            if self._tab == "raw" and self._raw_bg.visible and self._raw_layout is not None:
                bx = float(self._raw_bg.x)
                by = float(self._raw_bg.y)
                bw = float(self._raw_bg.width)
                bh = float(self._raw_bg.height)
                if bx <= float(x) <= bx + bw and by <= float(y) <= by + bh:
                    try:
                        self._raw_layout.view_y += int(scroll_y) * 28
                    except Exception:
                        pass
                    self.invalid = True
                    return

            if self._tab == "form" and self._form_elements_bg.visible:
                bx = float(self._form_elements_bg.x)
                by = float(self._form_elements_bg.y)
                bw = float(self._form_elements_bg.width)
                bh = float(self._form_elements_bg.height)
                if bx <= float(x) <= bx + bw and by <= float(y) <= by + bh:
                    step = float(self._row_h) * 3.0
                    self._elements_scroll_px -= float(scroll_y) * step
                    self._elements_scroll_px = max(0.0, min(float(self._elements_scroll_max_px), float(self._elements_scroll_px)))
                    self.invalid = True
                    return

            x0, y0, w, h = self._list_area()
            if float(x) > x0 + w or float(y) < y0 or float(y) > y0 + h:
                return
            step = float(self._row_h) * 3.0
            self._scroll_px -= float(scroll_y) * step
            self._scroll_px = max(0.0, min(float(self._scroll_max_px), float(self._scroll_px)))
            self.invalid = True

        def on_mouse_press(self, x: int, y: int, button: int, modifiers: int) -> None:
            if button != pyglet.window.mouse.LEFT:
                return

            if self._picker_active:
                bx = float(self._picker_bg.x)
                by = float(self._picker_bg.y)
                bw = float(self._picker_bg.width)
                bh = float(self._picker_bg.height)
                if bx <= float(x) <= bx + bw and by <= float(y) <= by + bh:
                    top_y = by + bh - 58.0
                    row = int((top_y - float(y)) / float(self._row_h))
                    if 0 <= row < len(self._picker_filtered):
                        item_idx = self._picker_filtered[row]
                        choice = self._picker_items[item_idx]
                        cb = self._picker_on_choose
                        self._close_picker()
                        if cb is not None:
                            cb(choice)
                        self.invalid = True
                        return
                    return
                self._close_picker()
                self.invalid = True
                return

            def _hit_rect(rect: pyglet.shapes.Rectangle) -> bool:
                bx = float(rect.x)
                by = float(rect.y)
                bw = float(rect.width)
                bh = float(rect.height)
                return bx <= float(x) <= bx + bw and by <= float(y) <= by + bh

            if _hit_rect(self._tab_form_bg):
                self._set_tab("form")
                return
            if _hit_rect(self._tab_raw_bg):
                self._set_tab("raw")
                return

            if self._btn_fork_bg.visible and _hit_rect(self._btn_fork_bg):
                self._fork_active_pool()
                return
            if self._btn_save_bg.visible and _hit_rect(self._btn_save_bg):
                self._save_active_pool()
                return
            if self._btn_regrow_bg.visible and _hit_rect(self._btn_regrow_bg):
                self._regrow()
                return

            if float(x) <= float(self._sidebar_w):
                self._raw_active = False
                bx = float(self._search_bg.x)
                by = float(self._search_bg.y)
                bw = float(self._search_bg.width)
                bh = float(self._search_bg.height)
                if bx <= float(x) <= bx + bw and by <= float(y) <= by + bh:
                    self._search_active = True
                    self.invalid = True
                    return

                x0, y0, w, h = self._list_area()
                top_y = y0 + h
                if float(x) < x0 or float(x) > x0 + w:
                    return
                if float(y) < y0 or float(y) > top_y:
                    return
                row = int((top_y - float(y) + float(self._scroll_px)) / float(self._row_h))
                if row < 0 or row >= len(self._filtered):
                    return
                self._selected = row
                self.invalid = True
                return

            self._search_active = False
            if self._tab == "raw" and self._raw_bg.visible and self._raw_layout is not None and self._raw_caret is not None:
                if _hit_rect(self._raw_bg):
                    if not self._can_edit_pool():
                        self._raw_active = False
                        self._raw_caret.visible = False
                        self.invalid = True
                        return
                    self._raw_active = True
                    self._raw_caret.visible = True
                    self._raw_caret.on_mouse_press(x, y, button, modifiers)
                    self.invalid = True
                    return
                self._raw_active = False
                self._raw_caret.visible = False
                return

            self._raw_active = False
            if self._tab != "form" or not self._form_fallback_bg.visible:
                return
            if not self._can_edit_pool():
                return

            if _hit_rect(self._form_fallback_bg):
                stack = self._get_stack()
                pools = [pid for pid, _o in list_template_pools(stack)]
                pools.append("minecraft:empty")

                def _choose(v: str) -> None:
                    if self._pool_obj is None:
                        return
                    self._pool_obj["fallback"] = v
                    self._pool_dirty = True
                    self._pool_error = None
                    self._sync_raw_from_obj()
                    self.invalid = True

                self._open_picker(title="fallback pool", items=pools, on_choose=_choose)
                return

            for name, rect, _lbl in self._form_el_btns:
                if not _hit_rect(rect):
                    continue
                elems_obj = []
                if self._pool_obj is not None:
                    raw_elems = self._pool_obj.get("elements")
                    if isinstance(raw_elems, list):
                        elems_obj = raw_elems
                    else:
                        elems_obj = []
                        self._pool_obj["elements"] = elems_obj
                if name == "+":
                    insert_at = min(len(elems_obj), int(self._elements_selected) + 1) if elems_obj else 0
                    new_el = {
                        "weight": 1,
                        "element": {
                            "element_type": "minecraft:single_pool_element",
                            "location": "minecraft:empty",
                            "processors": "minecraft:empty",
                            "projection": "rigid",
                        },
                    }
                    elems_obj.insert(insert_at, new_el)
                    self._elements_selected = insert_at
                elif name == "-":
                    if 0 <= int(self._elements_selected) < len(elems_obj):
                        elems_obj.pop(int(self._elements_selected))
                        self._elements_selected = max(0, min(len(elems_obj) - 1, int(self._elements_selected))) if elems_obj else 0
                elif name == "dup":
                    if 0 <= int(self._elements_selected) < len(elems_obj):
                        clone = json.loads(json.dumps(elems_obj[int(self._elements_selected)]))
                        elems_obj.insert(int(self._elements_selected) + 1, clone)
                        self._elements_selected = int(self._elements_selected) + 1
                elif name == "↑":
                    i = int(self._elements_selected)
                    if 1 <= i < len(elems_obj):
                        elems_obj[i - 1], elems_obj[i] = elems_obj[i], elems_obj[i - 1]
                        self._elements_selected = i - 1
                elif name == "↓":
                    i = int(self._elements_selected)
                    if 0 <= i < len(elems_obj) - 1:
                        elems_obj[i + 1], elems_obj[i] = elems_obj[i], elems_obj[i + 1]
                        self._elements_selected = i + 1
                self._pool_dirty = True
                self._pool_error = None
                self._sync_raw_from_obj()
                self.invalid = True
                return

            if _hit_rect(self._form_elements_bg):
                top_y = float(self._form_elements_bg.y + self._form_elements_bg.height - 6)
                row = int((top_y - float(y) + float(self._elements_scroll_px)) / float(self._row_h))
                elems = self._active_pool_elements()
                if 0 <= row < len(elems):
                    self._elements_selected = row
                    self.invalid = True
                return

            # Element detail fields.
            elems = self._active_pool_elements()
            if not (0 <= int(self._elements_selected) < len(elems)):
                return
            entry = elems[int(self._elements_selected)]
            elt = entry.get("element")
            if not isinstance(elt, dict):
                elt = {}
                entry["element"] = elt

            def _mutate() -> None:
                self._pool_dirty = True
                self._pool_error = None
                self._sync_raw_from_obj()
                self.invalid = True

            def _set_type(v: str) -> None:
                etype = v
                if "empty_pool_element" in etype:
                    elt.clear()
                    elt["element_type"] = etype
                else:
                    loc = elt.get("location")
                    if not isinstance(loc, str) or not loc:
                        loc = "minecraft:empty"
                    proc = elt.get("processors")
                    if not isinstance(proc, str) or not proc:
                        proc = "minecraft:empty"
                    proj = elt.get("projection")
                    if not isinstance(proj, str) or not proj:
                        proj = "rigid"
                    elt.clear()
                    elt["element_type"] = etype
                    elt["location"] = loc
                    elt["processors"] = proc
                    elt["projection"] = proj
                _mutate()

            def _set_location(v: str) -> None:
                elt["location"] = v
                if "element_type" not in elt:
                    elt["element_type"] = "minecraft:single_pool_element"
                _mutate()

            def _set_processors(v: str) -> None:
                elt["processors"] = v
                if "element_type" not in elt:
                    elt["element_type"] = "minecraft:single_pool_element"
                _mutate()

            def _set_projection(v: str) -> None:
                elt["projection"] = v
                if "element_type" not in elt:
                    elt["element_type"] = "minecraft:single_pool_element"
                _mutate()

            for field in ("type", "location", "processors", "projection", "weight"):
                bg = self._elem_field_bg[field]
                if not _hit_rect(bg):
                    continue
                stack = self._get_stack()
                if field == "type":
                    self._open_picker(
                        title="element_type",
                        items=["minecraft:single_pool_element", "minecraft:empty_pool_element"],
                        on_choose=_set_type,
                    )
                    return
                if field == "location":
                    self._open_picker(title="location", items=list_structure_templates(stack), on_choose=_set_location)
                    return
                if field == "processors":
                    self._open_picker(title="processors", items=list_processor_lists(stack), on_choose=_set_processors)
                    return
                if field == "projection":
                    self._open_picker(title="projection", items=["rigid", "terrain_matching"], on_choose=_set_projection)
                    return
                if field == "weight":
                    bx = float(bg.x)
                    bw = float(bg.width)
                    cur = entry.get("weight", 1)
                    try:
                        w0 = int(cur)
                    except Exception:
                        w0 = 1
                    if float(x) <= bx + 28:
                        entry["weight"] = max(1, w0 - 1)
                        _mutate()
                        return
                    if float(x) >= bx + bw - 28:
                        entry["weight"] = max(1, w0 + 1)
                        _mutate()
                        return
                    choices = [str(n) for n in (1, 2, 3, 4, 5, 8, 10, 15, 20, 30, 40, 50, 75, 100)]

                    def _choose_weight(v: str) -> None:
                        try:
                            entry["weight"] = max(1, int(v))
                        except Exception:
                            entry["weight"] = 1
                        _mutate()

                    self._open_picker(title="weight", items=choices, on_choose=_choose_weight)
                    return

        def on_key_press(self, symbol: int, modifiers: int) -> None:
            if self._picker_active:
                if symbol == pyglet.window.key.ESCAPE:
                    self._close_picker()
                    self.invalid = True
                    return
                if symbol in {pyglet.window.key.ENTER, pyglet.window.key.RETURN}:
                    if 0 <= int(self._picker_selected) < len(self._picker_filtered):
                        item_idx = self._picker_filtered[int(self._picker_selected)]
                        choice = self._picker_items[item_idx]
                        cb = self._picker_on_choose
                        self._close_picker()
                        if cb is not None:
                            cb(choice)
                    self.invalid = True
                    return
                if symbol == pyglet.window.key.BACKSPACE:
                    if self._picker_query:
                        self._picker_query = self._picker_query[:-1]
                        self._apply_picker_filter(reset_selection=True)
                        self.invalid = True
                    return
                if symbol == pyglet.window.key.UP:
                    self._picker_selected = max(0, int(self._picker_selected) - 1)
                    self.invalid = True
                    return
                if symbol == pyglet.window.key.DOWN:
                    self._picker_selected = min(max(0, len(self._picker_filtered) - 1), int(self._picker_selected) + 1)
                    self.invalid = True
                    return
                return

            if self._tab == "raw" and self._raw_active:
                if symbol == pyglet.window.key.ESCAPE:
                    self._raw_active = False
                    self.invalid = True
                    return
                if symbol == pyglet.window.key.S and (modifiers & pyglet.window.key.MOD_ACCEL):
                    self._save_active_pool()
                    return
                if symbol == pyglet.window.key.TAB:
                    self._set_tab("form")
                    return
                return

            if symbol in {pyglet.window.key.ESCAPE, pyglet.window.key.G}:
                self.close()
                return
            if symbol == pyglet.window.key.TAB:
                self._set_tab("raw" if self._tab == "form" else "form")
                return
            if symbol == pyglet.window.key.S and (modifiers & pyglet.window.key.MOD_ACCEL):
                self._save_active_pool()
                return
            if symbol == pyglet.window.key.SLASH:
                self._search_active = True
                self.invalid = True
                return
            if symbol in {pyglet.window.key.ENTER, pyglet.window.key.RETURN}:
                if self._search_active:
                    self._search_active = False
                    self.invalid = True
                    return
            if symbol == pyglet.window.key.BACKSPACE:
                if self._search_active or self._search_query:
                    self._search_active = True
                    if self._search_query:
                        self._search_query = self._search_query[:-1]
                    self._apply_filter(reset_scroll=True)
                    self.invalid = True
                return
            if symbol == pyglet.window.key.UP:
                self._selected = max(0, int(self._selected) - 1)
                self.invalid = True
                return
            if symbol == pyglet.window.key.DOWN:
                self._selected = min(max(0, len(self._filtered) - 1), int(self._selected) + 1)
                self.invalid = True
                return
            if symbol == pyglet.window.key.R:
                self._refresh_items()
                self.invalid = True
                return

        def on_text(self, text: str) -> None:
            if not isinstance(text, str) or not text:
                return
            if self._picker_active:
                changed = False
                for ch in text:
                    if ch in {"\r", "\n"}:
                        continue
                    if ord(ch) < 32:
                        continue
                    self._picker_query += ch
                    changed = True
                if changed:
                    self._apply_picker_filter(reset_selection=True)
                    self.invalid = True
                return

            if self._tab == "raw" and self._raw_active and self._raw_caret is not None:
                self._raw_caret.on_text(text)
                self._raw_modified = True
                self.invalid = True
                return

            if not self._search_active:
                return
            changed = False
            for ch in text:
                if ch in {"\r", "\n"}:
                    continue
                if ord(ch) < 32:
                    continue
                if ch == "/":
                    continue
                self._search_query += ch
                changed = True
            if not changed:
                return
            self._apply_filter(reset_scroll=True)
            self.invalid = True

        def on_text_motion(self, motion: int) -> None:
            if self._tab == "raw" and self._raw_active and self._raw_caret is not None:
                self._raw_caret.on_text_motion(motion)
                if motion in {pyglet.window.key.MOTION_BACKSPACE, pyglet.window.key.MOTION_DELETE}:
                    self._raw_modified = True
                self.invalid = True

        def on_text_motion_select(self, motion: int) -> None:
            if self._tab == "raw" and self._raw_active and self._raw_caret is not None:
                self._raw_caret.on_text_motion_select(motion)
                self.invalid = True

        def on_mouse_drag(self, x: int, y: int, dx: int, dy: int, buttons: int, modifiers: int) -> None:
            if self._tab == "raw" and self._raw_active and self._raw_caret is not None:
                self._raw_caret.on_mouse_drag(x, y, dx, dy, buttons, modifiers)
                self.invalid = True

        def on_close(self) -> None:
            try:
                self._on_closed()
            finally:
                super().on_close()


    return WorldgenEditorWindow(
        get_stack=get_stack,
        on_regrow=on_regrow,
        on_closed=on_closed,
        font_name=font_name,
    )
