# `guide_submit` patch sources

`plannotator_inbox` `guide_submit` submits a guided review by sending the
upstream Inbox a `guide` plus the exact unified diff it describes. Upstream
takes that diff only as inline tool-argument text ("the Inbox keeps the diff
as sent"), which a sandboxed agent cannot supply for a real-sized diff: the
shell spills large output to files, the Code Mode runtime that hosts the MCP
tools has no filesystem access, and re-typing a ~150 KB diff token-by-token
into a tool call is neither reliable nor cheap.

The shim (`tkt/_session_tools.py`) therefore resolves the patch **host-side**,
inside the workspace, and forwards the resulting bytes to upstream verbatim as
`patch`. Upstream stays untouched: its strict guide/patch validation still runs
and still refuses a malformed guide without publishing anything.

## Interface

`guide_submit` takes at most one patch source:

- `patch` — inline diff text (unchanged; fine for small diffs).
- `patch_file` — path to a unified-diff file **inside the tkt workspace**,
  read host-side as UTF-8. Resolved with `safe_subpath`, so workspace-relative
  and absolute-but-contained paths work and escapes are refused.
- `patch_spec` — `{"dir": ..., "base": ..., "head": ..., "paths": [...]}`:
  the shim runs `git diff --no-color <base>...<head> -- <paths>` host-side in
  `safe_subpath(workspace, dir)` (default: the workspace root) and sends the
  output. `base` is required; `head` defaults to `HEAD`; `paths` are pathspecs,
  which makes a scoped patch (e.g. production-only, tests deferred) a
  first-class submission.
- `snapshot` — a complete snapshot in place of guide + patch (unchanged).

A summary line naming the resolved patch's file count, byte count, and source
is appended to the result of file/spec submissions.

## Constraints

- `patch_file` and `patch_spec` resolve on the **host**. Sandbox-private paths
  (e.g. `/tmp` in a restricted run, which is tmpfs) are not host-visible; a
  missing-file error names the host path it looked for. Stage patches inside
  the workspace (the `.agent/` tree is a natural home).
- Spec refs must not start with `-` (git argument injection); a nonzero `git`
  exit surfaces git's stderr; an empty diff is an error. A non-UTF-8
  `patch_file` is refused.
- These are reads of workspace-local content only; the session surface grows no
  host-execution tool (`_git_diff_spec` runs a fixed `git diff` argv, and
  `safe_subpath` containment applies to both sources).
