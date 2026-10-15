# Host-Side Session Services & the Sandbox MCP Boundary — Design Spec

Date: 2026-10-12
Status: Gate 1 approved (in-chat design summary, same date)
Supersedes: the phase-1/phase-2 work on `feature/plannotator-integration` (kept as
reference, see §12). This design is built fresh on `main`.

## 1. Background and requirements

`tkt` restricted sandboxes isolate the agent in a network namespace. The current
bridge has two directions:

- **host-dialed (LLM) direction** — per-launch `net-*` dir with `llm-<port>.sock`
  unix sockets, host socats *dial* host ports 8080/8090 (ssh tunnels). Parallel-safe.
- **sandbox-to-host (s2h) direction** — the visual companion: host socat eagerly,
  exclusively `TCP-LISTEN`s the host `vc_port` (default 8081) for the sandbox's
  lifetime. Machine-exclusive: a second restricted sandbox cannot launch.

Plannotator integration (phase 1, on the old branch) added a second exclusive s2h
port (8082), and phase 2 pinned "one port, one bookmark" — buying predictability
with exclusivity.

Requirements (pinned by the human during the design conversation):

- **R1 (hard)**: N parallel restricted sandboxes must coexist. Machine-wide port
  exclusivity is unacceptable.
- **R2 (hard)**: Plannotator and the brainstorming visual companion integrate
  cleanly for both harnesses (OpenCode-in-sandbox and host Zed).
- **R3**: Concurrent browser sessions are a nice-to-have; adopt only where free.
- **R4**: Plannotator guided review must remain reachable from a sandboxed agent.
- **R5**: No regression of the LLM bridge or the host-Zed warm-sandbox MCP stack.
- **R6**: No packaging configuration (AGENTS.md doc lines only); clone-and-run stays.

The exclusivity came from a same-number invariant (in-sandbox server binds netns:P,
host forwarder owns host:P so the URL is verbatim): parallelism × static port ×
verbatim URL was a trilemma. This design dissolves it by removing the invariant:
**servers run on the host, where the browser and `~/.plannotator` already live.**

## 2. Central architectural decisions

1. **All session servers (companion, Plannotator) run on the host.** Their only
   client is the human's browser; they are reached by agents only through tool
   calls that cross the sandbox boundary on a unix socket. The s2h TCP direction
   is deleted entirely (the last host `TCP-LISTEN` goes away). Parallelism (R1)
   then holds *by construction*: what remains is per-sandbox unix sockets and
   host-dialed ports only.
2. **The boundary protocol is MCP; the MCP server lives on the host**, spawned
   per connection by socat (`fork SYSTEM:…`) inside the existing bridge block.
   The in-sandbox "server command" OpenCode spawns is a dumb byte pipe
   (`socat - UNIX-CONNECT:…`) — stdio naming refers to framing, not placement.
   Rejected alternative kept on record: an in-sandbox MCP *translator* speaking a
   private host-hop dialect (rejected: second protocol implementation, new
   in-sandbox MCP dependency, and future external-MCP proxying would have to
   re-invent MCP schema discovery).
3. **Host-side MCP server exposes session tools only** — `brainstorm_server`,
   `plannotator`, `plannotator_inbox`. It never exposes `bash` or file tools
   (guard-tested). A sandboxed agent has bash and can hand-craft any framing, so
   narrowness of the tool surface is the actual defense.
4. **Session servers always spawn detached** (new session/process group, no
   parent-death signal — the phase-2 anchored mechanism:
   `sh -c 'trap "kill -TERM 0" TERM INT; exec "$@"'` + `start_new_session` +
   atexit). Nothing's lifetime is tied to the ephemeral MCP connection process.
5. **Attach-or-start discovery**: every `open`/`start` action first discovers
   existing sessions from on-disk truth (`plannotator sessions --json`; companion
   pidfiles/state dirs) and reuses; cross-workspace concurrency is free (R3 —
   Plannotator's CLI self-allocates random ports per session; companion already
   keys per project). Same-target foreign sessions are refused and named (never
   silently adopted).
6. **The bookmark model retires.** Random per-session ports are Plannotator's
   native host behavior; the URL contract is "the tool returns it, you pass it
   verbatim, it arrives in chat." No port allocator is built. `PLANNOTATOR_PORT`
   stays a Plannotator-side knob tkt does not manage.
7. **Result read = records scan** (phase-2 contract retained): on-disk archive
   (`~/.plannotator/feedback/<project>/records/`), `mtime > opened_at`, name
   patterns `-annotate-feedback.md` / `-review-`, raw text back; classify by
   `decision`, never by message text. No `--result-file`, no stdout capture, no
   index.jsonl parsing.
8. **Guided review splits by direction** (§5): tool-mediated (compatible, in
   scope) vs browser→live-session push (ruled out class).
9. **Wake (feedback arriving as a message in a live session)** is a separable
   in-sandbox plugin layer polling the host over the socket and injecting
   messages in-process (§6). The baseline is pull; the human's typed nudge is
   the fallback. Host→session push is ruled out (§5).

## 3. Components and interface stubs

### 3.1 `tkt/_sessionmanagers.py` (new home)

`BrainstormManager` moves **verbatim** from `tkt/mcp_server.py` (its current
`main` behavior — start/status/stop, launcher-script spawn, random-port reuse
per project — is unchanged; `start` grows an optional `owner_pid` kwarg and the
shared tool builder omits it (plan decision #2: servers follow §4's death paths;
the Zed-visible change — companion bounded by the 240-min idle timeout instead
of dying with the MCP process — was surfaced and accepted). New alongside it:

```python
class PlannotatorManager:
    """Host-side lifecycle + results for `plannotator` sessions."""

    def open(self, workspace: str, target: str, *, kind: str = "annotate",
             gate: bool = False, base: str | None = None) -> dict[str, Any]:
        """Attach-or-start. Discovery first via `plannotator sessions --json`
        (match by resolved target); spawn detached on miss (annotate or review)
        and poll `sessions --json` until the session registers. Discovery also
        ensures a pidfile-registry entry exists (discovered sessions: pgid 0,
        source "discovered") so result()/close() work on reuse.
        Targets must resolve inside ``workspace`` (PR URLs allowed for
        kind="review"). Returns {url, session: "pn-<hex>" (or "local-<pid>"
        until discovered), reused: bool, ...}. (Plan decision #1: no
        open_browser — the CLI has no documented suppress-browser contract.)"""

    def status(self, workspace: str) -> list[dict[str, Any]]:
        """`plannotator sessions --json`, filtered to this workspace."""

    def result(self, workspace: str, session: str) -> dict[str, Any]:
        """Records scan: ~/.plannotator/feedback/<project>/records/ with
        mtime > opened_at, name patterns `-annotate-feedback.md` / `-review-`.
        Returns {type: "feedback"|"pending"|"closed", text, ...}: the disk
        archive carries raw feedback text only (phase-2 contract), so no
        machine-readable decision field is fabricated — approval with notes
        arrives as feedback text; liveness for discovered (pgid 0) sessions
        uses CLI truth (`sessions --json`)."""

    def close(self, workspace: str, session: str) -> dict[str, Any]:
        """SIGTERM the session's process group (anchored-spawn registry).
        Discovered (pgid 0) sessions are unregistered, never killed: returns
        type "unregistered"."""
```

Managers own an anchored-spawn registry (pidfile per session under the tkt state
dir) used by `close` and by the boot sweep.

### 3.2 `tkt/session_server.py` (host server reachable from the sandbox)

`FastMCP("tkt-session")`, launched per connection with
`tkt session-server --workspace <root>` (hidden CLI subcommand, mirroring
`tkt mcp-server` conventions). Registered tools — identical names and shapes in
Zed's `run_server` (§3.3):

```python
def brainstorm_server(action: str,             # "start"|"status"|"stop"
                      project_dir: str = ".", port: int | None = None,
                      idle_timeout_minutes: int | None = None,
                      open_browser: bool = True,
                      description: str | None = None) -> str: ...
    # EXACT current main signature/behavior (verbatim tool docstring too).

def plannotator(action: str,                   # "open"|"status"|"result"|"close"
                target: str | None = None,     # open: file/dir/folder/PR URL
                kind: str = "annotate",        # open: "annotate"|"review"
                gate: bool = False,            # open: approval-gate UI
                base: str | None = None,       # open+review: compare ref
                session: str | None = None,    # result/close: "pn-<hex>"
                description: str | None = None) -> str: ...

def plannotator_inbox(action: str,             # "send"|"read"|"wait"|"resolve"
                                                # |"decisions"|"record"
                                                # |"guide_brief"|"guide_submit"
                      text: str | None = None, thread: str | None = None,
                      reply_to: str | None = None,
                      payload: dict | str | None = None,
                      description: str | None = None) -> str: ...
```

`plannotator_inbox` is a **narrow allow-listed proxy**: the server spawns
`plannotator inbox mcp` (stdio child, host side) and re-exposes exactly those
eight upstream tools (mapped to the action names above). `wait` maps to
upstream's ~50 s cursor-wait and may return "no reply yet — call again".
The spawn-and-filter seam is deliberately the same shape a future general
external-MCP proxy will need (§12 deferred).

Contract notes baked into tool guidance strings (carried from the old branch's
corpus — see §7): hand the URL verbatim, end the turn, never poll open sessions,
`Plannotator:`-prefixed arrival convention, draft-restore recovery
("run the same open again — drafts are restored").

### 3.3 Zed `run_server` additions

Register `plannotator` and `plannotator_inbox` on the existing host MCP server,
delegating to the same managers. Zed never uses the socket path; tool names and
contracts are identical across harnesses. (Zed's wake story remains what it is:
pull; Zed has no push mechanism and gains none.)

### 3.4 Bridge spawn (in `_start_bridge`)

One addition to the existing block, inheriting `start_new_session`,
`preexec_fn=_set_pdeathsig`, log capture, early-exit check, `_stop_bridge`
teardown, and the `_reap_orphaned_socats` boot sweep:

```python
session_sock = os.path.join(net_dir, "session.sock")
procs.append(
    subprocess.Popen(
        ["socat", f"UNIX-LISTEN:{session_sock},fork",
         "SYSTEM:" + shlex.join(session_server_command(workspace_root))],
        stdout=…, stderr=…, start_new_session=True, preexec_fn=_set_pdeathsig,
    )
)
```

`session.sock` lives in the ephemeral per-launch `net-*` dir (NOT a stable
per-workspace path — the launch-time config injection of §3.5 removes any need
for static paths, and the existing reaper/stop machinery keys off `net-*`
verbatim). Per-connection server lifetime = MCP connection lifetime; crash
cleanup is automatic.

### 3.5 Environment injection (OpenCode sandbox only)

`Sandbox.run` adds (verified against `origin/2.0` of OpenCode — installed build
is v2.0.26; `OPENCODE_CONFIG_CONTENT` is merged last, in `"local"` scope,
config.ts:1464–1471; `mcp` is a config key at config.ts:950):

```python
env["TKT_SESSION_SOCKET"] = session_sock           # plugin enable marker
env["OPENCODE_CONFIG_CONTENT"] = json.dumps({
    "mcp": {"tkt-session": {"type": "local",
                            "command": ["socat", "-",
                                        f"UNIX-CONNECT:{session_sock}"]}},
})
```

**Per-launch channel wiring is injected; inert behavior content is installed**
(ruling after the plugin-load failures, §3.6). `session_mcp_config()` carries
`mcp` only: the socket path is per-launch (`net-*/session.sock`), so it cannot
be static config — a machine-wide entry would advertise three tools that fail
whenever no bridge lives. The `plugin` config key is not emitted: installed
v2.0.26 treats those entries as npm package specs and ignores local paths
(probe P4). No bwrap bind targets the config dir either — bwrap cannot
`mkdir` a mountpoint under an already-remounted-ro parent.

### 3.6 Wake plugin (`harnesses/opencode/plugin/…`, separable)

In-sandbox OpenCode plugin, delivered as a **real copied file** in
`~/.config/opencode/plugin/` by `tkt install-opencode-agent` (which already
symlinks `agents/` and skills, so it rides existing install state; symlinks
are silently skipped by the loader, and the dir cannot be bind-mounted in).
Module shape must be `export default { id, setup }` — the loader contract of
v2.0.26. Restricted launches compare the host copy against the repo source
bytewise and warn (non-fatally) when missing or stale, naming
`tkt install-opencode-agent` as the fix.

Enabled only when `process.env.TKT_SESSION_SOCKET` is present (host runs load
it inert). Polls the host session server over the socket for new feedback
records on sessions registered to this workspace (`status` = registry ∪ CLI,
so closed-but-unread records stay discoverable); injects
`Plannotator:`-prefixed messages in-process, independent of
`--standalone`/`acp` modes — no service, no network, no password.

**Separable by design, with a declared exit:** deleting the copy stops wake
and nothing else — the agent-side pull (`action="result"`, guidance-mandated)
and human nudges remain the fallback, proven live when wake first failed to
deliver. If acceptance shows the plugin misbehaving, removing it is a
single-commit change.

## 4. Lifecycle and ownership

- **Socket listener**: started by `Sandbox.run` with the bridge; killed by
  `_stop_bridge`; reaped at boot if its `net-*` dir is gone. One per sandbox;
  parallel sandboxes never collide (separate net dirs).
- **MCP server process**: spawned per connection by socat `fork`; exits on socket
  EOF (OpenCode exit/crash); inherits pdeathsig from the listener. Holds no state.
- **Session servers**: detached at spawn; die only by (1) explicit `close`,
  (2) idle timeout (companion existing default; Plannotator per its own policy),
  (3) boot-time orphan sweep (pidfile registry + liveness check, sibling of the
  socat reaper), (4) `tkt rm`.
- **Restart property**: killing the sandbox does not kill an open review. A
  restarted session discovers live sessions via `status` and reuses per
  attach-or-start; closed/lost sessions recover via re-open (drafts restore).
- Two OpenCode processes on one workspace: separate net dirs → separate
  listeners and server processes; harmless because all truth is on-disk;
  same-target ownership policy (§2.5) arbitrates.

## 5. Direction model and the ruled-out class

Three actors, two contact surfaces, **no browser→agent arrow**:

- Agent → host: tool calls over the socket (open/status/result/close, inbox,
  guide brief/submit). Triggered by workflow text or the human's request — never
  by the browser.
- Human → disk: the browser UI's only output is its own server writing
  records/threads. It is a state producer, never a session caller.
- Disk → agent: pull (`result`) or in-sandbox plugin push (§3.6). The plugin is
  the only actor that can make a message *appear* in a live session.

Ruled out (spec-level prohibition): **any mechanism where the host posts into
the live sandboxed OpenCode session** — Plannotator's native wake (posts to the
OpenCode background service; tkt launches `opencode acp` — embedded, no service —
and scrubs `OPENCODE_PASSWORD`/`OPENCODE_SERVER_PASSWORD`; netns blocks host→in
anyway), or anything resurrecting s2h reachability. The Plannotator UI's guided-
review *button* works only if its AI runs host-side (no sandbox involvement,
result reaches the agent via §4's paths); asking the working agent directly is
the native path (`guide_brief` → author → `guide_submit`).

## 6. Guidance and documentation rewrite

The old branch's guidance corpus is the **template** — contract wording is kept,
invocation mechanics are swapped:

- Keep (from `harnesses/shared/sandbox-agents.md`, phase-1 AGENTS.md.in wording,
  and the superpowers fork SDD human-browser-review step at submodule
  `76cc115`): URL-verbatim handoff, end-the-turn, never poll open sessions,
  `Plannotator:` arrival, draft-restore recovery sentence, SDD review step.
- Replace (invocation mechanics): in-sandbox `plannotator`/`start-server.sh`
  launches, bridge/port env, self-managed servers → host-tool usage.

New `AGENTS.md.in` guidance, mode-agnostic (replaces both the brainstorm_server
"prefer" paragraph and the `{{vc_port}}` self-launch paragraph):

> Session servers (visual companion, Plannotator reviews) run on the *host* and
> are reached through the `brainstorm_server` / `plannotator` tools. Never
> launch `start-server.sh` or `plannotator` yourself — an in-sandbox server is
> unreachable by design, and the host tools own the lifecycle. If a tool is
> missing from your toolset, stop and report it rather than improvising.

Repo `AGENTS.md`: update the sandbox file-layout bullet (reverse-direction vc
bridge description → session-socket description); dependency doc line unchanged.

## 7. Cleanup inventory (deletions)

| Delete | Touches |
|---|---|
| vc forwarder block in `_start_bridge` (`vc.sock`, `vc_log`, `TCP-LISTEN:{vc_port},fork,reuseaddr` spawn, "same port verbatim" comment) | listener spawn replaces it in the same block; `_stop_bridge`/reaper unchanged |
| `vc_port` config parsing + `_DEFAULT_VC_PORT` | `Sandbox.__init__`, `Sandbox.from_config`, config docs |
| `render_agents_md(…, vc_port)` param + `{{vc_port}}` substitution | signature → callers (`run`, `run_single_repo`), `tkt/AGENTS.md.in`, tests |
| `AGENTS.md.in` companion/self-launch paragraphs | unified guidance (§6) |
| repo `AGENTS.md` vc-bridge description | rewrite bullet |

Untouched: h2s LLM bridge (`"port": [8080, 8090]`, `llm-<port>.sock`),
warm-sandbox holder, `BrainstormManager` behavior (moved, not changed), the
`local.jsonc` jsonc migration (landed `8f2e187`).

## 8. Security posture

- No `bash`/file tools on the sandbox-facing server; guard test asserts the
  registered tool set.
- `plannotator open` targets resolve inside the session's workspace (passed as
  `--workspace` at spawn); PR-URL reviews allowed (host egress, human `gh`).
- Socket trust domain equals the existing net-dir trust domain (user-owned
  state dir); the threat model remains accidental misuse, consistent with tkt
  practice. Wake delivery requires no credentials of any kind.

## 9. Failure posture

Per repo policy ("developers get tracebacks"): listener fails at launch →
`RuntimeError` from `Sandbox.run` naming the session socket (same class/message
shape as bridge errors today); socket dead mid-session → MCP connection error,
human restarts the sandbox; missing host `plannotator` binary → tool error text
naming it (host install is a human prerequisite); path escape → clean refusal.

## 10. Testing plan (TDD per commit; gates `ruff check`, `ruff format --check`,
`mypy tkt/`, `python -m pytest`)

1. **`PlannotatorManager`**: fake `plannotator` CLI (shell-script fixtures) +
   `PLANNOTATOR_DATA_DIR` pointed at tmpdirs (documented relocation — real
   archive untouched). Cover: open/attach-or-start reuse (including
   registry-on-discovery, pgid 0 + CLI-truth liveness, and close→"unregistered"
   for host-started sessions), records-scan mtime
   logic, close→killpg, and the anchored-survival case
   (detached session outlives the spawn wrapper — the phase-2 spike trap).
2. **`session_server`**: direct tool-handler tests (existing `mcp_server`
   precedent); no-bash-tools guard; path-containment rejection; inbox proxy
   filters to the eight allow-listed actions.
3. **Bridge listener**: real `socat` in a tmpdir (`test_sandbox.py` pattern);
   connect through `session.sock` to a stub; reaper/stop coverage for the new
   proc.
4. **Env injection**: mocked bwrap (existing pattern) — assert
   `OPENCODE_CONFIG_CONTENT` carries `mcp` only (no `plugin` key),
   `TKT_SESSION_SOCKET` set, and **no `TCP-LISTEN` appears in any spawn
   list**; wake-copy staleness warns (missing/stale) and stays silent when
   matching; `install_opencode_agent` copy is atomic, idempotent, and
   replaces stale copies/symlinks (dry-run writes nothing).
5. **Zed server**: `plannotator`/`plannotator_inbox` registered, delegating to
   shared managers (mocked).
6. **Guidance rendering**: `render_agents_md` without `vc_port`; new-paragraph
   presence; old-paragraph absence.
7. **Wake plugin**: thin smoke test against a stub session server (no JS
   framework).

## 11. Probe backlog (early plan tasks / human's sandbox turns; nothing in the
design depends on their outcomes — they pin fixtures and error text)

- **P1** — **PASSED 2026-10-13.** `OPENCODE_CONFIG_CONTENT` + socat-MCP
  end-to-end against installed v2.0.26: settled `opencode mcp list` prints
  `✓ tkt-session  connected` with the exact `session_mcp_config()` schema —
  local-MCP shape confirmed. Caveat found: `opencode mcp list` attaches to
  OpenCode's shared managed *service*, which captures env at first spawn and
  ignores later invocations' env (file configs land one request late via
  watchers). A host with an already-running service prints "No MCP servers
  configured" even when wiring is correct — the probe script now cold-starts
  its own service in a throwaway HOME on a private port and judges the second
  run. The design-phase "verified on origin/2.0" claim was against a vendored
  exploration branch; the live mechanism is now verified directly instead.
  Live check CLOSED 2026-10-13: restricted tkt sandbox restarted on the branch
  lists the three `tkt-session` tools in a real `opencode` (acp in-process)
  session — including launching a host review via the tool.
- **P2**: **PASSED** — host-side `plannotator sessions --json` output shape +
  two-simultaneous-sessions probe captured on the host (empty, one live
  annotate session, two simultaneous live annotate sessions). The real
  two-entry shape is pinned as golden fixtures in tests/test_session_managers.py.
  The PR-URL entry shape was explicitly NOT probed — that surface is out of
  use by user decision, and `_match` remains target-absolute-path only.
- **P3** — **PASSED 2026-10-13.** All eight allow-listed actions map to real
  upstream `plannotator inbox mcp` tools; exact match, no renames/extras.
- **P4 (wake layer, 2026-10-14) — RESOLVED by three host probe rounds**
  (`investigations/host_session_probes/wake/`): (1) `plugin` array of
  `OPENCODE_CONFIG_CONTENT` NOT honored (entries are npm package specs);
  (2) global `~/.config/opencode/plugin/` autoload works, module shape must
  be `export default { id, setup }`, project-level `.opencode/plugin/` does
  not fire in this version; (3) symlinks (dir- or file-level) are silently
  skipped ⇒ delivery is a copied real file via `tkt install-opencode-agent`
  (2c3848a; the interim bwrap-bind attempt failed launch — bwrap cannot
  mkdir under a remounted-ro config dir — and was reset out). A
  manager-side root cause closed in parallel: `status()` = registry ∪ CLI
  with `id` on registry-backed entries (bc9ac49) — the plugin's id-keyed
  poll had been skipping every session and could not see post-close
  feedback. Open for acceptance (human turn): live `Plannotator:` delivery
  in a restricted sandbox; watch the probe-round-2 rc=124 hang class and the
  setup-context keys; B-exit (delete plugin, pull-only) declared in §3.6.

## 12. Scope boundaries

- **In scope**: everything in §2–§7, including the narrow Plannotator-inbox MCP
  proxy (the only "external MCP" slice; guide flow requires it).
- **Deferred (designed-for by the §3.2 spawn-and-filter seam)**: general
  external MCP server proxying (e.g. GitHub over HTTP) — needs its own config
  schema, allow-list policy, and credential story; separate follow-on spec.
- **Explicitly out / never**: host→session push (§5), port allocators/bookmarks
  (§2.6), in-sandbox session servers of any kind, changes to the LLM bridge or
  Zed warm stack.
- **Reference material**: old branch `feature/plannotator-integration`
  (phase-1 bridge/plugin corpus, phase-2 spec/plan, `_doctemplates` draft at
  `e205a4f`) and superpowers submodule branch `feature/plannotator-integration`
  (`76cc115` SDD browser-review step) — reference only; this project builds on
  `main` (post-jsonc `8f2e187`), on a fresh feature branch cut at build time.

## 13. Open questions for the plan phase

None architectural. Plan-phase obligations: sequence P1–P3 early; keep the wake
plugin a separable task; commit spec/plan docs first on the new branch per
AGENTS.md workflow.
