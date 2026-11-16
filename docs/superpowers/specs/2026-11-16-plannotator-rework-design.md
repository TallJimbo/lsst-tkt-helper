# Plannotator Adapter Rework — Design Spec

Date: 2026-11-16
Status: Draft — Gate 1 pending
Supersedes: `2026-10-12-host-session-services-design.md` §2.7 (records-scan result
contract), §3.2 (`plannotator` `result`/`status` merge; `plannotator_inbox`
action map), and §6's draft-restore recovery sentence. That spec's session
socket bridge (§3.4–3.5), lifecycle model (§4), direction model (§5), and
`BrainstormManager` remain in force.
Probe corpus: `investigations/plannotator-rework/` (fixtures graduate into
`tests/fixtures/` with provenance headers; see §9).

## 1. Background

The previous adapter captured nothing from the spawned `plannotator` CLI
(stdout to DEVNULL) and reconstructed results by scanning Plannotator's
**undocumented** feedback store: glob `feedback/<project>/records/` by
name patterns, filter by `mtime > opened_at`, prefer records whose names
contain a session id, and guess the project label from target or workspace
directory names. Every upgrade re-broke some assumption in that inference;
six successive fixes changed symptoms, not causes.

Probes (2026-11-15/16, Plannotator 0.28.65) pinned the documented surface.
The store works today — records are written for `--json` submits with mtimes
faithful to the submit instant (verified to the millisecond) — but it is
undocumented, and naming, project labels, and layout are precisely the things
that drifted across the six failed fixes. The documented stdout contract
makes any dependence on it unnecessary.

Requirements:

- **R1 (hard)**: Post-review results reach the sandboxed agent through the
  documented CLI contract (stdout decision JSON) plus a read-only mount. No
  tkt code reads Plannotator storage.
- **R2 (hard)**: Host-side tools cover only what needs host reach: browser
  launches and the Inbox server. Everything file-readable runs in the sandbox.
- **R3**: The inbox tool surface tracks upstream automatically (verbatim
  re-expose). No hand-maintained action→tool mapping.
- **R4**: Parallel sandboxes, sandbox-restart survival, and same-target
  attach-or-start are preserved (probe-pinned: Plannotator does **not** dedup
  same-target opens — two opens produce two servers).
- **R5**: No regression of the session socket bridge or the brainstorming
  companion.
- **R6**: Zed (host MCP) and sandboxed OpenCode keep identical tool contracts.

## 2. Central decisions

1. **Result = captured stdout.** `open` spawns the CLI with stdout redirected
   to a tkt-owned file; the decision JSON *is* the result artifact. The
   `result()` action, the store scan, mtime heuristics, project-label
   guessing, and session-id routing are deleted, not rewritten.
2. **A single result directory, mounted read-only.**
   `~/.local/state/tkt/plannotator/` (sibling of the existing `sessions/`
   registry dir) holds per-session `<handle>.out` / `<handle>.err` files and
   `<handle>.registry.json` entries. Restricted sandbox runs `--ro-bind` it at
   the same path and set `TKT_PLANNOTATOR_RESULTS` to it. Host-side (Zed)
   agents need no mount. Reading the mount is the agent's only fetch path;
   there is no host `result` tool.
3. **Plannotator session ids leave the tkt contract.** `open` returns a
   tkt-minted `handle` (`pna-<slug>-<epoch>`), the session `url`, the
   `result_path`, and `reused`. Liveness discovery still uses the documented
   `plannotator sessions --json`, but nothing on the agent-facing surface
   carries Plannotator ids.
4. **`--json` always.** Annotate and review sessions are spawned with
   `--json`; results are `{decision, feedback?, annotationCount?, target}`
   (annotate) or `{decision, message}` (review), shapes pinned by probes
   (§9). `--result-file` (atomic publish, pinned to work without
   `--require-approval`) is **not** used: review has no equivalent, and one
   mechanism beats two.
5. **Teardown targets the detached server pid, and is host-side only.**
   Probed: killing the spawned CLI does not end the session — Plannotator
   runs the session server outside the CLI's process group, and abandoned
   servers persist indefinitely. There is no agent-facing `close`: closing
   the browser tab is the normal teardown, and `tkt rm` / the boot sweep
   absorb reaping — they resolve sessions by target in `sessions --json`
   and TERM the entry's `pid`, alongside the registered CLI pgids. The old
   pgid-kill registry aimed at the wrong process.
6. **Inbox = verbatim pass-through.** The session server spawns
   `plannotator inbox mcp` with `cwd = <workspace>` (the Inbox scopes to the
   invoking cwd — probed), lists its tools, and re-exposes each upstream tool
   under its upstream name, description, and input schema, forwarding calls
   unchanged (content blocks and structured content preserved). Tool surface
   tracks upstream releases automatically.
7. **Guidance is tkt-owned in one place.** `PLANNOTATOR_SKILL_PATCHES`
   (regex surgery on vendored upstream skill prose at install time) is
   deleted; vendored Plannotator skills stay verbatim upstream. The code-mode
   call form and the pull-by-file result contract live only in
   `tkt/AGENTS.md.in`. The draft-restore promise is dropped: probes show
   closing a session discards the typed draft.

## 3. Components and interface stubs

### 3.1 `PlannotatorManager` (`tkt/_sessionmanagers.py`)

Reduces to `open` plus host-side cleanup; `result()`, `status()`, the
agent-facing `close`, the records glob, `_status_ts`, and id-adoption are
deleted (reaping is host-side: closing the tab is the normal teardown, and
workspace removal absorbs the rest).

```python
class PlannotatorManager:
    def open(self, workspace, target, *, kind="annotate", gate=False,
             base=None, extra_roots=()) -> dict:
        """Attach-or-start. Containment-checked target (workspace or plan
        dir; PR URLs verbatim for review). Match live sessions by resolved
        target in `sessions --json` (no upstream dedup — R4); on a tkt-spawned
        match return its registry entry's handle/result_path with
        reused=True; on a human-started match attach and return
        handle=<minted handle>, source="discovered", result_path=None (our
        capture never sees that CLI's stdout — the human's terminal holds it;
        guidance says ask the human or close the tab and re-open under tkt;
        the entry exists so workspace removal can reap that server). On miss:
        spawn detached (`start_new_session`, stdout→<handle>.out,
        stderr→<handle>.err), poll `sessions --json` for the URL, checking the
        listing then the stderr file each poll (its
        `Plannotator session ready: <url>` line covers a PR-URL review, whose
        target the listing never matches), write the registry entry, return
        {handle, url, result_path, err_path, reused, source}."""


def cleanup_workspace_sessions(workspace) -> int:
    """`tkt rm` hook: TERM registered CLI pgids and, per registry entry, the
    session-server pid — the recorded one, or the one resolved by target from
    `sessions --json` when the entry records none; drop the
    entries. Keeps the result files."""


def sweep_session_registry() -> int:
    """Launch-time: drop registry entries whose CLI, server and listed session
    are all dead (an entry with no recorded server pid is live while the
    listing still shows its target, and an unusable listing keeps entries),
    and prune result files older than 30 days."""
```

Registry entry (written as `<handle>.registry.json` in the result dir —
agents can read it too): `{handle, workspace, target, kind, gate, base,
opened_at, out_path, err_path, source: "spawned"|"discovered", pgid,
server_pid, url}`.

### 3.2 Result mount and environment

- `Sandbox` gains the result dir to the read-only mounts under the same
  condition as `TKT_SESSION_SOCKET` (restricted run with the LLM bridge), same
  path in and out, existing `mounts_ro` mechanics. A full-network run gets
  neither mount nor variable, so it has no view of other workspaces' captures.
  A configured read-write bind of the same path wins: the read-only mount is
  emitted after the configured read-write mounts and is skipped when that path
  is already bound read-write.
- `Sandbox.run` env injection adds `TKT_PLANNOTATOR_RESULTS=<dir>` next to
  `TKT_SESSION_SOCKET`, after the configured `--setenv` block.
- The directory is created by the host session server / manager on demand;
  mounting a nonexistent path is avoided by creating it at sandbox launch.

### 3.3 Session tools (`tkt/_session_tools.py`)

- `plannotator` tool → single-purpose `plannotator_open(target, kind, gate,
  base, description)`: no action enum, no session/handle param, no
  `status`/`result`/`close`. Its result text instructs: hand the URL
  verbatim, end the turn, then **read the result path** — absent file means
  the session is still open; empty file means closed without feedback;
  unparseable JSON means still open (re-read later), never a parse error.
  Post-restart rediscovery is `ls`/`read` over the mount (registry files are
  self-describing), not a tool call.
- `plannotator_inbox`: replaced by dynamic registration at server start —
  spawn `plannotator inbox mcp` (fixed argv, `cwd=workspace`), `tools/list`,
  register one proxy per upstream tool: synthesized
  `__signature__`/`__annotations__` from the schema properties (keyword-only
  `Any`, default `None`) and `tool.parameters` overwritten with the upstream
  schema (verified against the installed mcp SDK 2.x). The overwrite happens
  only when the registered tool is the one this call created, so an upstream
  listing entry whose name collides with a tkt tool is dropped instead of
  rewriting tkt's advertised surface. Calls forward with
  `None`-valued kwargs stripped; results forward content and structured
  content unchanged. Inbox spawn/list failure degrades to zero inbox tools
  with a logged warning; the rest of the session server is unaffected.
- Kept convenience, documented as a **tkt extension**: the re-exposed
  `submit_guide` gains `patch_file` (workspace path read host-side) and
  `patch_spec` (host `git diff base...head`), each converted to `patch`
  before forwarding. These exist because sandbox-private paths are invisible
  to the host; the schema addition is labeled `x-tkt-extension` and is the
  only place the surface departs from upstream.

### 3.4 Guidance (`tkt/AGENTS.md.in`, `tkt/install.py`)

The host-session paragraph is rewritten around: `plannotator_open` and the
inbox tools for host-side actions, the mount + `TKT_PLANNOTATOR_RESULTS` for
reading results (including post-restart rediscovery via the registry files),
never launching `start-server.sh` or the CLI (an in-sandbox companion is
unreachable by design), never polling (end the turn; read when the human
returns), no draft-restore promise. `install.py` deletes
`PLANNOTATOR_SKILL_PATCHES`; vendored skills install unmodified;
`PLANNOTATOR_HOST_ONLY_SKILLS` behavior (deny `plannotator-inbox`,
`plannotator-last` in sandboxes) is unchanged.

## 4. Lifecycle

- Spawn: CLI detached (survives sandbox restart); server is Plannotator's
  own process outside the CLI group (probe-pinned).
- Submitted/approved/dismissed → CLI exits, result file complete, `sessions`
  entry drops, server exits (probe-pinned for the normal path).
- Abandoned session (tab never closed) → server lives indefinitely: reaped
  by the human closing the tab (the normal path), by `tkt rm`, or by the
  boot sweep once its CLI and server are both dead; never silently adopted.
- Sandbox restart: `open` on the same target attaches to the live session
  and re-returns the existing `result_path`.
- Result files are pruned at 30 days by the launch-time sweep.

## 5. Failure posture

Repo policy (developers get tracebacks) with three named-text exceptions:

- `plannotator` missing from host PATH → `open` error naming it (unchanged).
- `sessions --json` unavailable/slow → `open` still spawns and returns
  `{handle, url: null, result_path}`; the URL also lands in `<handle>.err`
  in the mount for the agent to read directly. Result capture is unaffected
  (this is the point of the design — listing breakage no longer strands
  feedback).
- Inbox MCP spawn or `tools/list` failure → no inbox tools this connection,
  warning logged; session tools still work.

## 6. Security posture

Unchanged trust model: the result dir is the user's own state, mounted
read-only; the bridge socket trust domain is as before; the inbox proxy
executes a fixed command with only `cwd` varying; path containment rules for
`open` targets unchanged (workspace + `~/.opencode/plan` + PR URLs). The
sandbox-facing tool surface still never includes bash or file tools — the
guard test now asserts "no bash/file tools" rather than an exact name list,
since inbox tool names follow upstream.

## 7. Cleanup inventory (deletions)

| Delete | Replaced by |
|---|---|
| `PlannotatorManager.result()` (store glob, mtime, patterns, id preference, liveness inference) | result file + agent `read` |
| `status()` action (registry∪CLI merge, id routing) | nothing — mount + registry files cover discovery |
| `close` action | nothing agent-facing — tab-close is the normal teardown; `tkt rm` / sweep absorb reaping (server `pid`, not pgid) |
| `_status_ts`, `local-<pid>` id adoption | deleted outright |
| `INBOX_ACTION_TOOLS` and per-action argument marshaling | verbatim dynamic registration |
| `PLANNOTATOR_SKILL_PATCHES` (install.py) | AGENTS.md.in (already carries the call form) |
| draft-restore recovery sentence | honest guidance (close discards drafts) |
| pgid-kill teardown assumption | server-`pid` teardown from `sessions --json` |

Untouched: session socket bridge, per-connection session server,
`BrainstormManager`, `sweep_session_registry` skeleton (retargeted), Zed
`run_server` registration (shares `build_session_tools`).

Prior art on `feature/plannotator-rework` (archived branch) is reference
only; its six result-delivery commits are the exact design being deleted.

## 8. Testing plan

1. **Manager**: fake-CLI fixtures (existing pattern) + golden stdout fixtures
   copied from the probe corpus (provenance header per fixture):
   approved / annotated / dismissed / plain-close (empty) / review approved /
   review annotated; attach-or-start (two-entry `sessions --json` fixture
   from p3); `cleanup_workspace_sessions` killing registered pgids *and*
   the target-matched server pids; discovered-session attach
   (`result_path=None`).
2. **Verbatim inbox proxy**: stub `inbox mcp` server (installed mcp SDK);
   assert re-published name/description/schema equal upstream verbatim,
   `None`-stripping forward, content+structured-content preservation,
   degrade-to-zero on spawn failure, `cwd` scoping.
3. **Mount injection**: mocked bwrap — result dir ro-mounted at same path,
   `TKT_PLANNOTATOR_RESULTS` set, dir created at launch.
4. **Guidance rendering**: new paragraph present; `result`/`status`/`close`
   and draft-restore promises absent.
5. Real-Plannotator acceptance (human turns, sandbox runs): one gated
   approve and one plain annotate pull, end-to-end through the mount.

## 9. Pinned probe facts (Plannotator 0.28.65, 2026-11-16)

- No same-target dedup (p3): two `annotate <f>` opens → two servers/ports.
- `sessions --json` entry: `{index, mode, target, url, port, pid, project,
  label, startedAt}`; entry drops when the session resolves.
- stderr URL line: `  Plannotator session ready: http://localhost:<port>`.
- `annotate --json` (± `--gate`): `{decision: approved|annotated|dismissed,
  feedback?, annotationCount?, target}`; `dismissed` = closed without
  submitting (no feedback); gated UI is Approve / Request changes / close →
  approved / annotated / dismissed; approve-with-notes keeps notes in
  `feedback`.
- `review --json`: `{decision, message}` (documented shapes confirmed for
  approved/annotated).
- `--result-file` = atomic publish of the stdout JSON, requires only
  `--gate --json`; byte-identical to stdout (probe: published == captured).
- Killing the CLI leaves the session server alive; abandoned servers persist
  (one observed alive 6 days).
- Records exist for `--json` submits with faithful timing: record `mtime`
  equals its name timestamp to the millisecond, with `index.jsonl` bumped
  alongside (an earlier "no records" observation was a probe-window artifact
  of this investigation, not store behavior). Gated "request changes"
  archives under the same `annotate-feedback` suffix as ungated submits —
  the store does not distinguish gating.
- `plannotator inbox mcp` exposes 7 tools
  (`send_message, read_thread, search, resolve_message, wait_for_reply,
  get_guide_brief, submit_guide`); envelopes carry `content[]`,
  `structured_content`, and `meta`; project scope derives from the invoking
  cwd.
- Closing an annotate session discards the typed draft (not restored).

## 10. Resolved during design

- `close`: dropped from the agent surface — closing the tab is the normal
  teardown (the human closes tabs when done); `tkt rm` and the launch sweep
  absorb reaping host-side, now targeting the session-server `pid`.
- `status`: dropped — the registry files in the read-only mount are
  self-describing, so discovery after a restart is a file read; keeping the
  action would preserve the only remaining inference code for no gain.
