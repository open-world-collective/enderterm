from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from enderterm import params as params_mod


def test_effects_master_default_enabled_by_platform() -> None:
    assert params_mod.effects_master_default_enabled(platform="linux") is True
    assert params_mod.effects_master_default_enabled(platform="darwin") is False


def test_effects_master_enabled_decision_path_uses_store_or_platform_default() -> None:
    class _Store:
        def __init__(self, value: int) -> None:
            self.value = int(value)

        def get_int(self, _key: str) -> int:
            return int(self.value)

    assert params_mod.effects_master_enabled(_Store(1), platform="darwin") is True
    assert params_mod.effects_master_enabled(_Store(0), platform="linux") is False

    class _BrokenStore:
        def get_int(self, _key: str) -> int:
            raise RuntimeError("boom")

    assert params_mod.effects_master_enabled(_BrokenStore(), platform="darwin") is False
    assert params_mod.effects_master_enabled(_BrokenStore(), platform="linux") is True


def test_load_default_param_store_defaults_fx_off_on_darwin_without_explicit_value(tmp_path: Path) -> None:
    path = tmp_path / "params.json"
    store = params_mod.load_default_param_store(path=path, platform="darwin")
    assert store.get_int(params_mod.FX_MASTER_ENABLED_KEY) == 0


def test_load_default_param_store_respects_explicit_fx_value_on_darwin(tmp_path: Path) -> None:
    path = tmp_path / "params.json"
    path.write_text(json.dumps({params_mod.FX_MASTER_ENABLED_KEY: 1.0}), encoding="utf-8")
    store = params_mod.load_default_param_store(path=path, platform="darwin")
    assert store.get_int(params_mod.FX_MASTER_ENABLED_KEY) == 1


def test_macos_gestures_enabled_is_kvalue_only(monkeypatch: object, tmp_path: Path) -> None:
    path = tmp_path / "params.json"
    store = params_mod.load_default_param_store(path=path, platform="darwin")
    assert params_mod.macos_gestures_enabled(store, platform="darwin") is False

    monkeypatch.setenv("ENDERTERM_ENABLE_GESTURES", "1")
    assert params_mod.macos_gestures_enabled(store, platform="darwin") is False

    path.write_text(json.dumps({params_mod.MACOS_GESTURES_ENABLED_KEY: 1.0}), encoding="utf-8")
    store = params_mod.load_default_param_store(path=path, platform="darwin")
    assert params_mod.macos_gestures_enabled(store, platform="darwin") is True


def test_hover_pick_enabled_is_kvalue_only(monkeypatch: object, tmp_path: Path) -> None:
    path = tmp_path / "params.json"
    store = params_mod.load_default_param_store(path=path, platform="darwin")
    assert params_mod.hover_pick_enabled(store) is True

    monkeypatch.setenv("ENDERTERM_DISABLE_HOVER_PICK", "1")
    assert params_mod.hover_pick_enabled(store) is True

    path.write_text(json.dumps({params_mod.BUILD_HOVER_PICK_ENABLED_KEY: 0.0}), encoding="utf-8")
    store = params_mod.load_default_param_store(path=path, platform="darwin")
    assert params_mod.hover_pick_enabled(store) is False
