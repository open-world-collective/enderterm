from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType


def _defs(nbttool: ModuleType):
    return [
        nbttool.ParamDef(key="a", label="A", default=1.0, min_value=0.0, max_value=10.0),
        nbttool.ParamDef(key="b", label="B", default=0.0, min_value=-5.0, max_value=5.0, is_int=True),
    ]


def _write_seed_params(params_path: Path) -> None:
    params_path.write_text(
        json.dumps({"old_a": 9.0, "b": 2.2, "ignored": 123}),
        encoding="utf-8",
    )


def _flush_deferred_save(store) -> None:
    # ParamStore defers disk writes; force the pending save immediately.
    store._save_after_t = 0.0  # type: ignore[attr-defined]
    store.tick()


def test_param_store_loads_aliases_and_clamps_on_read(nbttool: ModuleType, tmp_path: Path) -> None:
    params_path = tmp_path / "params.json"
    _write_seed_params(params_path)

    store = nbttool.ParamStore(_defs(nbttool), params_path, aliases={"old_a": "a"})
    assert store.get("a") == 9.0
    assert store.get_int("b") == 2
    assert store.has_explicit_value("a") is True
    assert store.has_explicit_value("b") is True


def test_param_store_clamps_updates_and_saves(nbttool: ModuleType, tmp_path: Path) -> None:
    params_path = tmp_path / "params.json"
    _write_seed_params(params_path)

    store = nbttool.ParamStore(_defs(nbttool), params_path, aliases={"old_a": "a"})
    store.set("a", 999.0)
    store.set("b", -999.0)
    assert store.get("a") == 10.0
    assert store.get_int("b") == -5

    _flush_deferred_save(store)

    saved = json.loads(params_path.read_text(encoding="utf-8"))
    assert saved == {"a": 10.0, "b": -5.0}


def test_param_store_alias_with_non_numeric_value_still_triggers_migration_save(
    nbttool: ModuleType,
    tmp_path: Path,
) -> None:
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps({"old_a": "bad-value"}), encoding="utf-8")

    store = nbttool.ParamStore(_defs(nbttool), params_path, aliases={"old_a": "a"})
    assert store.get("a") == 1.0
    assert store.has_explicit_value("a") is False

    _flush_deferred_save(store)
    saved = json.loads(params_path.read_text(encoding="utf-8"))
    assert saved == {"a": 1.0, "b": 0.0}


def test_param_store_unknown_non_numeric_key_does_not_trigger_migration_save(
    nbttool: ModuleType,
    tmp_path: Path,
) -> None:
    params_path = tmp_path / "params.json"
    seed = {"ignored": "x"}
    params_path.write_text(json.dumps(seed), encoding="utf-8")

    store = nbttool.ParamStore(_defs(nbttool), params_path, aliases={"old_a": "a"})
    _flush_deferred_save(store)
    saved = json.loads(params_path.read_text(encoding="utf-8"))
    assert saved == seed
