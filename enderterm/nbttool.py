#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

# When executed as `python enderterm/nbttool.py`, sys.path[0] is `enderterm/`
# which breaks `import enderterm.*` package imports. Add the repo root.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Re-export the full implementation for compatibility with older imports/tests,
# including private helpers (some tests monkeypatch underscore-prefixed names).
#
# NOTE: Keep this as a normal import (not importlib.import_module) so packagers
# like PyInstaller can discover the dependency reliably.
from enderterm import nbttool_impl as _impl
for _k, _v in _impl.__dict__.items():
    if _k in {"__name__", "__loader__", "__package__", "__spec__", "__file__", "__cached__"}:
        continue
    globals()[_k] = _v


def __getattr__(name: str) -> object:  # pragma: no cover
    return getattr(_impl, name)


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(set(globals()).union(dir(_impl)))

if __name__ == "__main__":
    main()
