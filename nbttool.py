#!/usr/bin/env python3

from __future__ import annotations

"""
Compatibility shim for older entrypoints/tests.

EnderTerm is the canonical package name, but some tooling still expects
`python -m nbttool ...`.
"""

from enderterm.nbttool import *  # noqa: F403
from enderterm.nbttool import main


if __name__ == "__main__":
    raise SystemExit(main())

