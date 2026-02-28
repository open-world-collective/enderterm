# Lost features (archeology notes)

This is a scratchpad of features that were described in our working transcript but do not appear to exist in the current committed codebase. It also includes a concrete plan to reintroduce/replace them (write-down only; no execution).

## Likely “lost” features (from transcript)

- **Terrain rez stipple/dither masking**: the “staticy pixel mask” reveal (depth-test friendly; no blending/sorting) appears to have been replaced by (or fallen back to) **smooth alpha fades** for terrain coming in.
- **Stipple-specific kValues**: controls for **stipple scale** (you asked for ~16m/32m), **speed/Hz**, and a **phase/time driver** (“I want it FAST”) that made the mask flicker; these are either gone or no longer wired to the active render path.
- **kValue per-parameter help**: an improved kValue UI with **AI-generated help/explanations** “for all the values” (so you can tell what each slider does).
- **Global UI font scaling**: a kValue like `ui.font.scale` to make **all UI text bigger** (eyes tired).

## Instructions to restore/replace (write-down only)

### 1) Bring back stipple masking as a selectable mode

- Add a kValue: `env.ground.reveal.mode` = `alpha` or `stipple`.
- Keep the existing smooth fade path as `alpha` for A/B testing and as a safe fallback.

### 2) Implement stipple in a shader (not fixed-function polygon stipple)

In the terrain fragment shader:

- Compute `reveal` in `[0..1]` from “time since this patch spawned” and `env.ground.reveal.duration_s`.
- Compute a *mask noise* value in `[0..1]` from:
  - screen-space (`gl_FragCoord.xy / scale`) plus
  - a per-patch random offset (so different patches don’t line up), plus
  - time * phase rate (so it flickers)
- Do `if noise > reveal: discard;` (masked reveal).

Rationale:

- Avoids blending + sorting costs.
- Matches the legacy alpha-mask behavior better.

### 3) Reintroduce/standardize the stipple kValues (with clear names)

- `env.ground.reveal.duration_s` (how long until fully visible)
- `env.ground.stipple.scale.blocks` (size of pattern in “blocks” / world-ish terms; default 32m-ish)
- `env.ground.stipple.rate_hz` (flicker speed)
- `env.ground.stipple.phase_rate` (how fast we move through noise space)
- `env.ground.stipple.alpha_floor` (optional: minimum reveal so it never fully disappears)
- `env.ground.stipple.debug` (optional visualizer toggle)
- Add aliases so older names like `strip_fade` don’t strand saved JSON.

### 4) Restore the “AI help for kValues” UX without hard dependencies

- Extend `ParamDef` with `help: str` (or keep a separate `PARAM_HELP: dict[key,str]`).
- Add a right-side detail pane in the kValue window that shows:
  - key, label, current value, default, min/max, and help text (word-wrapped)
- Later/offline: write a one-shot script to generate `kvalue_help.json` from ParamDef list using an LLM; commit the JSON; runtime only reads it.

### 5) Add global font scaling cleanly

- Add `ui.font.scale` kValue.
- Replace hardcoded `font_size=10/12/14/...` with `base_size * ui.font.scale`.
- Also scale layout constants that depend on text size (row height, padding, panel widths) to avoid clipping.
