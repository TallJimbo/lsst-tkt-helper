## Context

`PreCommit.write` (invoked from `tkt new` / `tkt update` on the host, which has
network access) runs `prek install` / `pre-commit install` in each package's
main worktree. That command only writes a git hook shim; it defers installing
the hook environments (their dependencies) until the first commit.

The `Sandbox` runs agents in a network-restricted namespace (`sandbox-network`)
with `$HOME` replaced by an empty tmpfs. The hook-environment store lives under
`$HOME/.cache` (prek: `~/.cache/prek`; pre-commit: `~/.cache/pre-commit`), which
is neither populated before the sandbox starts nor mounted into it. So on the
agent's first commit, prek/pre-commit tries to download + install hook
environments and fails for lack of network.

Verified empirically: a hook shim installed in the main worktree's `.git/hooks/`
does run when committing from a linked worktree (`.agent/<pkg>`), and prek's
store is keyed by repo/revision, so environments pre-installed from the main
checkout are reused by the agent's linked worktree.

## Goals / Non-Goals

**Goals:**
- Pre-install all hook environments at pre-commit setup time (host, network
  available) instead of on first commit.
- Make the pre-built store visible (read-write) inside the sandbox so an agent's
  first commit runs hooks offline.
- Keep the change small and consistent with the existing `tkt` design
  (tools + per-environment `local.json` configuration).

**Non-Goals:**
- Granting the sandbox general network access.
- Changing the hook configuration format or hook execution semantics.
- Migrating or relocating the global hook-environment store for tools other than
  prek/pre-commit.

## Decisions

### Decision 1: Pre-install via the tool's native flag

In `_run_for_package`, append the pre-install flag to the existing `install`
invocation, selected by the already-computed `executable`:

- `prek install --prepare-hooks`
- `pre-commit install --install-hooks`

**Rationale**: These flags are the upstream-supported way to "install all hook
environments for the config without waiting for a commit." Both are present in
the installed versions (`prek 0.4.11`, `pre-commit 4.6.1`). Keeping the existing
`install` call means the shim is still registered, and the pre-install is
idempotent (only missing environments are fetched), so repeated `tkt update`
stays cheap.

**Alternatives considered**:
- Running `prek run --all` / `pre-commit run --all` at setup: would trigger the
  actual hooks (not just install their environments) and could fail if files
  need work, unrelated to dependency availability. Rejected.
- A separate new CLI subcommand: more surface than needed; pre-install naturally
  belongs in the existing setup path.

### Decision 2: Share the store read-write via the sandbox's mount config

Add the active hook-environment store to the `sandbox` tool's `mounts.rw` in
`local.json`:

```
"mounts": { "rw": [ ..., "~/.cache/prek" ] }
```

(`~/.cache/pre-commit` when a machine falls back to the non-prek path.)

**Rationale**: `_build_common_argv` already bind-mounts configured `rw` paths
with `--bind-try` after the `$HOME` tmpfs, so an explicit bind stacks over the
empty tmpfs and is visible at the same absolute path. The store is shared across
all ticket workspaces on the machine, so a single pre-install serves every
sandbox. This is purely an environment-config change (`local.json`), consistent
with how `sandbox` mounts are already declared, and requires no cross-tool code
coupling.

**Alternatives considered**:
- Redirecting `PREK_HOME` / `PRE_COMMIT_HOME` to an already-mounted path (e.g.
  under `~/.cache/opencode`): tkt would have to coordinate where the pre-install
  writes and inject the env var into the sandbox, coupling the two tools.
  Rejected as more complex than a direct mount.
- Mounting all of `~/.cache` read-write: broader writable exposure than today's
  granular `rw` list. Rejected.
- Keeping the store inside the workspace (e.g. `.agent/.cache`) via
  `PREK_HOME`: duplicated per ticket and placed in the agent directory that
  `sandbox-reset` cleans. Rejected.

### Decision 3: Keep the non-zero exit path as a warning

The existing `_run_for_package` already treats a non-zero `install` return code
as a `logging.warning` rather than aborting `tkt new` / `tkt update`. With
`--prepare-hooks` / `--install-hooks`, a setup-time network failure (e.g. an
unreachable hook repo) will therefore warn but not block workspace creation.

**Rationale**: Matches the project's "simple solutions, users can handle
failure modes" guidance and avoids hard-failing setup on a single bad hook repo.

## Risks / Trade-offs

- [Setup-time network call adds latency to `tkt new` / `tkt update`] → It is
  one-time and idempotent; subsequent runs only fetch what's missing. This is
  the explicit trade the change is buying.
- [A hook repo unreachable at setup warns but is not pre-installed] → The agent
  will hit the same failure on first commit as today; this is a degradation only
  for already-broken configs and does not regress the pre-install path.
- [Mounting `~/.cache/prek` rw gives the agent write access to the store] → The
  agent has no network egress, so it cannot pull new/poisoned content; it can
  only reuse existing environments. Accepted.
- [prek's default `PREK_HOME` derivation] → Verified empirically: with
  `PREK_HOME` unset, `prek cache dir` resolves to `~/.cache/prek` (here
  `/home/jbosch/.cache/prek`), matching the mount path in `local.json`.

## Migration Plan

- No data migration. Existing workspaces are unaffected; the change applies when
  `tkt new` / `tkt update` next runs and pre-installs hook environments.
- Rollback: revert the `precommit.py` flag addition and the `local.json` mount
  entry; behavior returns to lazy installation on first commit.

## Open Questions

- None blocking. `PREK_HOME` resolves to `~/.cache/prek` when unset (resolved
  above). Remaining minor uncertainty: whether any machine falls back to
  `pre-commit` (needing `~/.cache/pre-commit` mounted instead); prek is
  installed on the current host, so the prek store is the active mounted one.
