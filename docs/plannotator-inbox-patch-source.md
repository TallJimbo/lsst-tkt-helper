# `submit_guide` patch sources

`submit_guide` submits a guided review by sending the upstream Inbox a `guide`
plus the exact unified diff it describes. Upstream takes that diff only as
inline tool-argument text ("the Inbox keeps the diff as sent"), which a
sandboxed agent cannot supply for a real-sized diff: the shell spills large
output to files, the Code Mode runtime that hosts the MCP tools has no
filesystem access, and re-typing a ~150 KB diff token-by-token into a tool call
is neither reliable nor cheap.

tkt therefore grows the published `submit_guide` schema with two properties,
each marked `x-tkt-extension` (`_TKT_EXTENSIONS` in `tkt/_inbox_proxy.py`), and
resolves the patch **host-side**, inside the workspace: `_resolve_patch_source`
(`tkt/_session_tools.py`), injected into the proxy's forwarding path, turns a
tkt source into diff text that is forwarded to upstream verbatim as `patch`.
Upstream stays untouched: its strict guide/patch validation still runs and
still refuses a malformed guide without publishing anything.

## Interface

`submit_guide` takes at most one patch source (a call mixing `patch` with a
tkt source is refused):

- `patch` — inline diff text (upstream's own property; fine for small diffs).
- `patch_file` — path to a unified-diff file **inside the tkt workspace**,
  read host-side as UTF-8. Resolved with `safe_subpath`, so workspace-relative
  and absolute-but-contained paths work and escapes are refused.
- `patch_spec` — `{"dir": ..., "base": ..., "head": ..., "paths": [...]}`:
  the resolver runs `git diff --no-color <base>...<head> -- <paths>` host-side
  in `safe_subpath(workspace, dir)` (default: the workspace root) and sends the
  output. `base` is required; `head` defaults to `HEAD`; `paths` are pathspecs,
  which makes a scoped patch (e.g. production-only, tests deferred) a
  first-class submission.
- `snapshot` — a complete snapshot in place of guide + patch (upstream's own
  property).

## Constraints

- `patch_file` and `patch_spec` resolve on the **host**. Sandbox-private paths
  (e.g. `/tmp` in a restricted run, which is tmpfs) are not host-visible; a
  missing-file error names the host path it looked for. Stage patches inside
  the workspace.
- Spec refs must not start with `-` (git argument injection); a nonzero `git`
  exit surfaces git's stderr; an empty diff is an error. A non-UTF-8
  `patch_file` is refused.
- These are reads of workspace-local content only; the session surface grows no
  host-execution tool (`_git_diff_spec` runs a fixed `git diff` argv, and
  `safe_subpath` containment applies to both sources).
