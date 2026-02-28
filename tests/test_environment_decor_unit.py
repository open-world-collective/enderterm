from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


class _FakeSource:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    def read_json(self, path: str) -> object:
        assert path == "environments.json"
        return self._obj


def test_load_environments_config_defaults_when_source_is_none(nbttool: ModuleType) -> None:
    cfg = nbttool.load_environments_config(None)
    assert set(cfg.keys()) >= {"space", "grassy_hills", "desert"}
    assert cfg["space"].density == 0.0


def test_load_environments_config_parses_and_clamps(nbttool: ModuleType) -> None:
    src = _FakeSource(
        {
            "environments": {
                "grassy_hills": {
                    "decor": {
                        "density": 2.0,  # clamp01 -> 1.0
                        "scale": 0.001,  # clamp_scale -> 0.05
                        "blocks": [
                            "minecraft:short_grass",
                            {"id": "minecraft:poppy", "weight": 0},  # clamp -> 1
                            {"id": "minecraft:fern", "weight": 2_000_000},  # clamp -> 1_000_000
                            {"id": "", "weight": 10},  # invalid
                            123,  # invalid
                        ],
                    }
                },
                "desert": {"decor": {"enabled": False}},
            }
        }
    )

    cfg = nbttool.load_environments_config(src)
    hills = cfg["grassy_hills"]
    assert hills.density == 1.0
    assert hills.scale == 0.05
    blocks = [asdict(b) for b in hills.blocks]
    assert {"block_id": "minecraft:short_grass", "weight": 1} in blocks
    assert {"block_id": "minecraft:poppy", "weight": 1} in blocks
    assert {"block_id": "minecraft:fern", "weight": 1_000_000} in blocks

    desert = cfg["desert"]
    assert desert.density == 0.0

