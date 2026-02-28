# EnderTerm

Tiny utilities for inspecting Minecraft NBT, starting with converting **structure NBT** files (datapack `data/*/structures/**/*.nbt`) into **USDZ** so you can Quick Look them.

## TODO

See `enderterm/TODO.md`.

## v0: structure `.nbt` → `.usdz`

This renders each non-air block as a 1×1×1 cube with a stable flat color per block id (jigsaw blocks are colored by their `pool`). Optionally, it can emit **textured** cubes using the vanilla **blockstates + model JSON** (and PNG textures) from a local Minecraft client `.jar`.

### Setup

```bash
python3 -m venv ~/tmp/venv/enderterm
~/tmp/venv/enderterm/bin/pip install -r enderterm/requirements.txt
~/tmp/venv/enderterm/bin/python -m enderterm --version
```

### Convert a single structure file

```bash
~/tmp/venv/enderterm/bin/python -m enderterm structure-to-usdz path/to/structure.nbt out.usdz
```

Open the result right away:

```bash
~/tmp/venv/enderterm/bin/python -m enderterm structure-to-usdz path/to/structure.nbt out.usdz --preview
```

Textured cubes (per-face textures):

```bash
~/tmp/venv/enderterm/bin/python -m enderterm structure-to-usdz path/to/structure.nbt out.usdz --textured
```

If auto-detection doesn’t find your client jar, pass it explicitly (or set `$MINECRAFT_JAR`):

```bash
~/tmp/venv/enderterm/bin/python -m enderterm structure-to-usdz path/to/structure.nbt out.usdz --textured --minecraft-jar "~/Library/Application Support/minecraft/versions/1.20.1/1.20.1.jar"
```

### View a structure in an OpenGL window

```bash
~/tmp/venv/enderterm/bin/python -m enderterm nbt-view path/to/structure.nbt
```

Textured (auto-enabled if a Minecraft jar is found; pass `--minecraft-jar` to specify):

```bash
~/tmp/venv/enderterm/bin/python -m enderterm nbt-view path/to/structure.nbt --textured
```

For coordinated manual testing runs, you can add an on-screen banner:

```bash
~/tmp/venv/enderterm/bin/python -m enderterm nbt-view path/to/structure.nbt --test-banner "Test: entity rendering + UV flips"
```

Controls: left-drag rotate, middle-drag pan, scroll zoom, `R` reset, `Esc`/`Q` quit.

### Browse a datapack’s structures in an OpenGL window

```bash
~/tmp/venv/enderterm/bin/python -m enderterm datapack-view path/to/datapack.zip
```

If you omit the datapack input, `datapack-view` falls back to your local Minecraft client `.jar` (so you can browse vanilla structures):

```bash
~/tmp/venv/enderterm/bin/python -m enderterm datapack-view
```

Use Up/Down (and PageUp/PageDown) to move through the left list (template pools by default).

Start at a specific pool (or structure `.nbt`) by substring match:

```bash
~/tmp/venv/enderterm/bin/python -m enderterm datapack-view path/to/datapack.jar --textured --select village/plains/houses
```

For coordinated manual testing runs, you can add an on-screen banner:

```bash
~/tmp/venv/enderterm/bin/python -m enderterm datapack-view path/to/datapack.zip --test-banner "Test: build-tool rez fade overlap"
```

Mouse:
- Scroll wheel over the list scrolls the list (doesn’t load/change selection)
- Click a row to select it
- In the 3D view: left-drag orbit, middle-drag pan, scroll zoom

Jigsaw expansion controls:
- `Right`: expand one level of all open jigsaws (randomized)
- `Left`: undo one expansion level
- `Space`: reroll the seed for the current expansion level (previous levels stay fixed)

List modes:
- `M`: toggle the left list between **Jigsaw** template pools (`data/*/worldgen/template_pool/**/*.json`) and **NBT** structures (`data/*/structures/**/*.nbt`).

Optional macOS monolithic GUI smoke suite:
- Requires a desktop session plus Accessibility permission for the Python interpreter running pytest.
- Runs the viewer once and exercises:
  - expansion screenshot
  - second viewport screenshot parity
  - frame-cap cached-present stability
  - tool-window + viewport close focus handoff
  - OS-level key input (sidebar navigation + UI toggles)
  - OS-level mouse clicks (build edits)
- Command (recommended):
  `MINECRAFT_JAR=/path/to/client.jar /Users/qarl/tmp/venv/enderterm311/bin/python -m pytest -q --run-optional -m optional`
- Command (just the GUI suite):
  `MINECRAFT_JAR=/path/to/client.jar /Users/qarl/tmp/venv/enderterm311/bin/python -m pytest -q --run-optional tests/test_optional_monolithic_integration.py`
- Controls:
  - `ENDERTERM_OPTIONAL_SMOKE_TIMEOUT_S=120` (default) adjusts the internal `--smoke-timeout`.
  - `ENDERTERM_SMOKE_SUITE_STEPS=expand_once,second_viewport_fx,...` selects suite steps.
- Note: OS event injection requires keeping the app frontmost, so this will steal focus while it runs.

Optional legacy (per-launch) GUI tests (targeted debugging):
- These launch the app once per test, so they are **skipped by default on macOS** in favor of the monolithic suite.
- To run them anyway:
  `ENDERTERM_OPTIONAL_SUITE=legacy MINECRAFT_JAR=/path/to/client.jar /Users/qarl/tmp/venv/enderterm311/bin/python -m pytest -q --run-optional -m optional`
- Individual legacy tests live in `tests/test_*_integration.py` (real-window click/focus/keys/native-close, etc).

### Worldgen JSON editor (MVP)

While in `datapack-view`, press `G` to open the **Jigsaw Editor** window.

- Full guide + Minecraft concepts: `enderterm/WORLDGEN-EDITOR.md`.
- Left pane: lists `worldgen/template_pool` JSON found in the current **pack stack** (vendor datapack(s) + writable overlay).
- Vendor packs are treated as read-only. To edit, you must **Fork** first (copies the exact original file bytes into the work pack), then edit/save.
- The work pack lives at `enderterm/work-pack/` (created automatically).

Editor tabs:
- **Form**: schema-driven edit for `template_pool` (`fallback` + `elements[]` with type/location/processors/projection/weight).
- **Raw**: direct JSON editing escape hatch (use `Cmd+S` to save).

Metropolis integration:
- Toggle Ender Vision with `V`, hover a socket, then use hotkeys to act on it.
- The HUD shows the selected jigsaw’s `pool/target/name/joint/final_state/facing`.
- `J`: Open Pool, `Enter`: Regrow with the same seed tape.

Extra controls:
- `F`: frame view (keeps orbit angles)
- `U`: export USDZ (and Quick Look it)
- `N`: export NBT
- `P`: open the export folder (default: `~/tmp/enderterm-exports`, or `--export-dir`)
- `O`: toggle orthographic mode
- `E`: cycle environment

### Convert every structure in a datapack (zip or dir)

```bash
~/tmp/venv/enderterm/bin/python -m enderterm datapack-structures-to-usdz path/to/datapack.zip out_dir/
```

Textured batch export:

```bash
~/tmp/venv/enderterm/bin/python -m enderterm datapack-structures-to-usdz path/to/datapack.zip out_dir/ --textured
```

Open the output folder when done:

```bash
~/tmp/venv/enderterm/bin/python -m enderterm datapack-structures-to-usdz path/to/datapack.zip out_dir/ --open
```

If a structure is very large, `--mode auto` (the default) will render only the *surface* blocks to keep previews snappy:

```bash
~/tmp/venv/enderterm/bin/python -m enderterm structure-to-usdz big_structure.nbt out.usdz --mode full
```

Notes:
- Supports gzipped (`.nbt` from Structure Blocks) and raw NBT.
- Coordinates are centered around the structure bounds for nicer viewing.
- Textured mode packages a standards-compliant USDZ using `usdzip`.
- The OpenGL viewer bundles the `Glass TTY VT220` font: `enderterm/assets/fonts/Glass_TTY_VT220.ttf` (public domain; see `enderterm/assets/fonts/glasstty-LICENSE.txt`).
