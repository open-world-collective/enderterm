from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType


@pytest.mark.parametrize(
    ("turns", "expected"),
    [
        (0, (1, 2, 3)),
        (1, (3, 2, -1)),
        (2, (-1, 2, -3)),
        (3, (-3, 2, 1)),
        (-1, (-3, 2, 1)),
    ],
)
def test_rotate_y_vec_rotates_in_90_degree_quarters(
    nbttool: ModuleType, turns: int, expected: tuple[int, int, int]
) -> None:
    v = (1, 2, 3)
    assert nbttool._rotate_y_vec(v, turns) == expected


@pytest.mark.parametrize(
    ("turns", "expected"),
    [
        (0, (1.5, 2.0, -3.0)),
        (1, (-3.0, 2.0, -1.5)),
    ],
)
def test_rotate_y_vec_f_rotates_float_vectors(
    nbttool: ModuleType, turns: int, expected: tuple[float, float, float]
) -> None:
    assert nbttool._rotate_y_vec_f((1.5, 2.0, -3.0), turns) == expected


def test_vec_add_adds_componentwise(nbttool: ModuleType) -> None:
    assert nbttool._vec_add((1, 2, 3), (4, -5, 6)) == (5, -3, 9)


def test_vec_neg_negates_componentwise(nbttool: ModuleType) -> None:
    assert nbttool._vec_neg((1, -2, 3)) == (-1, 2, -3)


@pytest.mark.parametrize(
    ("base_y", "thickness", "extra", "expected"),
    [
        (10, 0, 0, 10),
        (10, 2, 0, 11),
    ],
)
def test_height_stack_top_y_has_min_height_of_one(
    nbttool: ModuleType, base_y: int, thickness: int, extra: int, expected: int
) -> None:
    assert nbttool._height_stack_top_y(base_y=base_y, thickness=thickness, extra=extra) == expected
