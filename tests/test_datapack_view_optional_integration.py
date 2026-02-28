from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
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
    # nbttool datapack-view lists NBT structures as `namespace/path/...`, not `namespace:path/...`.
    return template_id.replace(":", "/", 1)


@pytest.mark.optional
def test_datapack_view_smoke_expand_once(nbttool: ModuleType, tmp_path: Path) -> None:
    if sys.platform != "darwin" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("GUI smoke test requires a display")

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for a quick viewer smoke.")

    repo_root = Path(__file__).resolve().parents[1]
    export_dir = tmp_path / "exports"
    smoke_out = tmp_path / "smoke.json"
    test_home = tmp_path / "home"
    params_path = test_home / ".config" / "enderterm" / "params.json"
    params_path.parent.mkdir(parents=True, exist_ok=True)
    params_path.write_text(
        json.dumps(
            {
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
        "--select",
        label,
        "--export-dir",
        str(export_dir),
        "--smoke-expand-once",
        "--smoke-timeout",
        "25",
        "--smoke-out",
        str(smoke_out),
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
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout[-4000:]
    assert smoke_out.is_file()
    payload = json.loads(smoke_out.read_text(encoding="utf-8"))
    assert payload.get("ok") is True, payload
    assert payload.get("jigsaw_seeds"), payload
    shot = payload.get("expand_screenshot")
    assert isinstance(shot, dict), payload
    main_png = shot.get("main_png")
    assert isinstance(main_png, str) and main_png, shot
    assert Path(main_png).is_file(), shot
    main_sig = shot.get("main_signature")
    assert isinstance(main_sig, dict), shot
    assert "error" not in main_sig, shot
    assert isinstance(main_sig.get("dhash64"), str) and len(str(main_sig.get("dhash64"))) == 16, shot


@pytest.mark.optional
def test_datapack_view_smoke_second_viewport_fx(nbttool: ModuleType, tmp_path: Path) -> None:
    if sys.platform != "darwin" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("GUI smoke test requires a display")

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for second viewport FX smoke.")

    repo_root = Path(__file__).resolve().parents[1]
    smoke_out = tmp_path / "smoke_second_viewport_fx.json"
    test_home = tmp_path / "home"
    params_path = test_home / ".config" / "enderterm" / "params.json"
    params_path.parent.mkdir(parents=True, exist_ok=True)
    params_path.write_text(
        json.dumps(
            {
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
        "--select",
        label,
        "--smoke-second-viewport-fx",
        "--smoke-timeout",
        "25",
        "--smoke-out",
        str(smoke_out),
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
        timeout=75,
    )
    assert proc.returncode == 0, proc.stdout[-4000:]
    assert smoke_out.is_file()
    payload = json.loads(smoke_out.read_text(encoding="utf-8"))
    assert payload.get("ok") is True, payload
    assert payload.get("smoke_mode") == "second_viewport_fx", payload
    second_fx = payload.get("second_fx")
    assert isinstance(second_fx, dict), payload
    assert int(second_fx.get("world_draws", 0)) >= 1, payload
    assert int(second_fx.get("post_fx_draws", 0)) >= 1, payload

    shots = second_fx.get("viewport_screenshots")
    assert isinstance(shots, dict), payload
    main_png = shots.get("main_png")
    second_png = shots.get("second_png")
    assert isinstance(main_png, str) and main_png, shots
    assert isinstance(second_png, str) and second_png, shots
    assert Path(main_png).is_file(), shots
    assert Path(second_png).is_file(), shots

    main_sig = shots.get("main_signature")
    second_sig = shots.get("second_signature")
    assert isinstance(main_sig, dict), shots
    assert isinstance(second_sig, dict), shots
    assert "error" not in main_sig, shots
    assert "error" not in second_sig, shots
    main_hash = main_sig.get("dhash64")
    second_hash = second_sig.get("dhash64")
    assert isinstance(main_hash, str) and len(main_hash) == 16, shots
    assert isinstance(second_hash, str) and len(second_hash) == 16, shots
    assert float(main_sig.get("mean_luma", 0.0)) >= 0.5, shots
    assert float(second_sig.get("mean_luma", 0.0)) >= 0.5, shots

    compare = shots.get("comparison")
    assert isinstance(compare, dict), shots
    dhash_hamming = int(compare.get("dhash_hamming", -1))
    assert 0 <= dhash_hamming <= 56, compare
    luma_ratio_raw = compare.get("mean_luma_ratio")
    assert luma_ratio_raw is not None, compare
    luma_ratio = float(luma_ratio_raw)
    assert 0.35 <= luma_ratio <= 2.8, compare


@pytest.mark.optional
def test_datapack_view_smoke_focus_handoff_tool_windows(nbttool: ModuleType, tmp_path: Path) -> None:
    if sys.platform != "darwin" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("GUI smoke test requires a display")

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for focus-handoff smoke.")

    repo_root = Path(__file__).resolve().parents[1]
    smoke_out = tmp_path / "smoke_focus_handoff.json"
    test_home = tmp_path / "home"
    params_path = test_home / ".config" / "enderterm" / "params.json"
    params_path.parent.mkdir(parents=True, exist_ok=True)
    params_path.write_text(
        json.dumps(
            {
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
        "--select",
        label,
        "--smoke-focus-handoff",
        "--smoke-timeout",
        "35",
        "--smoke-out",
        str(smoke_out),
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
    assert smoke_out.is_file()
    payload = json.loads(smoke_out.read_text(encoding="utf-8"))
    assert payload.get("ok") is True, payload
    assert payload.get("smoke_mode") == "focus_handoff", payload

    expected_sources = ["palette", "debug", "param", "viewport"]

    validated = payload.get("validated_sources")
    assert isinstance(validated, list), payload
    assert [str(x) for x in validated] == expected_sources, payload

    key_focus = payload.get("key_focus_by_source")
    assert isinstance(key_focus, dict), payload
    for source in expected_sources:
        assert int(key_focus.get(source, 0)) >= 1, payload

    dwell = payload.get("dwell_before_close_s_by_source")
    assert isinstance(dwell, dict), payload
    for source in expected_sources:
        assert float(dwell.get(source, 0.0)) >= 0.95, payload

    close_paths = payload.get("close_path_by_source")
    assert isinstance(close_paths, dict), payload
    assert str(close_paths.get("palette", "")).startswith("child_"), payload
    assert str(close_paths.get("debug", "")).startswith("child_"), payload
    assert str(close_paths.get("param", "")).startswith("child_"), payload

    child_close_used = payload.get("child_close_path_used_by_source")
    assert isinstance(child_close_used, dict), payload
    assert bool(child_close_used.get("palette")) is True, payload
    assert bool(child_close_used.get("debug")) is True, payload
    assert bool(child_close_used.get("param")) is True, payload


@pytest.mark.optional
def test_datapack_view_smoke_focus_handoff_viewport_close_cycles(nbttool: ModuleType, tmp_path: Path) -> None:
    if sys.platform != "darwin" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("GUI smoke test requires a display")

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for viewport close-cycle smoke.")

    repo_root = Path(__file__).resolve().parents[1]
    smoke_out = tmp_path / "smoke_focus_handoff_viewport_cycles.json"
    test_home = tmp_path / "home"
    params_path = test_home / ".config" / "enderterm" / "params.json"
    params_path.parent.mkdir(parents=True, exist_ok=True)
    params_path.write_text(
        json.dumps(
            {
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
        "--select",
        label,
        "--smoke-focus-handoff",
        "--smoke-timeout",
        "45",
        "--smoke-out",
        str(smoke_out),
    ]
    env = os.environ.copy()
    env["HOME"] = str(test_home)
    env["MINECRAFT_JAR"] = str(jar_path)
    env["PYTHONPATH"] = str(repo_root)
    env["ENDERTERM_SMOKE_FOCUS_SOURCES"] = "viewport"
    env["ENDERTERM_SMOKE_VIEWPORT_CYCLES"] = "2"

    proc = subprocess.run(
        cmd,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=110,
    )
    assert proc.returncode == 0, proc.stdout[-4000:]
    assert smoke_out.is_file()
    payload = json.loads(smoke_out.read_text(encoding="utf-8"))
    assert payload.get("ok") is True, payload
    assert payload.get("smoke_mode") == "focus_handoff", payload
    assert int(payload.get("viewport_cycles_target", 0)) == 2, payload

    validated = payload.get("validated_sources")
    assert isinstance(validated, list), payload
    assert [str(x) for x in validated] == ["viewport"], payload

    baseline = payload.get("viewport_baseline_main_shot")
    assert isinstance(baseline, dict), payload
    baseline_sig = baseline.get("main_signature")
    assert isinstance(baseline_sig, dict), baseline
    assert "error" not in baseline_sig, baseline
    assert isinstance(baseline_sig.get("dhash64"), str) and len(str(baseline_sig.get("dhash64"))) == 16, baseline
    assert float(baseline_sig.get("mean_luma", 0.0)) >= 0.5, baseline

    close_shots = payload.get("viewport_close_main_shots")
    assert isinstance(close_shots, list), payload
    assert len(close_shots) >= 2, payload

    for idx, shot in enumerate(close_shots):
        assert isinstance(shot, dict), payload
        sig = shot.get("main_signature")
        assert isinstance(sig, dict), shot
        assert "error" not in sig, shot
        assert isinstance(sig.get("dhash64"), str) and len(str(sig.get("dhash64"))) == 16, shot
        assert float(sig.get("mean_luma", 0.0)) >= 0.5, shot

        base_cmp = shot.get("comparison_vs_baseline")
        assert isinstance(base_cmp, dict), shot
        assert 0 <= int(base_cmp.get("dhash_hamming", -1)) <= 56, shot
        base_ratio = base_cmp.get("mean_luma_ratio")
        assert base_ratio is not None, shot
        assert 0.35 <= float(base_ratio) <= 2.8, shot

        if idx > 0:
            prev_cmp = shot.get("comparison_vs_prev_close")
            assert isinstance(prev_cmp, dict), shot
            assert 0 <= int(prev_cmp.get("dhash_hamming", -1)) <= 56, shot
            prev_ratio = prev_cmp.get("mean_luma_ratio")
            assert prev_ratio is not None, shot
            assert 0.35 <= float(prev_ratio) <= 2.8, shot


@pytest.mark.optional
def test_datapack_view_smoke_build_edits(nbttool: ModuleType, tmp_path: Path) -> None:
    if sys.platform != "darwin" and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("GUI smoke test requires a display")

    jar_path = Path(os.environ["MINECRAFT_JAR"]).expanduser()
    assert jar_path.is_file()
    label = _find_smoke_label(nbttool, jar_path=jar_path, tmp_path=tmp_path)
    if label is None:
        pytest.skip("No suitable vanilla start piece found in MINECRAFT_JAR for build-edit smoke.")

    repo_root = Path(__file__).resolve().parents[1]
    smoke_out = tmp_path / "smoke_build_edits.json"
    test_home = tmp_path / "home"
    params_path = test_home / ".config" / "enderterm" / "params.json"
    params_path.parent.mkdir(parents=True, exist_ok=True)
    params_path.write_text(
        json.dumps(
            {
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
        "--select",
        label,
        "--smoke-build-edits",
        "--smoke-timeout",
        "25",
        "--smoke-out",
        str(smoke_out),
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
        timeout=75,
    )
    assert proc.returncode == 0, proc.stdout[-4000:]
    assert smoke_out.is_file()
    payload = json.loads(smoke_out.read_text(encoding="utf-8"))
    assert payload.get("ok") is True, payload
    assert payload.get("smoke_mode") == "build_edits", payload

    info = payload.get("build_edits")
    assert isinstance(info, dict), payload
    place_attempted = info.get("place_attempted")
    remove_attempted = info.get("remove_attempted")
    placed_ok = info.get("placed_ok")
    removed_ok = info.get("removed_ok")
    assert isinstance(place_attempted, list) and len(place_attempted) >= 3, info
    assert isinstance(remove_attempted, list) and len(remove_attempted) >= 3, info
    assert isinstance(placed_ok, list) and len(placed_ok) >= 2, info
    assert isinstance(removed_ok, list) and len(removed_ok) >= 2, info

    before_count = int(info.get("before_block_count", 0))
    after_count = int(info.get("after_block_count", 0))
    assert before_count > 0 and after_count > 0, info
    expected_delta = int(len(placed_ok) - len(removed_ok))
    assert int(after_count - before_count) == expected_delta, info
