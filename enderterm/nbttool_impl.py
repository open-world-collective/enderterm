#!/usr/bin/env python3
#
# NOTE: Implementation moved here from the legacy `enderterm/nbttool.py` wrapper.
# That original file remains as a thin compatibility entrypoint.

from __future__ import annotations

import argparse
import colorsys
import functools
from collections import deque
import ctypes
import gzip
import hashlib
import heapq
import io
import json
import math
import multiprocessing
import os
import random
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import queue as py_queue
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable, Iterable, Literal, TypeVar

# When executed via the legacy `python enderterm/nbttool.py` entrypoint,
# sys.path[0] is `enderterm/` which breaks `import enderterm.*` package imports.
# Add the repo root.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nbtlib

from enderterm.blockstate import (
    _block_id_base,
    _block_state_id,
    _block_state_id_from_json_state,
    _build_place_block_id_for_face,
    _parse_block_state_id,
)
from enderterm.core_types import BlockEntityInstance, BlockInstance, EntityInstance, Structure, Vec3i
from enderterm.datapack import (
    DatapackSource,
    JigsawDatapackIndex,
    PackStack,
    PackStackSource,
    canonical_processor_list_json,
    canonical_structure_template_nbt,
    canonical_template_pool_json,
    canonical_worldgen_structure_json,
    ensure_datapack_skeleton,
    iter_canonical_paths_in_source,
    list_processor_lists,
    list_structure_templates,
    list_template_pools,
    list_worldgen_jigsaw_structures,
    list_worldgen_structures,
)
from enderterm.jigsaw import (
    CappedProcessor,
    JigsawConnector,
    JigsawExpansionState,
    PoolDefinition,
    PoolElement,
    ProcessorPipeline,
    ProcessorSpec,
    RuleProcessor,
    RuleSpec,
    StructureTemplate,
    _block_id_from_jigsaw_final_state,
    apply_jigsaw_final_states_to_blocks,
    extract_jigsaw_connectors,
    parse_structure_template,
)
from enderterm.structure_nbt import (
    AUTO_SURFACE_THRESHOLD,
    GZIP_MAGIC,
    NEIGHBORS_6,
    apply_render_mode,
    filter_surface_blocks,
    load_nbt,
    load_nbt_bytes,
    parse_structure,
    structure_to_nbt_root,
)
from enderterm import params as params_mod
from enderterm.params import DEFAULT_PARAM_ALIASES, DEFAULT_PARAM_DEFS, DEFAULT_PARAM_HELP, ParamDef, ParamStore
from enderterm.terrain import (
    ENV_HEIGHT_MAX_DELTA,
    ENVIRONMENT_PRESETS,
    WORLD_MIN_Y,
    EnvironmentDecorBlock,
    EnvironmentDecorConfig,
    EnvironmentPreset,
    _ENV_DECOR_ALIASES,
    clamp_terrain_delta,
    env_height_offset,
    infer_environment_preset_name,
    load_environments_config,
)
from enderterm.util import _stable_seed
from enderterm import fx as fx_mod
from enderterm import debug_window as debug_window_mod
from enderterm.geom import (
    Mat4,
    _apply_element_rotation,
    _mat4_apply_point,
    _mat4_from_quat_xyzw,
    _mat4_identity,
    _mat4_mul,
    _mat4_scale,
    _mat4_translate,
    _nbt_float_n,
    _nbt_to_plain,
    _tri_normal,
)
from enderterm import jigsaw_editor_window as jigsaw_editor_window_mod
from enderterm import kvalue_window as kvalue_window_mod
from enderterm.mc_geometry import (
    FACE_DIRS,
    FACE_NEIGHBOR_DELTA,
    FACE_NORMALS,
    TextureFace,
    _UNIT_CUBE_BOUNDS,
    _UNIT_CUBE_FACE_QUADS,
    _UNIT_CUBE_FACE_UV_TRI,
    _UNIT_CUBE_UV_QUAD_DEFAULT,
    _cube_face_quad_points,
    _cube_face_uv_quad,
    _cube_face_uv_tri,
    _default_uv_rect_for_face,
    _element_face_points,
    _face_uv_axes,
    _uv_quad_from_rect,
    _uv_tri_for_face_rect,
)
from enderterm.mc_models import (
    MinecraftResourceResolver,
    ResolvedBlockAppearance,
    ResolvedBlockModel,
    ResolvedBlockModelPart,
    ResolvedModel,
    _block_model_bottom_coverage_frac,
    _block_model_is_full_cube,
    _compute_internal_face_cull_for_elements,
    _model_is_axis_aligned,
    _model_is_full_cube,
)
from enderterm.core_dump import (
    CORE_DUMP_SCHEMA_VERSION,
    CoreFxData,
    CoreMeshBuild,
    CoreMeshPart,
    CoreSceneData,
    core_build_mesh_for_structure,
    core_build_scene,
    dump_core_json,
    dump_datapack_core_json,
    dump_structure_core_json,
)
from enderterm import core_dump as core_dump_mod
from enderterm.mc_tint import _COLORMAP_CACHE, _decode_png_rgba8, _sample_colormap, _tint_rgb
from enderterm.mc_source import TextureSource, open_in_viewer
from enderterm.ui_anim import (
    TerminalTheme,
    Tween,
    _clamp01,
    _luma01_rgba,
    _mix_rgba,
    _mix_u8,
    _termui_theme_from_store,
    _u8_from01,
    ease_linear,
    ease_smoothstep,
)
from enderterm import render_world as render_world_mod

ENV_GROUND_THICKNESS = 32
ENV_GROUND_RADIUS = 30

DEFAULT_PARAM_PATH = params_mod.DEFAULT_PARAM_PATH


def load_default_param_store(path: Path | None = None) -> ParamStore:
    return params_mod.load_default_param_store(path or DEFAULT_PARAM_PATH)


def dump_structure_core_json(
    nbt_path: Path,
    *,
    mode: str,
    auto_threshold: int,
    textured: bool,
    minecraft_jar: Path | None,
    out_path: Path,
) -> None:
    return core_dump_mod.dump_structure_core_json(
        nbt_path,
        mode=mode,
        auto_threshold=auto_threshold,
        textured=textured,
        minecraft_jar=minecraft_jar,
        out_path=out_path,
        param_store=load_default_param_store(),
    )


def dump_datapack_core_json(
    datapack_path: Path,
    *,
    mode: str,
    auto_threshold: int,
    textured: bool,
    minecraft_jar: Path | None,
    select: str | None,
    out_path: Path,
) -> None:
    return core_dump_mod.dump_datapack_core_json(
        datapack_path,
        mode=mode,
        auto_threshold=auto_threshold,
        textured=textured,
        minecraft_jar=minecraft_jar,
        select=select,
        out_path=out_path,
        param_store=load_default_param_store(),
    )

def _polygon_stipple_pattern(
    level: int,
    *,
    phase_x: int = 0,
    phase_y: int = 0,
    seed: int = 0,
    style: int = 0,
    cell: int = 1,
    square_exp: float = 1.0,
    square_jitter: int = 0,
) -> ctypes.Array[ctypes.c_ubyte]:
    return fx_mod.polygon_stipple_pattern(
        level,
        phase_x=phase_x,
        phase_y=phase_y,
        seed=seed,
        style=style,
        cell=cell,
        square_exp=square_exp,
        square_jitter=square_jitter,
    )


def _height_stack_top_y(*, base_y: int, thickness: int, extra: int) -> int:
    height = int(thickness) + int(extra)
    if height < 1:
        height = 1
    return int(base_y) + int(height) - 1


def _vec_add(a: Vec3i, b: Vec3i) -> Vec3i:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _vec_neg(v: Vec3i) -> Vec3i:
    return (-v[0], -v[1], -v[2])


_CoordT = TypeVar("_CoordT", int, float)


def _rotate_y_components(
    x: _CoordT,
    y: _CoordT,
    z: _CoordT,
    quarters: int,
) -> tuple[_CoordT, _CoordT, _CoordT]:
    q = quarters % 4
    if q == 0:
        return (x, y, z)
    if q == 1:
        return (z, y, -x)
    if q == 2:
        return (-x, y, -z)
    return (-z, y, x)


def _rotate_y_vec(v: Vec3i, quarters: int) -> Vec3i:
    return _rotate_y_components(v[0], v[1], v[2], quarters)


def _rotate_y_pos(p: Vec3i, quarters: int) -> Vec3i:
    return _rotate_y_vec(p, quarters)


def _rotate_y_vec_f(v: tuple[float, float, float], quarters: int) -> tuple[float, float, float]:
    return _rotate_y_components(v[0], v[1], v[2], quarters)


def _jigsaw_expand_mod() -> object:  # pragma: no cover
    from enderterm import jigsaw_expand as _mod

    # Keep patchable globals (like `env_height_offset`) in sync for tests and
    # callers that monkeypatch `nbttool_impl.*` rather than the extracted module.
    try:
        setattr(_mod, "env_height_offset", env_height_offset)
    except Exception:
        pass
    return _mod


def _call_jigsaw_expand(name: str, *args, **kwargs):  # pragma: no cover
    _mod = _jigsaw_expand_mod()
    return getattr(_mod, name)(*args, **kwargs)


def _choose_weighted(*args, **kwargs):  # pragma: no cover
    return _call_jigsaw_expand("_choose_weighted", *args, **kwargs)


def _apply_rule_processor_to_block(*args, **kwargs):  # pragma: no cover
    return _call_jigsaw_expand("_apply_rule_processor_to_block", *args, **kwargs)


def _apply_processor_pipeline_to_blocks(*args, **kwargs):  # pragma: no cover
    return _call_jigsaw_expand("_apply_processor_pipeline_to_blocks", *args, **kwargs)


def _placed_template_blocks(*args, **kwargs):  # pragma: no cover
    return _call_jigsaw_expand("_placed_template_blocks", *args, **kwargs)


def build_jigsaw_expanded_structure(*args, **kwargs):  # pragma: no cover
    return _call_jigsaw_expand("build_jigsaw_expanded_structure", *args, **kwargs)


def _rez_worker_main(*args, **kwargs):  # pragma: no cover
    return _call_jigsaw_expand("_rez_worker_main", *args, **kwargs)
def view_structure_opengl(*args, **kwargs):  # pragma: no cover
    from enderterm import structure_viewer as _mod
    return _mod.view_structure_opengl(*args, **kwargs)


def view_datapack_opengl(*args, **kwargs):  # pragma: no cover
    from enderterm import datapack_viewer as _mod
    return _mod.view_datapack_opengl(*args, **kwargs)


def _call_usdz(name: str, *args, **kwargs):  # pragma: no cover
    from enderterm import usdz as _mod
    return getattr(_mod, name)(*args, **kwargs)


def find_minecraft_client_jar(*args, **kwargs):  # pragma: no cover
    return _call_usdz("find_minecraft_client_jar", *args, **kwargs)


def _stable_rgb(*args, **kwargs):  # pragma: no cover
    return _call_usdz("_stable_rgb", *args, **kwargs)


def _material_name(*args, **kwargs):  # pragma: no cover
    return _call_usdz("_material_name", *args, **kwargs)


def _material_name_prefixed(*args, **kwargs):  # pragma: no cover
    return _call_usdz("_material_name_prefixed", *args, **kwargs)


def structure_to_usda_text(*args, **kwargs):  # pragma: no cover
    return _call_usdz("structure_to_usda_text", *args, **kwargs)


def _cube_prototype_mesh_usda(*args, **kwargs):  # pragma: no cover
    return _call_usdz("_cube_prototype_mesh_usda", *args, **kwargs)


def structure_to_usda_textured(*args, **kwargs):  # pragma: no cover
    return _call_usdz("structure_to_usda_textured", *args, **kwargs)


def _try_usdzip(*args, **kwargs):  # pragma: no cover
    return _call_usdz("_try_usdzip", *args, **kwargs)


def _try_usdzconvert(*args, **kwargs):  # pragma: no cover
    return _call_usdz("_try_usdzconvert", *args, **kwargs)


def write_usdz(*args, **kwargs):  # pragma: no cover
    return _call_usdz("write_usdz", *args, **kwargs)


def _iter_structure_paths_in_datapack_dir(*args, **kwargs):  # pragma: no cover
    return _call_usdz("_iter_structure_paths_in_datapack_dir", *args, **kwargs)


def _iter_structure_entries_in_datapack_zip(*args, **kwargs):  # pragma: no cover
    return _call_usdz("_iter_structure_entries_in_datapack_zip", *args, **kwargs)


def convert_datapack_structures_to_usdz(*args, **kwargs):  # pragma: no cover
    return _call_usdz("convert_datapack_structures_to_usdz", *args, **kwargs)


@functools.lru_cache(maxsize=1)
def _enderterm_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            v = version("enderterm")
            if v:
                return str(v)
        except PackageNotFoundError:
            pass
    except Exception:
        pass

    try:
        repo_root = Path(__file__).resolve().parents[1]
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        ).stdout.strip()
        if out:
            return out
    except Exception:
        pass

    return "unknown"


class _EndertermVersionAction(argparse.Action):
    """Lazily resolve build/version string only when --version is requested."""

    def __init__(
        self,
        option_strings: list[str],
        dest: str = argparse.SUPPRESS,
        default: str = argparse.SUPPRESS,
        help: str | None = None,
    ) -> None:
        super().__init__(option_strings=option_strings, dest=dest, nargs=0, default=default, help=help)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | list[str] | None,
        option_string: str | None = None,
    ) -> None:
        _ = namespace, values, option_string
        parser._print_message(f"EnderTerm {_enderterm_version()}\n", sys.stdout)
        parser.exit()


@functools.lru_cache(maxsize=1)
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="enderterm", description="EnderTerm: Minecraft NBT odd jobs")
    parser.add_argument("--version", action=_EndertermVersionAction)
    sub = parser.add_subparsers(dest="cmd", required=True)

    s2u = sub.add_parser("structure-to-usdz", help="Convert a structure .nbt into a .usdz (flat-colored cubes)")
    s2u.add_argument("input", type=Path, help="Path to structure .nbt (gzipped or raw)")
    s2u.add_argument("output", type=Path, help="Output .usdz path")
    s2u.add_argument(
        "--mode",
        choices=("auto", "full", "surface"),
        default="auto",
        help=f"Render mode (default: auto; auto uses surface when blocks >= {AUTO_SURFACE_THRESHOLD})",
    )
    s2u.add_argument(
        "--auto-threshold",
        type=int,
        default=AUTO_SURFACE_THRESHOLD,
        help="Block count threshold for auto->surface (default: %(default)s)",
    )
    s2u.add_argument("--textured", action="store_true", help="Use per-face textures (requires a Minecraft jar)")
    s2u.add_argument(
        "--minecraft-jar",
        type=Path,
        default=None,
        help="Path to a Minecraft client .jar (defaults to $MINECRAFT_JAR, then tries to auto-find)",
    )
    s2u.add_argument("--preview", action="store_true", help="Open the resulting .usdz in Quick Look / default viewer")

    def _add_nbt_view_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("input", type=Path, help="Path to structure .nbt (gzipped or raw)")
        p.add_argument(
            "--mode",
            choices=("auto", "full", "surface"),
            default="auto",
            help=f"Render mode (default: auto; auto uses surface when blocks >= {AUTO_SURFACE_THRESHOLD})",
        )
        p.add_argument(
            "--auto-threshold",
            type=int,
            default=AUTO_SURFACE_THRESHOLD,
            help="Block count threshold for auto->surface (default: %(default)s)",
        )
        p.add_argument(
            "--textured",
            action="store_true",
            help="Use per-face textures (auto-enabled if a Minecraft jar is found)",
        )
        p.add_argument(
            "--minecraft-jar",
            type=Path,
            default=None,
            help="Path to a Minecraft client .jar (defaults to $MINECRAFT_JAR, then tries to auto-find)",
        )
        p.add_argument(
            "--dump-core",
            type=Path,
            default=None,
            help="Write a portable JSON core dump and exit (no viewer)",
        )
        p.add_argument(
            "--test-banner",
            type=str,
            default=None,
            help="Show an on-screen testing banner (build/version + this text)",
        )

    nbt_view = sub.add_parser("nbt-view", help="Open an OpenGL window to view a structure .nbt")
    _add_nbt_view_args(nbt_view)

    dp_view = sub.add_parser("datapack-view", help="Open an OpenGL window to browse a datapack's structures")
    dp_view.add_argument(
        "input",
        type=Path,
        nargs="?",
        default=None,
        help="Datapack zip/jar or directory (default: auto-detected Minecraft client .jar)",
    )
    dp_view.add_argument(
        "--mode",
        choices=("auto", "full", "surface"),
        default="auto",
        help=f"Render mode (default: auto; auto uses surface when blocks >= {AUTO_SURFACE_THRESHOLD})",
    )
    dp_view.add_argument(
        "--auto-threshold",
        type=int,
        default=AUTO_SURFACE_THRESHOLD,
        help="Block count threshold for auto->surface (default: %(default)s)",
    )
    dp_view.add_argument(
        "--textured",
        action="store_true",
        help="Use per-face textures (auto-enabled if a Minecraft jar is found)",
    )
    dp_view.add_argument(
        "--minecraft-jar",
        type=Path,
        default=None,
        help="Path to a Minecraft client .jar (defaults to $MINECRAFT_JAR, then tries to auto-find)",
    )
    dp_view.add_argument(
        "--select",
        type=str,
        default=None,
        help="Start on the first structure whose label contains this substring (case-insensitive)",
    )
    dp_view.add_argument(
        "--export-dir",
        type=Path,
        default=None,
        help="Where to write exported .usdz/.nbt files from the viewer (default: ~/tmp/enderterm-exports)",
    )
    dp_view.add_argument(
        "--dump-core",
        type=Path,
        default=None,
        help="Write a portable JSON core dump for the selected structure and exit (no viewer)",
    )
    dp_view.add_argument(
        "--test-banner",
        type=str,
        default=None,
        help="Show an on-screen testing banner (build/version + this text)",
    )
    dp_view.add_argument(
        "--cinematic",
        action="store_true",
        help="Start with the sidebar UI hidden (Tab toggles)",
    )
    dp_view.add_argument(
        "--jigsaw-seed",
        type=lambda s: int(s, 0),
        default=None,
        help="Base seed for deterministic pool expansion (hex or decimal)",
    )
    dp_view.add_argument(
        "--jigsaw-seeds",
        type=str,
        default=None,
        help="Comma-separated per-level pool seeds for Right expansions (hex or decimal)",
    )
    dp_view.add_argument(
        "--perf-seconds",
        type=float,
        default=0.0,
        help="Record per-frame timing stats for this many seconds, write JSON, then exit (default: off)",
    )
    dp_view.add_argument(
        "--perf-out",
        type=Path,
        default=Path("/tmp/enderterm_perf.json"),
        help="Where to write the perf JSON when --perf-seconds is used (default: %(default)s)",
    )
    dp_view.add_argument(
        "--smoke-expand-once",
        action="store_true",
        help="Smoke test: load the selected NBT structure, run one pool expansion step (Right), then quit",
    )
    dp_view.add_argument(
        "--smoke-second-viewport-fx",
        action="store_true",
        help="Smoke test: open second viewport and verify post-FX pipeline parity with the main viewport",
    )
    dp_view.add_argument(
        "--smoke-focus-handoff",
        action="store_true",
        help="Smoke test: close sub-windows and verify first post-close input reaches main window",
    )
    dp_view.add_argument(
        "--smoke-real-window-click",
        action="store_true",
        help="Smoke test: real-window click integration while tool windows are open/closing",
    )
    dp_view.add_argument(
        "--smoke-real-window-build-edits",
        action="store_true",
        help="Smoke test: real-window mouse injection to place and remove a few blocks, then quit",
    )
    dp_view.add_argument(
        "--smoke-real-window-keys",
        action="store_true",
        help="Smoke test: real-window keyboard injection to validate key delivery, then quit",
    )
    dp_view.add_argument(
        "--smoke-build-edits",
        action="store_true",
        help="Smoke test: use build tools to place and remove a few blocks, then quit",
    )
    dp_view.add_argument(
        "--smoke-suite",
        action="store_true",
        help=(
            "Smoke test: run the full optional GUI smoke suite in one app launch "
            "(steps configurable via $ENDERTERM_SMOKE_SUITE_STEPS)"
        ),
    )
    dp_view.add_argument(
        "--smoke-timeout",
        type=float,
        default=20.0,
        help="Smoke test: max seconds to wait for expansion before failing (default: %(default)s)",
    )
    dp_view.add_argument(
        "--smoke-out",
        type=Path,
        default=Path("/tmp/enderterm_smoke.json"),
        help="Smoke test: where to write a JSON result file (default: %(default)s)",
    )

    dp2u = sub.add_parser(
        "datapack-structures-to-usdz",
        help="Convert all datapack structure .nbt files (zip or dir) into a folder of .usdz files",
    )
    dp2u.add_argument(
        "input",
        type=Path,
        help="Datapack zip/jar or directory (must contain data/*/structures or data/*/structure)",
    )
    dp2u.add_argument("output_dir", type=Path, help="Output directory for .usdz files")
    dp2u.add_argument(
        "--mode",
        choices=("auto", "full", "surface"),
        default="auto",
        help=f"Render mode (default: auto; auto uses surface when blocks >= {AUTO_SURFACE_THRESHOLD})",
    )
    dp2u.add_argument(
        "--auto-threshold",
        type=int,
        default=AUTO_SURFACE_THRESHOLD,
        help="Block count threshold for auto->surface (default: %(default)s)",
    )
    dp2u.add_argument("--textured", action="store_true", help="Use per-face textures (requires a Minecraft jar)")
    dp2u.add_argument(
        "--minecraft-jar",
        type=Path,
        default=None,
        help="Path to a Minecraft client .jar (defaults to $MINECRAFT_JAR, then tries to auto-find)",
    )
    dp2u.add_argument("--open", action="store_true", help="Open the output directory in Finder when done")

    self_test = sub.add_parser("self-test", help="Run a tiny end-to-end conversion smoke test")
    self_test.add_argument(
        "--output",
        type=Path,
        default=Path("enderterm_smoke.usdz"),
        help="Where to write the generated .usdz (default: ./enderterm_smoke.usdz)",
    )

    return parser


def main(argv: Iterable[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else sys.argv[1:]
    if argv_list and argv_list[0] == "structure-view":
        argv_list[0] = "nbt-view"
    args = _build_parser().parse_args(argv_list)

    if args.cmd == "structure-to-usdz":
        root = load_nbt(args.input)
        structure = apply_render_mode(parse_structure(root), args.mode, args.auto_threshold)
        if args.textured:
            jar_path = args.minecraft_jar or find_minecraft_client_jar()
            if jar_path is None:
                raise SystemExit(
                    "textured mode requires a Minecraft client jar; pass --minecraft-jar or set $MINECRAFT_JAR"
                )
            with TextureSource(jar_path) as source:
                usda_text, extra_files = structure_to_usda_textured(structure, source)
                write_usdz(args.output, usda_text, extra_files=extra_files)
        else:
            usda_text = structure_to_usda_text(structure)
            write_usdz(args.output, usda_text)
        if args.preview:
            open_in_viewer(args.output)
        return 0

    if args.cmd == "nbt-view":
        if args.dump_core is not None:
            dump_structure_core_json(
                args.input,
                mode=args.mode,
                auto_threshold=args.auto_threshold,
                textured=args.textured,
                minecraft_jar=args.minecraft_jar,
                out_path=args.dump_core,
            )
            return 0
        root = load_nbt(args.input)
        structure = parse_structure(root)
        textured = bool(args.textured)
        jar_path = args.minecraft_jar
        if not textured:
            jar_path = args.minecraft_jar or find_minecraft_client_jar()
            textured = jar_path is not None
        view_structure_opengl(
            structure,
            mode=args.mode,
            auto_threshold=args.auto_threshold,
            textured=textured,
            minecraft_jar=jar_path,
            test_banner=args.test_banner,
        )
        return 0

    if args.cmd == "datapack-view":
        datapack_input = getattr(args, "input", None)
        if datapack_input is None:
            jar_for_dp = args.minecraft_jar or find_minecraft_client_jar()
            if jar_for_dp is None:
                raise SystemExit(
                    "datapack-view requires a datapack input or a Minecraft client jar; pass an input, "
                    "--minecraft-jar, or set $MINECRAFT_JAR"
                )
            datapack_input = jar_for_dp
        if args.dump_core is not None:
            dump_datapack_core_json(
                datapack_input,
                mode=args.mode,
                auto_threshold=args.auto_threshold,
                textured=args.textured,
                minecraft_jar=args.minecraft_jar,
                select=args.select,
                out_path=args.dump_core,
            )
            return 0
        textured = bool(args.textured)
        jar_path = args.minecraft_jar
        if not textured:
            jar_path = args.minecraft_jar or find_minecraft_client_jar()
            textured = jar_path is not None

        def parse_seed_list(spec: str | None) -> list[int] | None:
            if not isinstance(spec, str) or not spec.strip():
                return None
            out: list[int] = []
            for raw in spec.split(","):
                raw = raw.strip()
                if not raw:
                    continue
                out.append(int(raw, 0) & 0xFFFFFFFF)
            return out or None

        view_datapack_opengl(
            datapack_input,
            mode=args.mode,
            auto_threshold=args.auto_threshold,
            textured=textured,
            minecraft_jar=jar_path,
            export_dir=args.export_dir,
            select=args.select,
            cinematic=bool(args.cinematic),
            jigsaw_seed=args.jigsaw_seed,
            jigsaw_seeds=parse_seed_list(args.jigsaw_seeds),
            perf_seconds=float(getattr(args, "perf_seconds", 0.0) or 0.0),
            perf_out=getattr(args, "perf_out", None),
            smoke_expand_once=bool(getattr(args, "smoke_expand_once", False)),
            smoke_second_viewport_fx=bool(getattr(args, "smoke_second_viewport_fx", False)),
            smoke_focus_handoff=bool(getattr(args, "smoke_focus_handoff", False)),
            smoke_real_window_click=bool(getattr(args, "smoke_real_window_click", False)),
            smoke_real_window_build_edits=bool(getattr(args, "smoke_real_window_build_edits", False)),
            smoke_real_window_keys=bool(getattr(args, "smoke_real_window_keys", False)),
            smoke_build_edits=bool(getattr(args, "smoke_build_edits", False)),
            smoke_suite=bool(getattr(args, "smoke_suite", False)),
            smoke_timeout=float(getattr(args, "smoke_timeout", 0.0) or 0.0),
            smoke_out=getattr(args, "smoke_out", None),
            test_banner=args.test_banner,
        )
        return 0

    if args.cmd == "datapack-structures-to-usdz":
        ok, fail = convert_datapack_structures_to_usdz(
            args.input,
            args.output_dir,
            mode=args.mode,
            auto_threshold=args.auto_threshold,
            textured=args.textured,
            minecraft_jar=args.minecraft_jar,
        )
        print(f"Converted: {ok} ok, {fail} failed")
        if args.open:
            open_in_viewer(args.output_dir)
        return 0 if fail == 0 else 2

    if args.cmd == "self-test":
        # Minimal 1-block structure, written gzipped to exercise the loader path.
        root = nbtlib.Compound(
            {
                "size": nbtlib.List[nbtlib.Int]([1, 1, 1]),
                "palette": nbtlib.List[nbtlib.Compound](
                    [nbtlib.Compound({"Name": nbtlib.String("minecraft:stone")})]
                ),
                "blocks": nbtlib.List[nbtlib.Compound](
                    [
                        nbtlib.Compound(
                            {"pos": nbtlib.List[nbtlib.Int]([0, 0, 0]), "state": nbtlib.Int(0)}
                        )
                    ]
                ),
                "entities": nbtlib.List[nbtlib.Compound]([]),
            }
        )

        with tempfile.TemporaryDirectory(prefix="enderterm_smoke_") as td:
            structure_path = Path(td) / "smoke_structure.nbt"
            nbtlib.File(root).save(structure_path, gzipped=True)  # type: ignore[arg-type]

            loaded = load_nbt(structure_path)
            structure = parse_structure(loaded)
            usda_text = structure_to_usda_text(structure)
            write_usdz(args.output, usda_text)

        with zipfile.ZipFile(args.output, "r") as zf:
            names = set(zf.namelist())
            if "scene.usda" not in names:
                raise SystemExit(f"self-test failed: scene.usda missing from {args.output}")
            if not zf.read("scene.usda").startswith(b"#usda"):
                raise SystemExit(f"self-test failed: scene.usda doesn't look like USDA in {args.output}")

        return 0

    raise AssertionError(f"Unhandled cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
