from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType


def _make_block(nbttool: ModuleType, *, pos: tuple[int, int, int], block_id: str):
    return nbttool.BlockInstance(pos=pos, block_id=block_id, color_key=block_id)


def _make_connector(
    nbttool: ModuleType,
    *,
    pos: tuple[int, int, int],
    front: tuple[int, int, int],
    top: tuple[int, int, int] = (0, 1, 0),
    pool: str = "poolA",
    target: str = "socket",
    name: str = "socket",
    final_state: str = "minecraft:air",
    joint: str = "aligned",
    source: str = "tmpl",
    projection: str = "rigid",
):
    return nbttool.JigsawConnector(
        pos=pos,
        front=front,
        top=top,
        projection=projection,
        pool=pool,
        target=target,
        name=name,
        final_state=final_state,
        joint=joint,
        source=source,
    )


@dataclass(frozen=True)
class _FakeIndex:
    nbttool: ModuleType
    pools: dict[str, object]
    templates: dict[str, object]

    def load_pool(self, pool_id: str):
        return self.pools[pool_id]

    def load_template(self, location_id: str):
        return self.templates.get(location_id)

    def load_processor_list(self, _processor_id: str):
        return self.nbttool.ProcessorPipeline(ignore_base=frozenset(), processors=tuple(), unhandled_processors=tuple())


def test_build_jigsaw_expanded_structure_supports_terrain_matching_and_callbacks(
    nbttool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_blocks = []
    for x in range(2):
        for z in range(4):
            base_blocks.append(_make_block(nbttool, pos=(x, 0, z), block_id="minecraft:stone"))
    base_blocks.append(_make_block(nbttool, pos=(1, 1, 1), block_id="minecraft:jigsaw"))

    base = nbttool.StructureTemplate(
        template_id="base",
        size=(2, 2, 4),
        blocks=tuple(base_blocks),
        connectors=(
            _make_connector(
                nbttool,
                pos=(1, 1, 1),
                front=(1, 0, 0),
                top=(0, 1, 0),
                pool="poolA",
                target="socket",
                name="parent",
                final_state="minecraft:air",
                joint="aligned",
                source="base",
                projection="terrain_matching",
            ),
        ),
    )

    child = nbttool.StructureTemplate(
        template_id="child",
        size=(2, 2, 1),
        blocks=(
            _make_block(nbttool, pos=(0, 1, 0), block_id="minecraft:jigsaw"),
            _make_block(nbttool, pos=(1, 0, 0), block_id="minecraft:stone"),
        ),
        connectors=(
            _make_connector(
                nbttool,
                pos=(0, 1, 0),
                front=(-1, 0, 0),
                top=(0, 1, 0),
                pool="minecraft:empty",
                target="minecraft:empty",
                name="socket",
                final_state="minecraft:air",
                joint="aligned",
                source="child",
                projection="terrain_matching",
            ),
        ),
    )

    pool = nbttool.PoolDefinition(
        elements=(
            nbttool.PoolElement(
                location_id="child",
                weight=1,
                processors="minecraft:empty",
                projection="terrain_matching",
            ),
        ),
        fallback="minecraft:empty",
    )
    index = _FakeIndex(nbttool=nbttool, pools={"poolA": pool}, templates={"child": child})

    times = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    monkeypatch.setattr(nbttool.time, "monotonic", lambda: next(times, 999.0))
    monkeypatch.setattr(nbttool.time, "sleep", lambda _s: None)

    progress_calls: list[tuple[float, str]] = []
    piece_calls: list[tuple[str, int]] = []

    out, report, state = nbttool.build_jigsaw_expanded_structure(
        base,
        seeds=[0x12345678],
        index=index,
        terrain_preset="grassy_hills",
        terrain_seed=123,
        terrain_amp=128,
        throttle_sleep_ms=0.1,
        throttle_every=1,
        total_depth=1,
        progress=lambda frac, msg: progress_calls.append((float(frac), str(msg))),
        piece_callback=lambda blocks, loc: piece_calls.append((str(loc), len(blocks))),
    )

    assert progress_calls
    assert all(0.0 <= f <= 1.0 for f, _ in progress_calls)
    assert any("proj=terrain_matching" in line for line in report)
    assert piece_calls == [("child", 1)]

    state_no_bounds = nbttool.JigsawExpansionState(
        connectors=state.connectors,
        consumed=state.consumed,
        dead_end=state.dead_end,
        piece_bounds=(),
    )
    out2, report2, state2 = nbttool.build_jigsaw_expanded_structure(
        base,
        seeds=[],
        index=index,
        initial_structure=out,
        initial_state=state_no_bounds,
        initial_report=report,
    )
    assert report2 == report
    assert state2.piece_bounds


def _segment_templates_for_projection_tests(nbttool: ModuleType):
    """Return (base, segment, pool, index) for terrain projection regression tests."""

    base = nbttool.StructureTemplate(
        template_id="base",
        size=(1, 1, 1),
        blocks=(nbttool.BlockInstance(pos=(0, 0, 0), block_id="minecraft:jigsaw", color_key="minecraft:jigsaw"),),
        connectors=(
            _make_connector(
                nbttool,
                pos=(0, 0, 0),
                front=(1, 0, 0),
                top=(0, 1, 0),
                pool="poolA",
                target="socket",
                name="start",
                final_state="minecraft:air",
                joint="aligned",
                source="base",
                projection="terrain_matching",
            ),
        ),
    )

    # A simple 3-block "road segment": socket (west) -> stone -> socket (east).
    segment = nbttool.StructureTemplate(
        template_id="segment",
        size=(3, 1, 1),
        blocks=(
            nbttool.BlockInstance(pos=(0, 0, 0), block_id="minecraft:jigsaw", color_key="minecraft:jigsaw"),
            nbttool.BlockInstance(pos=(1, 0, 0), block_id="minecraft:stone", color_key="minecraft:stone"),
            nbttool.BlockInstance(pos=(2, 0, 0), block_id="minecraft:jigsaw", color_key="minecraft:jigsaw"),
        ),
        connectors=(
            # Consumed connector: use empty pool/target so it never becomes "open".
            _make_connector(
                nbttool,
                pos=(0, 0, 0),
                front=(-1, 0, 0),
                top=(0, 1, 0),
                pool="minecraft:empty",
                target="minecraft:empty",
                name="socket",
                final_state="minecraft:air",
                joint="aligned",
                source="segment",
                projection="terrain_matching",
            ),
            # Forward connector stays open so we can place another segment.
            _make_connector(
                nbttool,
                pos=(2, 0, 0),
                front=(1, 0, 0),
                top=(0, 1, 0),
                pool="poolA",
                target="socket",
                name="next",
                final_state="minecraft:air",
                joint="aligned",
                source="segment",
                projection="terrain_matching",
            ),
        ),
    )

    pool = nbttool.PoolDefinition(
        elements=(
            nbttool.PoolElement(
                location_id="segment",
                weight=1,
                processors="minecraft:empty",
                projection="terrain_matching",
            ),
        ),
        fallback="minecraft:empty",
    )
    index = _FakeIndex(nbttool=nbttool, pools={"poolA": pool}, templates={"segment": segment})
    return (base, segment, pool, index)


def test_terrain_matching_projection_preserves_unprojected_y_across_levels(
    nbttool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: terrain-matching connections must not accumulate vertical drift
    # when expanding multiple levels. The easiest way to see drift is to use a
    # deterministic "terrain" where dy == x and ensure later pieces still land
    # on y == dy(x,z) (not dy + prior-piece-y).
    monkeypatch.setattr(nbttool, "env_height_offset", lambda **kw: int(kw.get("x", 0)))

    base, _segment, _pool, index = _segment_templates_for_projection_tests(nbttool)

    piece_stones: list[tuple[str, tuple[int, int, int]]] = []

    def on_piece(blocks, loc: str) -> None:
        stones = [b for b in blocks if b.block_id == "minecraft:stone"]
        assert len(stones) == 1
        piece_stones.append((str(loc), stones[0].pos))

    _out, _report, _state = nbttool.build_jigsaw_expanded_structure(
        base,
        seeds=[0x1, 0x2],
        index=index,
        terrain_preset="grassy_hills",
        terrain_seed=0,
        terrain_anchor_off=0,
        terrain_base_y=0,
        terrain_amp=256,
        total_depth=2,
        piece_callback=on_piece,
    )

    # Segment 1 stone is at world x=2, so dy==2 => y==2.
    # Segment 2 stone is at world x=5, so dy==5 => y==5.
    assert piece_stones == [("segment", (2, 2, 0)), ("segment", (5, 5, 0))]


def test_terrain_matching_child_does_not_inherit_rigid_parent_y(
    nbttool: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: if the parent connector is rigid (floating), a terrain-matching
    # child should still project to the terrain base plane (not stay offset by the
    # parent's elevated y).
    monkeypatch.setattr(nbttool, "env_height_offset", lambda **kw: int(kw.get("x", 0)))

    base, _segment, _pool, index = _segment_templates_for_projection_tests(nbttool)
    rigid_base = nbttool.StructureTemplate(
        template_id="rigid_base",
        size=(1, 11, 1),
        blocks=(nbttool.BlockInstance(pos=(0, 10, 0), block_id="minecraft:jigsaw", color_key="minecraft:jigsaw"),),
        connectors=(
            _make_connector(
                nbttool,
                pos=(0, 10, 0),
                front=(1, 0, 0),
                top=(0, 1, 0),
                pool="poolA",
                target="socket",
                name="start",
                final_state="minecraft:air",
                joint="aligned",
                source="rigid_base",
                projection="rigid",
            ),
        ),
    )

    piece_stones: list[tuple[int, int, int]] = []

    def on_piece(blocks, _loc: str) -> None:
        stones = [b for b in blocks if b.block_id == "minecraft:stone"]
        assert len(stones) == 1
        piece_stones.append(stones[0].pos)

    _out, _report, _state = nbttool.build_jigsaw_expanded_structure(
        rigid_base,
        seeds=[0x1],
        index=index,
        terrain_preset="grassy_hills",
        terrain_seed=0,
        terrain_anchor_off=0,
        terrain_base_y=0,
        terrain_amp=256,
        total_depth=1,
        piece_callback=on_piece,
    )

    assert piece_stones == [(2, 2, 0)]
