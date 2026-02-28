# EnderTerm: Worldgen JSON Editor (Design)

Goal: add a **graphical (menu/form) editor** for Java-edition worldgen JSON (1.20.1) inside EnderTerm, tightly integrated with the existing **pool “metropolis growth” viewer** and `.nbt` structure editor.

This is intentionally a prototype for the “next gen system”: Python + pyglet, small steps, lots of whimsy, minimal ceremony.

## North Star workflows

### 1) Study → fork → tweak → regrow (the viral loop)

1. Open a vendor datapack (read-only) and pick a structure.
2. Grow the pool metropolis a few steps (seed-tape deterministic).
3. Click a pool connector block → see the pool/target/name/joint/final_state.
4. Hit **Edit** → the tool forks the relevant JSON/NBT into your **work pack** overlay.
5. Change a pool weight / swap an element / tweak a processor list.
6. Hit **Regrow** → the city changes immediately, using the same seed tape unless you reroll.

### 2) Author from scratch (no “guess the JSON”)

1. Create a new datapack skeleton (namespace, `pack.mcmeta`, folders).
2. Create a `template_pool` with a few elements (via menus + pickers).
3. Create a `structure` pointing at that pool.
4. Create a `structure_set` and wire placement.
5. Pick a “start template” and grow it in the viewer.

## Non-goals (for now)

- Full “world preview” with terrain + biome simulation.
- Server integration (push/reload/rcon) in this editor.
- Perfect coverage of every worldgen JSON type on day one.
- Replacing Photoshop (textures) or the game client (block palette building) as “the one true editor”.

## Core concept: Pack Stack

We treat content as a stack of sources:

- **Vendor pack(s)**: read-only; used for study.
- **Work pack**: writable overlay; everything you edit/save goes here.
- Optional: “Scratch pack” for temporary experiments.

Resolution rule: the topmost pack wins for a given resource id.

This gives us two superpowers:

1) Safety: no accidental edits to vendor relics.
2) Diff clarity: everything you changed exists in your work pack as a clean fork.

## UI layout (single-window)

Four panes (classic EnderTerm vibe, not a web app):

1. **Library** (left): pack stack + tree + search.
   - Tabs: `Structures (.nbt)` / `Worldgen (json)` / `Processors` / `Tags`
   - Fuzzy search by resource id or filename.
2. **Viewport** (center): 3D render, as now.
   - Same orbit/pan/zoom, plus optional “cinematic” hiding.
3. **Inspector** (right): the “menus and whatnot”.
   - Two tabs for every resource:
     - **Form**: schema-driven UI (dropdowns, sliders, checkboxes, pickers).
     - **Raw**: JSON/NBT text for power users + copy/paste + quick diff view.
4. **Timeline / Seed Tape** (bottom): growth steps, undo/redo, reroll, bookmarks.

## JSON editing approach: schema-driven forms (without a browser)

### Principle

Never make you memorize keys. The editor should:

- Provide a **dropdown** for every `type` field (and any “oneOf” branching).
- Reveal only the fields relevant to that selection.
- Validate values immediately (range, required, enum).
- Offer **pick-lists** instead of stringly-typed ids where possible.

### Where the schema comes from

Two-layer system:

1) **Hand-authored minimal schemas** for the MVP types we care about:
   - `worldgen/template_pool`
   - `worldgen/structure`
   - `worldgen/structure_set`
   - `worldgen/processor_list`
2) A fallback “generic JSON editor” that is still menu-driven:
   - object editor (add/remove keys)
   - array editor (reorder)
   - primitive widgets (string/number/bool)

This avoids licensing headaches and still gets us to “no guessing” quickly.

### Pickers (the secret sauce)

Populate dropdowns/search pickers by scanning:

- The pack stack itself (namespaces + resource ids).
- The local 1.20.1 client jar (blocks, biomes, tags, configured features).

Pickers we want early:

- Block id picker (`minecraft:stone`, …)
- Tag picker (`#minecraft:…`)
- Biome picker / biome tag picker
- Template pool id picker
- Processor list picker
- Structure / structure_set picker

## Resource model (internal)

### Canonical ids

We normalize everything to `(namespace, kind, path)`:

- kind examples: `structures_nbt`, `worldgen_template_pool`, `worldgen_structure`, `worldgen_structure_set`, `worldgen_processor_list`
- path is resource path without extension

### Read / write

- Vendor packs: read-only file access.
- Work pack: write access.
- Editing a vendor resource triggers **Fork**:
  - Copy the exact original file bytes into the work pack first.
  - Then apply modifications (so round-tripping starts from the same baseline).

### Round-tripping intuition (A5)

- Preserve key order when possible (parse with `object_pairs_hook`).
- Write JSON with stable formatting (2-space indent, trailing newline).
- Don’t sort keys globally (keep the author’s structure).
- Keep a “Raw” escape hatch always.

## MVP: what “Form” looks like for each type

### `template_pool`

- `name` (read-only display)
- `fallback` (pool picker)
- `elements[]` table:
  - element `type` dropdown (start with `single_pool_element` + `empty_pool_element`)
  - `location` structure picker (for single element)
  - `processors` picker (or `minecraft:empty`)
  - `projection` dropdown (`rigid`, `terrain_matching`)
  - `weight` slider/int
  - Buttons: add/remove/duplicate, move up/down
  - Optional thumbnail: quick tiny render of the element structure

### `structure`

- `type` dropdown (start with `minecraft:jigsaw`)
- `biomes` picker (biome/tag list)
- `spawn_overrides` (later; raw for MVP)
- `step` dropdown (gen step)
- Pool section:
  - `start_pool` picker
  - `start_jigsaw_name` (string)
  - `size` (int slider)
  - `start_height` picker (height provider form)
  - `project_start_to_heightmap` checkbox
  - `max_distance_from_center` int

### `structure_set`

- `structures[]` list:
  - `structure` picker
  - `weight` int
- `placement.type` dropdown:
  - MVP: `minecraft:random_spread`
  - Fields: `spacing`, `separation`, `salt`, `spread_type`, `locate_offset`

### `processor_list`

- Ordered list of processors with per-entry config forms:
  - MVP: support the ones we see in Dungeons & Taverns first.
  - Always keep a raw fallback when we encounter unknown processors.

## Integration with the existing metropolis viewer

- Clicking a **pool connector block** selects it and opens a pool inspector:
  - pool, target, name, joint, final_state, facing
  - buttons:
    - **Open Pool** (jumps Library to that template_pool)
    - **Fork + Edit Pool**
    - **Regrow From Here**
- Editing `template_pool` should invalidate pool expansion caches and rerun growth.
- Seed tape remains the “truth”; edits change the city without changing the tape.

## Milestones (small, testable steps)

1. **Pack Stack plumbing**
   - define work pack folder and “fork on edit”.
2. **Worldgen library tab**
   - list + search JSON resources in the stack.
3. **Generic form editor**
   - object/array/primitive widgets + add/remove/reorder.
4. **Typed forms for `template_pool`**
   - pickers + elements table + live regrow integration.
5. **Typed forms for `structure` + `structure_set`**
   - enough to author a minimal pool-structure pipeline.
6. **Processor preview toggle (subset)**
   - show Raw vs Processed effect in the viewport.

## Defaults (until told otherwise)

- New authored packs live under `worker/projects/<packname>/` (easy to zip + install elsewhere).
- Default namespace: `qarl`.
- Vendor packs are strict read-only; all edits happen via fork into work pack.

## Future candy (after MVP)

- A pool graph view: nodes = pools, edges = “this pool references this structure / structure references pool”.
- “Create from selection” helpers for `.nbt` templates (clip/rotate/mirror/palette swap).
- A “gallery export” mode: render thumbnails/turntables for every structure referenced by a pool graph.
