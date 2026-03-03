from __future__ import annotations

"""
Parameter system ("kValues").

This is part of the portable core (no pyglet/OpenGL). The OpenGL viewer and UI
layers build on top of this module.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time


@dataclass(slots=True)
class ParamDef:
    key: str
    label: str
    default: float
    min_value: float
    max_value: float
    is_int: bool = False
    fmt: str = "{:.3g}"
    help: str = ""


class ParamStore:
    def __init__(
        self,
        defs: list[ParamDef],
        path: Path,
        *,
        defaults_path: Path | None = None,
        aliases: dict[str, str] | None = None,
    ) -> None:
        self._defs = defs
        self._defs_by_key = {d.key: d for d in defs}
        self._values: dict[str, float] = {d.key: float(d.default) for d in defs}
        self._explicit_keys: set[str] = set()
        self._path = path
        self._aliases = dict(aliases or {})
        self._dirty = False
        self._save_after_t = 0.0
        if defaults_path is not None:
            self._load_from_path(defaults_path, mark_dirty=False, mark_explicit=False)
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def keys(self) -> list[str]:
        return [d.key for d in self._defs]

    def defs(self) -> list[ParamDef]:
        return list(self._defs)

    def def_for_key(self, key: str) -> ParamDef | None:
        return self._defs_by_key.get(key)

    def get(self, key: str) -> float:
        return float(self._values.get(key, 0.0))

    def get_int(self, key: str) -> int:
        return int(round(self.get(key)))

    def has_explicit_value(self, key: str) -> bool:
        return str(key) in self._explicit_keys

    def _normalized_value_for_def(self, d: ParamDef, value: float) -> float:
        v = float(value)
        if v < d.min_value:
            v = float(d.min_value)
        if v > d.max_value:
            v = float(d.max_value)
        if d.is_int:
            v = float(int(round(v)))
        return v

    def _value_changed(self, key: str, value: float) -> bool:
        return abs(self._values.get(str(key), float("nan")) - float(value)) >= 1e-9

    def set(self, key: str, value: float) -> None:
        d = self._defs_by_key.get(key)
        if d is None:
            return
        v = self._normalized_value_for_def(d, value)
        if not self._value_changed(key, v):
            return
        self._values[key] = v
        self._explicit_keys.add(str(key))
        self._dirty = True
        self._save_after_t = time.monotonic() + 0.25

    def tick(self) -> None:
        if not self._dirty:
            return
        if time.monotonic() < self._save_after_t:
            return
        self.save()

    def save(self) -> None:
        if not self._dirty:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {k: float(v) for (k, v) in sorted(self._values.items())}
            self._path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            self._dirty = False
        except Exception:
            # Parameter persistence is best-effort; failure shouldn't break the viewer.
            pass

    def _set_loaded_value(self, key: str, value: float) -> None:
        d = self._defs_by_key.get(key)
        if d is None:
            return
        self._values[key] = self._normalized_value_for_def(d, value)

    def _resolve_loaded_key(self, raw_key: object) -> tuple[str | None, bool]:
        key = str(raw_key)
        if key in self._defs_by_key:
            return (key, False)
        alias_key = self._aliases.get(key)
        if alias_key and alias_key in self._defs_by_key:
            return (str(alias_key), True)
        return (None, False)

    def _set_post_load_dirty_state(self, *, mark_dirty: bool, used_alias: bool) -> None:
        if not mark_dirty:
            return
        if used_alias:
            self._dirty = True
            self._save_after_t = time.monotonic() + 0.25
            return
        self._dirty = False
        self._save_after_t = 0.0

    def _load_from_path(self, path: Path, *, mark_dirty: bool, mark_explicit: bool) -> None:
        try:
            if not path.is_file():
                return
            raw = path.read_text()
            data = json.loads(raw)
            if not isinstance(data, dict):
                return
            used_alias = False
            for raw_key, raw_value in data.items():
                key, is_alias = self._resolve_loaded_key(raw_key)
                if key is None:
                    continue
                used_alias = bool(used_alias or is_alias)
                if not isinstance(raw_value, (int, float)):
                    continue
                self._set_loaded_value(key, float(raw_value))
                if mark_explicit:
                    self._explicit_keys.add(str(key))
            self._set_post_load_dirty_state(mark_dirty=mark_dirty, used_alias=used_alias)
        except Exception:
            return

    def _load(self) -> None:
        self._load_from_path(self._path, mark_dirty=True, mark_explicit=True)


FX_MASTER_ENABLED_KEY = "effects.master_enabled"
MACOS_GESTURES_ENABLED_KEY = "input.macos.gestures.enabled"
BUILD_HOVER_PICK_ENABLED_KEY = "build.hover_pick.enabled"


def _platform_name(*, platform: str | None = None) -> str:
    if platform is None:
        return str(sys.platform)
    return str(platform)


def effects_master_default_enabled(*, platform: str | None = None) -> bool:
    return _platform_name(platform=platform) != "darwin"


def _param_store_bool(param_store: object, *, key: str, default: bool) -> bool:
    """Read an integer-ish bool from a param-like object with default fallback."""

    try:
        get_int = getattr(param_store, "get_int", None)
        if callable(get_int):
            return bool(int(get_int(key)))
        get_value = getattr(param_store, "get", None)
        if callable(get_value):
            return bool(int(round(float(get_value(key)))))
    except Exception:
        pass
    return bool(default)


def effects_master_enabled(param_store: object, *, platform: str | None = None) -> bool:
    default_enabled = bool(effects_master_default_enabled(platform=platform))
    return _param_store_bool(param_store, key=FX_MASTER_ENABLED_KEY, default=default_enabled)


def macos_gestures_enabled(param_store: object, *, platform: str | None = None) -> bool:
    if _platform_name(platform=platform) != "darwin":
        return False
    return _param_store_bool(param_store, key=MACOS_GESTURES_ENABLED_KEY, default=False)


def hover_pick_enabled(param_store: object) -> bool:
    return _param_store_bool(param_store, key=BUILD_HOVER_PICK_ENABLED_KEY, default=True)


def _path_has_explicit_param_value(path: Path, *, key: str, aliases: dict[str, str]) -> bool:
    try:
        if not path.is_file():
            return False
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if key in data:
        return True
    for alias_key, target_key in aliases.items():
        if target_key == key and alias_key in data:
            return True
    return False


DEFAULT_PARAM_DEFS: list[ParamDef] = [
    # Debug-oriented ranges: allow extreme values for fast iteration.
    ParamDef("ui.slider.brightness", "UI slider brightness", 1.30942622951, 0.05, 10.0),
    ParamDef("ui.font.scale", "UI font scale", 1.4, 0.5, 3.0),
    ParamDef("ui.term.bg.luma", "Terminal bg brightness (0..1)", 232.0 / 255.0, 0.0, 1.0),
    ParamDef("ui.term.fg.luma", "Terminal text brightness (0..1)", 18.0 / 255.0, 0.0, 1.0),
    ParamDef("ui.term.selection.mix", "Terminal selection mix (bg→fg)", 0.12, 0.0, 1.0),
    ParamDef("ui.term.accent.mix", "Terminal accent Ender mix (0..1)", 0.12, 0.0, 1.0),
    ParamDef("ui.selection.border.frac", "Selection border thickness (block %)", 0.05, 0.0, 0.5),
    ParamDef("render.vsync", "Render vsync (1=on, 0=off)", 1, 0, 1, is_int=True, fmt="{:.0f}"),
    ParamDef(
        "render.frame_cap_hz",
        "Render frame cap Hz (0=tick-paced)",
        0,
        0,
        240,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(FX_MASTER_ENABLED_KEY, "FX master enabled (1=on, 0=off)", 1, 0, 1, is_int=True, fmt="{:.0f}"),
    ParamDef(
        MACOS_GESTURES_ENABLED_KEY,
        "macOS gestures enabled (1=on, 0=off)",
        0,
        0,
        1,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        BUILD_HOVER_PICK_ENABLED_KEY,
        "Build hover-pick enabled (1=on, 0=off)",
        1,
        0,
        1,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "walk.body.debug_draw",
        "Walk collision body debug draw (1=on, 0=off)",
        0,
        0,
        1,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("walk.body.radius.u", "Walk collision body radius (units)", 0.35, 0.01, 3.0),
    ParamDef("walk.body.height.u", "Walk collision body height (units)", 1.8, 0.05, 6.0),
    ParamDef("render.alpha_cutout.threshold", "Alpha cutout threshold", 0.5, 0.00, 1.00),
    ParamDef("camera.autoframe.cooldown_s", "Camera autoframe cooldown (s)", 5.0, 0.0, 60.0),
    ParamDef(
        "env.ground.patch.size",
        "Env ground patch size (blocks)",
        16,
        3,
        128,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "env.ground.patches_per_tick",
        "Env patches per tick",
        102,
        1,
        512,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("rez.fade_s", "Rez fade (s)", 0.2, 0.05, 30.0),
    ParamDef(
        "rez.fade.mode",
        "Rez fade mode (1=stipple, 0=alpha)",
        1,
        0,
        1,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "rez.fade.stipple.style",
        "Stipple fade style (0=ordered, 1=static, 2=single_square)",
        2,
        0,
        2,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "rez.fade.stipple.cell",
        "Stipple fade cell size (pixels)",
        4,
        1,
        8,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "rez.fade.stipple.lvl_jitter",
        "Stipple fade level jitter (0=off)",
        0,
        0,
        16,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "rez.fade.stipple.flicker_hz",
        "Stipple fade flicker (Hz)",
        60.0,
        0.0,
        240.0,
    ),
    ParamDef(
        "rez.fade.stipple.square.exp",
        "Stipple single-square curve exponent",
        1.0,
        0.05,
        16.0,
    ),
    ParamDef(
        "rez.fade.stipple.square.jitter_px",
        "Stipple single-square jitter (px)",
        16,
        0,
        32,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "env.ground.radius",
        "Env ground radius (blocks)",
        30,
        0,
        512,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "env.ground.bottom",
        "Env ground bottom Y",
        -64,
        -4096,
        4096,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "env.ground.strip_fade.height",
        "Env strip fade height (blocks)",
        48,
        0,
        4096,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "env.ground.strip_fade.levels",
        "Env strip fade levels",
        16,
        2,
        64,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "env.terrain.amp",
        "Env terrain amplitude (blocks)",
        24,
        0,
        512,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("env.terrain.scale", "Env terrain scale (blocks)", 96, 4.0, 2048.0),
    ParamDef(
        "env.terrain.octaves",
        "Env terrain octaves",
        5,
        1,
        12,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("env.terrain.lacunarity", "Env terrain lacunarity", 2, 1.0, 8.0),
    ParamDef("env.terrain.h", "Env terrain H", 1, 0.0, 2.5),
    ParamDef("env.terrain.ridged.offset", "Env terrain ridged offset", 1, 0.0, 3.0),
    ParamDef("env.terrain.ridged.gain", "Env terrain ridged gain", 2, 0.0, 6.0),
    ParamDef("rez.throttle.sleep_ms", "Rez throttle sleep (ms)", 50, 0.0, 50.0),
    ParamDef(
        "rez.throttle.every",
        "Rez throttle interval (candidates)",
        64,
        64,
        65_536,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("rez.pieces_per_s", "Rez preview rate (pieces/s, 0=unlimited)", 1.0, 0.0, 60.0),
    ParamDef("fx.color.ender.r", "FX Ender purple R", 0.803921568627, 0.00, 1.50),
    ParamDef("fx.color.ender.g", "FX Ender purple G", 0, 0.00, 1.50),
    ParamDef("fx.color.ender.b", "FX Ender purple B", 1, 0.00, 1.50),
    ParamDef("fx.color.accent.amber.r", "FX accent amber R", 1.0, 0.00, 1.50),
    ParamDef("fx.color.accent.amber.g", "FX accent amber G", 190.0 / 255.0, 0.00, 1.50),
    ParamDef("fx.color.accent.amber.b", "FX accent amber B", 90.0 / 255.0, 0.00, 1.50),
    ParamDef("fx.color.accent.green.r", "FX accent green R", 150.0 / 255.0, 0.00, 1.50),
    ParamDef("fx.color.accent.green.g", "FX accent green G", 1.0, 0.00, 1.50),
    ParamDef("fx.color.accent.green.b", "FX accent green B", 190.0 / 255.0, 0.00, 1.50),
    ParamDef("fx.color.ender.yellow.r", "FX Ender yellow R", 0.592157, 0.00, 1.50),
    ParamDef("fx.color.ender.yellow.g", "FX Ender yellow G", 0.635294, 0.00, 1.50),
    ParamDef("fx.color.ender.yellow.b", "FX Ender yellow B", 0.447059, 0.00, 1.50),
    ParamDef("fx.color.ender.pink.r", "FX Ender pink R", 0.639216, 0.00, 1.50),
    ParamDef("fx.color.ender.pink.g", "FX Ender pink G", 0.490196, 0.00, 1.50),
    ParamDef("fx.color.ender.pink.b", "FX Ender pink B", 0.607843, 0.00, 1.50),
    ParamDef(
        "fx.glitch.void_wash.warn_above.blocks",
        "Void Wash warn-above (blocks)",
        32,
        0,
        512,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "fx.glitch.void_wash.tick_hz",
        "Void Wash tick Hz",
        60,
        1,
        240,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("fx.glitch.void_wash.opacity", "Void Wash opacity", 0.97, 0.00, 1.00),
    ParamDef("fx.glitch.void_wash.exp", "Void Wash curve exp", 0.75, 0.01, 4.0),
    ParamDef("fx.glitch.void_wash.tint.mult", "Void Wash tint mult", 0.62, 0.00, 2.00),
    ParamDef(
        "fx.glitch.void_wash.strips.count",
        "Void Wash strips count",
        10,
        0,
        64,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("fx.glitch.void_wash.strips.alpha", "Void Wash strips alpha", 0.88, 0.00, 2.00),
    ParamDef("fx.glitch.void_wash.sparks.count.mult", "Void Wash sparks count mult", 1.0, 0.00, 10.0),
    ParamDef("fx.glitch.void_wash.sparks.size.mult", "Void Wash sparks size mult", 1.0, 0.00, 10.0),
    ParamDef("fx.glitch.void_wash.sparks.alpha.mult", "Void Wash sparks alpha mult", 1.0, 0.00, 10.0),
    ParamDef("fx.glitch.void_wash.sparks.tile.px", "Void Wash sparks tile (px)", 2.0, 0.25, 32.0),
    ParamDef(
        "fx.glitch.postfx.tick_hz",
        "Post-FX tick Hz",
        24,
        1,
        240,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("fx.glitch.scanline.strength", "Scanlines strength", 2.2, 0.00, 10.0),
    ParamDef("fx.glitch.scanline.spacing.px", "Scanlines spacing (px)", 4.0, 1.0, 64.0),
    ParamDef("fx.glitch.scanline.drift.pt_s", "Scanlines drift speed", 14.0, 0.00, 240.0),
    ParamDef("fx.glitch.scanline.thickness.mult", "Scanlines thickness mult", 1.0, 0.00, 10.0),
    ParamDef("fx.glitch.edge_noise.width.frac", "Edge noise width (screen %)", 0.02, 0.00, 0.25),
    ParamDef("fx.glitch.edge_noise.width.min_px", "Edge noise min width (px)", 10.0, 0.0, 2000.0),
    ParamDef("fx.glitch.edge_noise.width.max_px", "Edge noise max width (px)", 24.0, 0.0, 4000.0),
    ParamDef(
        "fx.glitch.edge_noise.count",
        "Edge noise block count",
        26,
        0,
        20000,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("fx.glitch.edge_noise.alpha", "Edge noise alpha", 0.042, 0.0, 1.00),
    ParamDef("fx.glitch.edge_noise.size.mult", "Edge noise size mult", 1.0, 0.00, 20.0),
    ParamDef(
        "fx.glitch.grain.count",
        "Grain count",
        54,
        0,
        20000,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("fx.glitch.grain.rez.mult", "Grain rez mult", 2.0, 0.00, 20.0),
    ParamDef("fx.glitch.grain.alpha.base", "Grain alpha base", 0.008, 0.000, 1.000),
    ParamDef("fx.glitch.grain.alpha.rez.mult", "Grain alpha rez mult", 2.5, 0.00, 20.0),
    ParamDef("fx.glitch.grain.size.mult", "Grain size mult", 1.0, 0.00, 20.0),
    ParamDef("fx.glitch.tear.chance.base", "Tear chance base", 0.004, 0.000, 1.000),
    ParamDef("fx.glitch.tear.chance.rez.mult", "Tear chance rez mult", 4.0, 0.00, 50.0),
    ParamDef("fx.glitch.tear.alpha.base", "Tear alpha base", 0.01925, 0.000, 1.000),
    ParamDef("fx.glitch.tear.alpha.rez.mult", "Tear alpha rez mult", 1.81818181818, 0.00, 20.0),
    ParamDef("fx.glitch.tear.height.mult", "Tear height mult", 1.0, 0.00, 20.0),
    ParamDef("fx.glitch.tear.shift.px", "Tear shift (px)", 27.0, 0.0, 2000.0),
    ParamDef("fx.glitch.tear.shift.rez.mult", "Tear shift rez mult", 1.62962962963, 0.00, 20.0),
    ParamDef("fx.glitch.band.chance", "Interference band chance", 0.2, 0.000, 1.000),
    ParamDef("fx.glitch.band.chance.rez.mult", "Interference band chance rez mult", 0.5, 0.00, 10.0),
    ParamDef(
        "fx.glitch.band.count",
        "Interference band count",
        1,
        0,
        256,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "fx.glitch.band.count.rez.extra",
        "Interference band rez extra",
        2,
        0,
        256,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("fx.glitch.band.alpha.base_mult", "Interference band alpha base mult", 0.5, 0.00, 20.0),
    ParamDef("fx.glitch.band.alpha.rez.mult", "Interference band alpha rez mult", 2.0, 0.00, 20.0),
    ParamDef("fx.glitch.band.height.mult", "Interference band height mult", 1.0, 0.00, 20.0),
    ParamDef("fx.glitch.band.shift.base", "Interference band shift base", 5.0, 0.00, 2000.0),
    ParamDef("fx.glitch.band.shift.rez.mult", "Interference band shift rez mult", 2.0, 0.00, 20.0),
    ParamDef(
        "fx.glitch.macroblock.count",
        "Macroblock count",
        3,
        0,
        20000,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "fx.glitch.macroblock.count.rez.extra",
        "Macroblock rez extra",
        8,
        0,
        20000,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("fx.glitch.macroblock.alpha.base_mult", "Macroblock alpha base mult", 0.5, 0.00, 20.0),
    ParamDef("fx.glitch.macroblock.alpha.rez.mult", "Macroblock alpha rez mult", 2.0, 0.00, 20.0),
    ParamDef("fx.glitch.macroblock.size.mult", "Macroblock size mult", 1.0, 0.00, 20.0),
    ParamDef("fx.glitch.warp.barrel.base", "Warp barrel base", 0.0407055630936, -2.00, 2.00),
    ParamDef("fx.glitch.warp.wobble.amp", "Warp wobble amp", 0.461329715061, 0.00, 4.00),
    ParamDef("fx.glitch.warp.wobble.hz", "Warp wobble Hz", 0.15141955836, 0.00, 24.0),
    ParamDef("fx.glitch.warp.energy.channel.mult", "Warp energy channel mult", 8, 0.00, 40.0),
    ParamDef(
        "fx.glitch.warp.grid",
        "Warp grid segments",
        36,
        4,
        240,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("fx.glitch.beam.alpha", "Glitch beam alpha", 0.08, 0.00, 1.00),
    ParamDef("fx.glitch.beam.rez.mult", "Glitch beam rez mult", 2.25, 0.00, 20.0),
    ParamDef("fx.glitch.beam.thick.px", "Glitch beam thickness (px)", 1.15, 0.00, 120.0),
    ParamDef("fx.glitch.beam.alpha.glow", "Glitch beam glow alpha", 0.06, 0.000, 1.000),
    ParamDef("fx.glitch.beam.alpha.core", "Glitch beam core alpha", 0.12, 0.000, 1.000),
    ParamDef(
        "fx.glitch.spark.count",
        "Glitch sparkles count",
        22,
        0,
        20000,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("fx.glitch.spark.rez.mult", "Glitch sparkles rez mult", 2.4, 0.00, 20.0),
    ParamDef("fx.glitch.spark.size.base", "Glitch sparkles size base", 1.1, 0.1, 80.0),
    ParamDef("fx.glitch.spark.size.extra", "Glitch sparkles size range", 2.2, 0.0, 200.0),
    ParamDef("fx.glitch.spark.alpha.base", "Glitch sparkles alpha base", 0.03, 0.000, 1.000),
    ParamDef("fx.glitch.spark.alpha.extra", "Glitch sparkles alpha range", 0.075, 0.000, 1.000),
    ParamDef("fx.glitch.spark.spread.frac", "Glitch sparkles spread (screen %)", 0.45, 0.00, 5.00),
    ParamDef("fx.glitch.spark.density.exp", "Glitch spark density exponent", 1, 0.05, 12.0),
    ParamDef("fx.glitch.text.rate_hz.normal", "Text glitch rate Hz normal", 0.192616372392, 0.00, 240.0),
    ParamDef("fx.glitch.text.rate_hz.rez", "Text glitch rate Hz rezzing", 5.00802568218, 0.00, 240.0),
    ParamDef(
        "fx.glitch.text.max_chars",
        "Text glitch intensity",
        1,
        1,
        64,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("fx.channel_change.duration_s", "Channel change duration (s)", 0.642494969819, 0.02, 10.0),
    ParamDef("fx.channel_change.tint.hold.frac", "Tint hold fraction", 0, 0.00, 0.98),
    ParamDef("fx.channel_change.tint.fade.exp", "Tint fade exponent", 0.771327967807, 0.05, 12.0),
    ParamDef("fx.channel_change.tint.strength", "Tint strength", 1, 0.00, 2.00),
    ParamDef("fx.channel_change.tile.size", "Reveal tile size", 8.47058823529, 1.0, 128.0),
    ParamDef("fx.channel_change.cover.exp", "Reveal fade exponent", 4.71196078431, 0.01, 12.0),
    ParamDef("fx.channel_change.band.exp", "Reveal band exponent", 7.76823529412, 0.01, 12.0),
    ParamDef("fx.channel_change.feather.frac", "Reveal feather (screen %)", 0.725490196078, 0.00, 2.00),
    ParamDef("fx.channel_change.beam.thick.base", "Beam thickness base", 6, 0.00, 60.0),
    ParamDef("fx.channel_change.beam.thick.extra", "Beam thickness extra", 10.4150943396, 0.00, 240.0),
    ParamDef("fx.channel_change.beam.alpha.glow", "Beam glow alpha", 0.00404858299595, 0.000, 1.000),
    ParamDef("fx.channel_change.beam.alpha.core", "Beam core alpha", 0.0688259109312, 0.000, 1.000),
    ParamDef(
        "fx.channel_change.spark.count.base",
        "Sparkles count base",
        8548,
        0,
        20000,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef(
        "fx.channel_change.spark.count.extra",
        "Sparkles count extra",
        740,
        0,
        60000,
        is_int=True,
        fmt="{:.0f}",
    ),
    ParamDef("fx.channel_change.spark.size.base", "Sparkles size base", 1.17295081967, 0.1, 80.0),
    ParamDef("fx.channel_change.spark.size.extra", "Sparkles size range", 29.5546558704, 0.0, 200.0),
    ParamDef("fx.channel_change.spark.alpha.base", "Sparkles alpha base", 0, 0.000, 1.000),
    ParamDef("fx.channel_change.spark.alpha.extra", "Sparkles alpha range", 0.0303643724696, 0.000, 1.000),
    ParamDef("fx.channel_change.spark.spread.frac", "Sparkles spread (screen %)", 0.769230769231, 0.00, 5.00),
    ParamDef("fx.channel_change.spark.density.exp", "Spark density exponent", 1.50141700405, 0.05, 12.0),
    ParamDef("fx.channel_change.warp.barrel.extra", "Channel warp barrel extra", 0.417910447761, -4.00, 4.00),
    ParamDef("fx.channel_change.warp.decay.exp", "Channel warp decay exponent", 1.15, 0.01, 12.0),
    ParamDef("fx.glitch.vignette.strength", "Vignette strength", 0.43137254902, 0.00, 2.00),
    ParamDef("fx.glitch.vignette.thickness.frac", "Vignette thickness (screen %)", 0.264150943396, 0.00, 2.00),
    ParamDef("fx.glitch.vignette.thickness.min_px", "Vignette min thickness (px)", 803.921568627, 0.0, 2000.0),
    ParamDef("fx.glitch.vignette.thickness.max_px", "Vignette max thickness (px)", 823.529411765, 0.0, 4000.0),
    ParamDef("fx.glitch.vignette.falloff.exp", "Vignette falloff exp", 2.74460784314, 0.05, 12.0),
    ParamDef("fx.glitch.ssao.strength", "SSAO strength", 0.35, 0.00, 2.00),
    ParamDef("fx.glitch.ssao.radius.blocks", "SSAO radius (blocks)", 0.75, 0.00, 16.0),
    ParamDef("fx.glitch.ssao.bias", "SSAO bias (blocks)", 0.02, 0.0, 1.0),
    ParamDef("fx.glitch.ssao.brightness", "SSAO brightness compensation", 1.12, 0.0, 4.0),
]


DEFAULT_PARAM_HELP: dict[str, str] = {
    "ui.slider.brightness": (
        "Controls how bright the slider tracks/knobs are in the kValue window.\n"
        "Turn it up if the sliders are hard to see; turn it down if the UI is too glowy.\n"
        "This only affects the kValue window widgets, not the main viewer.\n"
        "Try 1.0–1.6 for normal, 2–4 for “I’m tired and I need it loud”."
    ),
    "ui.font.scale": (
        "Scales text and spacing across the whole app (viewer, worldgen editor, kValue).\n"
        "Turn it up for readability; turn it down if you want more rows on screen.\n"
        "If layouts feel cramped, increase this before resizing windows.\n"
        "Try 1.2–1.6 for laptop, 1.8–2.3 for couch distance."
    ),
    "ui.term.bg.luma": (
        "Terminal UI background brightness for the glyph-grid panels (sidebar + kValue).\n"
        "0 = black background, 1 = white background.\n"
        "If you want classic terminal vibes, try 0.0–0.1.\n"
        "If you want the current ‘paper’ look, try ~0.9."
    ),
    "ui.term.fg.luma": (
        "Terminal UI text brightness for the glyph-grid panels (sidebar + kValue).\n"
        "0 = black text, 1 = white text.\n"
        "For white-on-black terminals: set this to 1.0 and bg.luma near 0.\n"
        "For black-on-light: set this near 0.05–0.12 and bg.luma near 0.85–0.95."
    ),
    "ui.term.selection.mix": (
        "How strong the selection highlight is in the terminal panels.\n"
        "0 keeps the selection background identical to the panel background.\n"
        "1 pushes selection all the way to the text color (very strong).\n"
        "Try 0.08–0.18 for subtle, 0.25+ for loud."
    ),
    "ui.term.accent.mix": (
        "How much Enderpurple bleeds into the terminal accents (cursor/knob/scroll thumb).\n"
        "0 = grayscale terminal (almost no purple). 1 = full Enderpurple accents.\n"
        "Try 0.05–0.20 for ‘sparingly’, 0.30+ for ‘glow stick’."
    ),
    "render.vsync": (
        "Syncs rendering to your monitor refresh to prevent tearing.\n"
        "Off can reduce latency and raise FPS, but can also cause tearing and extra GPU heat.\n"
        "This is applied when windows are created—close/reopen the viewer to see the change.\n"
        "Try 1 for normal use; try 0 when profiling performance."
    ),
    "render.frame_cap_hz": (
        "Caps how often frames are rendered, without changing the simulation/update tick cadence.\n"
        "Use this to reduce GPU load while keeping rez, environment updates, and input logic running normally.\n"
        "0 means tick-paced rendering (no extra cap); positive values set a target render cadence in Hz.\n"
        "Try 30 for quieter laptops, 60 for normal interactive use."
    ),
    "effects.master_enabled": (
        "Master kill-switch for optional rendering FX (post-fx, glitch, channel-change overlays).\n"
        "1 keeps the full visual stack enabled; 0 forces those effects off while keeping core world rendering/editing intact.\n"
        "On macOS (darwin) the default is 0 unless you explicitly set this key in your params file.\n"
        "Use this first when you need a quick performance fallback."
    ),
    "input.macos.gestures.enabled": (
        "Master toggle for macOS Cocoa gesture hooks (pinch/rotate/trackpad pan).\n"
        "1 enables gesture recognizers on macOS; 0 keeps gestures disabled.\n"
        "This is kValue-only and defaults to 0."
    ),
    "build.hover_pick.enabled": (
        "Controls continuous hover picking raycasts used for build hover highlights.\n"
        "1 keeps hover targeting on; 0 disables hover picking while preserving click-to-place/break behavior.\n"
        "This is kValue-only and defaults to 1."
    ),
    "walk.body.debug_draw": (
        "Renders the walk collision body in the 3D viewport while walk mode is active.\n"
        "1 draws a green wireframe capsule-like body around the walk camera; 0 hides it.\n"
        "Use this to visually debug collisions, clipping, and body placement."
    ),
    "walk.body.radius.u": (
        "Horizontal radius (in world units) of the walk collision body.\n"
        "Increase if you clip through corners; decrease for tighter navigation.\n"
        "Typical values are 0.25–0.45."
    ),
    "walk.body.height.u": (
        "Vertical height (in world units) of the walk collision body.\n"
        "Increase for taller collision; decrease for tighter underhang clearance.\n"
        "Typical values are 1.6–2.0."
    ),
    "render.alpha_cutout.threshold": (
        "Cuts masked textures (plants, fences, grates) into “on/off” pixels like Minecraft.\n"
        "Higher values clip more (cleaner edges, but thinner plants); lower keeps more pixels (bushier, but halo risk).\n"
        "If you see dark fringes/ghost pixels, increase this a bit.\n"
        "Try 0.45–0.60 for most packs; 0.65+ if leaves look mushy."
    ),
    "ui.selection.border.frac": (
        "Thickness of the selection border, as a fraction of a block/slot size.\n"
        "This drives both the 3D hover box outline and the hotbar (build bar) slot borders.\n"
        "0.05 = 5% of a block.\n"
        "Try 0.03–0.07 for normal, 0.09+ for chunky."
    ),
    "camera.autoframe.cooldown_s": (
        "How long the app waits after you move the camera before doing any auto-framing.\n"
        "Increase this if the camera ever feels like it’s “fighting you”.\n"
        "Decrease it if you like the viewer to keep re-centering as things rez.\n"
        "Try 3–8 seconds for sane, 12+ if you’re doing careful camera work."
    ),
    "env.ground.patch.size": (
        "Terrain is generated in square patches of this many blocks per side.\n"
        "Smaller patches feel smoother and “paint in” nicely, but cost more CPU overhead.\n"
        "Larger patches are faster per block, but you’ll see chunkier pop-in.\n"
        "Try 6–12 for smooth; 16–32 for speed."
    ),
    "env.ground.patches_per_tick": (
        "How many terrain patches may be built each tick.\n"
        "Higher fills terrain faster but can steal time from rendering (jank).\n"
        "Lower makes terrain “grow” gradually and keeps input snappy.\n"
        "Try 20–80 for interactive; 120+ when you just want it done."
    ),
    "rez.fade_s": (
        "Duration of the rez reveal effect (terrain + structure changes).\n"
        "Short = things snap in quickly; long = dreamy slow emergence.\n"
        "If you notice performance dips while new stuff appears, try shortening this.\n"
        "Try 1–2 seconds for subtle; 3–6 seconds for cinematic."
    ),
    "rez.fade.mode": (
        "Selects the style of the rez fade.\n"
        "1 = stipple fade (screen-door / dither mask): fast, Minecraft-ish, uses depth test naturally.\n"
        "0 = alpha fade (smooth transparency): softer, but can require blending/sorting and may look ‘ghosty’.\n"
        "This affects terrain rez, structure change fades, and live rez previews.\n"
        "Try 1 for the Ender Terminal vibe."
    ),
    "rez.fade.stipple.style": (
        "Chooses what the stipple fade pattern looks like.\n"
        "0 = ordered dither (Bayer): cleaner, more ‘classic’ screen-door.\n"
        "1 = static (square pixel noise): chunkier and easier to see as a glitch.\n"
        "2 = single square: one blocky ‘window’ that shrinks/grows as the fade changes (very obvious).\n"
        "If you can’t tell it’s stipple, set this to 1.\n"
        "Try 2 for loud debug, 0 for subtle."
    ),
    "rez.fade.stipple.cell": (
        "Controls the size of the ‘pixels’ in stipple fade.\n"
        "1 = tiny (almost invisible); higher = chunkier square blocks.\n"
        "If the fade feels too smooth, increase this.\n"
        "Try 3–5 for visible static; 6–8 for aggressive chunky dither."
    ),
    "rez.fade.stipple.lvl_jitter": (
        "Adds a small random offset to the effective stipple coverage level.\n"
        "This helps break up ‘see-through tunnels’ by making nearby fade bands use slightly different masks.\n"
        "0 disables the jitter.\n"
        "Try 1–3 for subtle de-sync, 4–8 for strong de-sync."
    ),
    "rez.fade.stipple.flicker_hz": (
        "How fast the stipple pattern changes over time.\n"
        "Higher values look more like fast TV static; lower values look more like a slow crawl.\n"
        "If it feels ‘frozen’, increase this.\n"
        "Try 24 for gentle, 60 for lively, 120+ for violent static."
    ),
    "rez.fade.stipple.square.exp": (
        "For ‘single square’ stipple style: shapes how quickly the visible square shrinks as the fade weakens.\n"
        "Higher values make the square collapse faster near the end (more dramatic); lower values feel more linear.\n"
        "This only matters when `rez.fade.stipple.style=2`.\n"
        "Try 0.8–1.2 for normal; 1.5–3 for punchy; 4+ for extreme."
    ),
    "rez.fade.stipple.square.jitter_px": (
        "For ‘single square’ stipple style: how far (in stipple pixels) the square can jitter each frame.\n"
        "Higher values look more like unstable TV lock; lower values feel steadier.\n"
        "This only matters when `rez.fade.stipple.style=2`.\n"
        "Try 0–2 for subtle, 3–6 for lively, 8+ for chaotic."
    ),
    "env.ground.radius": (
        "How far terrain extends around the loaded structures (in blocks).\n"
        "Increase it for a bigger “stage” around the model; decrease it for speed.\n"
        "Large values generate more ground and can cost memory/time.\n"
        "Try 20–40 for tight scenes; 60–120 for wide vistas."
    ),
    "env.ground.bottom": (
        "The underground floor: terrain columns extend down to this Y level.\n"
        "Lower values create deeper cliffs/strata; higher values make a thinner “slab world”.\n"
        "If you orbit under the world a lot, a lower bottom helps avoid seeing the cut.\n"
        "Try -64 for vanilla-ish, -256 for dramatic depth."
    ),
    "env.ground.strip_fade.height": (
        "Controls how high the underground walls fade from transparent to solid.\n"
        "Higher = taller fade region (so the cutaway disappears more gently).\n"
        "Lower = harder edge (more “slab on void”).\n"
        "Try 40–120 depending on how tall your terrain is."
    ),
    "env.ground.strip_fade.levels": (
        "Quantizes the strip fade into discrete bands.\n"
        "Low values give a stepped, layered look (very Minecraft); high values feel smoother.\n"
        "If the fade looks “noisy”, increase levels; if it looks too smooth, decrease.\n"
        "Try 8–20 for tasteful; 2–6 for crunchy stratification."
    ),
    "env.terrain.amp": (
        "Overall hill height in blocks.\n"
        "Turn it up for bigger mountains; turn it down for flatter, buildable terrain.\n"
        "If paths/roads float oddly, reducing amp makes projection problems less obvious.\n"
        "Try 8–24 for normal; 30–60 for dramatic."
    ),
    "env.terrain.scale": (
        "Horizontal feature size in blocks (bigger = smoother, slower-changing hills).\n"
        "If you see repetitive tight bumps, increase this.\n"
        "If the world feels too flat/boring, decrease this (or increase amp).\n"
        "Try 64–160 for natural hills; 200–600 for wide rolling plains."
    ),
    "env.terrain.octaves": (
        "How many layers of detail the terrain noise adds.\n"
        "More octaves = richer detail, but can look busy and cost a bit more CPU.\n"
        "If terrain looks like TV static, reduce octaves.\n"
        "Try 3–5 for clean; 6–8 for gnarly."
    ),
    "env.terrain.lacunarity": (
        "How quickly detail frequency increases per octave.\n"
        "Higher makes finer detail show up sooner (rougher); lower keeps detail broader.\n"
        "If the terrain gets “crispy” too fast, reduce this.\n"
        "Try 1.8–2.2 for standard; 2.5–3.5 for jagged."
    ),
    "env.terrain.h": (
        "How quickly amplitude decreases per octave (controls smoothness).\n"
        "Lower values keep higher-frequency detail subtle; higher values make it punchy.\n"
        "If the surface looks too noisy, reduce H.\n"
        "Try 0.7–1.2 for natural; 1.4+ for crunchy."
    ),
    "env.terrain.ridged.offset": (
        "Ridged-noise shaping: increases “peaks” vs “valleys”.\n"
        "Higher pushes toward sharper ridges; lower makes the ridges softer.\n"
        "If everything becomes spiky, reduce this.\n"
        "Try 0.8–1.4 for tasteful; 1.8+ for alien terrain."
    ),
    "env.terrain.ridged.gain": (
        "Ridged-noise contrast: how strong the ridge component is.\n"
        "Higher makes ridges pop more; lower blends them into smoother hills.\n"
        "If you get harsh creases everywhere, reduce gain.\n"
        "Try 1.2–2.5 for normal; 3–5 for aggressive."
    ),
    "rez.throttle.sleep_ms": (
        "How long the app pauses (milliseconds) when it decides to throttle heavy work.\n"
        "Increase this if rezzing makes the UI stutter; decrease it if rezzing feels too slow.\n"
        "If you want “always responsive”, prefer a slightly higher sleep.\n"
        "Try 10–30ms for smooth; 0–5ms for fast (but risk jank)."
    ),
    "rez.throttle.every": (
        "How often throttling happens (lower = more frequent small breaks).\n"
        "If rezzing feels like it freezes then catches up, decrease this.\n"
        "If rezzing is too slow but the UI is fine, increase this.\n"
        "Try 64–256 for smooth; 512+ for speed."
    ),
    "rez.pieces_per_s": (
        "How fast the live rez preview shows pool pieces being added.\n"
        "Lower values make the build feel “cinematic” and keep the UI responsive; higher values make it snap in.\n"
        "If you see stutters while rezzing, reduce this.\n"
        "Set to 0 to apply pieces as fast as they arrive (no preview throttling)."
    ),
    "fx.color.ender.r": (
        "Red channel of the Ender-purple accent used across UI and FX.\n"
        "Adjust R/G/B together to change hue; adjust all three up/down to change brightness.\n"
        "If purple looks too blue, raise R a bit; if it looks too magenta, reduce R.\n"
        "Try small moves (±0.05) to avoid wrecking the palette."
    ),
    "fx.color.ender.g": (
        "Green channel of the Ender-purple accent used across UI and FX.\n"
        "A little green shifts toward “electric violet”; too much makes it muddy.\n"
        "If purple feels too harsh, try adding a tiny bit of green.\n"
        "Try 0.00–0.10; keep it low for the classic Ender look."
    ),
    "fx.color.ender.b": (
        "Blue channel of the Ender-purple accent used across UI and FX.\n"
        "Higher blue makes it colder and more neon; lower blue makes it warmer/pinker.\n"
        "If your purple feels washed out, reduce B slightly and raise R.\n"
        "Try 0.9–1.2 for strong; 0.7–0.9 for warmer."
    ),
    "fx.color.accent.amber.r": (
        "Red channel of the amber accent used by some glitch sparkles/bands.\n"
        "This is a secondary palette highlight separate from Ender purple.\n"
        "If the amber feels too hot, reduce R slightly.\n"
        "Keep R near 1.0 for classic warm amber."
    ),
    "fx.color.accent.amber.g": (
        "Green channel of the amber accent.\n"
        "More green pushes it toward yellow; less green pushes toward orange.\n"
        "If you want a more golden highlight, raise G a bit.\n"
        "Small changes (±0.05) go a long way."
    ),
    "fx.color.accent.amber.b": (
        "Blue channel of the amber accent.\n"
        "More blue cools it toward beige; less blue makes it more saturated.\n"
        "If the amber looks muddy, lower B slightly.\n"
        "Try 0.25–0.45 for strong amber."
    ),
    "fx.color.accent.green.r": (
        "Red channel of the terminal-green accent.\n"
        "Higher red makes it minty/teal; lower red makes it more pure green.\n"
        "If it reads as cyan, reduce R a bit.\n"
        "Try 0.4–0.7 depending on taste."
    ),
    "fx.color.accent.green.g": (
        "Green channel of the terminal-green accent.\n"
        "This is usually near 1.0; lower values make it dim.\n"
        "If you want the green to pop, keep this high.\n"
        "Try 0.8–1.2 for strong."
    ),
    "fx.color.accent.green.b": (
        "Blue channel of the terminal-green accent.\n"
        "More blue makes it more aqua; less blue makes it more pure green.\n"
        "If it’s too cyan, reduce B slightly.\n"
        "Try 0.5–0.85 depending on taste."
    ),
    "fx.color.ender.yellow.r": (
        "Red channel of the Ender-yellow accent (borders/selection accents).\n"
        "Use small tweaks to shift between “aged terminal amber” and “sickly alien gold”.\n"
        "If yellow is too greenish, raise R a bit.\n"
        "Adjust with G/B to keep it feeling like one color."
    ),
    "fx.color.ender.yellow.g": (
        "Green channel of the Ender-yellow accent.\n"
        "More green pushes it toward chartreuse; less green makes it more amber.\n"
        "If the UI feels too “radioactive”, reduce G slightly.\n"
        "Try subtle changes (±0.03) to keep it classy."
    ),
    "fx.color.ender.yellow.b": (
        "Blue channel of the Ender-yellow accent.\n"
        "Blue lowers saturation and makes it paler; too much blue makes it gray.\n"
        "If the yellow is too intense, add a tiny bit of B.\n"
        "Keep B small for an authentic amber vibe."
    ),
    "fx.color.ender.pink.r": (
        "Red channel of the Ender-pink accent (warnings/secondary highlights).\n"
        "Higher R makes it hotter/stronger; lower R pushes toward lavender.\n"
        "If orange/pink UI accents feel too loud, reduce R a touch.\n"
        "Pair with G/B to keep it balanced."
    ),
    "fx.color.ender.pink.g": (
        "Green channel of the Ender-pink accent.\n"
        "More green makes it warmer (toward orange); less green makes it cooler (toward magenta).\n"
        "If you want a more “warning amber”, raise G slightly.\n"
        "Keep changes small to avoid turning it brown."
    ),
    "fx.color.ender.pink.b": (
        "Blue channel of the Ender-pink accent.\n"
        "More blue makes it more purple; less blue makes it more coral.\n"
        "If highlights are too pink, raise B a bit.\n"
        "Try 0.55–0.75 for the classic End palette."
    ),
    "fx.glitch.void_wash.warn_above.blocks": (
        "How early the Void Wash starts warning you as you approach the world bottom.\n"
        "Higher values fade in the warning sooner (more gentle); lower values make it sudden.\n"
        "If you keep getting surprised by the void effect, increase this.\n"
        "Try 24–64 for a roomy warning band."
    ),
    "fx.glitch.void_wash.tick_hz": (
        "How fast the Void Wash random pattern updates (ticks per second).\n"
        "Higher makes the wash feel more alive and jittery; lower makes it steadier.\n"
        "If the wash looks “frozen”, raise this; if it flickers too much, lower it.\n"
        "Try 30–90 for normal; 120+ for frantic."
    ),
    "fx.glitch.void_wash.opacity": (
        "Overall opacity of the Void Wash overlay when it is fully active.\n"
        "Higher makes the screen feel more “danger / out of bounds”; lower keeps things readable.\n"
        "If you want the warning to be unmistakable, raise this.\n"
        "Try 0.7–0.9 for strong; 0.3–0.6 for subtle."
    ),
    "fx.glitch.void_wash.exp": (
        "Curve exponent for how the Void Wash ramps in.\n"
        "Lower = it ramps in earlier and more aggressively; higher = it stays mild until the end.\n"
        "If you want a gentle early warning, increase this.\n"
        "Try 0.5–0.9 for punchy; 1.2–2.0 for gradual."
    ),
    "fx.glitch.void_wash.tint.mult": (
        "How bright the Ender-purple tint is inside the Void Wash.\n"
        "Higher makes the wash glow; lower makes it darker/sootier.\n"
        "If the wash feels too gray, raise this.\n"
        "Try 0.4–0.8 for dark; 0.9–1.3 for neon."
    ),
    "fx.glitch.void_wash.strips.count": (
        "How many vertical gradient strips are layered into the wash near the world bottom.\n"
        "More strips look smoother (and slightly busier); fewer strips look chunky.\n"
        "Set to 0 to disable the strip component.\n"
        "Try 6–16 for normal; 24+ for dense."
    ),
    "fx.glitch.void_wash.strips.alpha": (
        "Opacity of the gradient strip component of the Void Wash.\n"
        "Higher adds more “warning haze”; lower leaves mostly the flat wash + sparkles.\n"
        "If the wash looks too flat, raise this.\n"
        "Try 0.4–1.2 depending on taste."
    ),
    "fx.glitch.void_wash.sparks.count.mult": (
        "Multiplier for how many Void Wash sparkles are drawn.\n"
        "Higher looks more like buzzing static; lower looks calmer.\n"
        "If performance dips during wash, reduce this first.\n"
        "Try 0.5–1.5 for normal; 2+ for heavy."
    ),
    "fx.glitch.void_wash.sparks.size.mult": (
        "Multiplier for sparkle size inside the Void Wash.\n"
        "Small sparkles read as pixel noise; big sparkles read as chunky glitch blocks.\n"
        "If sparkles cover too much of the scene, reduce this.\n"
        "Try 0.6–1.4 for normal; 2+ for chunky."
    ),
    "fx.glitch.void_wash.sparks.alpha.mult": (
        "Multiplier for sparkle opacity inside the Void Wash.\n"
        "Higher makes the sparkles dominate; lower makes them a faint shimmer.\n"
        "If the wash feels too opaque/noisy, reduce this.\n"
        "Try 0.5–1.2 for normal; 2+ for loud."
    ),
    "fx.glitch.void_wash.sparks.tile.px": (
        "Base tile size (in pixels) used to size Void Wash sparkles.\n"
        "Smaller tiles look like fine static; larger tiles look like blocky noise.\n"
        "If you see tiny shimmering you don’t like, raise this.\n"
        "Try 1–3 for fine; 4–10 for chunky."
    ),
    "fx.glitch.postfx.tick_hz": (
        "How fast the post-FX overlay random pattern updates (ticks per second).\n"
        "Higher makes grain/edge-noise/tears change more rapidly; lower makes them steadier.\n"
        "If you want a 24fps VHS vibe, leave it near 24.\n"
        "Try 12–30 for film-y; 60+ for very jittery."
    ),
    "fx.glitch.scanline.strength": (
        "Overall intensity of the always-on CRT scanlines.\n"
        "Higher darkens and tints the lines more; lower makes them barely visible.\n"
        "Set to 0 to disable scanlines.\n"
        "Try 1.0–3.0 for subtle; 4+ for obvious."
    ),
    "fx.glitch.scanline.spacing.px": (
        "Distance between scanlines in pixels.\n"
        "Lower spacing = more lines (denser CRT feel); higher spacing = fewer lines.\n"
        "If scanlines look too “busy”, increase spacing.\n"
        "Try 3–6px for normal; 8–16px for subtle."
    ),
    "fx.glitch.scanline.drift.pt_s": (
        "How fast the scanlines drift vertically (screen units per second).\n"
        "Higher feels more alive/analog; lower feels stable.\n"
        "If the screen feels like it’s crawling, reduce this.\n"
        "Try 6–20 for normal; 0 to freeze."
    ),
    "fx.glitch.scanline.thickness.mult": (
        "Thickness multiplier for scanlines.\n"
        "Higher makes each line chunkier; lower makes them thinner.\n"
        "Set near 0 to effectively remove scanlines without touching strength.\n"
        "Try 0.6–1.6 for normal; 2+ for chunky."
    ),
    "fx.glitch.edge_noise.width.frac": (
        "How wide the edge-noise bands are, as a fraction of screen width.\n"
        "Higher makes the side static creep inward; lower keeps it tight to the edge.\n"
        "If side noise is distracting, reduce this.\n"
        "Try 0.01–0.03 for subtle; 0.05+ for bold."
    ),
    "fx.glitch.edge_noise.width.min_px": (
        "Minimum width (in pixels) for the edge-noise bands.\n"
        "Use this to keep edge noise visible even on small windows.\n"
        "If the band disappears when resizing, raise this.\n"
        "Try 6–24px for typical."
    ),
    "fx.glitch.edge_noise.width.max_px": (
        "Maximum width (in pixels) for the edge-noise bands.\n"
        "Caps how far the noise can expand on very large windows.\n"
        "If the noise gets too wide on big monitors, lower this.\n"
        "Try 16–48px for typical."
    ),
    "fx.glitch.edge_noise.count": (
        "How many edge-noise blocks are drawn per side.\n"
        "More looks richer but costs a bit more; fewer looks sparse.\n"
        "Set to 0 to disable the edge-noise blocks.\n"
        "Try 10–40 for subtle; 80+ for heavy."
    ),
    "fx.glitch.edge_noise.alpha": (
        "Opacity of the edge-noise blocks.\n"
        "Higher makes the side static more visible; lower makes it a faint whisper.\n"
        "If edges feel too clean, raise this.\n"
        "Try 0.02–0.08 depending on taste."
    ),
    "fx.glitch.edge_noise.size.mult": (
        "Size multiplier for the edge-noise blocks.\n"
        "Higher makes the blocks chunkier; lower makes them finer.\n"
        "If the edge noise looks like big rectangles, reduce this.\n"
        "Try 0.6–1.6 for normal."
    ),
    "fx.glitch.grain.count": (
        "How many full-screen grain blocks are drawn per frame.\n"
        "More grain looks richer but costs more; less grain is cleaner.\n"
        "If performance is an issue, lower count before lowering other FX.\n"
        "Try 20–120 for subtle; 300+ for heavy."
    ),
    "fx.glitch.grain.rez.mult": (
        "Multiplier for grain count while rezzing.\n"
        "Higher makes busy work feel more intense; lower keeps it readable.\n"
        "If rezzing gets too noisy, reduce this.\n"
        "Try 1–3 for normal; 4+ for frantic."
    ),
    "fx.glitch.grain.alpha.base": (
        "Base opacity of the full-screen grain.\n"
        "Higher makes the whole screen feel noisier; lower makes it cleaner.\n"
        "If you want a constant VHS texture, raise this slightly.\n"
        "Try 0.004–0.02 for normal."
    ),
    "fx.glitch.grain.alpha.rez.mult": (
        "Multiplier for grain opacity while rezzing.\n"
        "Higher makes rezzing feel louder; lower keeps the model readable.\n"
        "If rezzing becomes hard to see, reduce this first.\n"
        "Try 1–3 for normal; 4+ for loud."
    ),
    "fx.glitch.grain.size.mult": (
        "Size multiplier for grain blocks.\n"
        "Small grain feels like film/static; large grain feels like chunky compression.\n"
        "If grain looks too pixelated, reduce size.\n"
        "Try 0.6–1.6 for normal; 2+ for chunky."
    ),
    "fx.glitch.tear.chance.base": (
        "Probability per frame of spawning a VHS-style tear band (normal state).\n"
        "Higher makes tears happen more often; lower makes them rare.\n"
        "Set to 0 to disable the tear effect.\n"
        "Try 0.002–0.01 for occasional; 0.02+ for frequent."
    ),
    "fx.glitch.tear.chance.rez.mult": (
        "Multiplier for tear probability while rezzing.\n"
        "Higher makes rezzing feel more “unstable”; lower keeps it calm.\n"
        "If tears distract during build/debug, reduce this.\n"
        "Try 1–4 for normal; 6+ for loud."
    ),
    "fx.glitch.tear.alpha.base": (
        "Base opacity of the tear band.\n"
        "Higher makes the tear more obvious; lower makes it a faint shift.\n"
        "If the tear is invisible, raise this before raising chance.\n"
        "Try 0.01–0.05 for subtle; 0.08+ for bold."
    ),
    "fx.glitch.tear.alpha.rez.mult": (
        "Multiplier for tear opacity while rezzing.\n"
        "Higher makes tears pop; lower keeps them understated.\n"
        "If rezzing UI becomes hard to read, reduce this.\n"
        "Try 1–2.5 for normal; 4+ for loud."
    ),
    "fx.glitch.tear.height.mult": (
        "Height multiplier for the tear band.\n"
        "Higher makes the tear thicker; lower makes it a thin slice.\n"
        "If the tear covers too much of the model view, reduce this.\n"
        "Try 0.6–1.6 for normal."
    ),
    "fx.glitch.tear.shift.px": (
        "Horizontal shift amplitude of the tear in pixels.\n"
        "Higher shifts the model window more; lower is a gentle wiggle.\n"
        "If the tear feels too violent, reduce this.\n"
        "Try 12–60px for typical."
    ),
    "fx.glitch.tear.shift.rez.mult": (
        "Multiplier for tear shift amplitude while rezzing.\n"
        "Higher makes rezzing feel more unstable; lower keeps it calm.\n"
        "If you want only a small hint of motion, reduce this.\n"
        "Try 1–2 for normal; 3+ for loud."
    ),
    "fx.glitch.band.chance": (
        "Chance per frame of spawning rolling interference bands (model view).\n"
        "Higher creates more VHS-style “rolling” artifacts.\n"
        "Set to 0 to disable the interference band effect.\n"
        "Try 0.05–0.25 for normal."
    ),
    "fx.glitch.band.chance.rez.mult": (
        "Multiplier for interference-band chance while rezzing.\n"
        "Lower values keep rezzing readable; higher values make it feel chaotic.\n"
        "If rezzing is too noisy, reduce this.\n"
        "Try 0.25–1.0 for calmer; 2+ for louder."
    ),
    "fx.glitch.band.count": (
        "How many interference bands are drawn when the effect triggers.\n"
        "Higher looks richer but can obscure the model.\n"
        "Set to 0 to disable bands even if chance is nonzero.\n"
        "Try 1–2 for subtle; 3–6 for heavy."
    ),
    "fx.glitch.band.count.rez.extra": (
        "Extra interference bands added during rezzing.\n"
        "Use this to make rezzing feel busier without changing idle behavior.\n"
        "If rezzing overwhelms the view, reduce this.\n"
        "Try 0–3 for normal."
    ),
    "fx.glitch.band.alpha.base_mult": (
        "Opacity multiplier for interference bands (normal state).\n"
        "Higher makes bands more visible; lower makes them faint.\n"
        "If bands look too harsh, reduce this.\n"
        "Try 0.3–0.8 for normal."
    ),
    "fx.glitch.band.alpha.rez.mult": (
        "Multiplier for interference band opacity while rezzing.\n"
        "Higher makes rezzing look more chaotic; lower keeps it readable.\n"
        "If the model becomes hard to see, reduce this.\n"
        "Try 1–2.5 for normal."
    ),
    "fx.glitch.band.height.mult": (
        "Height multiplier for interference bands.\n"
        "Higher makes bands thicker; lower makes them thin.\n"
        "If bands cover too much of the view, reduce this.\n"
        "Try 0.6–1.6 for normal."
    ),
    "fx.glitch.band.shift.base": (
        "Base horizontal shift amplitude of interference bands.\n"
        "Higher makes bands slide more; lower makes them steadier.\n"
        "If bands feel like they’re warping the picture too much, reduce this.\n"
        "Try 2–10 for subtle; 12+ for loud."
    ),
    "fx.glitch.band.shift.rez.mult": (
        "Multiplier for band shift amplitude while rezzing.\n"
        "Higher makes rezzing feel more unstable; lower keeps it calm.\n"
        "If you only want a hint of motion, reduce this.\n"
        "Try 1–2.5 for normal."
    ),
    "fx.glitch.macroblock.count": (
        "How many macroblock “compression dropouts” are drawn per frame (normal state).\n"
        "More looks more like corrupted video; fewer is cleaner.\n"
        "Set to 0 to disable macroblocks.\n"
        "Try 1–6 for subtle; 12+ for heavy."
    ),
    "fx.glitch.macroblock.count.rez.extra": (
        "Extra macroblocks drawn while rezzing.\n"
        "Use this to make rezzing feel more “busy” without changing idle.\n"
        "If rezzing gets too dark/blocky, reduce this.\n"
        "Try 0–12 for normal."
    ),
    "fx.glitch.macroblock.alpha.base_mult": (
        "Opacity multiplier for macroblocks (normal state).\n"
        "Higher makes the dropouts darker/more visible.\n"
        "If macroblocks are too distracting, reduce this.\n"
        "Try 0.3–0.8 for normal."
    ),
    "fx.glitch.macroblock.alpha.rez.mult": (
        "Multiplier for macroblock opacity while rezzing.\n"
        "Higher makes rezzing look more corrupted; lower keeps it readable.\n"
        "If the view gets too dark, reduce this.\n"
        "Try 1–2.5 for normal."
    ),
    "fx.glitch.macroblock.size.mult": (
        "Size multiplier for macroblock dropouts.\n"
        "Small blocks look like fine compression noise; large blocks look like big missing chunks.\n"
        "If the effect looks too chunky, reduce size.\n"
        "Try 0.6–1.6 for normal."
    ),
    "fx.glitch.warp.barrel.base": (
        "Baseline barrel distortion on the 3D viewer (like an old CRT lens).\n"
        "Turn it up for stronger curvature; turn it down for a flatter, more modern look.\n"
        "Too high can make straight lines feel sickly; too low loses the “TV tube” vibe.\n"
        "Try 0.02–0.08 for tasteful; 0.12+ for extreme."
    ),
    "fx.glitch.warp.wobble.amp": (
        "How much the 3D viewer gently warps over time.\n"
        "Higher makes the screen feel alive; lower makes it stable for careful inspection.\n"
        "If the model feels like it’s swimming, reduce this.\n"
        "Try 0.2–0.6 for subtle motion; 1.0+ for noticeable warble."
    ),
    "fx.glitch.warp.wobble.hz": (
        "How fast the warp motion runs (cycles per second).\n"
        "Low values feel like slow analog drift; higher values feel like energetic instability.\n"
        "If it’s distracting, reduce Hz before reducing amplitude.\n"
        "Try 0.05–0.3 for drift; 0.8–2.0 for jitter."
    ),
    "fx.glitch.warp.energy.channel.mult": (
        "How much extra warp energy is injected during channel change (load/switch).\n"
        "Higher = the TV knob snap is wilder; lower = calmer transitions.\n"
        "If channel changes feel too conservative, raise this.\n"
        "Try 4–10 for punchy; 12+ for chaos."
    ),
    "fx.glitch.warp.grid": (
        "Resolution of the warp mesh.\n"
        "Higher = smoother distortion (and potentially more GPU cost); lower = chunkier, more glitchy.\n"
        "If you see visible blocky bending, increase this.\n"
        "Try 24–48 for normal; 60–120 for ultra-smooth."
    ),
    "fx.glitch.beam.alpha": (
        "Overall intensity of the horizontal beam/scanline glitch.\n"
        "Turn it up if you want more VHS/CRT energy; turn it down if it’s noisy.\n"
        "If beams are present but feel too “thin”, increase thickness instead.\n"
        "Try 0.02–0.10 for subtle; 0.15+ for obvious."
    ),
    "fx.glitch.beam.rez.mult": (
        "Multiplier for beam intensity while rezzing (busy work).\n"
        "Increase to make rezzing feel more “active”; decrease to keep rezzing readable.\n"
        "If the UI becomes hard to read during rezzing, reduce this.\n"
        "Try 1.5–3.0 for tasteful; 4+ for frantic."
    ),
    "fx.glitch.beam.thick.px": (
        "Base beam thickness in pixels.\n"
        "Thicker beams feel more like a CRT sweep; thinner beams feel like digital tearing.\n"
        "If you can’t see the beam, increase this before increasing alpha.\n"
        "Try 0.8–2.0px for normal; 3–8px for dramatic."
    ),
    "fx.glitch.beam.alpha.glow": (
        "Glow opacity around the beam.\n"
        "Higher glow feels softer and more neon; lower glow feels sharper.\n"
        "If beams look too “hard-edged”, raise glow.\n"
        "Try 0.02–0.10 depending on how neon you want it."
    ),
    "fx.glitch.beam.alpha.core": (
        "Core opacity of the beam (the bright center line).\n"
        "Higher core makes the beam read clearly; lower core makes it more atmospheric.\n"
        "If glow looks good but the beam is missing, raise core.\n"
        "Try 0.05–0.20 for readable beams."
    ),
    "fx.glitch.spark.count": (
        "How many sparkle glitches appear per frame (normal state).\n"
        "Increase for more “electric dust”; decrease for a cleaner UI.\n"
        "If the viewer feels busy even when idle, lower this.\n"
        "Try 10–40 for subtle; 80+ for heavy sparkle."
    ),
    "fx.glitch.spark.rez.mult": (
        "Sparkle multiplier while rezzing.\n"
        "Higher makes rezzing feel energized; lower keeps rezzing readable.\n"
        "If sparkles hide important geometry while loading, reduce this.\n"
        "Try 1.5–3.0 for nice; 5+ for chaos."
    ),
    "fx.glitch.spark.size.base": (
        "Base sparkle size in pixels.\n"
        "Small sparkles feel like static; big sparkles feel like lens artifacts.\n"
        "If you want glitter without blocking the model, keep this low.\n"
        "Try 0.8–2.0 for specks; 3–8 for chunky."
    ),
    "fx.glitch.spark.size.extra": (
        "Extra random sparkle size range.\n"
        "Higher adds more variety (some big pops); lower keeps everything uniform.\n"
        "If you want occasional “flash” moments, increase this.\n"
        "Try 1–4 for subtle variety; 10+ for big pops."
    ),
    "fx.glitch.spark.alpha.base": (
        "Base sparkle opacity.\n"
        "If sparkles are too faint to notice, increase this before increasing count.\n"
        "If sparkles are distracting, reduce alpha before reducing size.\n"
        "Try 0.01–0.05 for subtle; 0.08+ for obvious."
    ),
    "fx.glitch.spark.alpha.extra": (
        "Extra random sparkle opacity range.\n"
        "Higher gives occasional bright flashes; lower keeps sparkles consistent.\n"
        "If the scene has distracting “fireflies”, reduce this.\n"
        "Try 0.02–0.10 depending on how punchy you want it."
    ),
    "fx.glitch.spark.spread.frac": (
        "How far sparkles can spread across the screen (fraction of the view).\n"
        "Higher spreads them wider; lower keeps them clustered.\n"
        "If sparkles feel too concentrated, raise spread.\n"
        "Try 0.3–0.8 for normal; 1.2+ for wide scatter."
    ),
    "fx.glitch.spark.density.exp": (
        "Shapes sparkle density (bias toward horizon/center).\n"
        "Higher concentrates sparkles; lower distributes them more evenly.\n"
        "If sparkles look like a fog bank, reduce this.\n"
        "Try 0.8–1.8 for natural; 3+ for strong clustering."
    ),
    "fx.glitch.text.rate_hz.normal": (
        "How often text briefly flips into Ender-glyph variants when idle (per second, average).\n"
        "This is render-time only: the glitch lasts one frame, no timers or lingering state.\n"
        "If the UI feels too unstable, reduce this.\n"
        "Try 0.05–0.3 for occasional; 1–5 for noticeable."
    ),
    "fx.glitch.text.rate_hz.rez": (
        "How often text briefly flips into Ender-glyph variants during rezzing.\n"
        "Use this to make “busy” moments feel hotter without changing the base UI.\n"
        "If you can’t read status messages while loading, reduce this.\n"
        "Try 1–6 for energized; 10+ for aggressive."
    ),
    "fx.glitch.text.max_chars": (
        "Max number of characters that can glitch in a single frame burst.\n"
        "Higher makes the glitch feel like a stronger wave; lower keeps it subtle.\n"
        "If the UI becomes unreadable during spikes, reduce this first.\n"
        "Try 1–2 for subtle; 3–8 for obvious."
    ),
    "fx.channel_change.duration_s": (
        "How long the channel-change animation lasts when loading/switching models.\n"
        "Short feels snappy; long feels like a dramatic TV warm-up.\n"
        "If you switch models often, keep this shorter.\n"
        "Try 0.4–0.9s for normal; 1.5–3s for theatrical."
    ),
    "fx.channel_change.tint.hold.frac": (
        "How long the purple tint stays at full strength before it starts fading.\n"
        "Higher makes the start feel more “Ender vision”; lower makes it fade immediately.\n"
        "If the tint feels like it overstays its welcome, reduce this.\n"
        "Try 0.0–0.2 for quick; 0.3–0.6 for heavy tint."
    ),
    "fx.channel_change.tint.fade.exp": (
        "Fade curve for the tint.\n"
        "Higher values keep tint longer then drop quickly; lower values fade more evenly.\n"
        "If the last bit of tint lingers, increase the exponent.\n"
        "Try 0.8–2.0 for natural; 3+ for snap-off."
    ),
    "fx.channel_change.tint.strength": (
        "Strength of the initial purple tint.\n"
        "Higher makes the model start more “coated”; lower keeps colors closer to normal.\n"
        "If the tint crushes texture detail, reduce this.\n"
        "Try 0.6–1.2 for normal; 1.5+ for intense."
    ),
    "fx.channel_change.tile.size": (
        "Size of the reveal tiles (pixels).\n"
        "Smaller tiles = finer static; larger tiles = chunky mosaic reveal.\n"
        "If you see obvious tiling patterns, try slightly changing this.\n"
        "Try 4–12 for crisp static; 16–48 for chunky."
    ),
    "fx.channel_change.cover.exp": (
        "Overall timing curve for how quickly the model becomes fully revealed.\n"
        "Higher makes it stay hidden longer then pop; lower reveals earlier.\n"
        "If the reveal feels too sudden at the end, reduce this.\n"
        "Try 2–6 for punchy; 0.8–2 for smoother."
    ),
    "fx.channel_change.band.exp": (
        "Strength/shaping of the moving horizontal band in the reveal.\n"
        "Higher makes the band more pronounced; lower makes it a subtle hint.\n"
        "If the band is stealing attention from the model, reduce this.\n"
        "Try 2–8 depending on how “CRT sweep” you want it."
    ),
    "fx.channel_change.feather.frac": (
        "Softness of the reveal edge as a fraction of the screen.\n"
        "Higher feather = softer, smokier reveal; lower feather = harder wipe.\n"
        "If the reveal edge looks too “hard”, increase feather.\n"
        "Try 0.3–0.9 for soft; 0.05–0.2 for sharp."
    ),
    "fx.channel_change.beam.thick.base": (
        "Base thickness of channel-change beams.\n"
        "Increase for a stronger sweep line; decrease for subtle.\n"
        "If beams aren’t visible, raise thickness before raising alpha.\n"
        "Try 3–10 for normal; 12–30 for huge."
    ),
    "fx.channel_change.beam.thick.extra": (
        "Random extra beam thickness range.\n"
        "Higher values create more variation (some very fat beams).\n"
        "If beams look too uniform, increase this.\n"
        "Try 6–20 for variety; 40+ for wild."
    ),
    "fx.channel_change.beam.alpha.glow": (
        "Glow intensity for channel-change beams.\n"
        "Higher makes a softer neon look; lower makes it crisp.\n"
        "If beams look like a hard line, raise glow.\n"
        "Try 0.01–0.08 depending on neon preference."
    ),
    "fx.channel_change.beam.alpha.core": (
        "Core intensity for channel-change beams.\n"
        "Higher makes the sweep obvious; lower makes it subtle.\n"
        "If glow is visible but the beam isn’t, raise core.\n"
        "Try 0.03–0.15 for readable."
    ),
    "fx.channel_change.spark.count.base": (
        "Base sparkle count during channel change.\n"
        "Higher makes the reveal feel more “electrical”; lower makes it cleaner.\n"
        "If the reveal hides the model too much, reduce this.\n"
        "Try 1k–10k depending on how dense you like it."
    ),
    "fx.channel_change.spark.count.extra": (
        "Random extra sparkle count during channel change.\n"
        "Higher adds burstiness (sometimes very sparkly).\n"
        "If the reveal feels inconsistent, reduce this.\n"
        "Try 0–2k for controlled; 5k+ for chaos."
    ),
    "fx.channel_change.spark.size.base": (
        "Base sparkle size during channel change.\n"
        "Smaller feels like static; larger feels like chunks of light.\n"
        "If sparkles look like dust, raise size a bit.\n"
        "Try 0.8–2.0 for specks; 4–12 for chunky."
    ),
    "fx.channel_change.spark.size.extra": (
        "Random extra sparkle size during channel change.\n"
        "Higher introduces occasional huge flashes.\n"
        "If you want more “sparkle fireworks”, increase this.\n"
        "Try 4–20 for variety; 40+ for big events."
    ),
    "fx.channel_change.spark.alpha.base": (
        "Base sparkle opacity during channel change.\n"
        "If you can’t see the sparkles, raise this first.\n"
        "If sparkles dominate the scene, lower this first.\n"
        "Try 0.01–0.06 for subtle; 0.10+ for loud."
    ),
    "fx.channel_change.spark.alpha.extra": (
        "Random extra sparkle opacity during channel change.\n"
        "Higher creates occasional bright flashes; lower keeps it even.\n"
        "If the reveal has distracting strobe moments, reduce this.\n"
        "Try 0.02–0.08 for tasteful; 0.12+ for strobe."
    ),
    "fx.channel_change.spark.spread.frac": (
        "How widely sparkles can spread across the screen during channel change.\n"
        "Lower keeps them near the band; higher fills the whole view.\n"
        "If sparkles feel too confined, raise spread.\n"
        "Try 0.5–1.2 depending on taste."
    ),
    "fx.channel_change.spark.density.exp": (
        "Shapes where channel-change sparkles cluster.\n"
        "Higher concentrates sparkles; lower distributes evenly.\n"
        "If the band feels too busy, reduce this.\n"
        "Try 1–3 for natural; 4+ for strong clustering."
    ),
    "fx.channel_change.warp.barrel.extra": (
        "Extra barrel distortion applied at the start of channel change.\n"
        "Higher makes the TV tube “snap” into place more aggressively.\n"
        "If channel change looks too wobbly, reduce this.\n"
        "Try 0.2–0.8 for punch; 1.5+ for extreme."
    ),
    "fx.channel_change.warp.decay.exp": (
        "How quickly the channel-change warp settles down.\n"
        "Higher values settle faster (snappier); lower values linger (dreamier).\n"
        "If you want the warp to calm down sooner, increase this.\n"
        "Try 1–2 for normal; 3–6 for quick settle."
    ),
    "fx.glitch.vignette.strength": (
        "Overall strength of the Ender-tinted vignette over the 3D view.\n"
        "Turn it up for a heavier ‘coated glass’ look; down for a cleaner view.\n"
        "If the corners feel too dark or distracting, reduce strength first.\n"
        "Try 0.2–0.6 for subtle; 0.8–1.4 for dramatic."
    ),
    "fx.glitch.vignette.thickness.frac": (
        "How wide the vignette border is as a fraction of the screen.\n"
        "Increase to bring the dark border further into the frame; decrease to keep it on the edges.\n"
        "If the vignette looks boxy, adjust falloff too.\n"
        "Try 0.15–0.35 for normal; 0.45+ for heavy framing."
    ),
    "fx.glitch.vignette.thickness.min_px": (
        "Minimum vignette thickness in pixels (helps keep it visible on small windows).\n"
        "Increase if the vignette disappears when the window is small.\n"
        "Decrease if the vignette feels too thick at small sizes.\n"
        "Try 200–600 for typical laptop windows."
    ),
    "fx.glitch.vignette.thickness.max_px": (
        "Maximum vignette thickness in pixels (prevents it from becoming huge on large windows).\n"
        "Increase if big windows make the vignette too thin.\n"
        "Decrease if big windows make the vignette overpowering.\n"
        "Try 800–1800 depending on how big your monitor is."
    ),
    "fx.glitch.vignette.falloff.exp": (
        "How sharp the vignette edge is.\n"
        "Higher values = harder edge; lower values = softer gradient.\n"
        "If the vignette looks like a hard rectangle, lower the exponent.\n"
        "Try 1.2–3.0 for soft; 4+ for hard edge."
    ),
    "fx.glitch.ssao.strength": (
        "How strong the SSAO darkening is.\n"
        "Turn it up for deeper creases and contact shadows; down for a cleaner render.\n"
        "If everything looks dirty or over-contrasty, reduce strength first.\n"
        "Try 0.2–0.6 for subtle; 0.8–1.4 for dramatic."
    ),
    "fx.glitch.ssao.radius.blocks": (
        "Sets how far (in blocks) the SSAO ‘shadow’ reaches from edges and contact points.\n"
        "Turn it up for broader, softer darkening; turn it down for tight contact shadows.\n"
        "Too high makes the whole model look muddy; too low makes SSAO vanish.\n"
        "Try 0.6–1.0 for subtle, 1.2–2.0 for dramatic."
    ),
    "fx.glitch.ssao.bias": (
        "Depth bias to reduce self-occlusion / halos (in blocks).\n"
        "Increase bias if you see dark halos on flat faces; decrease if occlusion disappears.\n"
        "If SSAO looks detached from geometry, bias is usually too high.\n"
        "Try 0.01–0.05 as a starting range."
    ),
    "fx.glitch.ssao.brightness": (
        "Brightens the image after SSAO so shadows don’t crush the scene.\n"
        "Increase if SSAO makes everything too dark; decrease if the scene looks washed out.\n"
        "This is a global compensation, so use strength/radius for the ‘shape’ first.\n"
        "Try 1.0–1.3 for subtle compensation; 1.5+ for strong lift."
    ),
}


for _d in DEFAULT_PARAM_DEFS:
    _d.help = DEFAULT_PARAM_HELP.get(_d.key, _d.label)


DEFAULT_PARAM_ALIASES: dict[str, str] = {
    # Legacy flat keys.
    "ui_slider_brightness": "ui.slider.brightness",
    # Legacy text-glitch keys.
    "fx.text_glitch.rate_hz.normal": "fx.glitch.text.rate_hz.normal",
    "fx.text_glitch.rate_hz.rez": "fx.glitch.text.rate_hz.rez",
    "fx.text_glitch.max_chars": "fx.glitch.text.max_chars",
    "power_duration_s": "fx.channel_change.duration_s",
    "power_tile_size": "fx.channel_change.tile.size",
    "power_cover_exp": "fx.channel_change.cover.exp",
    "power_band_exp": "fx.channel_change.band.exp",
    "power_feather_frac": "fx.channel_change.feather.frac",
    "power_beam_thick_base": "fx.channel_change.beam.thick.base",
    "power_beam_thick_extra": "fx.channel_change.beam.thick.extra",
    "power_beam_glow_alpha": "fx.channel_change.beam.alpha.glow",
    "power_beam_core_alpha": "fx.channel_change.beam.alpha.core",
    "power_spark_count_base": "fx.channel_change.spark.count.base",
    "power_spark_count_extra": "fx.channel_change.spark.count.extra",
    "power_spark_size_base": "fx.channel_change.spark.size.base",
    "power_spark_size_extra": "fx.channel_change.spark.size.extra",
    "power_spark_alpha_base": "fx.channel_change.spark.alpha.base",
    "power_spark_alpha_extra": "fx.channel_change.spark.alpha.extra",
    "power_spark_max_dy_frac": "fx.channel_change.spark.spread.frac",
    # Legacy hierarchical keys.
    "fx.power_on.duration_s": "fx.channel_change.duration_s",
    "fx.power_on.tint.hold.frac": "fx.channel_change.tint.hold.frac",
    "fx.power_on.tint.fade.exp": "fx.channel_change.tint.fade.exp",
    "fx.power_on.tint.strength": "fx.channel_change.tint.strength",
    "fx.power_on.tile.size": "fx.channel_change.tile.size",
    "fx.power_on.cover.exp": "fx.channel_change.cover.exp",
    "fx.power_on.band.exp": "fx.channel_change.band.exp",
    "fx.power_on.feather.frac": "fx.channel_change.feather.frac",
    "fx.power_on.beam.thick.base": "fx.channel_change.beam.thick.base",
    "fx.power_on.beam.thick.extra": "fx.channel_change.beam.thick.extra",
    "fx.power_on.beam.alpha.glow": "fx.channel_change.beam.alpha.glow",
    "fx.power_on.beam.alpha.core": "fx.channel_change.beam.alpha.core",
    "fx.power_on.spark.count.base": "fx.channel_change.spark.count.base",
    "fx.power_on.spark.count.extra": "fx.channel_change.spark.count.extra",
    "fx.power_on.spark.size.base": "fx.channel_change.spark.size.base",
    "fx.power_on.spark.size.extra": "fx.channel_change.spark.size.extra",
    "fx.power_on.spark.alpha.base": "fx.channel_change.spark.alpha.base",
    "fx.power_on.spark.alpha.extra": "fx.channel_change.spark.alpha.extra",
    "fx.power_on.spark.spread.frac": "fx.channel_change.spark.spread.frac",
    "fx.power_on.spark.density.exp": "fx.channel_change.spark.density.exp",
    # Migration: Ender purple is now a shared top-level FX color.
    "fx.vignette.ender.color.r": "fx.color.ender.r",
    "fx.vignette.ender.color.g": "fx.color.ender.g",
    "fx.vignette.ender.color.b": "fx.color.ender.b",
    # Migration: vignette moved under fx.glitch.*
    "fx.vignette.ender.strength": "fx.glitch.vignette.strength",
    "fx.vignette.ender.thickness.frac": "fx.glitch.vignette.thickness.frac",
    "fx.vignette.ender.thickness.min_px": "fx.glitch.vignette.thickness.min_px",
    "fx.vignette.ender.thickness.max_px": "fx.glitch.vignette.thickness.max_px",
    "fx.vignette.ender.falloff.exp": "fx.glitch.vignette.falloff.exp",
    # Migration: SSAO radius now in world units (blocks).
    "fx.glitch.ssao.radius_px": "fx.glitch.ssao.radius.blocks",
    # Migration: baseline warp params moved under fx.glitch.warp.*
    "fx.channel_change.warp.barrel.base": "fx.glitch.warp.barrel.base",
    "fx.channel_change.warp.wobble.amp": "fx.glitch.warp.wobble.amp",
    "fx.channel_change.warp.wobble.hz": "fx.glitch.warp.wobble.hz",
    "fx.channel_change.warp.grid": "fx.glitch.warp.grid",
    # Migration: unify terrain + structure rez fades.
    "env.ground.rez_fade_s": "rez.fade_s",
    # Migration: terrain stipple toggle became app-wide rez fade mode.
    "env.ground.rez_fade.stipple": "rez.fade.mode",
    # Migration: hover/selection border thickness is now in block units.
    "render.target_box.line_width": "ui.selection.border.frac",
}


LEGACY_DEFAULT_PARAM_PATH = Path.home() / ".config" / "nbttool" / "params.json"
DEFAULT_PARAM_PATH = Path.home() / ".config" / "enderterm" / "params.json"


def load_default_param_store(path: Path | None = None, *, platform: str | None = None) -> ParamStore:
    defaults_path = Path(__file__).with_name("params.defaults.json")
    if not defaults_path.is_file():
        defaults_path = None
    target_path = path or DEFAULT_PARAM_PATH
    if path is None and (not DEFAULT_PARAM_PATH.is_file()) and LEGACY_DEFAULT_PARAM_PATH.is_file():
        try:
            DEFAULT_PARAM_PATH.parent.mkdir(parents=True, exist_ok=True)
            DEFAULT_PARAM_PATH.write_text(LEGACY_DEFAULT_PARAM_PATH.read_text(), encoding="utf-8")
        except Exception:
            pass
    store = ParamStore(
        DEFAULT_PARAM_DEFS,
        target_path,
        defaults_path=defaults_path,
        aliases=DEFAULT_PARAM_ALIASES,
    )
    if (not effects_master_default_enabled(platform=platform)) and (
        not store.has_explicit_value(FX_MASTER_ENABLED_KEY)
    ):
        store.set(FX_MASTER_ENABLED_KEY, 0.0)
    return store
