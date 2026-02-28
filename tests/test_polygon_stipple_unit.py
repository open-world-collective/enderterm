from __future__ import annotations

from types import ModuleType


def _bitcount(data: bytes) -> int:
    return sum(int(b).bit_count() for b in data)


def _stipple_rows(data: bytes) -> list[int]:
    rows: list[int] = []
    for y in range(32):
        base = y * 4
        rows.append(int(data[base]) | (int(data[base + 1]) << 8) | (int(data[base + 2]) << 16) | (int(data[base + 3]) << 24))
    return rows


def _hash32_u32_reference(value: int) -> int:
    hashed = int(value) & 0xFFFFFFFF
    hashed ^= hashed >> 16
    hashed = (hashed * 0x7FEB352D) & 0xFFFFFFFF
    hashed ^= hashed >> 15
    hashed = (hashed * 0x846CA68B) & 0xFFFFFFFF
    hashed ^= hashed >> 16
    return hashed & 0xFFFFFFFF


def _style1_reference_pattern(
    *,
    level: int,
    phase_x: int,
    phase_y: int,
    seed: int,
    cell: int,
) -> bytes:
    lvl = max(0, min(64, int(level)))
    cell_px = max(1, min(8, int(cell)))
    s = int(seed) & 0xFFFFFFFF
    px = int(phase_x) & 0xFFFFFFFF
    py = int(phase_y) & 0xFFFFFFFF
    mix_base = (s ^ (px * 0x9E3779B1) ^ (py * 0x85EBCA6B)) & 0xFFFFFFFF

    out = bytearray(128)
    for y in range(32):
        row_bits = 0
        cy = (y // cell_px) & 0xFFFFFFFF
        x0 = 0
        while x0 < 32:
            cx = (x0 // cell_px) & 0xFFFFFFFF
            hv = _hash32_u32_reference(mix_base ^ (cx * 0xD1B54A35) ^ (cy * 0x94D049BB))
            if int(hv & 63) < lvl:
                x1 = min(32, x0 + cell_px)
                for x in range(x0, x1):
                    row_bits |= 1 << x
            x0 += cell_px
        base = int(y) * 4
        out[base + 0] = row_bits & 0xFF
        out[base + 1] = (row_bits >> 8) & 0xFF
        out[base + 2] = (row_bits >> 16) & 0xFF
        out[base + 3] = (row_bits >> 24) & 0xFF
    return bytes(out)


def _is_circular_run(bits: int, width: int) -> bool:
    for start in range(32):
        mask = 0
        for i in range(width):
            mask |= 1 << ((start + i) & 31)
        if int(bits) == mask:
            return True
    return False


def test_polygon_stipple_pattern_covers_modes_and_is_deterministic(nbttool: ModuleType) -> None:
    zeros = b"\x00" * 128
    ones = b"\xFF" * 128

    # Ordered dither (Bayer).
    assert bytes(nbttool._polygon_stipple_pattern(-5, style=0)) == zeros
    assert bytes(nbttool._polygon_stipple_pattern(999, style=0)) == ones

    # Single-square mask.
    assert bytes(nbttool._polygon_stipple_pattern(0, style=2)) == zeros
    assert bytes(nbttool._polygon_stipple_pattern(64, style=2)) == ones
    sq1 = bytes(nbttool._polygon_stipple_pattern(32, style=2, seed=123, phase_x=7, phase_y=9, square_jitter=2))
    sq2 = bytes(nbttool._polygon_stipple_pattern(32, style=2, seed=123, phase_x=7, phase_y=9, square_jitter=2))
    assert sq1 == sq2

    # Static noise (default branch).
    assert bytes(nbttool._polygon_stipple_pattern(0, style=1, seed=0x1234, cell=0)) == zeros
    assert bytes(nbttool._polygon_stipple_pattern(64, style=1, seed=0x1234, cell=99)) == ones
    lo = bytes(nbttool._polygon_stipple_pattern(16, style=1, seed=0x1234, cell=4))
    hi = bytes(nbttool._polygon_stipple_pattern(48, style=1, seed=0x1234, cell=4))
    assert _bitcount(lo) < _bitcount(hi)


def test_polygon_stipple_pattern_clamps_square_params(nbttool: ModuleType) -> None:
    zeros = b"\x00" * 128
    ones = b"\xFF" * 128

    # Exercise square_exp coercion + clamping.
    assert bytes(nbttool._polygon_stipple_pattern(63, style=2, square_exp=1.0)) == ones
    assert bytes(nbttool._polygon_stipple_pattern(1, style=2, square_exp=16.0)) == zeros
    assert bytes(nbttool._polygon_stipple_pattern(32, style=2, square_exp=float("nan"))) == bytes(
        nbttool._polygon_stipple_pattern(32, style=2, square_exp=1.0)
    )
    assert bytes(nbttool._polygon_stipple_pattern(32, style=2, square_exp=999.0)) == bytes(
        nbttool._polygon_stipple_pattern(32, style=2, square_exp=16.0)
    )

    # Exercise square_jitter clamping.
    base = bytes(nbttool._polygon_stipple_pattern(32, style=2, seed=1, phase_x=2, phase_y=3))
    assert bytes(nbttool._polygon_stipple_pattern(32, style=2, square_jitter=-5, seed=1, phase_x=2, phase_y=3)) == base
    assert bytes(nbttool._polygon_stipple_pattern(32, style=2, square_jitter=999, seed=1, phase_x=2, phase_y=3)) == bytes(
        nbttool._polygon_stipple_pattern(32, style=2, square_jitter=32, seed=1, phase_x=2, phase_y=3)
    )


def test_polygon_stipple_square_rows_share_single_mask(nbttool: ModuleType) -> None:
    # For style=2, every filled row of the square uses the same X-span bitmask.
    data = bytes(nbttool._polygon_stipple_pattern(32, style=2, seed=7, phase_x=11, phase_y=5, square_exp=1.0))
    rows = _stipple_rows(data)
    filled = [row for row in rows if row != 0]

    assert len(filled) == 16
    assert len(set(filled)) == 1
    assert int(filled[0]).bit_count() == 16
    assert _is_circular_run(int(filled[0]), 16)


def test_polygon_stipple_square_rows_form_contiguous_wraparound_band(nbttool: ModuleType) -> None:
    data = bytes(nbttool._polygon_stipple_pattern(32, style=2, seed=9, phase_x=13, phase_y=29, square_exp=1.0))
    rows = _stipple_rows(data)
    filled_rows = [y for y, bits in enumerate(rows) if bits != 0]
    filled_set = set(filled_rows)

    assert len(filled_rows) == 16
    assert len(filled_set) == 16
    assert any(all(((start + offset) & 31) in filled_set for offset in range(16)) for start in filled_set)


def test_polygon_stipple_static_noise_is_deterministic_for_same_inputs(nbttool: ModuleType) -> None:
    p1 = bytes(nbttool._polygon_stipple_pattern(37, style=1, seed=0xABCDEF01, phase_x=5, phase_y=17, cell=3))
    p2 = bytes(nbttool._polygon_stipple_pattern(37, style=1, seed=0xABCDEF01, phase_x=5, phase_y=17, cell=3))
    assert p1 == p2


def test_polygon_stipple_static_noise_matches_reference_cell_fill(nbttool: ModuleType) -> None:
    params = [
        {"level": 13, "phase_x": 1, "phase_y": 2, "seed": 0x11111111, "cell": 1},
        {"level": 31, "phase_x": 5, "phase_y": 7, "seed": 0xABCDEF01, "cell": 3},
        {"level": 47, "phase_x": 17, "phase_y": 9, "seed": 0x2468ACE0, "cell": 4},
        {"level": 63, "phase_x": 29, "phase_y": 31, "seed": 0xDEADBEEF, "cell": 8},
    ]
    for p in params:
        expected = _style1_reference_pattern(**p)
        got = bytes(
            nbttool._polygon_stipple_pattern(
                p["level"],
                style=1,
                phase_x=p["phase_x"],
                phase_y=p["phase_y"],
                seed=p["seed"],
                cell=p["cell"],
            )
        )
        assert got == expected


def test_polygon_stipple_square_high_coverage_wraps_rows_without_gaps(nbttool: ModuleType) -> None:
    # level=62 with square_exp=1.0 produces side=31, so this stresses near-full
    # row fill with wrap-around row writing.
    data = bytes(nbttool._polygon_stipple_pattern(62, style=2, seed=11, phase_x=3, phase_y=31, square_exp=1.0))
    rows = _stipple_rows(data)
    filled_rows = [y for y, bits in enumerate(rows) if bits != 0]
    filled_set = set(filled_rows)

    assert len(filled_rows) == 31
    assert len(filled_set) == 31
    assert len([y for y, bits in enumerate(rows) if bits == 0]) == 1
    first_bits = rows[filled_rows[0]]
    assert all(rows[y] == first_bits for y in filled_rows)
    assert int(first_bits).bit_count() == 31
    assert _is_circular_run(int(first_bits), 31)
