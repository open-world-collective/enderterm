from __future__ import annotations

from enderterm.terrain import clamp_terrain_delta, env_height_offset, infer_environment_preset_name, load_environments_config


def _grassy_hills_height(
    *,
    seed: int = 1,
    x: int = 0,
    z: int = 0,
    **kwargs,
) -> int:
    return env_height_offset(preset="grassy_hills", seed=seed, x=x, z=z, **kwargs)


def test_env_height_offset_returns_zero_for_unknown_preset_and_non_positive_amp() -> None:
    assert env_height_offset(preset="unknown", seed=0, x=0, z=0) == 0
    assert _grassy_hills_height(seed=0, amp=0) == 0


def test_clamp_terrain_delta_respects_max_delta_bounds() -> None:
    assert clamp_terrain_delta(50, max_delta=100) == 50
    assert clamp_terrain_delta(200, max_delta=100) == 100
    assert clamp_terrain_delta(-200, max_delta=100) == -100
    assert clamp_terrain_delta(1, max_delta=-5) == 0


def test_env_height_offset_is_deterministic_and_within_expected_bounds() -> None:
    h1 = _grassy_hills_height(seed=123, x=10, z=20)
    h2 = _grassy_hills_height(seed=123, x=10, z=20)
    assert h1 == h2
    assert 0 <= h1 <= 18

    desert = env_height_offset(preset="desert", seed=123, x=10, z=20)
    assert 0 <= desert <= 9


def test_env_height_offset_clamps_inputs_and_internal_weights() -> None:
    # scale/octaves/lacunarity clamps
    _grassy_hills_height(
        amp=10,
        scale=0.0,
        octaves=0,
        lacunarity=0.0,
        ridged_offset=1.0,
        ridged_gain=-1.0,
    )
    # weight > 1 clamp
    _grassy_hills_height(
        amp=10,
        ridged_offset=2.0,
        ridged_gain=10.0,
    )
    # signal < 0 clamp
    _grassy_hills_height(
        amp=10,
        ridged_offset=0.0,
    )


def test_load_environments_config_applies_clamps_and_disable_flag() -> None:
    class _Source:
        def read_json(self, rel: str) -> dict[str, object]:
            assert rel == "environments.json"
            return {
                "environments": {
                    "grassy_hills": {
                        "decor": {
                            "density": 2.0,
                            "scale": 0.0,
                            "blocks": [
                                {"id": "minecraft:poppy", "weight": 0},
                                {"id": "minecraft:dandelion", "weight": 2_000_000},
                                "minecraft:fern",
                            ],
                        }
                    },
                    "desert": {"decor": {"enabled": False}},
                }
            }

    cfg = load_environments_config(_Source())
    grassy = cfg["grassy_hills"]
    assert grassy.density == 1.0
    assert grassy.scale == 0.05
    assert [(b.block_id, b.weight) for b in grassy.blocks] == [
        ("minecraft:poppy", 1),
        ("minecraft:dandelion", 1_000_000),
        ("minecraft:fern", 1),
    ]

    desert = cfg["desert"]
    assert desert.density == 0.0
    assert desert.scale == 0.72
    assert [(b.block_id, b.weight) for b in desert.blocks] == [
        ("minecraft:dead_bush", 8),
        ("minecraft:cactus", 2),
    ]


def test_infer_environment_preset_name_uses_hint_tokens_and_block_thresholds() -> None:
    # Tokenization should avoid substring false positives ("enderterm" contains "end").
    assert infer_environment_preset_name(hint="enderterm", template_id=None, block_ids=None) == "grassy_hills"
    assert infer_environment_preset_name(hint="minecraft:end_city", template_id=None, block_ids=None) == "end"

    # A single weak desert signal should not override defaults.
    assert infer_environment_preset_name(
        hint=None,
        block_ids=["minecraft:cactus", *["minecraft:stone"] * 20],
    ) == "grassy_hills"

    # Strong nether evidence should win.
    assert infer_environment_preset_name(
        hint=None,
        block_ids=[*["minecraft:netherrack"] * 10, *["minecraft:stone"] * 5],
    ) == "nether"
