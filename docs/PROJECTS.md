# GitHub Projects + Issues (scheduling)

This repo uses **GitHub Issues** as the unit of work and a single **GitHub Project** as the schedule.

## Create the Project (one-time)

In GitHub:

1. Go to the repo → **Projects** tab → **New project**
2. Pick **Board** layout
3. Name it: `EnderTerm`

## Columns (suggested)

Use these columns, in order:

- `Inbox` (untriaged ideas / requests)
- `Next` (committed work for the next couple days)
- `In Progress` (someone is actively working)
- `Review` (PR open; waiting on review / qarl eyeball)
- `Done`

## Project settings

Suggested options:

- Turn on the **“Pull requests”** field (so you can see PR status per issue).
- Add a **“Priority”** single-select field if you want, otherwise rely on labels (`prio:P0/P1/P2`).

## How work flows

**One Issue → one branch → one PR.**

1. Create/triage issues into `Inbox`
2. When you commit to doing something soon, move it to `Next`
3. When a worker starts, assign them + move to `In Progress`
4. When a PR is up, move to `Review`
5. Merge → move to `Done`

Rule: keep `Next` small. If `Next` is huge, it stops meaning anything.

## “qarl eyeball” rules

Any issue that requires qarl to visually validate should:

- Have label `needs-qarl-eyeball`
- Include exactly one copy/paste command using `--test-banner`

Example:

`python -m enderterm datapack-view <pack.zip> --test-banner 'TEST: non-square anim textures update correctly in build bar (seagrass)'`

## Friday demo milestone

For a deadline (e.g. the YouTube drop), use a Milestone:

1. Repo → **Issues** → **Milestones** → **New milestone**
2. Name it: `Demo: <date>`
3. Put all demo-critical issues in that milestone

## Triage cadence

- Daily: empty `Review` and keep `In Progress` honest
- Twice a week: triage `Inbox` → `Next` (or close)
