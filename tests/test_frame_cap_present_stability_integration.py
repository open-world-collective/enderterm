"""Optional frame-cap stability integration smoke.

Validates that low render caps actively use cached-frame present on skipped
draws, which avoids rapid swap-buffer flicker under idle camera.

Run:
  MINECRAFT_JAR=/path/to/client.jar \
  /Users/qarl/tmp/venv/enderterm311/bin/python -m pytest -q --run-optional \
    tests/test_frame_cap_present_stability_integration.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
from types import ModuleType

import pytest


def _find_smoke_label(nbttool: ModuleType, *, jar_path: Path, tmp_path: Path) -> str | None:
    template_id: str | None = None
    with zipfile.ZipFile(jar_path, "r") as zf:
        dp_source = nbttool.DatapackSource(jar_path, zf)
        stack = nbttool.PackStack(work_dir=tmp_path / "work-pack", vendors=[dp_source])
        index = nbttool.JigsawDatapackIndex(stack.source)

        jigsaw_structures = nbttool.list_worldgen_jigsaw_structures(stack)
        if not jigsaw_structures:
            return None

        for structure_id in jigsaw_structures[:50]:
            obj = stack.source.read_json(nbttool.canonical_worldgen_structure_json(structure_id)) or {}
            start_pool = obj.get("start_pool")
            if not isinstance(start_pool, str) or not start_pool:
                continue
            pool_def = index.load_pool(start_pool)
            for elem in pool_def.elements[:50]:
                tmpl = index.load_template(elem.location_id)
                if tmpl is None:
                    continue
                open_conns = [
                    c for c in tmpl.connectors if c.pool not in {"", "minecraft:empty"} and c.target not in {"", "minecraft:empty"}
                ]
                if open_conns:
                    template_id = tmpl.template_id
                    break
            if template_id is not None:
                break

    if template_id is None:
        return None
    return template_id.replace(":", "/", 1)


@pytest.mark.optional
def test_frame_cap_present_stability(nbttool: ModuleType, tmp_path: Path) -> None:
    if sys.platform != "darwin" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("GUI stability smoke test requires a display")

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for frame-cap stability smoke.")

    repo_root = Path(__file__).resolve().parents[1]
    perf_out = tmp_path / "perf_frame_cap_stability.json"
    test_home = tmp_path / "home"
    params_path = test_home / ".config" / "enderterm" / "params.json"
    params_path.parent.mkdir(parents=True, exist_ok=True)
    params_path.write_text(
        json.dumps(
            {
                "render.frame_cap_hz": 2,
                "effects.master_enabled": 0,
                "build.hover_pick.enabled": 0,
                "input.macos.gestures.enabled": 0,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    cmd = [
        sys.executable,
        "-m",
        "nbttool",
        "datapack-view",
        str(jar_path),
        "--minecraft-jar",
        str(jar_path),
        "--textured",
        "--select",
        label,
        "--perf-seconds",
        "8",
        "--perf-out",
        str(perf_out),
        "--test-banner",
        "AUTOMATED TESTING DO NOT TOUCH",
    ]
    env = os.environ.copy()
    env["HOME"] = str(test_home)
    env["MINECRAFT_JAR"] = str(jar_path)
    env["PYTHONPATH"] = str(repo_root)
    proc = subprocess.run(
        cmd,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stdout[-4000:]
    assert perf_out.is_file(), proc.stdout[-4000:]

    payload = json.loads(perf_out.read_text(encoding="utf-8"))
    summary = payload.get("summary")
    frames = payload.get("frames")
    assert isinstance(summary, dict), payload
    assert isinstance(frames, list) and frames, payload

    last = frames[-1]
    assert isinstance(last, dict), payload
    fps_smooth = float(last.get("fps_smooth", 0.0))
    assert 1.0 <= fps_smooth <= 3.5, last
    skip_count = int(last.get("draw_skip_cap_count", 0))
    cache_count = int(last.get("draw_cache_present_count", 0))
    assert skip_count >= 40, last
    assert cache_count >= int(skip_count * 0.95), last
