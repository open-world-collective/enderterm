from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def test_apply_processor_pipeline_capped_updates_at_most_limit(nbttool: ModuleType) -> None:
    blocks_by_pos = {
        (0, 0, 0): nbttool.BlockInstance(pos=(0, 0, 0), block_id="minecraft:stone", color_key="minecraft:stone"),
        (1, 0, 0): nbttool.BlockInstance(pos=(1, 0, 0), block_id="minecraft:stone", color_key="minecraft:stone"),
    }

    delegate = nbttool.RuleProcessor(
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
                output_state_id="minecraft:dirt",
            ),
        ),
        unhandled_predicates=tuple(),
    )
    pipeline = nbttool.ProcessorPipeline(
        ignore_base=frozenset(),
        processors=(nbttool.CappedProcessor(limit=1, delegate=delegate),),
        unhandled_processors=tuple(),
    )

    out1 = nbttool._apply_processor_pipeline_to_blocks(
        blocks_by_pos,
        pipeline=pipeline,
        seed=0x12345678,
        context=("cap",),
        existing_blocks_by_pos=None,
    )
    out2 = nbttool._apply_processor_pipeline_to_blocks(
        blocks_by_pos,
        pipeline=pipeline,
        seed=0x12345678,
        context=("cap",),
        existing_blocks_by_pos=None,
    )

    assert out1 == out2
    assert sum(1 for b in out1.values() if b.block_id == "minecraft:dirt") == 1


def test_apply_processor_pipeline_skips_jigsaw_blocks(nbttool: ModuleType) -> None:
    blocks_by_pos = {
        (0, 0, 0): nbttool.BlockInstance(pos=(0, 0, 0), block_id="minecraft:jigsaw", color_key="minecraft:jigsaw"),
        (1, 0, 0): nbttool.BlockInstance(pos=(1, 0, 0), block_id="minecraft:stone", color_key="minecraft:stone"),
    }

    proc = nbttool.RuleProcessor(
        rules=(
            nbttool.RuleSpec(
                input_type="minecraft:block_match",
                input_blocks_base=frozenset({"minecraft:stone", "minecraft:jigsaw"}),
                input_block_states=frozenset(),
                input_probability=None,
                location_type="minecraft:always_true",
                location_blocks_base=frozenset(),
                location_block_states=frozenset(),
                location_probability=None,
                output_state_id="minecraft:dirt",
            ),
        ),
        unhandled_predicates=tuple(),
    )
    pipeline = nbttool.ProcessorPipeline(
        ignore_base=frozenset(),
        processors=(proc,),
        unhandled_processors=tuple(),
    )

    out = nbttool._apply_processor_pipeline_to_blocks(
        blocks_by_pos,
        pipeline=pipeline,
        seed=0,
        context=("skip-jigsaw",),
        existing_blocks_by_pos=None,
    )

    assert out[(0, 0, 0)].block_id == "minecraft:jigsaw"
    assert out[(1, 0, 0)].block_id == "minecraft:dirt"


def test_apply_processor_pipeline_rule_location_uses_existing_blocks(nbttool: ModuleType) -> None:
    pos = (1, 0, 0)
    blocks_by_pos = {pos: nbttool.BlockInstance(pos=pos, block_id="minecraft:stone", color_key="minecraft:stone")}
    existing_blocks_by_pos = {
        pos: nbttool.BlockInstance(pos=pos, block_id="minecraft:gold_block", color_key="minecraft:gold_block")
    }

    proc = nbttool.RuleProcessor(
        rules=(
            nbttool.RuleSpec(
                input_type="minecraft:block_match",
                input_blocks_base=frozenset({"minecraft:stone"}),
                input_block_states=frozenset(),
                input_probability=None,
                location_type="minecraft:block_match",
                location_blocks_base=frozenset({"minecraft:gold_block"}),
                location_block_states=frozenset(),
                location_probability=None,
                output_state_id="minecraft:dirt",
            ),
        ),
        unhandled_predicates=tuple(),
    )
    pipeline = nbttool.ProcessorPipeline(
        ignore_base=frozenset(),
        processors=(proc,),
        unhandled_processors=tuple(),
    )

    out = nbttool._apply_processor_pipeline_to_blocks(
        blocks_by_pos,
        pipeline=pipeline,
        seed=0,
        context=("loc",),
        existing_blocks_by_pos=existing_blocks_by_pos,
    )
    assert out[pos].block_id == "minecraft:dirt"


def test_apply_processor_pipeline_rule_location_miss_keeps_block(nbttool: ModuleType) -> None:
    pos = (1, 0, 0)
    blocks_by_pos = {pos: nbttool.BlockInstance(pos=pos, block_id="minecraft:stone", color_key="minecraft:stone")}

    proc = nbttool.RuleProcessor(
        rules=(
            nbttool.RuleSpec(
                input_type="minecraft:block_match",
                input_blocks_base=frozenset({"minecraft:stone"}),
                input_block_states=frozenset(),
                input_probability=None,
                location_type="minecraft:block_match",
                location_blocks_base=frozenset({"minecraft:gold_block"}),
                location_block_states=frozenset(),
                location_probability=None,
                output_state_id="minecraft:dirt",
            ),
        ),
        unhandled_predicates=tuple(),
    )
    pipeline = nbttool.ProcessorPipeline(
        ignore_base=frozenset(),
        processors=(proc,),
        unhandled_processors=tuple(),
    )

    out = nbttool._apply_processor_pipeline_to_blocks(
        blocks_by_pos,
        pipeline=pipeline,
        seed=0,
        context=("loc-miss",),
        existing_blocks_by_pos=None,
    )
    assert out[pos].block_id == "minecraft:stone"


def test_rule_processor_random_block_match_probability_zero_never_matches(nbttool: ModuleType) -> None:
    pos = (0, 0, 0)
    blocks_by_pos = {pos: nbttool.BlockInstance(pos=pos, block_id="minecraft:stone", color_key="minecraft:stone")}

    proc = nbttool.RuleProcessor(
        rules=(
            nbttool.RuleSpec(
                input_type="minecraft:random_block_match",
                input_blocks_base=frozenset({"minecraft:stone"}),
                input_block_states=frozenset(),
                input_probability=0.0,
                location_type="minecraft:always_true",
                location_blocks_base=frozenset(),
                location_block_states=frozenset(),
                location_probability=None,
                output_state_id="minecraft:dirt",
            ),
        ),
        unhandled_predicates=tuple(),
    )
    pipeline = nbttool.ProcessorPipeline(
        ignore_base=frozenset(),
        processors=(proc,),
        unhandled_processors=tuple(),
    )

    out = nbttool._apply_processor_pipeline_to_blocks(
        blocks_by_pos,
        pipeline=pipeline,
        seed=0x12345678,
        context=("prob0",),
        existing_blocks_by_pos=None,
    )
    assert out[pos].block_id == "minecraft:stone"


def test_rule_processor_random_block_match_probability_one_always_matches(nbttool: ModuleType) -> None:
    pos = (0, 0, 0)
    blocks_by_pos = {pos: nbttool.BlockInstance(pos=pos, block_id="minecraft:stone", color_key="minecraft:stone")}

    proc = nbttool.RuleProcessor(
        rules=(
            nbttool.RuleSpec(
                input_type="minecraft:random_block_match",
                input_blocks_base=frozenset({"minecraft:stone"}),
                input_block_states=frozenset(),
                input_probability=1.0,
                location_type="minecraft:always_true",
                location_blocks_base=frozenset(),
                location_block_states=frozenset(),
                location_probability=None,
                output_state_id="minecraft:dirt",
            ),
        ),
        unhandled_predicates=tuple(),
    )
    pipeline = nbttool.ProcessorPipeline(
        ignore_base=frozenset(),
        processors=(proc,),
        unhandled_processors=tuple(),
    )

    out = nbttool._apply_processor_pipeline_to_blocks(
        blocks_by_pos,
        pipeline=pipeline,
        seed=0x12345678,
        context=("prob1",),
        existing_blocks_by_pos=None,
    )
    assert out[pos].block_id == "minecraft:dirt"
