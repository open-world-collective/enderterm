# EnderTerm Worldgen Editor (User Guide)

This is the “menus and whatnot” editor for Minecraft 1.20.1 worldgen JSON inside EnderTerm. It’s built to support the viral loop: **grow → inspect → tweak → regrow**.

## Quickstart (the 30-second loop)

1) Launch the viewer on a datapack:

```bash
~/tmp/venv/enderterm/bin/python -m enderterm datapack-view path/to/datapack.zip --textured
```

2) Press `G` to open the **Worldgen** editor window.

3) In the 3D view:
- Toggle Ender Vision with `V`.
- Hover a pool socket, click to select it.
- Press `J` to open its pool (or `Shift+J` to fork+edit it).
- Press `Enter` to regrow using the same seed tape (so changes are attributable).

## Mental model: datapacks, ids, and overrides

Minecraft treats worldgen data as a registry keyed by **resource id**:

- Resource id: `<namespace>:<path>` (example: `minecraft:overworld/continents`)
- On disk: `data/<namespace>/<category>/<path>.json` (or `.nbt` for structures)

Multiple datapacks can define the same resource id. Minecraft loads them as a stack; the topmost pack wins.

EnderTerm mirrors this with a **Pack Stack**:

- Vendor pack(s): read-only (for study)
- Work pack overlay: writable; anything you edit is saved here

The work pack lives at `enderterm/work-pack/` (internal package dir).

## Worldgen concepts you’ll see in this editor

### “Features” vs “Structures”

- **Features**: trees, flowers, ore veins, lakes, etc.
  - Controlled per-biome via `worldgen/biome` → `placed_feature` → `configured_feature`.
- **Structures**: villages, temples, cities, dungeons, etc.
  - Controlled globally via `worldgen/structure_set` + `worldgen/structure`.

The worldgen editor MVP is focused on **pool-based structures**, because they’re the “metropolis growth” magic.

### Structure placement: `structure_set`

Files: `data/<ns>/worldgen/structure_set/*.json`

This answers: “where do we attempt to start a structure?”

Common fields:
- `placement`: spacing/separation rules (often `minecraft:random_spread`)
- `structures[]`: which structure(s) can spawn, with weights

### Structure definition: `structure`

Files: `data/<ns>/worldgen/structure/*.json`

This answers: “what do we build when we decide to start it?”

For pool-based structures, key fields include:
- `start_pool`: the first pool the generator pulls a starting piece from
- `size`: how many “expansion rounds” to attempt
- `biomes`: which biomes (or biome tags) can host this structure

### Pool growth: `template_pool` (the star of the show)

Files: `data/<ns>/worldgen/template_pool/**/*.json`

A template pool is a **weighted menu of pieces**. When the generator needs “the next piece”, it picks from a pool.

Pool shape:
- `fallback`: pool to use if nothing fits / nothing loads
- `elements[]`: weighted items (each element usually points at an NBT structure template)

Common element types (you’ll see these as `type`):
- `minecraft:single_pool_element`: one NBT structure at `location`
- `minecraft:empty_pool_element`: intentionally “place nothing” (a terminator)

Common element fields:
- `location`: structure template id (`data/<ns>/structures/.../*.nbt`)
- `weight`: how likely it is to be chosen
- `projection`:
  - `rigid`: preserve the template’s Y (good for buildings)
  - `terrain_matching`: adapt to terrain (good for paths/ground stuff)
- `processors`: optional `worldgen/processor_list` to mutate blocks on placement

### NBT structure templates: `.nbt`

Files: `data/<ns>/structures/**/*.nbt`

These are the actual structure pieces. They can contain **pool connector blocks**; these “ports” say:

- “I connect to a piece from pool X”
- “I want to attach to a target socket named Y”

That’s why the editor links pool sockets ↔ pools.

## Using the editor (what the panes mean)

### Library (left): where the pool list comes from

The pool list is a scan of the pack stack for:

`data/<namespace>/worldgen/template_pool/**/*.json`

Each file becomes a resource id like:

`<namespace>:<path-without-.json>`

If multiple packs define the same id, the editor should show which one “wins” (effective definition) and which ones are shadowed.

### Inspector (right): Form vs Raw

- **Form**: menus/fields for common worldgen types (MVP focuses on `template_pool`).
- **Raw**: direct JSON editing escape hatch.

Saving should write only to the **work pack**, never back into vendor packs.

### Fork-on-edit (the safety switch)

When you try to edit a vendor resource:

1) The editor **copies the exact original file bytes** into `enderterm/work-pack/` first.
2) Then it applies your edits to the forked copy.

This keeps vendor packs pristine and makes diffs obvious.

### Regrow (why it feels so immediate)

The viewer’s “metropolis” is built by iteratively expanding pool sockets:

- Each expansion step picks elements from pools.
- The “seed tape” makes those picks deterministic.

So when you change pool weights or swap pieces, you can hit **Regrow** and see what changed while keeping earlier random choices stable.

## Practical recipes

### Make the “big piece” happen more often

Open the pool that contains the big piece and:

- Increase its `weight`
- Reduce (or remove) tiny/boring pieces
- Ensure the big piece’s pool sockets point at pools that can sustain growth (not dead-end pools)

### Make a pool stop growing (intentional dead ends)

- Add `minecraft:empty_pool_element` with a non-zero weight, or
- Point sockets at `minecraft:empty` (as a pool id) if the pack uses that convention

### When something refuses to connect

Common causes:
- Pool “target” name mismatch (socket expects a different target)
- Wrong pool id in the pool connector block
- `projection` mismatch (terrain matching vs rigid) making it fail placement rules
- Processor list turning required blocks into air (rare, but spicy)

## Getting edits into Minecraft (server/client)

`enderterm/work-pack/` is a real datapack root (it has `pack.mcmeta`).

- Zip `enderterm/work-pack/` and drop it into `<world>/datapacks/`, then `/reload`.
- Remember: worldgen edits only affect **newly generated chunks** unless you regenerate.
