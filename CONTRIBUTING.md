# Contributing to EnderTerm

This repo is actively developed by multiple parallel workers.

Start here:
- `docs/WORKFLOW.md`
- `docs/PROJECTS.md`

## Development flow

1. Open an issue with scope and acceptance checks.
2. Implement a focused branch.
3. Provide verification commands and outputs.
4. Submit PR with risk/caveat notes.

If you are submitting a PR that needs visual validation, include one copy/paste test command that uses `--test-banner`.

## Quality baseline

- Behavior-preserving refactors are welcome when clearly justified.
- User-facing behavior changes require updated docs/tests.
- Keep helper scripts dependency-light and portable.

## Compatibility note

Assume standard vanilla Java clients in near-term workflows.
