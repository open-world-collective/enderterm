from __future__ import annotations

import pytest

from enderterm.util import _stable_seed


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (("hello", 123, (1, 2, 3)), 3213538802771895931),
        ((), 15724779818122431245),
        (("a", "b"), 3580599379204363428),
        (("b", "a"), 11560781695469923261),
        (("1",), 10856159797040954865),
        ((1,), 15963469267972788103),
    ],
)
def test_stable_seed_matches_known_vectors(parts: tuple[object, ...], expected: int) -> None:
    assert _stable_seed(*parts) == expected


def test_stable_seed_changes_when_inputs_change() -> None:
    base_seed = _stable_seed("hello", 123, (1, 2, 3))
    assert _stable_seed("hello", 124, (1, 2, 3)) != base_seed


def test_stable_seed_uses_repr_for_parts() -> None:
    assert _stable_seed("1") != _stable_seed(1)
