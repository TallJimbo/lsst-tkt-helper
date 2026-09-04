# W1 — Sandboxed MCP `Write`/`Edit` Tools — Design Handover

**Date:** 2026-09-04
**Status:** Approved by human in conversation (before implementation).
**Implements:** workstream **W1** in `docs/zed-agent-roadmap.md` — move the
write/edit surface into the sandbox as MCP tools.

## Goal

Add sandboxed `Write` and `Edit` MCP tools to the tkt MCP server, replacing the
native, unsandboxed Zed `write_file`/`edit_file`. Writes run in the bwrap
sandbox (sharing the existing project-level tracked cwd), so they are confined
by the sandbox mount model — `.agent/**` writable in a workspace, whole repo
writable in single-repo mode — which also fixes writes to git-ignored scratch
under `<pkg>/.superpowers/` that native tools refused. Each call returns a
markdown confirmation/diff so the human can review the change in the tool card.
The Zed harness mapping (`zed-tools.md`) is updated to point create/edit at the
new tools, and the native `write_file`/`edit_file` are disabled in the agent
profile (human-applied). OpenCode is untouched throughout.

## Architecture

The tools follow the established channel for `read`/`ls`/`glob`/`grep`: each
call runs a command through the existing **warm holder** (`WarmSandbox.run`),
which gives it the sandbox's mount model automatically (workspace read-only +
`.agent`/git writable, `$HOME` tmpfs-blocked, `~/.agents/skills` and configured
ro/rw mounts) and returns via the existing base64 framing. The MCP server
process runs host-side, so writing/editing host-side would be unscoped; running
through `warm` is what keeps these tools sandboxed. No new sandbox mounts are
needed.

### Sandbox-side logic lives in a `tkt` module (`python -m`)

Because the sandbox bind-mounts `/` read-only and inherits the host environment
(only `$HOME` is swapped to tmpfs), `tkt` is importable inside the sandbox. The
fiddly part of these tools — capture-before, apply the write/edit, compute the
diff, count lines, branch on the 100-line budget, cap bytes, emit stats — is
therefore implemented as **testable Python** in a new module,
`tkt/mcp_files.py`, invoked inside the sandbox as:

```sh
python -m tkt.mcp_files <op> <args...>
```

This is a deliberate departure from the shell-command builders used by
`read`/`ls`/`glob`/`grep` (which are simple enough for shell), because the
diff/cap/branch logic here is real logic that belongs in unit-testable Python.
It is **not** a human-facing `tkt` CLI subcommand (the user prefers not to
pollute the `tkt` command surface); `python -m` is the interface.

The MCP tool definitions (`Write`/`Edit`) are registered in `mcp_server.py`
alongside the existing tools; they build the `python -m` command and format the
returned output into markdown.

### Content transport

Content passes to the module as **base64 argv** (base64 is shell-safe —
`[A-Za-z0-9+/=]` — so no `shlex.quote` on content, no injection, and arbitrary
bytes round-trip). The module decodes it. This is _content_ decoding specific
to these tools, not the driver's _transport_ b64 framing (cwd/command/timeout
in, stdout/stderr out), which is unchanged.

Content rides in the command's `bash -c` argv, so it is capped at roughly
90 KiB of raw content (Linux per-argument MAX_ARG_STRLEN, 128 KiB, ÷ base64
4/3 inflation, with margin). Over that,
the tool returns a graceful markdown error (see Error handling) rather than an
argv blowup.

### Path resolution & writability

Relative paths resolve against the tracked project-level cwd (the driver does
`cd "$cwd"` before running; the module `os.path.abspath`s the target). The
module emits the resolved **absolute path** for the host to build a clickable
link. Writability is enforced by the sandbox mount model — no host-side path
validation is added.

## Tool shapes and outputs

### `Write`

```
Write(file_path: str, content: str) -> markdown
```

- Creates or overwrites `file_path` with `content`.
- **Auto-creates missing parent directories** (within the writable mount) —
  a deliberate divergence from native Zed `write_file`, which requires the
  parent to exist.
- Writes arbitrary bytes (`content` is decoded from base64).
- **Output:** no content, no diff. A confirmation carrying the absolute path
  (clickable):

  ```
  Wrote /workspace/foo/bar.py
  ```

### `Edit`

```
Edit(file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> markdown
```

- Replaces `old_string` with `new_string` in `file_path`; with `replace_all`,
  replaces every occurrence; otherwise exactly one (and errors if it matches
  more than once).
- Uses Python `read()`/`write()`, which is byte-exact (no trailing-newline
  stripping that bash `$(<file)` would cause).
- **Output — snapshot-based, per-call diff:**
  - Capture the file's before-content (or `/dev/null` if new), apply the edit,
    and compute the unified diff.
  - **≤ 100 diff lines** → show the diff (byte-capped).
  - **> 100 diff lines** → no diff; a confirmation with the absolute path plus
    a stat line (replacements applied, ±lines), e.g.:

    ```
    Edited /workspace/foo/bar.py: 5 replacements, +12/−3 lines
    ```

- The **100-line budget and the byte cap are enforced inside the sandbox,
  before anything crosses the wire** — mirroring how `read` caps bytes
  sandbox-side. In the `>100` branch we never generate-and-ship the big diff;
  we ship only the small confirmation/stats line. In the `≤100` branch, the
  diff stream is byte-capped in the command (`head -c`-style) so a ≤100-line
  diff with pathologically long lines still cannot blow the wire or context.
- Because the host does not receive the diff in the `>100` branch, the stats
  (replacements applied, ±lines) are computed and emitted by the module.
- The 100-line budget and the cap are revisited in a live-testing phase after
  implementation.

## Error handling — graceful markdown, never tracebacks

MCP calls return clean markdown errors (unlike tkt CLI commands, where
tracebacks are acceptable). Cases:

| Condition                                     | Result                                                    |
| --------------------------------------------- | --------------------------------------------------------- |
| `Edit`: `old_string` not found                | `Edit failed: pattern not found at <abs path>`            |
| `Edit`: multiple matches, `replace_all=false` | `Edit failed: pattern matches N times at <abs path>`      |
| `Edit`: target is not valid UTF-8             | `Edit failed: file is not valid UTF-8 text at <abs path>` |
| `Write`/`Edit`: content too large (> ~90 KiB) | `... failed: content too large (N bytes, max ~90 KiB)`    |
| Target path unwritable / other failure        | sandbox-enforced; returned as `... failed: <detail>`      |

## Concretes (authoritative for implementation)

### New module `tkt/mcp_files.py`

Contains the sandbox-side logic, unit-testable in pure Python. Public entry
points follow the existing `_tool(warm, ...)` helper pattern where sensible;
the in-sandbox entry point is reached via `python -m tkt.mcp_files` and:

- takes `<op>` plus base64-encoded content args on argv (target path passed
  shell-quoted);
- resolves the target to an absolute path against its cwd;
- for `edit`: captures before-content, applies the substitution (counting
  replacements), errors gracefully on the not-found / multi-match / non-UTF-8
  cases;
- for `write`: auto-creates parents, writes the decoded bytes;
- computes the snapshot unified diff, counts lines, applies the 100-line budget
  and the in-sandbox byte cap, and emits the resolved absolute path plus the
  diff-or-stats body (and, on failure, the error body), exiting nonzero on
  failure.

Exact framing of the emitted path+body is left to implementation (a first-line
path or a base64 field are both acceptable); the host parses it and formats the
final markdown. **BSD-3 license header required** (all `.py` files).

### `tkt/mcp_server.py` additions

- `__all__` additions: `build_write_command`, `build_edit_command`, `write_tool`,
  `edit_tool`.
- `write_tool(warm, *, file_path, content) -> str` and `edit_tool(warm, *,
file_path, old_string, new_string, replace_all=False) -> str` — build the
  `python -m tkt.mcp_files ...` command, run it through `warm.run`, and return
  the module's markdown summary **as a plain `str`**.
- Register `Write` and `Edit` `@mcp.tool()`s inside `run_server`, after the
  existing tools, with Claude-Code-shaped signatures, each returning the plain
  `str` markdown.

**Why plain `str` (not a JSON `BaseModel` result):** returning JSON breaks Zed's
rich tool-output display — Zed renders MCP text content through its markdown
engine, but a JSON-encoded result is shown as opaque JSON instead of the rendered
summary. The new `Write`/`Edit` return the markdown directly as a `str`. (The
pre-existing tools return typed `BaseModel` results; migrating them off JSON to
get the same rich rendering is tracked as out of scope for W1.)

### `superpowers/skills/using-superpowers/references/zed-tools.md`

Two table rows change (file lives inside the `superpowers` submodule):

```markdown
| Create a file | `Write` |
| Edit a file | `Edit` |
```

Delete/copy/move/mkdir remain on `bash` (`rm`/`cp`/`mv`/`mkdir`) — unchanged.

### Agent profile (human-applied, not committed here)

Disable native `write_file` and `edit_file` in the Zed agent profile, enabling
the MCP `Write`/`Edit` in their place. Delivered as a paste-ready snippet in the
plan's final chat summary, matching the convention from the `read`/`grep`/`glob`
batches.

### Testing

- **Unit tests** for `tkt/mcp_files.py`: diff computation, the ≤100-line
  branch, the in-sandbox byte cap, replacement counting, `replace_all`,
  `mkdir -p` parents, and every graceful-error case (pure Python, mocked `warm`
  where the host side is involved).
- **Integration tests** through `WarmSandbox` in the style of
  `tests/test_sandbox.py`.
- Run with `python -m pytest`; keep `ruff check .`, `ruff format --check .`,
  and `mypy tkt/` clean.

### `docs/zed-agent-roadmap.md`

When W1 lands, remove the W1 block from section 2 (per the roadmap's update
rule) and update the tool-surface table: `write_file`/`edit_file` row moves to
`Write`/`Edit` under "tkt MCP (sandboxed)".

## Key decisions log

1. **Claude Code shapes, exactly** — `Write(file_path, content)` and
   `Edit(file_path, old_string, new_string, replace_all?)`, not the Zed
   native shapes (multi-edit list / `write_file` names). Explicit human choice.
2. **Snapshot-based, per-call diffs**, not git-based — git `diff` is cumulative
   since the last commit, so sequential edits would show combined deltas and be
   confusing; snapshot shows exactly _this call's_ change. New-file diffs use
   `/dev/null` as "before".
3. **`Write` returns a path-only confirmation** (no content/diff) — the human
   and agent don't need the body; the absolute path gives a clickable link.
4. **`Edit` is size-budgeted** — ≤100 diff lines shown; >100 lines replaced by
   confirmation + stats (replacements, ±lines). Avoids blowing up context on
   `replace_all`/large rewrites. 100-line budget & cap revisited in live
   testing.
5. **Budget + byte cap enforced inside the sandbox before shipping** — the
   "too late" concern: we never pull a giant diff over the wire; in the `>100`
   branch only the stats/confirmation is shipped, so stats are computed and
   emitted by the module.
6. **Sandbox-side logic as `python -m tkt.mcp_files`, not shell** — the
   diff/cap/branch logic is real logic better in testable Python than shell;
   `tkt` is available in the sandbox (inherited host env). **Not** a
   human-facing `tkt` subcommand (avoid polluting the CLI).
7. **Content as base64 argv, decoded by the module** — no `shlex.quote` on
   content, no injection, arbitrary bytes round-trip. Content-decoding is
   specific to these tools; the driver's transport b64 framing is unchanged.
8. **Content rides in argv (option A), with a graceful oversized error** —
   accepts the ~90 KiB limit rather than extending the driver protocol; MCP
   calls get clean markdown errors, not tracebacks.
9. **`Write` auto-creates parent dirs** — a deliberate divergence from native
   `write_file` (which requires the parent to exist).
10. **Graceful markdown errors for all failure cases** (not-found, multi-match,
    non-UTF-8, too-large) — tracebacks are fine for `tkt` CLI but not MCP calls.
11. **No host-side path validation** — writability comes from the sandbox mount
    model, preserved automatically by reusing the existing warm-holder setup.
12. **OpenCode untouched** — coexistence maintained (roadmap constraint).
13. **`Write`/`Edit` return a plain `str`, not a JSON `BaseModel`** — returning
    JSON breaks Zed's rich tool-output display (JSON renders as opaque text, not
    through the markdown engine). The new tools return markdown directly. The
    pre-existing tools' `BaseModel` returns stay as-is; migrating them off JSON
    is out of scope for W1.

## Open items / assumptions

- **`zed-tools.md` lives in the `superpowers` submodule.** Editing it means
  committing inside the submodule, then bumping the submodule pointer in the
  main repo (convention from `a2e1e84 Update superpowers submodule for Zed tool
mapping`). The implementing agent must do both, or flag it if the submodule
  is pinned/read-only for this work.
- **Agent profile is human-applied** — disabling native `write_file`/`edit_file`
  and enabling MCP `Write`/`Edit` lives on the human's machine and is **not**
  committed; delivered as a paste-ready snippet in the plan.
- **`python` availability in the sandbox** is assumed (conda/EUPS setup makes it
  available); `python -m tkt.mcp_files` additionally requires `tkt` importable
  in the sandbox, which follows from the inherited host env. Flagged for the
  live-testing phase.
- **100-line diff budget and byte cap are starting points** to be tuned in live
  testing.
