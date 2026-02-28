## TODO

Dev note: when adding a new visual effect, start with intensity turned way up so the behavior is obvious, then dial it down.

### Highest impact (next)

- [ ] NBT export fidelity: include block-entity `blocks[].nbt` and `entities[]` (currently blocks+palette only)
- [ ] NBT export: preserve original positions (store origin offset in a sidecar `.json`, or embed via a convention)
- [ ] Pool fidelity: support more pool element types and `projection`/processors closer to vanilla behavior

### Input / UX

- [ ] Search: add `Esc` behavior to clear query (decide whether it exits filter mode or keeps the filter UI open)

### Viewer / UI polish

- [ ] Use tweens to animate camera “jumps” (orbit-target changes); cancel on user input (partially done)
- [ ] Use tweens to animate Rez Log open/close (height + opacity) (partially done)
- [ ] Rez effect: emit per-block “rez cube” flashes (for newly-added blocks); add caps/sampling so it stays fast (partially done)
- [ ] Rez effect: have rez cubes grow slightly as they fade out

### Maintenance

- [ ] Add a small “repro pack” / fixture for testing pool expansion determinism
- [ ] Add a minimal CLI `self-test` that exercises NBT export+import roundtrip

### Done (recent)

- [x] Model load transition: “screen power-on” effect for initial load + Up/Down selection changes
- [x] Add a subtle vignette over the 3D view (model viewport only)
- [x] “Constants” window (C): live sliders + JSON persistence + scrollbar
- [x] Post-fx pipeline: render model view to an offscreen buffer so glitches/vignette are true screen-space and scalable to 60fps
- [x] Help overlay (`?`): floating panel with controls + live debug stats (FPS, blocks, rez queue depth, seed, selection)
- [x] macOS trackpad gestures: pinch-to-zoom (around cursor hit-point), two-finger pan, optional rotate gesture
- [x] Treat “precise” trackpad scroll deltas as pan (and mouse wheel as zoom); keep current mouse controls unchanged
- [x] Make gestures optional: no PyObjC installed → no gesture support; add a flag/env var to force-disable
