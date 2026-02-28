from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

_DEFAULT_TICK_RATE = 20.0


@dataclass(frozen=True, slots=True)
class TextureAnimationFrame:
    index: int
    time: int


@dataclass(frozen=True, slots=True)
class TextureAnimationSpec:
    image_width: int
    image_height: int
    frame_width: int
    frame_height: int
    grid_cols: int
    grid_rows: int
    sheet_frame_count: int
    frames: tuple[TextureAnimationFrame, ...]
    total_ticks: int


def _normalize_frame_dimensions(
    *,
    anim_obj: dict[str, Any],
    image_width: int,
    image_height: int,
) -> tuple[int, int]:
    frame_w = _positive_int(anim_obj.get("width"), image_width)
    frame_w = max(1, min(int(frame_w), int(image_width)))
    frame_h = _positive_int(anim_obj.get("height"), frame_w)
    frame_h = max(1, min(int(frame_h), int(image_height)))
    return (int(frame_w), int(frame_h))


def _sheet_grid_shape(*, image_width: int, image_height: int, frame_width: int, frame_height: int) -> tuple[int, int, int]:
    grid_cols = max(1, int(image_width) // int(frame_width))
    grid_rows = max(1, int(image_height) // int(frame_height))
    sheet_count = max(1, int(grid_cols) * int(grid_rows))
    return (int(grid_cols), int(grid_rows), int(sheet_count))


def _default_frame_sequence(*, sheet_count: int, frame_time: int) -> tuple[TextureAnimationFrame, ...]:
    return tuple(TextureAnimationFrame(index=i, time=int(frame_time)) for i in range(int(sheet_count)))


def _coerce_frame_entries(
    *,
    frames_obj: object,
    default_frame_time: int,
    sheet_count: int,
) -> tuple[TextureAnimationFrame, ...]:
    if not isinstance(frames_obj, list):
        return tuple()
    out: list[TextureAnimationFrame] = []
    for raw in frames_obj:
        frame = _coerce_frame_entry(raw, default_frame_time=int(default_frame_time), sheet_count=int(sheet_count))
        if frame is not None:
            out.append(frame)
    return tuple(out)


def _total_duration_ticks(frames: tuple[TextureAnimationFrame, ...]) -> int:
    total_ticks = sum(_frame_duration_ticks(fr) for fr in frames)
    if total_ticks <= 0:
        return 1
    return int(total_ticks)


def _coerce_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        out = int(value)
    except Exception:
        return None
    return int(out)


def _positive_int(value: Any, default: int) -> int:
    out = _coerce_int_or_none(value)
    if out is None:
        return int(default)
    if out <= 0:
        return int(default)
    return int(out)


def _coerce_frame_index(index_raw: Any, *, sheet_count: int) -> int | None:
    index = _coerce_int_or_none(index_raw)
    if index is None:
        return None
    if index < 0 or index >= int(sheet_count):
        return None
    return int(index)


def _frame_duration_ticks(frame: TextureAnimationFrame) -> int:
    return max(1, int(frame.time))


def _coerce_finite_float(value: Any, *, default: float) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    if not math.isfinite(out):
        return float(default)
    return float(out)


def _coerce_elapsed_seconds(elapsed_seconds: Any) -> float:
    elapsed = _coerce_finite_float(elapsed_seconds, default=0.0)
    if elapsed < 0.0:
        return 0.0
    return elapsed


def _coerce_tick_rate(tick_rate: Any) -> float:
    hz = _coerce_finite_float(tick_rate, default=_DEFAULT_TICK_RATE)
    if hz <= 0.0:
        return float(_DEFAULT_TICK_RATE)
    return hz


def _cycle_tick_for_elapsed(spec: TextureAnimationSpec, *, elapsed_seconds: Any, tick_rate: Any) -> int:
    elapsed = _coerce_elapsed_seconds(elapsed_seconds)
    hz = _coerce_tick_rate(tick_rate)
    elapsed_ticks = int(math.floor(elapsed * hz))
    return int(elapsed_ticks) % int(max(1, spec.total_ticks))


def _sequence_position_for_cycle(spec: TextureAnimationSpec, *, cycle_tick: int) -> int:
    accumulated = 0
    for sequence_pos, frame in enumerate(spec.frames):
        accumulated += _frame_duration_ticks(frame)
        if cycle_tick < accumulated:
            return int(sequence_pos)
    return max(0, len(spec.frames) - 1)


def _sequence_pos_mod(spec: TextureAnimationSpec, *, sequence_pos: int) -> int:
    return int(sequence_pos) % len(spec.frames)


def _parse_animation_obj(mcmeta_bytes: bytes | None) -> dict[str, Any]:
    if not mcmeta_bytes:
        return {}
    try:
        obj = json.loads(mcmeta_bytes.decode("utf-8"))
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}
    anim = obj.get("animation")
    if not isinstance(anim, dict):
        return {}
    return anim


def _coerce_frame_entry(raw: Any, *, default_frame_time: int, sheet_count: int) -> TextureAnimationFrame | None:
    if isinstance(raw, dict):
        frame_index_raw: Any = raw.get("index")
        frame_time_raw: Any = raw.get("time", default_frame_time)
    else:
        frame_index_raw = raw
        frame_time_raw = default_frame_time

    frame_index = _coerce_frame_index(frame_index_raw, sheet_count=int(sheet_count))
    if frame_index is None:
        return None
    frame_time = _positive_int(frame_time_raw, default_frame_time)
    return TextureAnimationFrame(index=int(frame_index), time=int(frame_time))


def build_texture_animation_spec(*, image_width: int, image_height: int, mcmeta_bytes: bytes | None) -> TextureAnimationSpec:
    img_w = max(1, int(image_width))
    img_h = max(1, int(image_height))
    anim = _parse_animation_obj(mcmeta_bytes)

    frametime = _positive_int(anim.get("frametime"), 1)
    frame_w, frame_h = _normalize_frame_dimensions(anim_obj=anim, image_width=img_w, image_height=img_h)
    grid_cols, grid_rows, sheet_count = _sheet_grid_shape(
        image_width=img_w,
        image_height=img_h,
        frame_width=frame_w,
        frame_height=frame_h,
    )

    frames = _coerce_frame_entries(
        frames_obj=anim.get("frames"),
        default_frame_time=int(frametime),
        sheet_count=int(sheet_count),
    )
    if not frames:
        frames = _default_frame_sequence(sheet_count=int(sheet_count), frame_time=int(frametime))

    total_ticks = _total_duration_ticks(frames)

    return TextureAnimationSpec(
        image_width=int(img_w),
        image_height=int(img_h),
        frame_width=int(frame_w),
        frame_height=int(frame_h),
        grid_cols=int(grid_cols),
        grid_rows=int(grid_rows),
        sheet_frame_count=int(sheet_count),
        frames=tuple(frames),
        total_ticks=int(total_ticks),
    )


def frame_sequence_pos_for_elapsed(spec: TextureAnimationSpec, *, elapsed_seconds: float, tick_rate: float = 20.0) -> int:
    if not spec.frames:
        return 0
    if len(spec.frames) <= 1:
        return 0
    cycle_tick = _cycle_tick_for_elapsed(spec, elapsed_seconds=elapsed_seconds, tick_rate=tick_rate)
    return _sequence_position_for_cycle(spec, cycle_tick=int(cycle_tick))


def frame_sheet_index(spec: TextureAnimationSpec, *, sequence_pos: int) -> int:
    if not spec.frames:
        return 0
    pos = _sequence_pos_mod(spec, sequence_pos=int(sequence_pos))
    return int(spec.frames[pos].index)


def frame_rect_top_left(spec: TextureAnimationSpec, *, sequence_pos: int) -> tuple[int, int, int, int]:
    idx = int(frame_sheet_index(spec, sequence_pos=int(sequence_pos)))
    col = idx % max(1, int(spec.grid_cols))
    row = idx // max(1, int(spec.grid_cols))
    x = int(col) * int(spec.frame_width)
    y = int(row) * int(spec.frame_height)
    return (int(x), int(y), int(spec.frame_width), int(spec.frame_height))


def frame_rect_bottom_left(spec: TextureAnimationSpec, *, sequence_pos: int) -> tuple[int, int, int, int]:
    x, y_top, w, h = frame_rect_top_left(spec, sequence_pos=int(sequence_pos))
    y = int(spec.image_height) - int(y_top) - int(h)
    if y < 0:
        y = 0
    return (int(x), int(y), int(w), int(h))


def uses_subframes(spec: TextureAnimationSpec) -> bool:
    return bool(int(spec.frame_width) != int(spec.image_width) or int(spec.frame_height) != int(spec.image_height))
