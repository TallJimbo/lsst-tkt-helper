# Migrate tkt's MCP usage to mcp SDK 2.x

Date: 2026-10-16
Status: approved in chat (Gate 1) — design reviewed via the code examples below.

## Context

tkt's MCP servers/clients were pinned to `mcp` (Python SDK) 1.28.1 after a conda
upgrade pulled in 2.3.0, which broke `from mcp.server.fastmcp import FastMCP`
(v2 replaces that module with a raising stub pointing at `MCPServer`). A
feasibility probe (scripts left at `/tmp/opencode/mcp2-probe/`) ran real stdio
and streamable-HTTP round trips against the cached 2.3.0 build and verified the
migration is a ~15-line diff with byte-identical tool schemas and full
wire interop in both directions (both SDK lines negotiate protocol
`2025-11-25`; the identical `HANDSHAKE_PROTOCOL_VERSIONS` set means no client —
OpenCode or GitHub's remote MCP — can tell which SDK built the server).

Key facts verified, not assumed:

- `MCPServer` (`mcp.server.mcpserver`) is the renamed `FastMCP`; `tool()` and
  `run()` (default stdio) behave identically for tkt's usage. Tool
  `inputSchema`s are byte-identical to 1.28.1 (required fields, defaults,
  `Args:`-docstring param descriptions). `-> str` returns wrap as
  `structuredContent: {"result": ...}` exactly as 1.28.1 already did; `-> dict`
  unchanged.
- The low-level `Server` **lost** the `@server.list_tools()` /
  `@server.call_tool()` decorators. Replacement:
  `add_request_handler(method, params_type, handler)`; handlers receive
  validated param models and results relay verbatim (tested with an exotic
  passthrough schema: enums, `$defs`, `x-` keys, `_meta` — returned verbatim).
- `streamable_http_client(...)` now yields a **2-tuple** `(read, write)`
  (was 3-tuple). `create_mcp_http_client(headers=...)` unchanged (returns an
  `httpx2` client). `stdio_server()`, `Server.run(...)`, `ClientSession`,
  `stdio_client`, `mcp.types` all unchanged.
- mcp 2.3.0 hard deps (httpx2, mcp-types==2.3.0, opentelemetry-api,
  pydantic>=2.12, starlette, sse-starlette, uvicorn, jsonschema, pyjwt,
  python-multipart, typing-inspection) are **all already satisfied** in the
  live conda env; only the `mcp` package itself changes.
- v2 adds `resultType: "complete"` to `CallToolResult` (additive; v1 peers
  ignore it) and defaults `serverInfo.version` to `""` (1.x stamped the SDK
  version).

## Decisions

1. **2.x only, no compat shim.** tkt requires `mcp>=2` going forward; no
   try/except import branches, no dual-version code. Matches the repo's
   prefer-simple policy; conda-forge's default resolution now yields 2.x, so
   fresh dev envs are v2 anyway. Old-env users get a loud
   `ModuleNotFoundError: No module named 'mcp.server.mcpserver'`.
   Rejected: dual-version shim (~25 extra lines carried indefinitely).
2. **Proxy uses low-level `add_request_handler`**, not an `MCPServer`
   subclass override. Smallest diff, keeps `remote_mcp_proxy.py`'s structure,
   and relays upstream `ListToolsResult`/`CallToolResult` verbatim — which is
   the proxy contract. (`MCPServer.add_tool` is unusable here: it builds
   schemas from Python callables and cannot register a pre-built upstream
   `Tool`.)
3. **No argument validation in the relay.** `params.arguments` (a plain dict,
   validated only as `CallToolRequestParams`) passes untouched to
   `upstream.call_tool`; calls missing upstream-required fields reach the
   upstream server untouched — correct passthrough semantics.
4. **Do not set `serverInfo.version`.** Cosmetic; nothing in tkt or OpenCode
   consumes it.
5. **No runtime version enforcement in code** beyond the loud import failure.
   The requirement is documented in `AGENTS.md` instead.

## Changes

### `tkt/mcp_server.py`, `tkt/session_server.py`, `tkt/_session_tools.py` — rename

```python
-from mcp.server.fastmcp import FastMCP
+from mcp.server.mcpserver import MCPServer

-    mcp = FastMCP(name="tkt")          # mcp_server.py; session_server.py uses "tkt-session"
+    mcp = MCPServer(name="tkt")

-def build_session_tools(mcp: FastMCP, *, workspace: str) -> None:   # _session_tools.py
+def build_session_tools(mcp: MCPServer, *, workspace: str) -> None:
```

Everything else in these files — `@mcp.tool()` decorated tools (all use
parens; bare `@mcp.tool` now raises `TypeError`, tkt never uses bare), return
annotations, and `mcp.run()` are unchanged. One attribute-read correction was
required: `_inbox_call`'s `getattr(res, "isError", False)` →
`getattr(res, "is_error", False)` (2.x `mcp_types` fields are snake_case).
The rest of the `_inbox_call` client (`ClientSession`,
`StdioServerParameters`, `stdio_client`, `TextContent`) works unchanged.

### `tkt/remote_mcp_proxy.py` — handler registration + tuple fix

Replace the two removed decorators inside `_serve()` (upstream session is
established by the existing `streamable_http_client`/`ClientSession` stack,
unchanged):

```python
server = Server("tkt-remote-mcp")

async def _list_tools(
    ctx: Context, params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    return await upstream.list_tools()


async def _call_tool(ctx: Context, params: types.CallToolRequestParams) -> types.CallToolResult:
    return await upstream.call_tool(params.name, params.arguments)


server.add_request_handler("tools/list", types.PaginatedRequestParams, _list_tools)
server.add_request_handler("tools/call", types.CallToolRequestParams, _call_tool)
```

(`Context` import per the v2 low-level example conventions; handler
registration signature: `add_request_handler(method: str, params_type: type,
handler)`; `initialize` is reserved and must not be registered.)

And the unpacking fix (v2 yields a 2-tuple; the session id, which we ignored,
is now internal to the transport):

```python
-        async with streamable_http_client(url, http_client=http) as (read, write, _get_session_id):
+        async with streamable_http_client(url, http_client=http) as (read, write):
```

`stdio_server()` usage and `await server.run(client_read, client_write,
server.create_initialization_options())` are unchanged. The host-side token
injection (Authorization header set only in the host proxy process) is
unchanged; v2 logs tool failures to server stderr with repr-quoted values
only — consistent with the no-token-in-logs invariant.

### `AGENTS.md`

- `Development setup` dependency line: add `mcp` with a note that tkt requires
  2.x (`mcp.server.mcpserver.MCPServer`; `mcp.server.fastmcp` no longer
  exists).
- Pending unrelated hunk: restore the two-space continuation indent before
  `--shared-worktree` (line ~68) in the uncommitted diff.

## Environment step (human, host terminal)

```sh
conda install --offline -c conda-forge --override-channels 'mcp=2.3.0'
```

Fully satisfied from the local pkgs cache; all transitive deps already
installed. Sequencing vs. the code change is order-free — either order leaves
the MCP tools briefly broken (minutes).

## Verification

1. `ruff check . && ruff format --check . && mypy tkt/`.
2. Full `python -m pytest` **from the upgraded env directly** — the
   `/tmp/opencode/tkt-mcp1-venv` overlay hack is obsolete once the env itself
   carries mcp. `tests/test_remote_mcp_proxy.py` is the key test; extend/adjust
   it for the 2-tuple/handler shape if it asserts on internals.
3. Live smoke from a fresh `tkt sandbox-run`:
   - Plannotator review round trip (session server over `session.sock`).
   - One real `github_search_repositories` call through the remote-MCP proxy
     (`mcp-github.sock`).

## Out of scope

- Dual-version (1.x) support / compat shims.
- Pinning or vendoring `mcp` (no packaging config, per repo policy).
- Adopting newer protocol revisions (2026-07-28 types exist in mcp-types but
  are not negotiable by 2.3.0's handshake).
