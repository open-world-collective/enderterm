# EnderTerm workflow (multi-worker)

This repo is being worked on by multiple parallel “workers”. The goal is to avoid collisions and make it easy to schedule work with **GitHub Issues**.

## The rule of the road

**One Issue → one branch → one PR.**

- Create an Issue for any non-trivial work (even “quick” fixes if they might conflict).
- Do the work on a branch.
- Open a PR early (draft is fine), then merge when done.

## Daily worker loop

1. **Sync first**
   - `git pull --ff-only`
2. **Pick an Issue**
   - Assign yourself (or comment “I’m taking this”).
   - If the issue needs coordination, comment your plan before coding.
3. **Create a branch**
   - Suggested names:
     - `bug/<short-slug>`
     - `feat/<short-slug>`
     - `chore/<short-slug>`
     - `issue/<number>-<short-slug>`
4. **Open a PR early**
   - Keeps work visible and prevents “two people fixed it differently”.
5. **Keep the PR tight**
   - Small, testable increments.
   - Don’t mix unrelated changes.
6. **Merge**
   - Prefer squash merges for small PRs, regular merges for larger feature branches (team preference).

## Manual testing: required `--test-banner`

If a change needs qarl to visually validate (rendering/UI/feel), the PR **must** include one copy/paste command line that runs EnderTerm with an on-screen banner describing what to look for.

The banner automatically includes the build id (git describe / version), so qarl can tell which build he’s testing.

Examples:

- `python -m enderterm datapack-view path/to/pack.zip --test-banner 'TEST: rez fade overlaps correctly (no pops) when placing blocks fast'`
- `python -m enderterm nbt-view path/to/structure.nbt --test-banner 'TEST: texture UVs correct on rotated stairs'`

## Recommended labels

These make scheduling + filtering fast:

- Priority: `prio:P0`, `prio:P1`, `prio:P2`
- Type: `type:bug`, `type:feat`, `type:chore`
- Area: `area:termui`, `area:worldgen`, `area:render`, `area:packaging`, `area:tests`
- Review: `needs-qarl-eyeball`

Keeping labels consistent:

- Repo includes `scripts/github_labels.json` + `scripts/sync_github_labels.py`.
- GitHub Actions workflow “Sync labels” can be run manually (Actions → Sync labels → Run workflow) to create/update labels.

## Project board (optional, but useful)

Setup checklist:
- `docs/PROJECTS.md`

Use a GitHub Project with columns:

`Inbox` → `Next` → `In Progress` → `Review` → `Done`

Scheduling rule:
- Only `Next` is “committed work”.
- Everything else stays in `Inbox`.
