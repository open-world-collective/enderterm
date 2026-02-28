from __future__ import annotations

import ctypes
import importlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType
import zipfile

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-optional",
        action="store_true",
        default=False,
        help="Run optional integration tests (requires $MINECRAFT_JAR).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "optional: Optional integration tests (run with --run-optional; may require MINECRAFT_JAR)",
    )
    if config.getoption("--run-optional"):
        jar = os.environ.get("MINECRAFT_JAR", "").strip()
        if not jar:
            raise pytest.UsageError("--run-optional requires MINECRAFT_JAR=/path/to/client.jar")
        if not Path(jar).expanduser().is_file():
            raise pytest.UsageError(f"--run-optional requires an existing file at MINECRAFT_JAR={jar!r}")


def optional_smoke_find_label(nbttool: ModuleType, *, jar_path: Path, tmp_path: Path) -> str | None:
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
                    c
                    for c in tmpl.connectors
                    if c.pool not in {"", "minecraft:empty"} and c.target not in {"", "minecraft:empty"}
                ]
                if open_conns:
                    template_id = tmpl.template_id
                    break
            if template_id is not None:
                break

    if template_id is None:
        return None
    return template_id.replace(":", "/", 1)


def optional_smoke_is_accessibility_trusted() -> bool:
    if sys.platform != "darwin":
        return False
    app = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    app.AXIsProcessTrusted.argtypes = []
    app.AXIsProcessTrusted.restype = ctypes.c_bool
    return bool(app.AXIsProcessTrusted())


def optional_smoke_load_json_if_ready(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-optional"):
        default_mode = "monolithic" if sys.platform == "darwin" else "legacy"
        mode = os.environ.get("ENDERTERM_OPTIONAL_SUITE", default_mode).strip().lower()
        if mode not in {"legacy", "all", "0", "false", "no", "off"}:
            skip_legacy = pytest.mark.skip(
                reason="covered by monolithic optional smoke suite (set ENDERTERM_OPTIONAL_SUITE=legacy to run)"
            )
            legacy_gui_optional = {
                "test_datapack_view_optional_integration.py",
                "test_frame_cap_present_stability_integration.py",
                "test_real_window_build_edits_integration.py",
                "test_real_window_click_integration.py",
                "test_real_window_focus_handoff_integration.py",
                "test_real_window_key_input_integration.py",
                "test_real_window_native_close_integration.py",
            }
            for item in items:
                if item.get_closest_marker("optional") is None:
                    continue
                try:
                    basename = Path(str(getattr(item, "fspath", ""))).name
                except Exception:
                    basename = ""
                if basename in legacy_gui_optional:
                    item.add_marker(skip_legacy)
        return
    skip_optional = pytest.mark.skip(reason="needs --run-optional")
    for item in items:
        if item.get_closest_marker("optional") is not None:
            item.add_marker(skip_optional)


@pytest.fixture(scope="session")
def nbttool() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    return importlib.import_module("enderterm.nbttool_impl")


@pytest.fixture(autouse=True)
def _disable_external_usdz_tools(monkeypatch: pytest.MonkeyPatch, nbttool: ModuleType) -> None:
    # Keep the default test run fast + deterministic: avoid probing external tools
    # like `xcrun --find usdzconvert` during USDZ writes.
    monkeypatch.setattr(nbttool, "_try_usdzip", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(nbttool, "_try_usdzconvert", lambda *_args, **_kwargs: False)

    # Some refactors move USDZ helpers into `enderterm/usdz.py`. Patch those too
    # so tests don't depend on external tooling being installed.
    try:
        from enderterm import usdz as usdz_mod

        monkeypatch.setattr(usdz_mod, "_try_usdzip", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(usdz_mod, "_try_usdzconvert", lambda *_args, **_kwargs: False)
    except Exception:
        pass
