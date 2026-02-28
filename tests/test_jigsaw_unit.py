from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType


def _bounds_intersect(a: tuple[int, int, int, int, int, int], b: tuple[int, int, int, int, int, int]) -> bool:
    ax0, ay0, az0, ax1, ay1, az1 = a
    bx0, by0, bz0, bx1, by1, bz1 = b
    return not (
        ax1 < bx0
        or bx1 < ax0
        or ay1 < by0
        or by1 < ay0
        or az1 < bz0
        or bz1 < az0
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
):
    return nbttool.JigsawConnector(
        pos=pos,
        front=front,
        top=top,
        projection="rigid",
        pool=pool,
        target=target,
        name=name,
        final_state=final_state,
        joint=joint,
        source=source,
    )


def test_block_id_from_jigsaw_final_state_strips_nbt(nbttool: ModuleType) -> None:
    got = nbttool._block_id_from_jigsaw_final_state("minecraft:stone{foo:1b}")
    assert got == "minecraft:stone"
    got = nbttool._block_id_from_jigsaw_final_state("minecraft:oak_planks   {bar:2}")
    assert got == "minecraft:oak_planks"


def test_build_jigsaw_expansion_is_deterministic_and_collision_free(nbttool: ModuleType) -> None:
    base = nbttool.StructureTemplate(
        template_id="base",
        size=(1, 1, 1),
        blocks=(
            _make_block(nbttool, pos=(0, 0, 0), block_id="minecraft:jigsaw"),
        ),
        connectors=(
            _make_connector(
                nbttool,
                pos=(0, 0, 0),
                front=(1, 0, 0),
                top=(0, 1, 0),
                pool="poolA",
                target="socket",
                name="parent",
                final_state="minecraft:air",
                joint="aligned",
                source="base",
            ),
        ),
    )

    child = nbttool.StructureTemplate(
        template_id="child",
        size=(2, 1, 1),
        blocks=(
            _make_block(nbttool, pos=(0, 0, 0), block_id="minecraft:jigsaw"),
            _make_block(nbttool, pos=(1, 0, 0), block_id="minecraft:stone"),
        ),
        connectors=(
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
                source="child",
            ),
        ),
    )

    pool = nbttool.PoolDefinition(
        elements=(
            nbttool.PoolElement(
                location_id="child",
                weight=1,
                processors="minecraft:empty",
                projection="rigid",
            ),
        ),
        fallback="minecraft:empty",
    )

    index = _FakeIndex(nbttool=nbttool, pools={"poolA": pool}, templates={"child": child})

    out1, report1, state1 = nbttool.build_jigsaw_expanded_structure(base, seeds=[0x12345678], index=index)
    out2, report2, state2 = nbttool.build_jigsaw_expanded_structure(base, seeds=[0x12345678], index=index)

    assert report1 == report2
    assert state1.consumed == state2.consumed
    assert state1.dead_end == state2.dead_end
    assert state1.piece_bounds == state2.piece_bounds

    blocks1 = {(b.pos, b.block_id) for b in out1.blocks}
    blocks2 = {(b.pos, b.block_id) for b in out2.blocks}
    assert blocks1 == blocks2

    assert blocks1 == {((2, 0, 0), "minecraft:stone")}
    assert state1.consumed == frozenset({(0, 0, 0), (1, 0, 0)})
    assert state1.dead_end == frozenset()
    assert len(state1.piece_bounds) == 2
    assert not _bounds_intersect(state1.piece_bounds[0], state1.piece_bounds[1])


def test_build_jigsaw_marks_dead_end_when_piece_bounds_overlap(nbttool: ModuleType) -> None:
    base = nbttool.StructureTemplate(
        template_id="base",
        size=(2, 1, 1),
        blocks=(
            _make_block(nbttool, pos=(0, 0, 0), block_id="minecraft:jigsaw"),
        ),
        connectors=(
            _make_connector(
                nbttool,
                pos=(0, 0, 0),
                front=(1, 0, 0),
                top=(0, 1, 0),
                pool="poolA",
                target="socket",
                name="parent",
                final_state="minecraft:air",
                joint="aligned",
                source="base",
            ),
        ),
    )

    # This child would be placed at translation t=(1,0,0) and has bounds x=1..2.
    # Base bounds are x=0..1, and touching at x=1 counts as overlap in our AABB check.
    child = nbttool.StructureTemplate(
        template_id="child",
        size=(2, 1, 1),
        blocks=(
            _make_block(nbttool, pos=(0, 0, 0), block_id="minecraft:jigsaw"),
        ),
        connectors=(
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
                source="child",
            ),
        ),
    )

    pool = nbttool.PoolDefinition(
        elements=(
            nbttool.PoolElement(
                location_id="child",
                weight=1,
                processors="minecraft:empty",
                projection="rigid",
            ),
        ),
        fallback="minecraft:empty",
    )
    index = _FakeIndex(nbttool=nbttool, pools={"poolA": pool}, templates={"child": child})

    out, report, state = nbttool.build_jigsaw_expanded_structure(base, seeds=[0x12345678], index=index)
    assert report
    assert state.consumed == frozenset()
    assert state.dead_end == frozenset({(0, 0, 0)})
    assert len(state.piece_bounds) == 1
    assert {(b.pos, b.block_id) for b in out.blocks} == set()


def test_build_jigsaw_applies_processors_to_placed_template(nbttool: ModuleType) -> None:
    base = nbttool.StructureTemplate(
        template_id="base",
        size=(1, 1, 1),
        blocks=(
            _make_block(nbttool, pos=(0, 0, 0), block_id="minecraft:jigsaw"),
        ),
        connectors=(
            _make_connector(
                nbttool,
                pos=(0, 0, 0),
                front=(1, 0, 0),
                pool="poolA",
                target="socket",
                name="parent",
                final_state="minecraft:air",
                source="base",
            ),
        ),
    )

    child = nbttool.StructureTemplate(
        template_id="child",
        size=(2, 1, 1),
        blocks=(
            _make_block(nbttool, pos=(0, 0, 0), block_id="minecraft:jigsaw"),
            _make_block(nbttool, pos=(1, 0, 0), block_id="minecraft:stone"),
        ),
        connectors=(
            _make_connector(
                nbttool,
                pos=(0, 0, 0),
                front=(-1, 0, 0),
                pool="minecraft:empty",
                target="minecraft:empty",
                name="socket",
                final_state="minecraft:air",
                source="child",
            ),
        ),
    )

    pool = nbttool.PoolDefinition(
        elements=(
            nbttool.PoolElement(
                location_id="child",
                weight=1,
                processors="proc_remove_stone",
                projection="rigid",
            ),
        ),
        fallback="minecraft:empty",
    )

    remove_stone = nbttool.ProcessorPipeline(
        ignore_base=frozenset(),
        processors=(
            nbttool.RuleProcessor(
                rules=(
                    nbttool.RuleSpec(
                        input_type="minecraft:block_match",
                        input_blocks_base=frozenset({"minecraft:stone"}),
                        input_block_states=frozenset(),
                        input_probability=None,
                        location_type="minecraft:always_true",
                        location_blocks_base=frozenset(),
                        location_block_states=frozenset(),
                        location_probability=None,
                        output_state_id="minecraft:air",
                    ),
                ),
                unhandled_predicates=tuple(),
            ),
        ),
        unhandled_processors=tuple(),
    )

    @dataclass(frozen=True)
    class _IndexWithProc(_FakeIndex):
        def load_processor_list(self, processor_id: str):
            if processor_id == "proc_remove_stone":
                return remove_stone
            return super().load_processor_list(processor_id)

    index = _IndexWithProc(nbttool=nbttool, pools={"poolA": pool}, templates={"child": child})

    out, _report, _state = nbttool.build_jigsaw_expanded_structure(base, seeds=[0x12345678], index=index)
    assert {(b.pos, b.block_id) for b in out.blocks} == set()
