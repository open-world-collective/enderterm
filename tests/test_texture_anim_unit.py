from __future__ import annotations

from enderterm.texture_anim import (
    build_texture_animation_spec,
    frame_rect_bottom_left,
    frame_rect_top_left,
    frame_sequence_pos_for_elapsed,
    frame_sheet_index,
    uses_subframes,
)


def _build_spec(
    *,
    image_width: int,
    image_height: int,
    mcmeta_bytes: bytes | None = None,
):
    return build_texture_animation_spec(
        image_width=image_width,
        image_height=image_height,
        mcmeta_bytes=mcmeta_bytes,
    )


def test_vertical_strip_defaults_to_square_frames_with_mcmeta_frametime() -> None:
    spec = _build_spec(
        image_width=16,
        image_height=288,
        mcmeta_bytes=b'{"animation":{"frametime":2}}',
    )
    assert spec.frame_width == 16
    assert spec.frame_height == 16
    assert spec.sheet_frame_count == 18
    assert len(spec.frames) == 18
    assert spec.total_ticks == 36
    assert frame_rect_top_left(spec, sequence_pos=0) == (0, 0, 16, 16)
    assert frame_rect_top_left(spec, sequence_pos=17) == (0, 272, 16, 16)
    assert frame_rect_bottom_left(spec, sequence_pos=0) == (0, 272, 16, 16)


def test_mcmeta_frame_sequence_and_timing() -> None:
    spec = _build_spec(
        image_width=16,
        image_height=48,
        mcmeta_bytes=b'{"animation":{"frametime":2,"frames":[0,{"index":2,"time":4},1]}}',
    )
    assert [fr.index for fr in spec.frames] == [0, 2, 1]
    assert [fr.time for fr in spec.frames] == [2, 4, 2]
    assert spec.total_ticks == 8

    pos0 = frame_sequence_pos_for_elapsed(spec, elapsed_seconds=0.00, tick_rate=20.0)
    pos1 = frame_sequence_pos_for_elapsed(spec, elapsed_seconds=0.10, tick_rate=20.0)  # 2 ticks
    pos2 = frame_sequence_pos_for_elapsed(spec, elapsed_seconds=0.30, tick_rate=20.0)  # 6 ticks
    pos3 = frame_sequence_pos_for_elapsed(spec, elapsed_seconds=0.40, tick_rate=20.0)  # 8 ticks -> wrap

    assert frame_sheet_index(spec, sequence_pos=pos0) == 0
    assert frame_sheet_index(spec, sequence_pos=pos1) == 2
    assert frame_sheet_index(spec, sequence_pos=pos2) == 1
    assert frame_sheet_index(spec, sequence_pos=pos3) == 0


def test_non_square_frame_dimensions_and_rect_slicing() -> None:
    spec = _build_spec(
        image_width=32,
        image_height=48,
        mcmeta_bytes=b'{"animation":{"width":16,"height":24}}',
    )
    assert spec.frame_width == 16
    assert spec.frame_height == 24
    assert spec.grid_cols == 2
    assert spec.grid_rows == 2
    assert spec.sheet_frame_count == 4
    assert uses_subframes(spec) is True
    assert frame_rect_top_left(spec, sequence_pos=3) == (16, 24, 16, 24)
    assert frame_rect_bottom_left(spec, sequence_pos=3) == (16, 0, 16, 24)


def test_invalid_mcmeta_frames_fall_back_to_sheet_order() -> None:
    spec = _build_spec(
        image_width=16,
        image_height=32,
        mcmeta_bytes=b'{"animation":{"frames":[5,{"index":-1},{},false]}}',
    )
    assert [fr.index for fr in spec.frames] == [0, 1]


def test_frame_sequence_elapsed_and_tick_rate_sanitize_invalid_values() -> None:
    spec = _build_spec(
        image_width=16,
        image_height=32,
        mcmeta_bytes=b'{"animation":{"frames":[0,1]}}',
    )

    assert frame_sequence_pos_for_elapsed(spec, elapsed_seconds=float("nan"), tick_rate=20.0) == 0
    assert frame_sequence_pos_for_elapsed(spec, elapsed_seconds=-1.0, tick_rate=20.0) == 0
    assert frame_sequence_pos_for_elapsed(spec, elapsed_seconds=0.05, tick_rate=float("nan")) == 1
    assert frame_sequence_pos_for_elapsed(spec, elapsed_seconds=0.05, tick_rate=0.0) == 1
    assert frame_sequence_pos_for_elapsed(spec, elapsed_seconds="bad", tick_rate="bad") == 0  # type: ignore[arg-type]


def test_mcmeta_frame_time_coercion_with_mixed_validity_entries() -> None:
    spec = _build_spec(
        image_width=16,
        image_height=48,
        mcmeta_bytes=(
            b'{"animation":{"frametime":3,"frames":['
            b'{"index":0,"time":0},'
            b'{"index":1,"time":"x"},'
            b'{"index":true,"time":7},'
            b'{"index":2,"time":-5}'
            b']}}'
        ),
    )

    assert [fr.index for fr in spec.frames] == [0, 1, 2]
    assert [fr.time for fr in spec.frames] == [3, 3, 3]
    assert spec.total_ticks == 9


def test_single_frame_sequence_is_stable_and_negative_sequence_wraps() -> None:
    spec = _build_spec(image_width=16, image_height=16, mcmeta_bytes=None)
    assert len(spec.frames) == 1
    assert frame_sequence_pos_for_elapsed(spec, elapsed_seconds=1234.5, tick_rate=20.0) == 0
    assert frame_sheet_index(spec, sequence_pos=-999) == 0


def test_frame_dimensions_and_frametime_are_clamped_to_valid_ranges() -> None:
    spec = _build_spec(
        image_width=16,
        image_height=16,
        mcmeta_bytes=b'{"animation":{"width":64,"height":0,"frametime":0}}',
    )
    assert spec.frame_width == 16
    assert spec.frame_height == 16
    assert [fr.time for fr in spec.frames] == [1]
    assert spec.total_ticks == 1
