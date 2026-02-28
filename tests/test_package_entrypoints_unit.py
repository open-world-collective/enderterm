from __future__ import annotations

import runpy

import pytest


def test_python_m_enderterm_calls_main(monkeypatch: pytest.MonkeyPatch) -> None:
    import enderterm.nbttool as enderterm_pkg

    monkeypatch.setattr(enderterm_pkg, "main", lambda: 0)
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("enderterm", run_name="__main__")
    assert excinfo.value.code == 0
