# GitHub MCP via Host-Fronted Remote-MCP Proxy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the remote GitHub MCP server usable from a network-restricted `tkt` sandbox by fronting it with a host-side stdio↔streamable-HTTP MCP proxy, so the PAT never enters the sandbox.

**Architecture:** The existing per-launch `net-*` bridge directory (already carrying `llm-<port>.sock` and `session.sock`) gains one `mcp-<name>.sock` listener per configured remote MCP server; socat spawns a host-side `tkt.remote_mcp_proxy` per connection that injects `Authorization` and relays JSON-RPC verbatim to the upstream. OpenCode's `github` MCP entry is redirected to the socket via `OPENCODE_CONFIG_CONTENT` (merged last, tkt-owned).

**Tech Stack:** Python 3.13, `mcp` SDK 1.28.1 (already a dependency: low-level `mcp.server.Server`, `ClientSession`, `streamable_http_client`, `stdio_server`), socat, pytest (sync tests driving `asyncio.run`).

**Spec:** `docs/superpowers/specs/2026-10-15-github-mcp-host-proxy-design.md`

## Global Constraints

- Python 3.13; dependencies limited to what `local.jsonc`/existing code already uses — no new packages (the `mcp` SDK is already required by `tkt/session_server.py`).
- Every new `.py` file carries the BSD-3-Clause license header verbatim from `tkt/session_server.py` (preserve it; see repo AGENTS.md).
- Lint gates before every commit: `ruff check .`, `ruff format --check .`, `mypy tkt/` (line-length 110, numpydoc convention; watch the single-line-docstring ruff-format gotcha noted in repo AGENTS.md).
- Tests run with `python -m pytest`; do not introduce new pytest plugins — async code is driven via `asyncio.run` inside sync tests.
- The token value must never appear in bwrap argv/env/mounts in **any**
  mode (full-network `--network` runs are explicitly unsupported for remote
  MCP — spec decision D3); restricted-mode injection is config-only
  (`OPENCODE_CONFIG_CONTENT`), token-file paths only.
- Doc root is in-repo `docs/superpowers/`; commit spec + plan on the feature branch at the start.

---

### Task 1: Feature branch, docs commit, and the host-side MCP proxy module

**Files:**

- Create: `tkt/remote_mcp_proxy.py`
- Create: `tests/fake_github_mcp.py` (test-support upstream server, not collected by pytest)
- Create: `tests/test_remote_mcp_proxy.py`
- Commit: `docs/superpowers/specs/2026-10-15-github-mcp-host-proxy-design.md`, this plan

**Interfaces:**

- Consumes: nothing from earlier tasks.
- Produces: `python -m tkt.remote_mcp_proxy --url URL --token-file PATH [--header 'Bearer {token}']` — a stdio MCP server that relays `tools/list` / `tools/call` verbatim to a remote streamable-HTTP MCP server with the `Authorization` header injected; exits nonzero on unreadable/empty token file. Task 2 spawns exactly this command from socat.

- [ ] **Step 1: Create the feature branch and commit the design docs**

```bash
git checkout -b github-mcp-host-proxy
git add docs/superpowers/specs/2026-10-15-github-mcp-host-proxy-design.md \
        docs/superpowers/plans/2026-10-15-github-mcp-host-proxy.md
git commit -m "docs: spec and plan for host-fronted remote MCP bridging"
```

- [ ] **Step 2: Write the failing integration tests**

Create `tests/fake_github_mcp.py` — a stand-in for GitHub's MCP endpoint. It exposes one tool that echoes the `Authorization` header it received, so tests can prove host-side injection and verbatim schema relay without real GitHub calls:

```python
# (BSD license header copied verbatim from tkt/session_server.py)

"""Fake remote MCP server for proxy tests: serves streamable-HTTP on
``127.0.0.1:<port>/mcp`` and exposes ``whoami``, which echoes the
``Authorization`` header that reached this (host-side) server.

Run as ``python tests/fake_github_mcp.py <port>``.  Not a pytest module.
"""

import sys

from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP(name="fake-github")


@mcp.tool()
async def whoami(ctx: Context) -> str:
    """Return the Authorization header received by this server."""
    request = ctx.request_context.request
    if request is None:  # pragma: no cover - streamable-http always provides it
        return ""
    return request.headers.get("authorization", "")


if __name__ == "__main__":
    mcp.settings.port = int(sys.argv[1])
    mcp.run(transport="streamable-http")
```

Create `tests/test_remote_mcp_proxy.py`:

```python
# (same BSD license header)

"""Integration tests for tkt.remote_mcp_proxy: verbatim tool relay and
host-side Authorization injection, driven over real stdio against a
real streamable-HTTP upstream."""

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

UPSTREAM = str(Path(__file__).parent / "fake_github_mcp.py")
TOKEN = "ghp_test-s3cret"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def upstream_url():
    """Run the fake upstream on a free port; yield its MCP URL."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, UPSTREAM, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                break
        except OSError:
            time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError("fake upstream did not come up")
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


def _drive_proxy(url: str, token_file: Path, extra: list[str] | None = None):
    """Run the proxy as a subprocess over stdio; return (tools, whoami result)."""

    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "tkt.remote_mcp_proxy",
                "--url",
                url,
                "--token-file",
                str(token_file),
                *(extra or []),
            ],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                result = await session.call_tool("whoami", {})
                return tools, result

    return asyncio.run(go())


def test_proxy_relays_tools_verbatim(upstream_url, tmp_path):
    token = tmp_path / "tok"
    token.write_text(TOKEN + "\n")  # trailing newline must be tolerated
    tools, _ = _drive_proxy(upstream_url, token)
    assert [t.name for t in tools] == ["whoami"]
    assert tools[0].description == "Return the Authorization header received by this server."


def test_proxy_injects_default_bearer_header(upstream_url, tmp_path):
    token = tmp_path / "tok"
    token.write_text(TOKEN + "\n")
    _, result = _drive_proxy(upstream_url, token)
    assert result.content[0].text == f"Bearer {TOKEN}"


def test_proxy_honors_header_template(upstream_url, tmp_path):
    token = tmp_path / "tok"
    token.write_text(TOKEN)
    _, result = _drive_proxy(upstream_url, token, extra=["--header", "token {token}"])
    assert result.content[0].text == f"token {TOKEN}"


def test_proxy_missing_token_file_exits_nonzero(upstream_url, tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tkt.remote_mcp_proxy",
            "--url",
            upstream_url,
            "--token-file",
            str(tmp_path / "absent"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "absent" in proc.stderr or "absent" in proc.stdout


def test_proxy_empty_token_file_exits_nonzero(upstream_url, tmp_path):
    token = tmp_path / "tok"
    token.write_text("   \n")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tkt.remote_mcp_proxy",
            "--url",
            upstream_url,
            "--token-file",
            str(token),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_remote_mcp_proxy.py -v`
Expected: FAIL/ERROR — `No module named tkt.remote_mcp_proxy` (stdio_client raises `CommonError`/spawn failure; the `subprocess.run` tests show a nonzero exit with "No module named" — treat the *missing-module* stderr as the failure signal, not a pass).

- [ ] **Step 4: Write `tkt/remote_mcp_proxy.py`**

```python
# (BSD license header copied verbatim from tkt/session_server.py)

"""Host-side MCP proxy bridging a restricted sandbox's stdio MCP
connection to a remote streamable-HTTP MCP server, injecting an auth
header on the host so the token never enters the sandbox.

Spawned per connection by the sandbox bridge's socat listener
(``net-*/mcp-<name>.sock``); serves stdio MCP until EOF.  Tool schemas
are relayed verbatim: this module knows nothing about any particular
upstream server.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any, NoReturn

import mcp.types as types
from mcp import ClientSession
from mcp.server.stdio import stdio_server  # stdio_server lives in mcp.server.stdio
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.server import Server


def _read_token(token_file: str) -> str:
    """Read the single-line token, stripped; exit nonzero if unusable."""
    path = os.path.expanduser(token_file)
    try:
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
    except OSError as exc:
        raise SystemExit(f"tkt.remote_mcp_proxy: cannot read token file {path}: {exc}")
    if not token:
        raise SystemExit(f"tkt.remote_mcp_proxy: token file {path} is empty")
    return token


async def _serve(url: str, headers: dict[str, str]) -> None:
    """Relay stdio JSON-RPC to the upstream, verbatim, with ``headers``."""
    # streamable_http_client does not close a caller-provided client, so
    # own its lifecycle here.
    async with create_mcp_http_client(headers=headers) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _get_session_id):
            async with ClientSession(read, write) as upstream:
                await upstream.initialize()
                server = Server("tkt-remote-mcp")

                @server.list_tools()
                async def list_tools() -> list[types.Tool]:
                    return (await upstream.list_tools()).tools

                @server.call_tool()
                async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
                    return await upstream.call_tool(name, arguments)

                async with stdio_server() as (client_read, client_write):
                    await server.run(client_read, client_write, server.create_initialization_options())


def main() -> NoReturn:
    parser = argparse.ArgumentParser(prog="tkt.remote_mcp_proxy")
    parser.add_argument("--url", required=True, help="Remote streamable-HTTP MCP endpoint")
    parser.add_argument("--token-file", required=True, help="Host-side single-line token file")
    parser.add_argument(
        "--header",
        default="Bearer {token}",
        help="Authorization value template; {token} is replaced with the file contents",
    )
    args = parser.parse_args()
    headers = {"Authorization": args.header.format(token=_read_token(args.token_file))}
    asyncio.run(_serve(args.url, headers))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_remote_mcp_proxy.py -v`
Expected: 5 PASS.

- [ ] **Step 6: Lint and commit**

```bash
ruff check . && ruff format --check . && mypy tkt/
git add tkt/remote_mcp_proxy.py tests/fake_github_mcp.py tests/test_remote_mcp_proxy.py
git commit -m "feat: host-side stdio<->streamable-HTTP MCP proxy with auth injection"
```

---

### Task 2: Sandbox wiring — config, bridge listener, OpenCode override

**Files:**

- Modify: `tkt/sandbox.py` (`session_mcp_config` at ~82–98; `Sandbox.__init__` at ~327–342; `from_json_data` at ~344–368; `_start_bridge` at ~593–692; `_build_common_argv` session-env block at ~913–924)
- Modify: `tests/test_sandbox.py` (import at ~41–48; `test_session_mcp_config_has_no_plugin_key` at ~331–336; add new tests near the existing bridge tests ~258–330)

**Interfaces:**

- Consumes: `python -m tkt.remote_mcp_proxy --url --token-file [--header]` from Task 1.
- Produces:
  - `Sandbox.__init__(..., remote_mcp: dict[str, dict[str, str]] | None = None)` — entries have keys `url`, `token_file`, optional `header`.
  - `Sandbox._bridged_remote_mcp() -> dict[str, dict[str, str]]` — entries whose token file exists on the host.
  - `opencode_config_content(net_dir: str, remote_mcp_names: Iterable[str] = ()) -> dict[str, Any]` — replaces `session_mcp_config` (renamed; no back-compat alias).
  - Bridge sockets named `mcp-<name>.sock` in `net_dir`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_sandbox.py`, update the import (line ~47): replace `session_mcp_config` with `opencode_config_content` in the `from tkt.sandbox import (...)` block, and replace `test_session_mcp_config_has_no_plugin_key` with:

```python
def test_opencode_config_content_wires_bridged_mcp():
    """Session socket plus one local socat override per bridged remote MCP."""
    cfg = opencode_config_content("/x/net-1", ["github"])
    assert set(cfg) == {"mcp"}  # the env-config plugin channel is dead: mcp-only
    assert cfg["mcp"]["tkt-session"]["command"][-1] == "UNIX-CONNECT:/x/net-1/session.sock"
    assert cfg["mcp"]["github"]["type"] == "local"
    assert cfg["mcp"]["github"]["command"][-1] == "UNIX-CONNECT:/x/net-1/mcp-github.sock"
    # no names => session only (unchanged behavior)
    assert set(opencode_config_content("/x/net-1")["mcp"]) == {"tkt-session"}
```

(Keep the intent of the removed test — `set(cfg) == {"mcp"}` — as shown above.)

Add new tests after `test_start_bridge_session_listener`:

```python
def test_from_json_data_parses_remote_mcp():
    """remote_mcp config parses; missing keys and extra keys are rejected."""
    ok = Sandbox.from_json_data(
        {"command": [], "remote_mcp": {"github": {"url": "https://x/mcp/", "token_file": "~/tok"}}}
    )
    assert ok._remote_mcp == {"github": {"url": "https://x/mcp/", "token_file": "~/tok"}}
    # absent => empty
    assert Sandbox.from_json_data({"command": []})._remote_mcp == {}
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "remote_mcp": {"github": {"url": "x"}}})
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "remote_mcp": {"github": {"token_file": "t"}}})
    with pytest.raises(ValueError):
        Sandbox.from_json_data(
            {"command": [], "remote_mcp": {"github": {"url": "x", "token_file": "t", "bogus": 1}}}
        )
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "remote_mcp": ["github"]})


def test_bridged_remote_mcp_requires_token_file(tmp_path):
    """Only entries whose host token file exists are bridged."""
    tok = tmp_path / "tok"
    sb = Sandbox(command=[], remote_mcp={"github": {"url": "u", "token_file": str(tok)}})
    assert sb._bridged_remote_mcp() == {}
    tok.write_text("t\n")
    assert set(sb._bridged_remote_mcp()) == {"github"}


def test_start_bridge_remote_mcp_listener(workspace, tmp_path, monkeypatch):
    """A bridged entry gets an mcp-<name>.sock listener; an unbridged one
    does not and must not stall or fail bridge startup."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tok = tmp_path / "tok"
    tok.write_text("secret\n")
    sandbox = Sandbox(
        command=[],
        port=18089,
        remote_mcp={
            "github": {"url": "http://127.0.0.1:1/mcp", "token_file": str(tok)},
            "nope": {"url": "http://127.0.0.1:1/mcp", "token_file": str(tmp_path / "absent")},
        },
    )
    net_dir, procs = sandbox._start_bridge(str(tmp_path / "ws"))
    try:
        assert os.path.exists(os.path.join(net_dir, "mcp-github.sock"))
        assert not os.path.exists(os.path.join(net_dir, "mcp-nope.sock"))
    finally:
        sandbox._stop_bridge(net_dir, procs)
    assert not os.path.exists(net_dir)


def test_common_argv_injects_remote_mcp_override(workspace, tmp_path, monkeypatch):
    """Restricted runs redirect the bridged server key to its socket;
    unbridged entries get no override (host config entry stays)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tok = tmp_path / "tok"
    tok.write_text("secret\n")
    net_dir = tmp_path / "net-r"
    net_dir.mkdir()
    remote = {
        "github": {"url": "https://api.githubcopilot.com/mcp/", "token_file": str(tok)},
        "ghost": {"url": "https://example.invalid/mcp/", "token_file": str(tmp_path / "absent")},
    }
    argv = Sandbox(command=[], remote_mcp=remote)._build_common_argv(
        home=str(tmp_path),
        mounts=[],
        inner="exec true",
        network=False,
        net_dir=str(net_dir),
        ports=(18089,),
    )
    cfg = json.loads(argv[argv.index("OPENCODE_CONFIG_CONTENT") + 1])
    assert cfg["mcp"]["github"]["command"][-1] == f"UNIX-CONNECT:{net_dir / 'mcp-github.sock'}"
    assert "ghost" not in cfg["mcp"]
    # the token value never appears in restricted-mode argv
    assert "secret" not in " ".join(argv)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_sandbox.py -v -k "remote_mcp or opencode_config"`
Expected: import error on `opencode_config_content`, then failures for `unexpected keyword 'remote_mcp'` / missing `_bridged_remote_mcp`.

- [ ] **Step 3: Implement in `tkt/sandbox.py`**

3a. Rename `session_mcp_config` → `opencode_config_content` and extend (docstring kept informative; the wake-plugin rationale paragraph stays):

```python
def opencode_config_content(net_dir: str, remote_mcp_names: Iterable[str] = ()) -> dict[str, Any]:
    """OpenCode config (OPENCODE_CONFIG_CONTENT) wiring the bridge MCP servers.

    The session socket plus one ``local`` socat override per bridged
    remote-MCP name; the override key matches the host config's server
    key (e.g. ``github``) so the merged-last env config replaces the
    host's unreachable remote entry.  Only the ``mcp`` block goes
    through this channel: the ``plugin`` array in env config is NOT
    honored by the installed OpenCode (its entries are npm package
    specs), so the wake plugin is delivered as a real copied file in
    ``~/.config/opencode/plugin/`` instead (see tkt.install).
    """

    def local(sock: str) -> dict[str, Any]:
        return {"type": "local", "command": ["socat", "-", f"UNIX-CONNECT:{sock}"]}

    mcp: dict[str, Any] = {"tkt-session": local(os.path.join(net_dir, SESSION_SOCKET_NAME))}
    for name in remote_mcp_names:
        mcp[name] = local(os.path.join(net_dir, f"mcp-{name}.sock"))
    return {"mcp": mcp}
```

3b. `Sandbox.__init__`: add keyword parameter `remote_mcp: dict[str, dict[str, str]] | None = None` after `port`, store `self._remote_mcp = dict(remote_mcp) if remote_mcp else {}`, and extend the class docstring with a `remote_mcp` entry:

```
    remote_mcp
        Mapping of MCP server name to ``{"url": ..., "token_file": ...,
        "header": ...}`` (``header`` optional, default ``"Bearer {token}"``)
        for remote streamable-HTTP MCP servers to front in restricted
        sandboxes.  Each entry with an existing host token file gets a
        host-side ``tkt.remote_mcp_proxy`` listener at
        ``<net_dir>/mcp-<name>.sock`` and an OPENCODE_CONFIG_CONTENT
        override of the same key; the token never enters the sandbox.
        Full-network (``network=True``) runs do not front remote MCP
        servers at all (no token export either).
```

3c. `from_json_data`: after the `port` pop, add:

```python
        remote_mcp = data.pop("remote_mcp", {})
        if not isinstance(remote_mcp, dict):
            raise ValueError(f"'remote_mcp' must be a mapping, got {remote_mcp!r}.")
        for name, entry in remote_mcp.items():
            if not isinstance(entry, dict) or not {"url", "token_file"} <= set(entry):
                raise ValueError(f"remote_mcp entry {name!r} needs 'url' and 'token_file': {entry!r}.")
            if set(entry) - {"url", "token_file", "header"}:
                raise ValueError(f"Unexpected keys in remote_mcp entry {name!r}: {sorted(entry)}.")
```

and pass `remote_mcp=remote_mcp` to `cls(...)`.

3d. Add the helper method to `Sandbox` (e.g. just above `_start_bridge`):

```python
    def _bridged_remote_mcp(self) -> dict[str, dict[str, str]]:
        """Remote-MCP entries whose host token file exists (bridged now).

        Checked at bridge-start and argv-build time in the same process,
        so the two views cannot diverge in practice.
        """
        return {
            name: entry
            for name, entry in self._remote_mcp.items()
            if os.path.isfile(os.path.expanduser(entry["token_file"]))
        }
```

3e. In `_start_bridge`, after the `session.sock` listener block and **before** the readiness-wait comment, add (and append sockets to `sock_paths` so the existing 5 s readiness check and early-exit error cover them — unbridged entries add nothing to `sock_paths`, matching the test):

```python
            for name, entry in self._bridged_remote_mcp().items():
                remote_sock = os.path.join(net_dir, f"mcp-{name}.sock")
                remote_log = os.path.join(net_dir, f"host-mcp-{name}-socat.log")
                server_cmd = [
                    sys.executable,
                    "-m",
                    "tkt.remote_mcp_proxy",
                    "--url",
                    entry["url"],
                    "--token-file",
                    os.path.expanduser(entry["token_file"]),
                ]
                if "header" in entry:
                    server_cmd += ["--header", entry["header"]]
                sock_paths.append(remote_sock)
                with open(remote_log, "w", encoding="utf-8") as f:
                    procs.append(
                        subprocess.Popen(
                            [
                                "socat",
                                f"UNIX-LISTEN:{remote_sock},fork",
                                _socat_system_arg(server_cmd),
                            ],
                            stdout=f,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                            preexec_fn=_set_pdeathsig,
                        )
                    )
```

Extend the early-exit log-gathering list (the `logs = [...]` line ~668) with the per-server logs:

```python
                logs = [os.path.join(net_dir, f"host-llm-{p}.log") for p in self._ports] + [
                    os.path.join(net_dir, "host-session-socat.log")
                ] + [os.path.join(net_dir, f"host-mcp-{n}-socat.log") for n in self._bridged_remote_mcp()]
```

Also extend the `_start_bridge` docstring bullet list with one bullet describing the `mcp-<name>.sock` listeners (spawn one host-side `tkt.remote_mcp_proxy` per connection to front remote MCP servers; token stays host-side).

3f. In `_build_common_argv`, update the session-env block (line ~918) to:

```python
            argv += [
                "--setenv",
                "OPENCODE_CONFIG_CONTENT",
                json.dumps(opencode_config_content(net_dir, self._bridged_remote_mcp())),
            ]
```

(keeps its position after the configured `env` block — tkt-owned, local.jsonc cannot clobber).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_sandbox.py -v`
Expected: all PASS (existing session-bridge/env tests unaffected; the updated import and the replaced config test included).

- [ ] **Step 5: Lint and commit**

```bash
ruff check . && ruff format --check . && mypy tkt/
git add tkt/sandbox.py tests/test_sandbox.py
git commit -m "feat: bridge configured remote MCP servers into restricted sandboxes"
```

---

### Task 3: local.jsonc stanza, docs, no-token-anywhere guard, final gate

**Files:**

- Modify: `tests/test_sandbox.py` (append a guard test near `test_full_network_argv_shares_host_network` ~206)
- Modify: `local.jsonc` (add `remote_mcp` stanza inside `tools.sandbox`, after `"port"`)
- Modify: `AGENTS.md` (sandbox.py bullet + new `tkt/remote_mcp_proxy.py` bullet)

**Interfaces:**

- Consumes: `Sandbox._remote_mcp` from Task 2 (constructor parameter only).
- Produces: `local.jsonc` `tools.sandbox.remote_mcp.github` stanza; regression guard that **no** mode (restricted or full-network) ever places a token value or a remote-MCP override in the bwrap argv (spec decision D3: full-network is unsupported by design).

- [ ] **Step 1: Write the guard test**

```python
def test_no_remote_mcp_token_or_override_in_full_network_argv(tmp_path, monkeypatch):
    """Full-network runs are unsupported for remote MCP by design (D3):
    no token export, no config override — and restricted runs already
    carry only the socket override, never the token (Task 2 test)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tok = tmp_path / "tok"
    tok.write_text("ghp_secret\n")
    remote = {"github": {"url": "https://api.githubcopilot.com/mcp/", "token_file": str(tok)}}
    argv = Sandbox(command=[], remote_mcp=remote)._build_common_argv(
        home=str(tmp_path),
        mounts=[],
        inner="exec true",
        network=True,
    )
    joined = " ".join(argv)
    assert "ghp_secret" not in joined
    assert "GITHUB_TOKEN" not in joined
    assert "mcp-github.sock" not in joined  # no override without the bridge
```

- [ ] **Step 2: Run it to verify it passes (regression guard, no code change)**

Run: `python -m pytest tests/test_sandbox.py::test_no_remote_mcp_token_or_override_in_full_network_argv -v`
Expected: PASS immediately — this pins the D3 decision (it exists to fail if a future change re-adds token export). If it unexpectedly fails, Task 2 leaked token material into a mode where it must not appear; fix Task 2 before continuing.

- [ ] **Step 3: Add the `local.jsonc` stanza**

In `local.jsonc`, inside `tools.sandbox`, immediately after `"port": [8080, 8090],`:

```jsonc
      "remote_mcp": {
        "github": {
          "url": "https://api.githubcopilot.com/mcp/",
          "token_file": "~/.github-mcp-token"
        }
      },
```

(Manual, human-side — do not generate or commit a token: create the fine-grained PAT at
<https://github.com/settings/tokens?type=flow> and `printf '%s' <pat> > ~/.github-mcp-token && chmod 600 ~/.github-mcp-token`.)

- [ ] **Step 4: Update repo `AGENTS.md`**

Extend the `tkt/sandbox.py` bullet's sentence on the restricted bridge:

> Restricted runs also front each configured `remote_mcp` server (e.g. GitHub) with a per-launch `mcp-<name>.sock` listener spawning a host-side `tkt.remote_mcp_proxy` (token injected on the host only), redirected from OpenCode's same-key config entry via `OPENCODE_CONFIG_CONTENT`; full-network (`--network`) runs do not front remote MCP at all.

Add a file-layout bullet after the `session_server`-related entries:

> - **`tkt/remote_mcp_proxy.py`** — host-side stdio↔streamable-HTTP MCP proxy spawned per connection by the bridge socat; relays `tools/list`/`tools/call` verbatim to a remote MCP server with an injected `Authorization` header so the PAT never enters the sandbox.

- [ ] **Step 5: Full verification**

```bash
python -m pytest
ruff check . && ruff format --check . && mypy tkt/
```

Expected: all green.

- [ ] **Step 6: Manual smoke (record the outcome in the commit message body)**

With the token file in place: `tkt sandbox-run --shell` (or a normal run) inside a workspace, ask the agent to call the GitHub MCP `whoami`-equivalent (`gh_whoami` in the current catalog) and confirm it returns the authenticated user. This doubles as the spec's build-phase probe of `OPENCODE_CONFIG_CONTENT` merge semantics: on success the sandboxed OpenCode must treat `github` as a *local* command (`socat - UNIX-CONNECT:.../net-*/mcp-github.sock`), not the remote URL. On failure, inspect the newest `~/.local/state/tkt/net-*/host-mcp-github-socat.log`.

- [ ] **Step 7: Lint gates and commit**

```bash
git add tests/test_sandbox.py local.jsonc AGENTS.md
git commit -m "feat: configure GitHub MCP host-fronting; guard token absence in all modes"
```
