# Remove the zed-bell OpenCode plugin — Implementation Plan

Date: 2026-10-23

## Background / intent

The `zed-bell` plugin (added in b1f6e90, `harnesses/opencode/plugin/zed-bell/`)
rings the terminal bell on turn-end events. It fires at the wrong times and
the primary harness is now ACP, where it is not wanted. The human asked to
remove it. No spec document exists for this removal; this plan carries the
decisions (see Rulings).

Precedent: commit 29d16cd (removal of the Plannotator wake plugin) deleted the
plugin source, added its name to `LEGACY_PLUGIN_NAMES` so stale host copies
are purged on `tkt install-opencode-agent`, and updated tests/docs in the same
commit. Historical plan docs under `docs/superpowers/plans/` are never
deleted, so `2026-10-14-zed-bell.md` stays as a record.

## Rulings (controller decisions, no separate spec)

- **R1 — Delete the plugin-copy machinery outright.** After removing
  `zed-bell`, nothing copies plugins into `~/.config/opencode/plugin/`
  anymore, so delete `PLUGIN_NAMES`, `plugin_source`, `_install_plugin`,
  `_install_plugin_dir`, `_plugin_tree`, and the now-unused `tempfile`
  import from `tkt/install.py` (YAGNI; trivially restored from git history if
  a future plugin needs it). Keep `plugin_dest` — the legacy purge still
  needs it. `shutil` stays (needed for `rmtree`).
- **R2 — Purge stale host copies.** Users who ran `tkt install-opencode-agent`
  have `~/.config/opencode/plugin/zed-bell/` installed and it would keep
  ringing. Add `"zed-bell"` to `LEGACY_PLUGIN_NAMES` and extend the legacy
  purge loop to remove directories (`shutil.rmtree`) as well as files.
- **R3 — Keep `docs/superpowers/plans/2026-10-14-zed-bell.md`** as historical
  record (precedent: the wake-plugin plan/spec were never deleted).

## Global Constraints

- ruff (line-length 110, numpy docstrings), ruff format, mypy must pass:
  `ruff check . && ruff format --check . && mypy tkt/`.
- Tests: `python -m pytest tests/test_install.py` must pass, then full
  `python -m pytest`.
- Preserve the BSD-3 license headers on all `.py` files.
- `install_opencode_agent` must keep working in dry-run mode: report, change
  nothing.

## Task 1: Remove zed-bell plugin, source and installed copies, and update tests/docs

**Files:**
- Delete: `harnesses/opencode/plugin/zed-bell/` (the whole directory; if
  `harnesses/opencode/plugin/` becomes empty, it disappears from git with the
  files).
- Modify: `tkt/install.py`, `tkt/_cli.py`, `tests/test_install.py`,
  `AGENTS.md`.

### 1.1 `tkt/install.py`

Remove these entirely: `PLUGIN_NAMES` (and its comment block),
`plugin_source`, `_install_plugin`, `_install_plugin_dir`, `_plugin_tree`,
and the `tempfile` import. Keep `plugin_dest`, updating its docstring to drop
the "delivery is a copy" rationale (it now only locates legacy copies to
purge), e.g.:

```python
def plugin_dest(name: str, home: str | None = None) -> str:
    """Host path of the plugin artifact ``name`` under
    ``~/.config/opencode/plugin/``.

    Used to purge legacy copies: the installed OpenCode's plugin loader
    autoloads real files and directories here (it skips symlinks), so a
    stale copy keeps loading until removed.
    """
    return os.path.join(home or os.path.expanduser("~"), ".config", "opencode", "plugin", name)
```

Replace the constants block (the `PLUGIN_NAMES` comment + definition and the
`LEGACY_PLUGIN_NAMES` comment + definition) with:

```python
# tkt-owned plugin artifacts from older installs that live in the host plugin
# dir but must be *removed* on install (their feature was removed from tkt;
# a stale copy would otherwise keep loading).  A name may be a single file or
# a directory plugin (e.g. ``zed-bell``).
LEGACY_PLUGIN_NAMES: tuple[str, ...] = ("zed-bell.js", "zed-bell", "plannotator-wake.js")
```

In `install_opencode_agent`, update the docstring's plugin paragraph to say
it removes legacy plugin copies (both the bell plugin and the former wake
plugin) from `~/.config/opencode/plugin/` — no more copying — and replace the
two loops (legacy purge + plugin copy loop) with just the legacy purge:

```python
    for name in LEGACY_PLUGIN_NAMES:
        legacy = plugin_dest(name, home)
        if not os.path.lexists(legacy):
            continue
        logging.warning(f"Removing legacy plugin copy {legacy} (removed from tkt)")
        if not dry_run:
            if os.path.isdir(legacy) and not os.path.islink(legacy):
                shutil.rmtree(legacy)
            else:
                os.remove(legacy)
```

(The symlink-to-directory case takes the `os.remove` branch, unlinking the
link without touching its target — intended.)

### 1.2 `tkt/_cli.py`

Update the `install-opencode-agent` command help: replace
"and copy the autoloaded plugins (zed-bell) into ~/.config/opencode/plugin/."
with "and remove legacy tkt plugin copies from ~/.config/opencode/plugin/."

### 1.3 `tests/test_install.py`

- In `_make_repo`, delete the block that creates
  `harnesses/opencode/plugin/zed-bell/` (no plugin sources remain).
- Delete these tests (they test the removed copy machinery):
  `test_install_opencode_agent_copies_plugins`,
  `test_install_opencode_agent_plugins_idempotent`,
  `test_install_opencode_agent_zed_bell_replaces_stale_directory`.
- Rewrite `test_install_opencode_agent_removes_legacy_zed_bell_file`: keep it
  asserting the legacy single file is gone with a "legacy" warning, but drop
  the trailing assertion that the directory plugin still installs.
- Rewrite `test_install_opencode_agent_removes_legacy_wake_plugin`: drop the
  "surviving plugin still installs" assertion; keep the legacy-file and
  symlink-at-destination cases.
- Add `test_install_opencode_agent_removes_legacy_zed_bell_dir`: create
  `~/.config/opencode/plugin/zed-bell/` containing `index.js` and `tui.js`,
  run `install_opencode_agent`, assert the directory is gone entirely
  (`not os.path.lexists(...)`) and a "legacy" warning was logged; also cover
  a symlink named `zed-bell` pointing at a directory elsewhere — after
  install the link is gone but the target directory still exists.
- Rewrite `test_install_opencode_agent_plugins_dry_run_writes_nothing` (may
  rename to `test_install_opencode_agent_legacy_dry_run_writes_nothing`):
  pre-create the legacy `zed-bell.js` file, the `zed-bell/` directory and the
  `plannotator-wake.js` file; dry run must leave all three in place.
- The `_plugin_dst` helper stays.

### 1.4 `AGENTS.md`

In the "OpenCode integration" section, replace the final sentence block about
copying the `harnesses/opencode/plugin/zed-bell/` directory plugin with a
sentence saying `tkt install-opencode-agent` also removes tkt-owned plugin
copies from older installs — the former bell plugin (it misfired, especially
under ACP) and the former wake plugin (its server-side prompt injection
cannot wake ACP-driven sessions) among them; see `LEGACY_PLUGIN_NAMES` in
`tkt/install.py`.

### 1.5 Verify

`python -m pytest tests/test_install.py` then `python -m pytest`;
`ruff check . && ruff format --check . && mypy tkt/`.

Commit message shape (single commit): "Remove the zed-bell plugin; bell events
misfire and ACP is the primary harness" with a short body noting the source
deletion, the LEGACY_PLUGIN_NAMES purge extension to directories, and the
machinery removal.
