from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable

ENV_HEIGHT_MAX_DELTA = 100
WORLD_MIN_Y = -64
_HEIGHT_PRESETS = frozenset({"grassy_hills", "snowy_hills", "desert"})
_DECOR_WEIGHT_MAX = 1_000_000
_DEFAULT_ENV_PRESET = "grassy_hills"
_ENV_INFERENCE_IGNORE_BLOCKS = frozenset(
    {
        "minecraft:air",
        "minecraft:structure_void",
        "minecraft:jigsaw",
        "minecraft:barrier",
    }
)
_ENV_INFERENCE_BLOCK_NAMES = ("desert", "snowy_hills", "nether", "end")
_NETHER_HINT_TOKENS = frozenset({"nether", "bastion", "fortress", "crimson", "warped", "piglin", "hoglin"})
_END_HINT_TOKENS = frozenset({"end", "endcity", "chorus", "purpur", "shulker"})
_DESERT_HINT_TOKENS = frozenset({"desert", "mesa", "badlands"})
_SNOW_HINT_TOKENS = frozenset({"snow", "snowy", "ice", "frozen", "taiga"})


def _coerce_height_noise_params(
    *,
    amp: int | None,
    scale: float | None,
    octaves: int | None,
    lacunarity: float | None,
    h: float | None,
    ridged_offset: float | None,
    ridged_gain: float | None,
) -> tuple[int, float, int, float, float, float, float]:
    amp_value = int(amp) if isinstance(amp, int) else 18
    scale_value = float(scale) if isinstance(scale, (int, float)) else 96.0
    octaves_value = int(octaves) if isinstance(octaves, int) else 5
    lacunarity_value = float(lacunarity) if isinstance(lacunarity, (int, float)) else 2.0
    h_value = float(h) if isinstance(h, (int, float)) else 1.0
    ridged_offset_value = float(ridged_offset) if isinstance(ridged_offset, (int, float)) else 1.0
    ridged_gain_value = float(ridged_gain) if isinstance(ridged_gain, (int, float)) else 2.0

    if scale_value <= 1e-6:
        scale_value = 1.0
    if octaves_value < 1:
        octaves_value = 1
    if lacunarity_value <= 1e-6:
        lacunarity_value = 2.0

    return (
        int(amp_value),
        float(scale_value),
        int(octaves_value),
        float(lacunarity_value),
        float(h_value),
        float(ridged_offset_value),
        float(ridged_gain_value),
    )


def _apply_preset_height_scalars(preset: str, *, amp_value: int, scale_value: float) -> tuple[int, float]:
    if preset == "desert":
        return (int(round(float(amp_value) * 0.5)), float(scale_value) * 2.0)
    if preset == "snowy_hills":
        return (int(round(float(amp_value) * 0.8)), float(scale_value) * 1.35)
    return (int(amp_value), float(scale_value))


def _smoothstep(t: float) -> float:
    t = 0.0 if t <= 0.0 else (1.0 if t >= 1.0 else t)
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _hash01(ix: int, iz: int, *, seed: int) -> float:
    v = (int(ix) * 0x9E3779B1) ^ (int(iz) * 0x85EBCA77) ^ int(seed)
    v &= 0xFFFFFFFF
    v ^= v >> 16
    v = (v * 0x7FEB352D) & 0xFFFFFFFF
    v ^= v >> 15
    v = (v * 0x846CA68B) & 0xFFFFFFFF
    v ^= v >> 16
    return float(v) / 4294967296.0


def _value_noise(seed: int, *, xf: float, zf: float, scale: float) -> float:
    fx = float(xf) / float(scale)
    fz = float(zf) / float(scale)
    ix0 = int(math.floor(fx))
    iz0 = int(math.floor(fz))
    tx = fx - float(ix0)
    tz = fz - float(iz0)
    sx = _smoothstep(tx)
    sz = _smoothstep(tz)
    v00 = _hash01(ix0, iz0, seed=seed)
    v10 = _hash01(ix0 + 1, iz0, seed=seed)
    v01 = _hash01(ix0, iz0 + 1, seed=seed)
    v11 = _hash01(ix0 + 1, iz0 + 1, seed=seed)
    ix_a = _lerp(v00, v10, sx)
    ix_b = _lerp(v01, v11, sx)
    return _lerp(ix_a, ix_b, sz)


def _ridged_noise_height_delta(
    *,
    seed: int,
    x: int,
    z: int,
    amp: int,
    scale: float,
    octaves: int,
    lacunarity: float,
    h: float,
    ridged_offset: float,
    ridged_gain: float,
) -> int:
    exp_step = float(lacunarity) ** (-float(h)) if abs(float(h)) > 1e-9 else 1.0
    exponent = 1.0
    frequency = 1.0
    weight = 1.0
    accum = 0.0
    norm = 0.0
    xf = float(x)
    zf = float(z)

    for _ in range(int(octaves)):
        octave_scale = float(scale) / float(max(1e-6, frequency))
        n = _value_noise(seed, xf=xf, zf=zf, scale=octave_scale) * 2.0 - 1.0
        signal = abs(n)
        signal = float(ridged_offset) - signal
        if signal < 0.0:
            signal = 0.0
        signal *= signal
        signal *= weight
        accum += signal * exponent
        norm += (float(ridged_offset) ** 2) * exponent
        weight = _clamp01(signal * float(ridged_gain))
        frequency *= float(lacunarity)
        exponent *= float(exp_step)

    if norm > 1e-9:
        accum /= norm
    accum = _clamp01(accum)
    return int(round(accum * float(amp)))


def env_height_offset(
    *,
    preset: str,
    seed: int,
    x: int,
    z: int,
    amp: int | None = None,
    scale: float | None = None,
    octaves: int | None = None,
    lacunarity: float | None = None,
    h: float | None = None,
    ridged_offset: float | None = None,
    ridged_gain: float | None = None,
) -> int:
    if preset not in _HEIGHT_PRESETS:
        return 0

    (
        amp_value,
        scale_value,
        octaves_value,
        lacunarity_value,
        h_value,
        ridged_offset_value,
        ridged_gain_value,
    ) = _coerce_height_noise_params(
        amp=amp,
        scale=scale,
        octaves=octaves,
        lacunarity=lacunarity,
        h=h,
        ridged_offset=ridged_offset,
        ridged_gain=ridged_gain,
    )

    if amp_value <= 0:
        return 0
    amp_value, scale_value = _apply_preset_height_scalars(
        preset,
        amp_value=int(amp_value),
        scale_value=float(scale_value),
    )

    return _ridged_noise_height_delta(
        seed=int(seed) & 0xFFFFFFFF,
        x=int(x),
        z=int(z),
        amp=int(amp_value),
        scale=float(scale_value),
        octaves=int(octaves_value),
        lacunarity=float(lacunarity_value),
        h=float(h_value),
        ridged_offset=float(ridged_offset_value),
        ridged_gain=float(ridged_gain_value),
    )


def clamp_terrain_delta(dy: int, *, max_delta: int = ENV_HEIGHT_MAX_DELTA) -> int:
    max_delta = max(0, int(max_delta))
    if dy < -max_delta:
        return -max_delta
    if dy > max_delta:
        return max_delta
    return dy


@dataclass(frozen=True, slots=True)
class EnvironmentPreset:
    name: str
    sky_rgb: tuple[float, float, float]
    top_block_id: str | None = None
    fill_block_id: str | None = None
    deep_block_id: str | None = None

    def is_space(self) -> bool:
        return not self.top_block_id


ENVIRONMENT_PRESETS: tuple[EnvironmentPreset, ...] = (
    EnvironmentPreset("space", (0.0, 0.0, 0.0)),
    EnvironmentPreset(
        "grassy_hills",
        (0.46, 0.74, 1.0),
        "minecraft:grass_block[snowy=false]",
        "minecraft:dirt",
        "minecraft:stone",
    ),
    EnvironmentPreset(
        "desert",
        (0.62, 0.82, 1.0),
        "minecraft:sand",
        "minecraft:sandstone",
        "minecraft:stone",
    ),
    EnvironmentPreset(
        "snowy_hills",
        (0.74, 0.84, 1.0),
        "minecraft:snow_block",
        "minecraft:dirt",
        "minecraft:stone",
    ),
    EnvironmentPreset(
        "nether",
        (0.20, 0.04, 0.04),
        "minecraft:netherrack",
        "minecraft:netherrack",
        "minecraft:blackstone",
    ),
    EnvironmentPreset(
        "end",
        (0.10, 0.00, 0.12),
        "minecraft:end_stone",
        "minecraft:end_stone",
        "minecraft:obsidian",
    ),
)


@dataclass(frozen=True, slots=True)
class EnvironmentDecorBlock:
    block_id: str
    weight: int


@dataclass(frozen=True, slots=True)
class EnvironmentDecorConfig:
    density: float
    scale: float
    blocks: tuple[EnvironmentDecorBlock, ...]


def _default_environment_decor() -> dict[str, EnvironmentDecorConfig]:
    return {
        "space": EnvironmentDecorConfig(density=0.0, scale=0.7, blocks=()),
        "grassy_hills": EnvironmentDecorConfig(
            density=0.055,
            scale=0.68,
            blocks=(
                EnvironmentDecorBlock("minecraft:short_grass", 10),
                EnvironmentDecorBlock("minecraft:tall_grass", 4),
                EnvironmentDecorBlock("minecraft:dandelion", 2),
                EnvironmentDecorBlock("minecraft:poppy", 2),
                EnvironmentDecorBlock("minecraft:fern", 2),
            ),
        ),
        "desert": EnvironmentDecorConfig(
            density=0.022,
            scale=0.72,
            blocks=(
                EnvironmentDecorBlock("minecraft:dead_bush", 8),
                EnvironmentDecorBlock("minecraft:cactus", 2),
            ),
        ),
        "snowy_hills": EnvironmentDecorConfig(
            density=0.02,
            scale=0.72,
            blocks=(
                EnvironmentDecorBlock("minecraft:spruce_sapling", 3),
                EnvironmentDecorBlock("minecraft:fern", 2),
                EnvironmentDecorBlock("minecraft:sweet_berry_bush", 1),
            ),
        ),
        "nether": EnvironmentDecorConfig(
            density=0.02,
            scale=0.72,
            blocks=(
                EnvironmentDecorBlock("minecraft:crimson_fungus", 2),
                EnvironmentDecorBlock("minecraft:warped_fungus", 2),
                EnvironmentDecorBlock("minecraft:crimson_roots", 6),
                EnvironmentDecorBlock("minecraft:warped_roots", 6),
                EnvironmentDecorBlock("minecraft:nether_sprouts", 4),
            ),
        ),
        "end": EnvironmentDecorConfig(
            density=0.016,
            scale=0.72,
            blocks=(
                EnvironmentDecorBlock("minecraft:chorus_plant", 3),
                EnvironmentDecorBlock("minecraft:chorus_flower", 2),
            ),
        ),
    }


def _read_environments_json(source: "PackStackSource | DatapackSource | None") -> dict[str, object] | None:
    if source is None:
        return None
    try:
        obj = source.read_json("environments.json")  # type: ignore[union-attr]
    except Exception:
        return None
    if not isinstance(obj, dict) or not obj:
        return None
    return obj


def _environment_cfg_root(obj: dict[str, object]) -> dict[str, object]:
    envs_obj = obj.get("environments")
    if isinstance(envs_obj, dict):
        return envs_obj
    return obj


def _resolve_decor_cfg(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    decor_cfg_obj = value.get("decor")
    if decor_cfg_obj is None:
        return value
    if not isinstance(decor_cfg_obj, dict):
        return None
    return decor_cfg_obj


def _parse_decor_blocks(value: object) -> tuple[EnvironmentDecorBlock, ...]:
    if not isinstance(value, list):
        return ()
    out_blocks: list[EnvironmentDecorBlock] = []
    for item in value:
        if isinstance(item, str) and item:
            out_blocks.append(EnvironmentDecorBlock(str(item), 1))
            continue
        if not isinstance(item, dict):
            continue
        bid = item.get("id")
        if not isinstance(bid, str) or not bid:
            continue
        w_obj = item.get("weight", 1)
        weight = w_obj if isinstance(w_obj, int) else 1
        if weight < 1:
            weight = 1
        if weight > _DECOR_WEIGHT_MAX:
            weight = _DECOR_WEIGHT_MAX
        out_blocks.append(EnvironmentDecorBlock(str(bid), int(weight)))
    return tuple(out_blocks)


def _finite_or_default(value: object, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(out):
        return float(default)
    return float(out)


def _clamp_to_range(value: object, *, default: float, lo: float, hi: float) -> float:
    out = _finite_or_default(value, default)
    if out < lo:
        return float(lo)
    if out > hi:
        return float(hi)
    return float(out)


def _resolved_environment_decor_cfg(
    *,
    defaults: EnvironmentDecorConfig,
    decor_cfg: dict[str, object],
) -> EnvironmentDecorConfig:
    enabled = decor_cfg.get("enabled", True)
    if isinstance(enabled, bool) and not enabled:
        return EnvironmentDecorConfig(density=0.0, scale=defaults.scale, blocks=defaults.blocks)

    density = _clamp_to_range(
        decor_cfg.get("density", defaults.density),
        default=defaults.density,
        lo=0.0,
        hi=1.0,
    )
    scale = _clamp_to_range(
        decor_cfg.get("scale", defaults.scale),
        default=defaults.scale,
        lo=0.05,
        hi=1.0,
    )
    blocks = _parse_decor_blocks(decor_cfg.get("blocks", []))
    if not blocks:
        blocks = defaults.blocks
    return EnvironmentDecorConfig(density=density, scale=scale, blocks=blocks)


def load_environments_config(source: "PackStackSource | DatapackSource | None") -> dict[str, EnvironmentDecorConfig]:
    """Load tool-side environment decoration settings from environments.json.

    This file is optional and lives at the datapack root (not under data/).
    Vanilla ignores it; EnderTerm reads it to control ambient decoration.
    """

    out = _default_environment_decor()
    obj = _read_environments_json(source)
    if obj is None:
        return out
    cfg_obj = _environment_cfg_root(obj)

    for env_name, defaults in list(out.items()):
        decor_cfg = _resolve_decor_cfg(cfg_obj.get(env_name))
        if decor_cfg is None:
            continue
        out[env_name] = _resolved_environment_decor_cfg(defaults=defaults, decor_cfg=decor_cfg)

    return out


_ENV_DECOR_ALIASES: dict[str, str] = {
    # 1.20 renamed the old grass plant; many packs/jars still only have minecraft:grass.
    "minecraft:short_grass": "minecraft:grass",
}


def _tokenize_environment_hint(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if token}


def _collect_environment_hint_tokens(*, hint: str | None, template_id: str | None) -> set[str]:
    hint_tokens: set[str] = set()
    if isinstance(hint, str) and hint.strip():
        hint_tokens |= _tokenize_environment_hint(hint)
    if isinstance(template_id, str) and template_id.strip():
        hint_tokens |= _tokenize_environment_hint(template_id)
    return hint_tokens


def _bump_environment_score(scores: dict[str, float], env_name: str, amount: float) -> None:
    if env_name in scores:
        scores[env_name] += float(amount)


def _score_environment_hints(scores: dict[str, float], hint_tokens: set[str]) -> None:
    if not hint_tokens:
        return
    if _NETHER_HINT_TOKENS & hint_tokens:
        _bump_environment_score(scores, "nether", 500.0)
    if _END_HINT_TOKENS & hint_tokens:
        _bump_environment_score(scores, "end", 500.0)
    if _DESERT_HINT_TOKENS & hint_tokens:
        _bump_environment_score(scores, "desert", 450.0)
    if _SNOW_HINT_TOKENS & hint_tokens:
        _bump_environment_score(scores, "snowy_hills", 450.0)


def _base_block_id(block_state_id: str) -> str:
    return str(block_state_id).split("[", 1)[0]


def _normalized_block_name(block_state_id: str) -> str:
    name = _base_block_id(block_state_id).lower()
    if name.startswith("minecraft:"):
        return name[len("minecraft:") :]
    return name


def _count_environment_block_hits(block_ids: Iterable[str] | None) -> tuple[int, dict[str, int]]:
    hits: dict[str, int] = {env: 0 for env in _ENV_INFERENCE_BLOCK_NAMES}
    total = 0
    if block_ids is None:
        return (0, hits)

    for block_state_id in block_ids:
        block_base = _base_block_id(block_state_id)
        if not block_base or block_base in _ENV_INFERENCE_IGNORE_BLOCKS:
            continue
        total += 1
        block_name = _normalized_block_name(block_base)

        # Nether-ish.
        if (
            "nether" in block_name
            or "netherrack" in block_name
            or "blackstone" in block_name
            or "basalt" in block_name
            or "soul_sand" in block_name
            or "soul_soil" in block_name
            or "crimson" in block_name
            or "warped" in block_name
        ):
            hits["nether"] += 1

        # End-ish.
        if block_name.startswith("end_") or "purpur" in block_name or "chorus" in block_name:
            hits["end"] += 1

        # Desert-ish.
        if (
            block_name == "cactus"
            or block_name == "dead_bush"
            or block_name.endswith("_sand")
            or "sandstone" in block_name
            or "terracotta" in block_name
        ):
            hits["desert"] += 1

        # Snow/ice.
        if "snow" in block_name or "ice" in block_name:
            hits["snowy_hills"] += 1

    return (int(total), hits)


def _score_environment_block_evidence(scores: dict[str, float], *, total: int, hits: dict[str, int]) -> None:
    if total <= 0:
        return
    for env_name, count in hits.items():
        if count <= 0:
            continue
        fraction = float(count) / float(total)
        if count >= 10 or fraction >= 0.08:
            _bump_environment_score(scores, env_name, float(count) * 6.0)


def infer_environment_preset_name(
    *,
    hint: str | None,
    template_id: str | None = None,
    block_ids: Iterable[str] | None = None,
) -> str:
    """Heuristically pick an environment preset for a structure/pool.

    The viewer can always override manually (E), but this gives better defaults
    when loading NBT structures or starting a jigsaw expansion from a pool.
    """

    # Prefer grounded worlds by default; "space" remains a manual choice.
    default = _DEFAULT_ENV_PRESET

    # Tokenize hints (pool ids, template ids, filenames, etc.) so we avoid
    # substring false positives (e.g., "enderterm" contains "end").
    hint_tokens = _collect_environment_hint_tokens(hint=hint, template_id=template_id)

    scores: dict[str, float] = {
        "desert": 0.0,
        "snowy_hills": 0.0,
        "nether": 0.0,
        "end": 0.0,
        default: 0.0,
    }

    # Strong signal from names/paths.
    _score_environment_hints(scores, hint_tokens)

    # Block evidence: count "biome-ish" blocks. Keep thresholds conservative to
    # avoid flipping environments for a single decorative block.
    total, hits = _count_environment_block_hits(block_ids)
    _score_environment_block_evidence(scores, total=total, hits=hits)

    best_env, best_score = max(scores.items(), key=lambda kv: (kv[1], kv[0]))
    if best_score <= 0.0:
        return default
    return best_env
