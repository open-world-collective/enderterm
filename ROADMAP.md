# EnderTerm Roadmap

This roadmap is meant to show a credible path from prototype to durable platform.

## Current reality

- The project already ships usable tooling and integration tests.
- The current runtime is intentionally vanilla-compatible and pragmatic.
- Stability and clarity are prioritized over premature rewrites.

## 30-day goals

- Harden integration-test reliability and reduce flaky UI paths.
- Document operational runbooks for repeatable local setup and smoke tests.
- Improve profiling visibility so optimization work targets measured hotspots.

## 60-day goals

- Isolate performance-critical paths behind stable internal interfaces.
- Reduce coupling between game-specific assumptions and core tooling modules.
- Add stricter quality gates for regressions in render and interaction loops.

## 90-day goals

- Land a first engine-abstraction layer for core world-interface operations.
- Establish baseline performance budgets and automated checks.
- Publish migration notes for contributors as internals evolve.

## Long-term technical direction

- Migrate performance-critical runtime and rendering paths to Rust.
- Use native GPU backends behind an engine-agnostic interface:
  - Metal on Apple platforms.
  - DirectX 12 on Windows.
  - Vulkan for cross-platform targets.
- Continue starting from vanilla-compatible workflows while decoupling toward a broader runtime architecture.

## Non-goals (current phase)

- No claim that EnderTerm is already a full replacement game client.
- No lock-in to a single engine backend while architecture is still evolving.
