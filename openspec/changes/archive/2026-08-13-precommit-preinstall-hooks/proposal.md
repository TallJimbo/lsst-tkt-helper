## Why

The `PreCommit` tool currently runs only `prek install` / `pre-commit install`,
which registers the git hook shim but defers hook-environment (dependency)
installation to the first commit. Because the sandbox is network-restricted
(`sandbox-network`), an agent that tries to make the first commit triggers that
lazy dependency install, which fails for lack of network access. We want the
hook environments to be pre-installed at pre-commit setup time (on the host,
where the network is available) so the agent's first commit runs hooks offline.

## What Changes

- The `PreCommit` tool's `install` step will pre-install all hook environments
  from each package's config, instead of only registering the hook shim:
  - `prek install --prepare-hooks`
  - `pre-commit install --install-hooks`
- The shared hook-environment store must be visible (read-write) inside the
  sandbox so the agent reuses the pre-built environments without needing
  network. This is a per-environment configuration change (the `sandbox`
  tool's `mounts.rw` in `local.json`), where the active store is mounted
  (`~/.cache/prek`, or `~/.cache/pre-commit` when prek is unavailable).

No breaking changes; existing behavior for setups that already have populated
stores is unchanged (pre-installing is idempotent).

## Capabilities

### New Capabilities

- `precommit-hooks`: The `PreCommit` tool pre-installs hook environments at
  setup time (host, network available), and the shared hook-environment store
  is made read-write accessible inside the sandbox so agents can run pre-commit
  hooks on their first commit without network access.

### Modified Capabilities

<!-- None: no existing spec's requirements change. `sandbox-network` is only
     touched implicitly via the sandbox's mount configuration. -->

## Impact

- `tkt/precommit.py`: add `--prepare-hooks` (prek) / `--install-hooks`
  (pre-commit) to the `install` invocation in `_run_for_package`.
- `local.json`: add the hook-environment store to the `sandbox` tool's
  `mounts.rw`.
- Behavior: `tkt new` / `tkt update` will download hook environments at setup
  (one-time, idempotent) rather than on first commit.
