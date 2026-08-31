# tkt MCP Server (sandboxed `bash` tool) — Design Handover

**Date:** 2026-08-31
**Status:** Approved by human in conversation (before implementation).

## Goal

Replace Zed's native agent `terminal` tool with a custom MCP server exposeing a
`bash` tool that runs commands inside the `bwrap`-sandboxed environment that
`tkt sandbox-run` provides, protecting the host filesystem (esp. `$HOME`) from
the agent while letting it work in the current project.

## Architecture

A single Python `FastMCP` stdio server per project, spawned and owned by Zed as
an MCP context server (`command`-based, so Zed spawns/kills the process). Lazily
spawns one long-lived `bwrap` "warm holder" that runs the conda/EUPS setup once.
Each `bash` tool call runs the command in a **fresh child** `bash -lc` of the
holder (inheriting warm env + exported functions), returning
`{stdout, stderr, exit_code}`. "Warm but stateless": setup warmth, stateless
calls. Working directory is tracked server-side (session) via end-of-call `pwd`.

## Tech Stack

- Python `FastMCP` (`mcp` official SDK, `mcp.server.fastmcp`) — stdio transport,
  Pydantic-derived tool schema. New third-party dependency (user installed).
- `bwrap` — the sandbox primitive (sticking with it; not containers).
- Pydantic for API models (user preference).
- Reuses `tkt.sandbox.Sandbox` mount-building logic and `tkt._cli.sandbox_run`
  workspace-vs-repo detection.

## Key decisions (signed off in conversation)

1. **Zed spawns the MCP server itself** via the `Stdio` context-server variant.
   Confirmed from Zed source (`crates/context_server/src/client.rs` `Client::stdio`
   "spawns a child process"; `protocol.rs:41-50` — Zed does NOT advertise the
   `roots` capability, so no workspace-root association is available).
   **Consequence:** the server deduces its project context from `os.getcwd()`
   (Zed sets child cwd = project root), because no `ZED_*` env / root arg is
   passed and `roots` is not enabled.

2. **Multiple projects in one window** each get their own server process (per-
   project `ContextServerStore`); switching the foreground project does NOT
   restart servers. So a single server normally has ONE warm holder for ONE
   project root.

3. **Multiple worktrees = option (A): explicit id in tool args** (future, not
   built today). Reserve a future optional `worktree_id` field on `BashRequest`
   without breaking the schema. Not implemented now.

4. **Warm-but-stateless.** One long-lived warm holder runs conda+EUPS setup once;
   every `bash` call runs a **fresh child** `bash -lc -- <command>` of the holder
   so the child inherits warm env + exported shell functions (via `BASH_FUNC_*`)
   while mutable state (cwd, non-exported vars, background jobs) dies with it.

5. **Stateless-by-default-fresh-child but tracked cwd (option (ii)).** The server
   tracks the current working directory server-side (a "session"), init from
   project root. Each fresh child starts in the tracked cwd. After each call the
   server records the end-of-call `pwd` (DO NOT scan for `cd` in command text —
   flaky). So `cd` works as agents expect, but env/jobs/aliases never persist.

6. **Lazy warm start.** The warm holder is spawned on the FIRST `bash` call, not
   eagerly at server startup (avoids paying setup for a project where commands
   are never run).

7. **Workspace detection** reuses `tkt._cli.sandbox_run` pattern: if
   `os.path.isdir(os.path.join(cwd, ".agent"))` → workspace mode
   (`setup -r .agent`); else single-repo mode (`setup -r .` if `ups/` exists,
   else bare conda).

8. **Interface mimics Claude Code `Bash` tool** (not Zed's terminal interface).
   User said: make it whatever the LLM finds most natural/ergonomic. Design:
   one `bash` tool, `command` required, optional `timeout_ms`/`description`,
   returns `{stdout, stderr, exit_code, timed_out}`.

9. **Extension path:** structure around a single "run command in warm sandbox"
   primitive so future tools (`edit_file`, `read_file`, ...) can be built on it.

## Wire framing (server ↔ warm holder)

Two directions, out-of-band from Zed's MCP stdio, over the holder child's
stdin/stdout pipes.

- **Server → holder (command):** base64-encoded command + newline
  (`<b64(command)>\n`). Handles multiline/quotes/unicode unambiguously. The
  driver decodes and runs it in a fresh child.
- **Holder → server (result):** single line: `b64(json)`, where
  `json = {"stdout": str, "stderr": str, "exit_code": int, "cwd": str}`. The
  server decodes and maps to `BashResult`.

Driver loop inside the warm bwrap:

```bash
<conda activate + EUPS setup, once, for the detected mode>
while IFS= read -r line; do
    cmd=$(printf '%s' "$line" | base64 -d)
    out=$(mktemp); err=$(mktemp)
    bash -lc -- "$cmd" >"$out" 2>"$err"
    rc=$?
    cwd=$(pwd)
    # emit one line: base64 of JSON {stdout:<"$out" contents>, stderr:<"$err" contents>, exit_code:$rc, cwd:$cwd}
    rm -f "$out" "$err"
done
```

## Tool schema

```python
class BashRequest(BaseModel):
    command: str                          # the shell command to run
    timeout_ms: int | None = None         # optional override
    description: str | None = None        # optional: why running it

class BashResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False

@mcp.tool()
def bash(request: BashRequest) -> BashResult: ...
```

`worktree_id` reserved for future (option A).

## Pydantic / FastMCP

- `@mcp.tool()` decorator on a function whose parameter/return types are Pydantic
  models → FastMCP builds the MCP JSON input/output schema from the models
  automatically. Pydantic models are the single source of truth.

## Project context notes

- Repo is `tkt` (Python 3.13; deps click, GitPython, pyyaml, json5). License:
  BSD-3-Clause; every `.py` file needs the license header (preserve it).
- `tkt` is NOT pip-distributed; no packaging config. Non-`.py` files live beside
  source, located at runtime via `os.path.dirname(__file__)`.
- Run before committing: `ruff check .`, `ruff format --check .`, `mypy tkt/`.
- Tests: pytest, `tests/` (see `tests/test_sandbox.py` for bwrap argv-testing
  conventions). Run `python -m pytest`.
