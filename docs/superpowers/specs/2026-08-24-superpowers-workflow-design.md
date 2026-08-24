# Superpowers workflow for tkt — design

Date: 2026-08-24
Status: Approved (pending this spec's review)

## Problem

`tkt` currently drives change control through **OpenSpec** (the `openspec` tool
installs its skills and points at a shared store at `~/LSST/openspec`). We want
to make **superpowers** the default workflow for changes, while keeping OpenSpec
available as something to revert to.

Superpowers differs from OpenSpec in two ways that matter here:

- Its skills are provided by a **fork** at `~/.config/opencode/superpowers`
  (loaded via `skills.paths` in `opencode.jsonc`), not installed per-workspace by
  an `init` command.
- It version-controls its durable artifacts (design specs, implementation plans)
  under `docs/superpowers/{specs,plans}/`, with the path **hardcoded in the skill
  text**. There is no "store" concept like OpenSpec's.

The central problem is where those durable doc artifacts live for **multi-repo
DM ticket workspaces** (`tkt new DM-XXXXX ...`): we do **not** want superpowers
subdirectories inside the individual git repos of a tkt-managed project. The
natural analog to OpenSpec's store is a **shared git repo**, namespaced per
ticket by `DM-XXXXX`.

## Goals

- Make superpowers the default workflow for changes (including changes to `tkt`
  itself), selected via `default_tools`.
- Keep OpenSpec installed but no longer default, so it can be reverted to.
- In multi-repo DM workspaces, store superpowers design specs and implementation
  plans in a **shared git repo**, namespaced by `DM-XXXXX`.
- In single-repo projects (including `tkt` itself, and other sandbox projects
  that do not use the multi-repo layout), store them **in-repo** under
  `docs/superpowers/`, as superpowers already does.
- Keep transient superpowers scratch (SDD execution workspace, brainstorm
  mockups) out of committed docs and out of per-package repos.
- Let an existing OpenSpec workspace migrate to superpowers: the two tools may
  coexist, and OpenSpec (and its artifacts) can be removed from a workspace.

## Non-goals

- Not removing OpenSpec or its tooling from `local.json`; it stays installed.
- Not changing how the sandbox itself works (the bridge work is already done).
- Not generalizing superpowers to arbitrary external stores beyond the single
  shared repo described here.

## Two regimes, selected by `SUPERPOWERS_DIR`

The superpowers fork skills read an environment variable, `SUPERPOWERS_DIR`:

- **If set**, the skills write design specs to `$SUPERPOWERS_DIR/specs/` and
  implementation plans to `$SUPERPOWERS_DIR/plans/`.
- **If unset**, they fall back to the in-repo defaults `docs/superpowers/specs/`
  and `docs/superpowers/plans/` — exactly today's behavior.

This gives two regimes with no special-casing:

| Regime                              | `SUPERPOWERS_DIR`                                     | Docs location                     |
| ----------------------------------- | ----------------------------------------------------- | --------------------------------- |
| Multi-repo DM workspace             | `<shared>/<ticket>` (set by the workspace EUPS table) | Shared repo, per-ticket namespace |
| Single-repo project (`tkt`, others) | unset                                                 | In-repo `docs/superpowers/`       |

Transient scratch is **not** governed by `SUPERPOWERS_DIR`. The SDD execution
workspace and brainstorm mockups keep their existing `git rev-parse` behavior,
which, in a DM workspace sandbox, lands them in the writable `.agent/<pkg>`
worktree under `.superpowers/` — git-ignored and easy to clean up when a ticket
is done. No fork change is needed for scratch.

## Components

### 1. Shared repo

A plain git repository at `~/LSST/superpowers-docs` (distinct from the fork at
`~/.config/opencode/superpowers`). It is **not** an EUPS product. It holds a
per-ticket namespace:

```
~/LSST/superpowers-docs/
  DM-12345/
    specs/        # design specs for DM-12345
    plans/        # implementation plans for DM-12345
  DM-99999/
    ...
```

Only durable docs live here (committed). Transient scratch does not.

### 2. Fork: skill changes (superpowers repo)

- `skills/brainstorming/SKILL.md`: change the spec-save instruction to write to
  `$SUPERPOWERS_DIR/specs/` when `SUPERPOWERS_DIR` is set, otherwise
  `docs/superpowers/specs/`. (The skill already notes "user preferences for spec
  location override this default"; this makes the preference machine-provided.)
- `skills/writing-plans/SKILL.md`: same for plans → `$SUPERPOWERS_DIR/plans/`
  otherwise `docs/superpowers/plans/`.

The relevant script (if any) and prose should ensure the target directory
exists before writing.

### 3. Fork: agent permission changes (superpowers repo)

`sp-brainstorm` and `sp-plan` currently scope `edit` to
`docs/superpowers/{specs,plans}/*.md` with `*: deny`, and scope `bash` to
`{ "*": ask, "git": allow }`. We open both tools fully for these two agents:

- **`edit: allow`**: docs may now live in an external, per-environment path, so
  the relative glob no longer describes them, and we do not want to bake a
  machine-specific path into the fork.
- **`bash: allow`**: the design-doc flow requires the agent to `git add` /
  `git commit` its own artifacts, which the scoped `bash` config would otherwise
  prompt on.

In both cases we rely on the skill and system-prompt instructions to keep the
agent writing docs and using git, rather than on permission scoping. Rationale:
the scoping was an unnecessary guard against rare, low-stakes misbehavior, and
the real safety boundary is the sandbox.

### 4. opencode.jsonc: `external_directory`

The shared repo is outside any project root, so opencode's
`permission.external_directory` must allow it. Add
`"/home/jbosch/LSST/superpowers-docs/**": "allow"`, alongside the existing
`/home/jbosch/LSST/openspec/**` and `/home/jbosch/LSST/install/**` entries. This
mirrors how external writes are already enabled.

### 5. tkt: `Superpowers` tool (`tkt/superpowers.py`)

A new `Superpowers(Tool)` mirroring the `OpenSpec` tool's shape:

- Config: `path` = the shared repo root (e.g. `/home/jbosch/LSST/superpowers-docs`).
- `from_json_data`: pops `path` from the tool config.
- `write(ticket, directory, packages, workspace, environment)`: creates the
  per-ticket namespace `<path>/<ticket>/specs` and `<path>/<ticket>/plans` (and
  `<path>/<ticket>`), so the ticket has a ready-to-use docs home.

### 6. tkt: workspace EUPS table (`tkt/_workspace.py`)

`_write_eups_table` gains, when `superpowers` is in the workspace's tools, a line

```
envSet(SUPERPOWERS_DIR, <shared-path>/<ticket>)
```

where `<shared-path>` is read from `environment.get_tool("superpowers").path`.
This matches the table's existing literal-path pattern (e.g.
`setupRequired(tkt -r {tkt_dir})`). Because the sandbox sets up EUPS from the
workspace table, the var is present in the agent process inside the sandbox with
no cross-tool coupling.

### 7. local.json

- `default_tools`: replace `openspec` with `superpowers` (order-preserving). The
  `openspec` tool config remains under `tools`, so it can be re-added or
  reverted later.
- `tools`: add
  `"superpowers": { "module": "tkt.superpowers", "cls": "Superpowers", "path": "/home/jbosch/LSST/superpowers-docs" }`.
- `sandbox` tool `mounts.rw`: add `/home/jbosch/LSST/superpowers-docs` so the
  agent can write docs into the shared repo from inside the sandbox. (The fork
  at `~/.config/opencode/superpowers` is already writable under
  `~/.config/opencode`.)

### 8. tkt: `AGENTS.md.in`

Replace the OpenSpec `allowedEditRoots` section with a superpowers one covering:
where docs live (`$SUPERPOWERS_DIR`/shared in DM workspaces, in-repo
`docs/superpowers/` otherwise), that doc work happens from the shared/main
workspace, that all code changes belong under `.agent/**`, and that the
`.superpowers/` scratch dirs live under `.agent/<pkg>` and are git-ignored.

### 9. tkt: `Tool.remove()` for artifact cleanup

The `Tool` base gains an optional `remove(directory)` method (default no-op) to
clean up the artifacts a tool wrote into a workspace. `OpenSpec.remove(directory)`
deletes the `openspec/` dir and the `.opencode/skills/openspec-*` files (and the
`.opencode/skills` dir if it becomes empty). `Superpowers` keeps the default
no-op: its namespace dirs live in the shared repo, which we do not destroy on
removal.

## Migration from OpenSpec to superpowers

Coexistence works out of the box: after the `default_tools` swap, `tkt update`
on an existing workspace adds `superpowers` as a missing default and leaves
`openspec` in place.

`update` additionally recognizes tools that are **configured but no longer in
`default_tools`** (`env.get_tool(t) is not None and t not in env.default_tools`)
and prompts to remove them. On confirmation it drops the name from the
workspace's tools and calls `env.get_tool(t).remove(workspace.directory)` to
clean up the written artifacts. Declining the prompt keeps the tool (and its
artifacts) — that is coexistence. `-n/--dry-run` reports which tools would be
prompted for removal.

So migration is: swap `default_tools` in `local.json`, then `tkt update` on each
existing workspace → adds superpowers, prompts to remove openspec + artifacts.

## Data flow (multi-repo DM workspace)

1. `tkt new DM-12345 ...` writes the workspace, including the EUPS table with
   `envSet(SUPERPOWERS_DIR, <shared>/DM-12345)`; the `Superpowers` tool creates
   `<shared>/DM-12345/{specs,plans}`.
2. `tkt sandbox-run` starts the sandbox; EUPS setup propagates `SUPERPOWERS_DIR`.
   The shared repo is rw-mounted.
3. In-sandbox, the agent runs an `sp-*` agent; the brainstorming/writing-plans
   skills read `SUPERPOWERS_DIR` and save docs to the shared namespace.
4. Docs are committed to the shared repo (agent or user). Scratch stays under
   `.agent/<pkg>/.superpowers/`.

## Error handling / edge cases

- **Shared repo absent or not a git repo**: `Superpowers.write` creates the
  namespace dirs under `path`; if `path` does not exist, it is created. If the
  user expects a git repo and it is missing, that is a setup concern surfaced at
  the shell, consistent with tkt's "users are developers" stance.
- **`SUPERPOWERS_DIR` unset but docs expected shared**: impossible by
  construction in a DM workspace (the table always sets it); in single-repo mode
  the fallback is the intended behavior.
- **Agent writes outside intended dirs**: now permitted by `edit: allow`, but
  bounded by the sandbox and the skill instructions; acceptable per the
  non-goals.
- **SDD scratch permission**: the SDD execution workspace derives its location
  from the git top-level (`$(git rev-parse --show-toplevel)/.superpowers/sdd/`),
  so in a DM workspace it lands under the writable worktree
  `.agent/<pkg>/.superpowers/sdd/`. Because opencode permissions are relative to
  the project (ticket) root, the `sp-implement` agent must allow
  `.agent/**/.superpowers/sdd/**` (in addition to `.superpowers/sdd/**` for
  single-repo projects) or SDD ledger/brief/report writes are denied.

## Testing

- `Superpowers.from_json_data` validates `path`.
- `Superpowers.write` creates `<path>/<ticket>/specs` and `/plans`.
- `_write_eups_table` emits `envSet(SUPERPOWERS_DIR, <path>/<ticket>)` when
  `superpowers` is in tools, and nothing when it is not.
- `OpenSpec.remove` deletes `openspec/` and `.opencode/skills/openspec-*` (and
  the empty `.opencode/skills` dir); the default `Tool.remove` is a no-op.
- `update` prompts to remove configured-but-no-longer-default tools and calls
  `remove()` on confirmation; `-n` reports them without removing.
- Fork: a lightweight check (e.g. grep in the fork's tests or a manual check)
  that the skill texts reference `SUPERPOWERS_DIR` and its fallback.
- Manual: create a DM workspace, confirm the namespace dirs and table line, run a
  sandbox, and confirm an in-sandbox brainstorm writes to the shared namespace.

## Sequencing

1. Fork: skill changes (`SUPERPOWERS_DIR`) + agent `edit: allow` for
   `sp-brainstorm`/`sp-plan`; opencode.jsonc `external_directory` entry.
2. Shared repo scaffold (`~/LSST/superpowers-docs`, initial commit).
3. tkt: `Superpowers` tool, table `envSet`, `local.json` (default_tools swap +
   tool entry + sandbox mount), `AGENTS.md.in`.
4. Verify end-to-end in a real DM workspace.
5. Migrate an existing OpenSpec workspace via `tkt update` (add superpowers,
   prompt-remove openspec + artifacts).
6. Use superpowers for tkt itself going forward (this doc -> plan -> implement
   via `sp-plan`/`sp-implement`).

## Open items

- None blocking. Minor: the exact `SUPERPOWERS_DIR` naming is confirmed; the
  shared repo name `~/LSST/superpowers-docs` is a placeholder and could be
  adjusted.
