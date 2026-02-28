from __future__ import annotations

from enderterm.termui import TermScroll, TermScrollbar, TerminalSurface


def test_terminal_surface_put_fill_rect_and_draw_box() -> None:
    surf = TerminalSurface(cols=5, rows=4, default_fg=(1, 2, 3, 4), default_bg=(0, 0, 0, 0))
    surf.put(0, 0, "hi")
    assert surf.cell(0, 0) is not None and surf.cell(0, 0).ch == "h"
    assert surf.cell(1, 0) is not None and surf.cell(1, 0).ch == "i"

    surf.fill_rect(0, 1, 2, 2, ch="x")
    assert surf.cell(0, 1) is not None and surf.cell(0, 1).ch == "x"
    assert surf.cell(1, 2) is not None and surf.cell(1, 2).ch == "x"

    surf.draw_box(1, 1, 4, 3, title="T")
    assert surf.cell(1, 1) is not None and surf.cell(1, 1).ch == "┌"
    assert surf.cell(4, 1) is not None and surf.cell(4, 1).ch == "┐"
    assert surf.cell(1, 3) is not None and surf.cell(1, 3).ch == "└"
    assert surf.cell(4, 3) is not None and surf.cell(4, 3).ch == "┘"


def test_terminal_surface_color_normalization_is_shared_across_draw_paths() -> None:
    surf = TerminalSurface(cols=5, rows=5, default_fg=(11, 12, 13, 14), default_bg=(1, 2, 3, 4))

    surf.put(0, 0, "a", fg=(1.9, 2.1, 3.8, 4.2), bg=(9.9, 8.1, 7.2, 6.8))  # type: ignore[arg-type]
    c_put = surf.cell(0, 0)
    assert c_put is not None
    assert c_put.fg == (1, 2, 3, 4)
    assert c_put.bg == (9, 8, 7, 6)

    surf.fill_rect(1, 1, 1, 1, ch="x", fg=(5.9, 6.1, 7.5, 8.9), bg=(4.9, 3.1, 2.2, 1.7))  # type: ignore[arg-type]
    c_fill = surf.cell(1, 1)
    assert c_fill is not None
    assert c_fill.fg == (5, 6, 7, 8)
    assert c_fill.bg == (4, 3, 2, 1)

    surf.draw_box(0, 2, 3, 3, fg=(8.9, 7.1, 6.8, 5.2), bg=(0.9, 1.9, 2.9, 3.9))  # type: ignore[arg-type]
    c_box = surf.cell(0, 2)
    assert c_box is not None
    assert c_box.fg == (8, 7, 6, 5)
    assert c_box.bg == (0, 1, 2, 3)


def test_terminal_surface_hotspots_basic() -> None:
    surf = TerminalSurface(cols=4, rows=3, default_fg=(255, 255, 255, 255), default_bg=(0, 0, 0, 0))
    surf.add_hotspot(x=1, y=1, w=2, h=2, kind="pick", payload=123)
    assert surf.hotspot_at(0, 0) is None
    hs = surf.hotspot_at(1, 1)
    assert hs is not None
    assert hs.kind == "pick"
    assert hs.payload == 123
    # Second hotspot should override the first where they overlap.
    surf.add_hotspot(x=2, y=2, w=2, h=1, kind="pick", payload=456)
    hs2 = surf.hotspot_at(2, 2)
    assert hs2 is not None
    assert hs2.payload == 456
    surf.clear()
    assert surf.hotspot_at(1, 1) is None


def test_terminal_surface_shared_rect_clipping_for_fill_and_hotspot() -> None:
    surf = TerminalSurface(cols=4, rows=3, default_fg=(255, 255, 255, 255), default_bg=(0, 0, 0, 0))

    surf.fill_rect(-2, -1, 3, 3, ch="z")
    assert surf.cell(0, 0) is not None and surf.cell(0, 0).ch == "z"
    assert surf.cell(0, 1) is not None and surf.cell(0, 1).ch == "z"
    assert surf.cell(1, 0) is not None and surf.cell(1, 0).ch == " "

    surf.add_hotspot(x=-2, y=-1, w=3, h=3, kind="clip", payload=7)
    hs = surf.hotspot_at(0, 0)
    assert hs is not None
    assert (hs.x, hs.y, hs.w, hs.h) == (0, 0, 1, 2)
    assert hs.payload == 7
    assert surf.hotspot_at(1, 0) is None


def test_term_scroll_clamp_scroll_and_ensure_visible() -> None:
    s = TermScroll()
    assert s.clamp(0) == 0
    assert s.pos_f == 0.0

    s.pos_f = 10.0
    assert s.clamp(3) == 3
    assert s.pos_f == 3.0

    s.pos_f = 0.0
    assert s.scroll_wheel(-2.0, max_scroll=10) == 2
    assert s.pos_f == 2.0
    assert s.follow_selection is False

    s.follow_selection = True
    s.pos_f = 0.0
    assert s.ensure_visible(5, visible=3, max_scroll=10) == 3
    assert s.pos_f == 3.0

    s.pos_f = 6.0
    assert s.ensure_visible(2, visible=3, max_scroll=10) == 2
    assert s.pos_f == 2.0

    s.pos_f = 9.0
    assert s.ensure_visible(None, visible=3, max_scroll=10) == 0
    assert s.pos_f == 0.0


def test_term_scroll_clamp_preserves_fractional_in_range_and_clamps_bounds() -> None:
    s = TermScroll()

    s.pos_f = -2.75
    assert s.clamp(10) == 0
    assert s.pos_f == 0.0

    s.pos_f = 3.9
    assert s.clamp(10) == 3
    assert s.pos_f == 3.9

    s.pos_f = 99.1
    assert s.clamp(7) == 7
    assert s.pos_f == 7.0


def test_term_scrollbar_hit_testing_and_track_click() -> None:
    sb = TermScrollbar()
    sb.update(track_top=10, track_rows=12, visible_rows=5, total_rows=30, scroll_top=7)
    assert sb.thumb_rows >= 1
    assert sb.hit_test(row=9) is None
    assert sb.hit_test(row=10) in {"track_before", "thumb"}
    assert sb.hit_test(row=sb.thumb_top) == "thumb"
    assert sb.hit_test(row=sb.thumb_top + sb.thumb_rows) in {"track_after", None}

    cur = 10.0
    up = sb.track_click(row=sb.thumb_top - 1, current_scroll=cur)
    down = sb.track_click(row=sb.thumb_top + sb.thumb_rows + 1, current_scroll=cur)
    assert up is not None and down is not None
    assert up < cur
    assert down > cur


def test_term_scrollbar_drag_lifecycle() -> None:
    sb = TermScrollbar()
    sb.update(track_top=0, track_rows=20, visible_rows=5, total_rows=100, scroll_top=40)
    assert sb.begin_drag(row=sb.thumb_top)
    assert sb.drag_active is True

    before = sb.drag_to(row=sb.thumb_top)
    assert before is not None
    after = sb.drag_to(row=sb.thumb_top + 5)
    assert after is not None
    assert after > before

    sb.end_drag()
    assert sb.drag_active is False
    assert sb.drag_to(row=sb.thumb_top) is None


def test_term_scrollbar_mixed_wheel_and_drag_behavior() -> None:
    scroll = TermScroll()
    sb = TermScrollbar()
    max_scroll = 80

    # Wheel input should disable follow-selection and move the list first.
    scroll.scroll_wheel(-3.0, max_scroll=max_scroll)
    assert scroll.follow_selection is False
    sb.update(track_top=0, track_rows=16, visible_rows=6, total_rows=max_scroll + 6, scroll_top=int(scroll.pos_f))

    assert sb.begin_drag(row=sb.thumb_top)
    moved = sb.drag_to(row=sb.thumb_top + 6)
    assert moved is not None
    scroll.pos_f = moved
    assert 0.0 <= scroll.pos_f <= float(max_scroll)

    sb.end_drag()
    # Track click after drag should still be monotonic and bounded.
    clicked = sb.track_click(row=sb.thumb_top + sb.thumb_rows + 2, current_scroll=scroll.pos_f)
    assert clicked is not None
    assert clicked >= scroll.pos_f
    assert clicked <= float(max_scroll)


def test_term_scrollbar_thumb_press_keeps_single_click_path() -> None:
    sb = TermScrollbar()
    sb.update(track_top=4, track_rows=12, visible_rows=4, total_rows=40, scroll_top=10)
    row = int(sb.thumb_top)

    drag_started, new_scroll, consumed = sb.press(row=row, current_scroll=10.0)

    assert drag_started is True
    assert sb.drag_active is True
    assert new_scroll is None
    # Thumb press should not swallow the list's first-click selection path.
    assert consumed is False


def test_term_scrollbar_track_press_is_consumed_and_pages() -> None:
    sb = TermScrollbar()
    sb.update(track_top=0, track_rows=16, visible_rows=5, total_rows=45, scroll_top=6)
    row = int(sb.thumb_top + sb.thumb_rows + 1)

    drag_started, new_scroll, consumed = sb.press(row=row, current_scroll=6.0)

    assert drag_started is False
    assert consumed is True
    assert new_scroll is not None
    assert new_scroll > 6.0


def test_term_scrollbar_track_click_clamps_out_of_range_scroll_inputs() -> None:
    sb = TermScrollbar()
    sb.update(track_top=0, track_rows=16, visible_rows=5, total_rows=45, scroll_top=20)

    up = sb.track_click(row=int(sb.thumb_top - 1), current_scroll=-999.0)
    down = sb.track_click(row=int(sb.thumb_top + sb.thumb_rows + 1), current_scroll=999.0)

    assert up == 0.0
    assert down == float(sb.max_scroll)
