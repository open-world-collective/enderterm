from __future__ import annotations

from enderterm.terrain import infer_environment_preset_name


def test_infer_environment_defaults_to_grassy_hills() -> None:
    assert infer_environment_preset_name(hint=None, template_id=None, block_ids=None) == "grassy_hills"


def test_infer_environment_uses_hint_keywords() -> None:
    assert infer_environment_preset_name(hint="minecraft:village/desert/town_centers", block_ids=[]) == "desert"
    assert infer_environment_preset_name(hint="minecraft:bastion/treasure", block_ids=[]) == "nether"
    assert infer_environment_preset_name(hint="minecraft:end_city/start_pool", block_ids=[]) == "end"
    assert infer_environment_preset_name(hint="minecraft:village/snowy/town_centers", block_ids=[]) == "snowy_hills"


def test_infer_environment_avoids_substring_false_positives() -> None:
    # "enderterm" contains "end", but should not force the End preset.
    assert infer_environment_preset_name(hint="enderterm:cool_structure", block_ids=[]) == "grassy_hills"


def test_infer_environment_uses_block_evidence_when_strong() -> None:
    assert infer_environment_preset_name(hint=None, block_ids=["minecraft:netherrack"] * 20) == "nether"
    assert infer_environment_preset_name(hint=None, block_ids=["minecraft:end_stone"] * 20) == "end"
    assert infer_environment_preset_name(hint=None, block_ids=["minecraft:sandstone"] * 20) == "desert"
    assert infer_environment_preset_name(hint=None, block_ids=["minecraft:snow_block"] * 20) == "snowy_hills"


def test_infer_environment_ignores_weak_block_evidence() -> None:
    # One sand block shouldn't flip the whole environment.
    blocks = ["minecraft:stone"] * 60 + ["minecraft:sand"]
    assert infer_environment_preset_name(hint=None, block_ids=blocks) == "grassy_hills"

