# mcp SDK 2.x Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate tkt's MCP servers, proxy, and test fake from mcp Python SDK 1.28.1 (`mcp.server.fastmcp.FastMCP`) to 2.x (`mcp.server.mcpserver.MCPServer`), dropping 1.x support.

**Architecture:** Three high-level server modules get a pure rename (`FastMCP` → `MCPServer`); the tools-only remote-MCP proxy replaces the removed low-level `@server.list_tools()` / `@server.call_tool()` decorators with `add_request_handler` registrations and fixes the `streamable_http_client` 2-tuple unpacking; the fake test upstream moves to `MCPServer` with `run(transport="streamable-http", port=...)`. All wire behavior — tool schemas, structured output, error semantics, protocol negotiation (`2025-11-25`) — was verified byte-identical by the migration probe; no new functionality, no compat shims.

**Tech Stack:** Python 3.13, `mcp` 2.3.0 (conda-forge build), pytest, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-10-16-mcp-sdk-2-migration-design.md` (travels with this plan; read both).

## Global Constraints

- Target SDK: `mcp==2.3.0` (conda-forge, installed via `conda install --offline -c conda-forge --override-channels 'mcp=2.3.0'`). tkt requires `mcp>=2`; **no** 1.x-compat code paths (spec Decision 1).
- Every `.py` file keeps its BSD-3-Clause license header untouched.
- ruff: line-length 110, doc-length 79, numpy docstring convention; `ruff check .` and `ruff format --check .` clean. mypy: `mypy tkt/` clean.
- No packaging config (`setup.py` etc.) — tkt runs from the git clone (AGENTS.md).
- Proxy no-token-in-logs invariant: `remote_mcp_proxy.py` must never log/print header values, token contents, or request bodies; tracebacks fine (no interpolated values). The relay handlers log nothing (spec Decision context + file docstring).
- Proxy stays tools-only: do not register `prompts/*` or `resources/*` handlers (spec D4 of the GitHub-MCP-proxy spec).
- Do not set `serverInfo.version` (spec Decision 4). Do not add runtime mcp-version enforcement (spec Decision 5).

---

### Task 1: Upgrade the environment and rename the high-level servers

**Files:**

- Modify: `tkt/mcp_server.py` (import line 60, constructor line 738, docstring line 725)
- Modify: `tkt/session_server.py` (import line 34, constructor line 41)
- Modify: `tkt/_session_tools.py` (import line 37, annotation line 93)
- Modify: `tests/test_mcp_server.py` (monkeypatch target line 214)
- Test: existing `tests/test_mcp_server.py`, `tests/test_wake_plugin_client.py` (no new tests — this is a rename; the suite is the harness)

**Interfaces:**

- Consumes: environment with `mcp==2.3.0` (Step 1, human-run).
- Produces: `build_session_tools(mcp: MCPServer, *, workspace: str) -> None` in `tkt/_session_tools.py` (Task 2 does not consume it; later tasks and any future code use this signature); `mcp_server.MCPServer` importable as `mcp_server.MCPServer` (the monkeypatch target in tests).

- [ ] **Step 1: Upgrade the conda env to mcp 2.3.0 (human step — the agent's sandbox sees the env read-only)**

Ask the human to run in their own terminal:

```sh
conda install --offline -c conda-forge --override-channels 'mcp=2.3.0'
```

All transitive deps (`httpx2`, `mcp-types==2.3.0`, `opentelemetry-api`, etc.) are already installed. Verify from the agent shell (read-only inspection is fine):

```sh
python -c "import mcp.server.mcpserver as m; print('v2 OK')"
```

Expected: prints `v2 OK`.

- [ ] **Step 2: Run the high-level server tests to verify they fail**

```sh
python -m pytest tests/test_mcp_server.py -x -q
```

Expected: FAIL — `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` at import of `tkt.mcp_server`. This is the "failing test": the v1 import path is a raising stub in 2.x.

- [ ] **Step 3: Apply the rename in the three server modules**

`tkt/mcp_server.py` — three edits:

```python
# line 60
-from mcp.server.fastmcp import FastMCP
+from mcp.server.mcpserver import MCPServer

# line 725 (docstring inside run_server; reword to match)
-    """Run the FastMCP stdio server exposing the ``bash`` tool.
+    """Run the MCPServer stdio server exposing the ``bash`` tool.

# line 738
-    mcp = FastMCP(name="tkt")
+    mcp = MCPServer(name="tkt")
```

`tkt/session_server.py` — two edits:

```python
# line 34
-from mcp.server.fastmcp import FastMCP
+from mcp.server.mcpserver import MCPServer

# line 41
-    mcp = FastMCP(name="tkt-session")
+    mcp = MCPServer(name="tkt-session")
```

`tkt/_session_tools.py` — two edits:

```python
# line 37
-from mcp.server.fastmcp import FastMCP
+from mcp.server.mcpserver import MCPServer

# line 93
-def build_session_tools(mcp: FastMCP, *, workspace: str) -> None:
+def build_session_tools(mcp: MCPServer, *, workspace: str) -> None:
```

Nothing else changes in these files: `@mcp.tool()` (always called with parens — a bare `@mcp.tool` raises `TypeError` in v2, but tkt never uses the bare form), `-> str` / `-> dict` returns, and `mcp.run()` (stdio default) are v2-compatible verbatim. Exception found during implementation: `_session_tools._inbox_call` read `getattr(res, "isError", False)` — 2.x `mcp_types.CallToolResult` uses snake_case `is_error`, so the read became `getattr(res, "is_error", False)` (with `getattr` kept for tolerance).

- [ ] **Step 4: Fix the test monkeypatch target**

`tests/test_mcp_server.py` line 214 patches the name as imported into the `mcp_server` module:

```python
-    monkeypatch.setattr(mcp_server.FastMCP, "run", lambda self: None)
+    monkeypatch.setattr(mcp_server.MCPServer, "run", lambda self: None)
```

Confirm no other `FastMCP` references remain in code (stale prose in comments is acceptable):

```sh
grep -rn "FastMCP" tkt/ tests/ --include='*.py' | grep -v '^\s*#\|"""'
```

Expected: the only remaining hits are the two prose comment mentions in `tests/test_wake_plugin_client.py` (lines 45, 226) — those stay. Any hit in `tkt/` or in live test code is a miss.

- [ ] **Step 5: Run the tests to verify they pass**

```sh
python -m pytest tests/test_mcp_server.py tests/test_wake_plugin_client.py -q
python -m pytest -q   # full suite; test_remote_mcp_proxy may still fail — that's Task 2
```

Expected: target files PASS; full suite green except `tests/test_remote_mcp_proxy.py` (Task 2 territory: the proxy and the fake upstream still import v1).

- [ ] **Step 6: Lint/type gate and commit**

```sh
ruff check . && ruff format --check . && mypy tkt/
git add tkt/mcp_server.py tkt/session_server.py tkt/_session_tools.py tests/test_mcp_server.py
git commit -m "feat: rename FastMCP to MCPServer for mcp SDK 2.x."
```

---

### Task 2: Rewrite the remote-MCP proxy handlers and the fake upstream

**Files:**

- Modify: `tkt/remote_mcp_proxy.py` (imports lines 50-54, `_serve` lines 70-89)
- Modify: `tests/fake_github_mcp.py` (lines 34, 36, 49-50)
- Test: existing `tests/test_remote_mcp_proxy.py` — **unchanged**; it drives the proxy as a subprocess over real stdio against the real streamable-HTTP fake and asserts tool names, schema/description passthrough, and Authorization injection behaviorally.

**Interfaces:**

- Consumes: `mcp==2.3.0` env (Task 1 Step 1). Independent of Task 1's files.
- Produces: `tkt/remote_mcp_proxy.py` CLI unchanged (`--url`, `--token-file`, `--header`); `tests/fake_github_mcp.py` still runnable as `python tests/fake_github_mcp.py <port>` serving `http://127.0.0.1:<port>/mcp` with a `whoami` tool echoing the `Authorization` header.

- [ ] **Step 1: Run the proxy tests to verify they fail**

```sh
python -m pytest tests/test_remote_mcp_proxy.py -x -q
```

Expected: FAIL — the fake upstream subprocess dies on `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` (fixture times out: `RuntimeError: fake upstream did not come up`), and/or the proxy subprocess fails on the removed `@server.list_tools()` decorator (`AttributeError: 'Server' object has no attribute 'list_tools'`).

- [ ] **Step 2: Rewrite `_serve` in `tkt/remote_mcp_proxy.py`**

Replace the import block (lines 50-54):

```python
import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.server.stdio import stdio_server
```

Replace `_serve` (lines 70-89) with — note the 2-tuple unpacking (`streamable_http_client` no longer yields a get-session-id callable; the session id is internal to the transport and was ignored anyway) and the two `add_request_handler` registrations replacing the removed decorators:

```python
async def _serve(url: str, headers: dict[str, str]) -> None:
    """Relay stdio JSON-RPC to the upstream, verbatim, with ``headers``."""
    # streamable_http_client does not close a caller-provided client, so
    # own its lifecycle here.
    async with create_mcp_http_client(headers=headers) as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as upstream:
                await upstream.initialize()
                server = Server("tkt-remote-mcp")

                async def list_tools(
                    ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams
                ) -> types.ListToolsResult:
                    # Relay the upstream result verbatim (no pagination
                    # handling: the proxy is stateless per connection).
                    return await upstream.list_tools()

                async def call_tool(
                    ctx: ServerRequestContext[Any, Any], params: types.CallToolRequestParams
                ) -> types.CallToolResult:
                    # params.arguments is relayed untouched: calls missing
                    # upstream-required fields fail upstream, not here.
                    return await upstream.call_tool(params.name, params.arguments)

                server.add_request_handler("tools/list", types.PaginatedRequestParams, list_tools)
                server.add_request_handler("tools/call", types.CallToolRequestParams, call_tool)

                async with stdio_server() as (client_read, client_write):
                    await server.run(client_read, client_write, server.create_initialization_options())
```

Notes for the implementer (all verified by the migration probe against the 2.3.0 build):

- `add_request_handler(method, params_type, handler)`: the handler never receives `None` (all-optional params models validate `{}` to defaults), `initialize` is reserved and must not be registered, and results pass through verbatim — exactly the proxy contract.
- `types.PaginatedRequestParams` / `types.CallToolRequestParams` are exported from `mcp.types` (a documented mirror of the standalone `mcp-types` package in 2.x).
- Do **not** register `prompts/*` or `resources/*` handlers — tools-only is the design (spec D4).
- Do not set `version=` on `Server(...)`; v2 defaults `serverInfo.version` to `""` and nothing consumes it (spec Decision 4).
- v2 logs tool failures to server stderr with repr-quoted values only; our handlers add no logging, preserving the no-token-in-logs invariant (file docstring).
- `Any` is already imported (line 48). `stdio_server()`, `Server.run(...)`, `create_mcp_http_client(headers=...)` (now returning an `httpx2` client) keep their shapes.

- [ ] **Step 3: Migrate `tests/fake_github_mcp.py`**

The fake runs under `sys.executable` (the same v2 env), so it must move too. Three edits:

```python
# line 34
-from mcp.server.fastmcp import Context, FastMCP
+from mcp.server.mcpserver import Context, MCPServer

# line 36
-mcp = FastMCP(name="fake-github")
+mcp = MCPServer(name="fake-github")

# lines 49-50 (v2 has no Settings object; transport options are run() kwargs.
# Defaults host=127.0.0.1 and streamable_http_path="/mcp" match the test URL.)
-    mcp.settings.port = int(sys.argv[1])
-    mcp.run(transport="streamable-http")
+    mcp.run(transport="streamable-http", port=int(sys.argv[1]))
```

The `whoami` tool body is unchanged: v2's `Context` still exposes `request_context.request`.

- [ ] **Step 4: Run the proxy tests to verify they pass**

```sh
python -m pytest tests/test_remote_mcp_proxy.py -q
```

Expected: all 5 tests PASS (verbatim schema passthrough, default Bearer injection, `--header` templating, missing-token and empty-token fatal exits).

- [ ] **Step 5: Lint/type gate and commit**

```sh
ruff check . && ruff format --check . && mypy tkt/
git add tkt/remote_mcp_proxy.py tests/fake_github_mcp.py
git commit -m "feat: rewrite remote MCP proxy on mcp SDK 2.x request handlers."
```

---

### Task 3: Document the requirement; full gate

**Files:**

- Modify: `AGENTS.md` (`Development setup` dependency line)

**Interfaces:**

- Consumes: Tasks 1-2 landed.
- Produces: documented `mcp>=2` requirement; green gate output recorded for the reviewer.

- [ ] **Step 1: Add mcp to the documented dependencies**

In `AGENTS.md`, under `## Development setup`, the dependency line currently reads:

```markdown
- **Dependencies**: `click`, `GitPython`, `pyyaml`, `json5`
```

Replace with:

```markdown
- **Dependencies**: `click`, `GitPython`, `pyyaml`, `json5`, `mcp` (requires
  2.x: `mcp.server.mcpserver.MCPServer`; `mcp.server.fastmcp` no longer
  exists)
```

- [ ] **Step 2: Run the complete gate from the real env**

No overlay venv needed anymore — the env itself carries mcp 2.x (the
`/tmp/opencode/tkt-mcp1-venv` hack from the 1.x repair is obsolete).

```sh
ruff check . && ruff format --check .
mypy tkt/
python -m pytest -q
```

Expected: all clean; 354+ passed, 0 failed.

- [ ] **Step 3: Commit**

```sh
git add AGENTS.md
git commit -m "docs: record mcp>=2 requirement in AGENTS.md."
```

---

### Task 4: Live smoke through a real sandbox launch

**Files:** none (verification only — no commit).

**Interfaces:**

- Consumes: all prior tasks; a fresh `tkt sandbox-run` launched by the human.

- [ ] **Step 1: Human launches a fresh restricted sandbox run** (`tkt sandbox-run` on this branch's workspace). Confirm in the OpenCode log (`~/.local/share/opencode/log/opencode.log`) that both `mcp connected server=tkt-session` and the `github` remote-MCP proxy connect (previous runs logged `tools=46`).

- [ ] **Step 2: Plannotator round trip** — inside the sandbox session, open a review (`plannotator` action `open`, kind `review`, target `.`, base `HEAD^`), have the human approve, then `result` and `close`. Expected: review served at a `localhost` URL; result retrieved.

- [ ] **Step 3: Live GitHub call through the proxy** — one real allowed call, e.g. `tools.github.search_repositories` with query `lsst-tkt in:name`. Expected: live results (total_count >= 1).

- [ ] **Step 4: Record outcome.** If anything fails, apply systematic-debugging before declaring the migration done.

## Migration probe artifacts

End-to-end v2 round trips (schemas byte-identical vs 1.28.1, verbatim
passthrough, 2-tuple unpack, header-injected upstream auth) were pre-verified
by scripts in `/tmp/opencode/mcp2-probe/` — throwaway, safe to delete after
the build passes.
