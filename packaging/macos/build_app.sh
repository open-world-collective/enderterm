#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PY="${PY:-"$HOME/tmp/venv/worker3/bin/python"}"
if [[ ! -x "$PY" ]]; then
  echo "Python not found at: $PY"
  echo "Set PY=/path/to/python (recommended: ~/tmp/venv/worker3/bin/python)"
  exit 1
fi

cd "$REPO_ROOT"

"$PY" -m pip install --quiet --upgrade "pyinstaller>=6,<7"

OUT_BASE="${OUT_BASE:-"$HOME/tmp/enderterm-pyinstaller"}"
DIST_PATH="$OUT_BASE/dist"
WORK_PATH="$OUT_BASE/build"
SPEC_PATH="$OUT_BASE/spec"

rm -rf "$DIST_PATH" "$WORK_PATH" "$SPEC_PATH"

"$PY" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --argv-emulation \
  --icon "$REPO_ROOT/icons/EnderTerm.icns" \
  --distpath "$DIST_PATH" \
  --workpath "$WORK_PATH" \
  --specpath "$SPEC_PATH" \
  --name "EnderTerm" \
  --add-data "$REPO_ROOT/enderterm/params.defaults.json:enderterm/params.defaults.json" \
  --add-data "$REPO_ROOT/enderterm/assets:enderterm/assets" \
  enderterm/app_macos.py

echo ""
echo "Built: $DIST_PATH/EnderTerm.app"
echo "Run:   open \"$DIST_PATH/EnderTerm.app\""
