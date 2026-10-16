# GitHub MCP via host-fronted remote-MCP proxy — design spec

Date: 2026-10-15
Status: approved in chat (Gate 1)
Areas: `tkt/sandbox.py`, new `tkt/remote_mcp_proxy.py`, `local.jsonc`, tests

## Problem

The GitHub MCP server (`https://api.githubcopilot.com/mcp/`, remote
streamable-HTTP, PAT auth via `Authorization: Bearer`) is configured in the
human's `~/.config/opencode/opencode.jsonc`. Inside a network-restricted
`sandbox-run` it is unreachable (`--unshare-net`; only the LLM ports and
`session.sock` are bridged), and the token is deliberately absent from the
sandbox (`$HOME` tmpfs hides all credential stores).

Two sub-problems and their resolution:

1. **Secret exposure.** Sandbox processes run as the same UID as OpenCode, so
   any secret OpenCode holds (env, file, memory) is readable by every sandbox
   process — isolation *within* the sandbox at equal UID is not achievable.
   The design instead ensures **the token never enters the sandbox at all**:
   a host-side proxy injects the `Authorization` header per request. OpenCode
   inside the sandbox holds no credential either — strictly better than the
   original ask.
2. **Reachability.** A per-launch unix socket in the existing `net-*` bridge
   directory fronts the remote server, reusing the exact `tkt-session`
   pattern: sandbox-side `socat -` (stdio↔socket) as a `type: "local"` MCP
   server; host-side `socat UNIX-LISTEN,fork` spawning a proxy per
   connection.

## Approach (chosen among three)

**Chosen — Approach A: host-side MCP proxy on a dedicated unix socket.**
A stdio↔streamable-HTTP pass-through proxy runs **on the host**, spawned per
connection by socat, holding the token in host memory only. No new TCP ports,
no TLS termination, no plaintext-HTTP-URL questions.

Rejected:
- **B — dumb TCP relay to `api.githubcopilot.com:443` + `--setenv
  GITHUB_TOKEN` into the sandbox.** Simplest (socat only) but puts the token
  in every sandbox process's reach — the exact thing to avoid.
- **C — host HTTP reverse proxy on a bridged TCP port.** Token stays on the
  host but needs a new bridged port, depends on OpenCode accepting an
  `http://` remote MCP URL, and relies on replace-vs-merge semantics for the
  `headers` key. Strictly worse than A for similar effort.

## Key insight: zero GitHub-API duplication

MCP's stdio and streamable-HTTP transports carry the **same JSON-RPC 2.0
messages**, only enveloped differently (newline-delimited on a pipe vs one
message per HTTP POST, response as JSON body or short SSE stream). The proxy
is a transport remuxer with two delegating handlers; upstream tool schemas
flow through as opaque data. GitHub is **config data, not code**. The
existing `mcp` Python SDK (1.28.1, already a dependency; precedent:
`_session_tools.py` proxies `plannotator inbox mcp` the same way) owns the
handshake, `Mcp-Session-Id` threading, protocol-version negotiation, and SSE
parsing.

Because the mechanism has no GitHub-specific machinery, it is **general by
default**: any remote streamable-HTTP MCP server needing an auth header is a
`local.jsonc` stanza. GitHub is the only entry today.

## Design

### 1. Configuration (`local.jsonc` → `tools.sandbox`)

```jsonc
"remote_mcp": {
  "github": {
    "url": "https://api.githubcopilot.com/mcp/",
    "token_file": "~/.github-mcp-token"   // chmod 600, single-line PAT
  }
}
```

- Optional per-entry `"header"` template, default `"Bearer {token}"` (future
  non-Bearer servers stay config-only).
- Parsed in `Sandbox.from_json_data()` into
  `self._remote_mcp: dict[str, dict[str, str]]` (default `{}` when absent).
- The token file lives on the **host only**; only its *path* ever appears in
  bwrap-visible state. Contents are stripped of surrounding whitespace
  (trailing newline tolerant).

### 2. Host-side bridge (`tkt/sandbox.py`, `_start_bridge()`)

Alongside the `llm-<port>.sock` and `session.sock` listeners, one listener
per entry, same spawn-per-connection pattern and same lifecycle (`procs` list
→ `_stop_bridge()` reaps; `_set_pdeathsig`; stale cleanup by
`sandbox-cleanup` already reaps socats whose `net-*` dir is gone):

```python
bridged: list[str] = []
for name, entry in self._remote_mcp.items():
    token_file = os.path.expanduser(entry["token_file"])
    if not os.path.isfile(token_file):
        continue  # not bridged; sandboxed OpenCode shows its own MCP failure UI
    sock = os.path.join(net_dir, f"mcp-{name}.sock")
    server_cmd = [sys.executable, "-m", "tkt.remote_mcp_proxy",
                  "--url", entry["url"], "--token-file", token_file]
    if "header" in entry:
        server_cmd += ["--header", entry["header"]]
    procs.append(subprocess.Popen(
        ["socat", f"UNIX-LISTEN:{sock},fork", _socat_system_arg(server_cmd)],
        stdout=log_f, stderr=subprocess.STDOUT,          # per-name log in net_dir
        start_new_session=True, preexec_fn=_set_pdeathsig))
    bridged.append(name)
```

Socket naming `mcp-<name>.sock` matches `llm-<port>.sock` / `session.sock`.
Startup readiness check (existing "wait up to 5 s for sockets") covers the
new sockets **only for bridged entries** (an unbridged entry creates no
socket and must not stall startup).

### 3. OpenCode config injection

`session_mcp_config(net_dir)` generalizes (callers updated; internal
function, no back-compat shim needed):

```python
def opencode_config_content(net_dir: str, bridged: Iterable[str]) -> dict[str, Any]:
    def local(sock: str) -> dict[str, Any]:
        return {"type": "local", "command": ["socat", "-", f"UNIX-CONNECT:{sock}"]}
    mcp: dict[str, Any] = {"tkt-session": local(os.path.join(net_dir, SESSION_SOCKET_NAME))}
    for name in bridged:
        mcp[name] = local(os.path.join(net_dir, f"mcp-{name}.sock"))
    return {"mcp": mcp}
```

Injected exactly as today via `--setenv OPENCODE_CONFIG_CONTENT` (tkt-owned,
set last so `local.jsonc` env cannot clobber it). Keyed `github`, the entry
replaces the host file's remote entry — `OPENCODE_CONFIG_CONTENT` merges last
(established in the host-session-services design doc). **Build-phase probe:**
confirm per-key replacement leaves no stale `headers`/`url` fields merged
from the host entry; a leftover `Authorization` on a `local`-type server is
harmless (the field is ignored), so worst case is cosmetic.

Only **bridged** entries get overrides — with no token file, OpenCode keeps
the host config's (unreachable) remote entry: today's behavior.

### 4. The proxy — new `tkt/remote_mcp_proxy.py`

Runnable as `python -m tkt.remote_mcp_proxy` (mirrors `tkt/session_server.py`,
launched by socat's `SYSTEM:` on the host). Interface stub:

```python
def main() -> NoReturn:
    """CLI: --url URL --token-file PATH [--header 'Bearer {token}'].
    Reads the token, then serves stdio MCP until EOF (socat's other end).
    Exits nonzero if the token file is unreadable."""

async def _serve(url: str, headers: dict[str, str]) -> None:
    http = create_mcp_http_client(headers=headers)
    async with streamable_http_client(url, httpx_client=http) as (read, write, _):
        async with ClientSession(read, write) as upstream:
            await upstream.initialize()
            server = Server("tkt-remote-mcp")          # low-level mcp.server.Server

            @server.list_tools()
            async def list_tools() -> list[types.Tool]:
                return (await upstream.list_tools()).tools        # verbatim pass-through

            @server.call_tool()
            async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
                return await upstream.call_tool(name, arguments)  # verbatim pass-through

            async with stdio_server() as (c_read, c_write):
                await server.run(c_read, c_write, server.create_initialization_options())
```

Notes:
- Token read **after fork, on the host**; never in bwrap argv/env/filesystem
  (argv carries only the token-file *path*).
- Low-level `Server` (not FastMCP): two handlers, no per-tool registration,
  no schema interpretation — the maximal "arbitrary JSON" relay the SDK
  permits. Notifications (`notifications/initialized`, `$/cancel`, progress)
  are handled by the server/session frameworks; `tools/list_changed`
  forwarding is out of scope (client re-`list_tools` per session start makes
  staleness moot within a run).
- One upstream connection per stdio connection (socat `fork`): OpenCode's
  single MCP connection ⇒ single upstream session; proxy crash = failed
  server start = OpenCode's own MCP-failure UI indicator. No tkt-side
  warning plumbing (decision: nothing visible to warn into; OpenCode already
  shows auth/startup failure).

### 5. Modes and scope

- **Restricted `sandbox-run`** (workspace and single-repo): bridged per
  above. Shared-worktree mode uses the same restricted path ⇒ covered.
- **Full-network mode** (`--network`, shares host netns): **not supported**
  — no bridge sockets and no token export. GitHub MCP is a restricted-mode
  affordance only; full-network runs keep today's behavior (the host config
  entry works there only if the user separately exports `GITHUB_TOKEN`
  themselves). The maintainer uses `--network` extremely rarely and accepts
  the reduced functionality; carrying the only code path that puts a token
  value into a bwrap argv was not worth it.
- **Warm-holder** (`warm_holder_argv`, Zed `mcp-server` path):
  `bridge_llm=False`, no `net_dir` ⇒ not bridged. Same availability rule as
  the LLM bridge today.

### 6. Permissions (no change)

OpenCode enforces tool permissions in its own (sandboxed) process, keyed on
the MCP server key and tool name. The override keeps key `github` and relays
tool names/schemas byte-identical, so the existing global
`{action: github_*, effect: deny}` plus per-agent frontmatter allow-lists
(`code-review.md`, `pr-responder.md`) keep working unchanged. **Decision: no
host-side allow-list layer** — the harness permission model is the
enforcement point.

## Decisions made

| # | Decision | Choice |
|---|----------|--------|
| D1 | Token file location | `~/` directly, default `~/.github-mcp-token` (name adjustable; path is config) |
| D2 | Missing token file | Launch anyway; entry simply not bridged; no tkt warning (OpenCode shows its own MCP-failure UI); per-connection log in `net_dir/` |
| D3 | Full-network mode | **Not supported** (no token export): GitHub MCP is restricted-mode only; `--network` is rarely used and the reduced functionality is accepted — avoids the only path placing a token in bwrap argv |
| D4 | Proxy depth | Low-level `mcp.server.Server` with two delegating handlers; schemas pass through verbatim; zero GitHub-API duplication |
| D5 | Host-side tool allow-list | None — existing per-harness permission rules suffice; a process piping to `mcp-<name>.sock` directly bypasses them, accepted |
| D6 | Warm-holder sandbox | Not bridged (matches LLM-bridge scope) |
| — | General vs GitHub-specific | General mechanism (config-driven); GitHub is the sole entry today |

Residual risk: bwrap runs **without** `--unshare-pid`, so sandbox
processes can enumerate the proxy's PID and (equal UID) read its
`/proc/<pid>/{cmdline,environ}` (path/host-env only, pre-existing
pattern) and may attempt `/proc/<pid>/mem` subject to yama
`ptrace_scope`; the token itself is held in proxy memory — accepted for
now.  `--unshare-pid` feasibility is tracked as a follow-up, not in this
branch.

## Testing

- **`tests/test_remote_mcp_proxy.py`** (new): run a `streamable_http`-served
  FastMCP upstream in-process (localhost) whose middleware *asserts* the
  `Authorization: Bearer <expected>` header; launch the proxy as a subprocess
  with a temp token file; drive it with a stdio `ClientSession`:
  - `list_tools` returns upstream tools verbatim (schemas equal);
  - `call_tool` relays args and result content verbatim;
  - upstream sees the injected header;
  - missing/unreadable token file → subprocess exits nonzero.
- **`tests/test_sandbox.py`**: `remote_mcp` parsing in `from_json_data`;
  `_start_bridge` skips (and readiness check ignores) entries with absent
  token files; `opencode_config_content()` emits the `local` socat override
  per bridged entry plus `tkt-session`, and unbridged names are absent;
  existing env-ordering assertions updated for the renamed function.
- **Manual smoke**: `tkt sandbox-run` from a workspace, ask the agent for a
  `gh_whoami`-style call; confirm host-side `net-*/host-mcp-<name>-socat.log`
  shows the session.

## Out of scope

- OAuth against GitHub's endpoints (unsupported in this setup; PAT is the
  auth model).
- Stdio (`type: local`) upstream servers — remote-mcp entries are
  streamable-HTTP only.
- Host-side tool allow-listing / rate limiting / audit.
- Bridging in the warm-holder sandbox.
- Token refresh or multi-account rotation.
