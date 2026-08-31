# tkt MCP Server (sandboxed `bash` tool) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `tkt mcp-server` command that runs a FastMCP stdio server exposing a `bash` tool which executes commands inside the same `bwrap`-sandboxed, warm-but-stateless environment that `tkt sandbox-run` provides.

**Architecture:** A long-lived `bwrap` "warm holder" (one per project, lazily spawned) runs the conda/EUPS setup once; each `bash` tool call runs the command in a **fresh child** `bash -lc` that inherits the warm env + exported functions, then exits. The server tracks the working directory across calls via end-of-call `pwd`. Baseline, mounts, and workspace-vs-repo detection are reused from the existing `tkt.sandbox.Sandbox` and `tkt._cli.sandbox_run`.

**Tech Stack:** Python 3.13, `mcp` official SDK (`FastMCP`), Pydantic models, `bwrap`, `click`, existing `tkt` code (`Sandbox`, `Environment`, `Workspace`). `fastmcp` already installed by the user.

**Spec:** `docs/superpowers/specs/2026-08-31-tkt-mcp-server-design.md`

## Global Constraints

- Python 3.13; deps that already exist: `click`, `GitPython`, `pyyaml`, `json5`. New dep: `mcp` (official SDK, `mcp.server.fastmcp`). Do **not** add packaging config.
- License: BSD-3-Clause. Every new `.py` file MUST carry the exact license header from `tkt/__init__.py` (lines 1-23) at the top.
- Numpy-style docstrings, doc-length 79, line-length 110 (ruff). Run `ruff check .`, `ruff format --check .`, `mypy tkt/` before committing.
- FastMCP builds the MCP tool schema from Pydantic type hints; Pydantic models are the source of truth.
- The warm holder uses network-restricted mode with **no** LLM socat bridge (no LLM lives inside the sandbox).
- Tests use pytest, in `tests/`, mirroring `tests/test_sandbox.py` conventions (inspect built argv, do not run `bwrap`).

---

### Task 1: Refactor `Sandbox` to expose a warm-holder argv builder

**Files:**

- Modify: `tkt/sandbox.py` (add `_workspace_mounts`, `_single_repo_mounts`, `warm_holder_argv`; add `bridge_llm` param to `_build_common_argv`)
- Test: `tests/test_sandbox.py`

**Interfaces:**

- Consumes: existing `Sandbox`, `_build_common_argv`, `Workspace`.
- Produces: `Sandbox.warm_holder_argv(*, workspace=None, repo_dir=None, inner: str, network: bool = False) -> list[str]` returning a bwrap argv whose inner script is `inner` (the warm driver), with the mode-appropriate mounts and config mounts, and no LLM socat bridge.

- [ ] **Step 1: Extract the workspace mount list into `_workspace_mounts`.**

Read `tkt/sandbox.py` `_build_bwrap_argv` (currently ~L600-639). Extract the mount-building block into a helper so both the existing method and the new warm-holder builder can share it:

```python
def _workspace_mounts(self, workspace: "Workspace") -> list[str]:
    """Build the mount list for a tkt workspace.

    The agent directory is the only writable location in the workspace; main
    workspace, per-package ``.git`` dirs are writable, and externals are
    read-only.
    """
    mounts: list[str] = []
    mounts += ["--ro-bind", workspace.directory, workspace.directory]
    agent_dir = os.path.join(workspace.directory, AGENT_SUBDIR)
    mounts += ["--bind", agent_dir, agent_dir]
    for package in workspace.packages:
        package_dir = os.path.join(workspace.directory, package)
        git_dir = os.path.join(package_dir, ".git")
        if os.path.exists(package_dir):
            mounts += ["--ro-bind", package_dir, package_dir]
            mounts += ["--bind", git_dir, git_dir]
    for external_path in workspace.externals.values():
        if os.path.exists(external_path):
            mounts += ["--ro-bind", external_path, external_path]
        else:
            logging.warning(f"External path {external_path} does not exist; skipping mount.")
    return mounts
```

(Replace the inline mount-building in `_build_bwrap_argv` with `mounts = self._workspace_mounts(workspace)`.)

- [ ] **Step 2: Add `_single_repo_mounts`.**

```python
def _single_repo_mounts(self, repo_dir: str) -> list[str]:
    """Build the mount list for a single repository (not a ticket workspace)."""
    return ["--bind", repo_dir, repo_dir]
```

(Replace the inline `mounts: list[str] = ["--bind", repo_dir, repo_dir]` in `_build_single_repo_argv`.)

- [ ] **Step 3: Add `bridge_llm` parameter to `_build_common_argv`.**

Change the `_build_common_argv` signature to add `bridge_llm: bool = True` after `network`. Wrap the socat bridge block (the `if not network:` block that appends `--bind net_dir` and the in-sandbox socats) so it only runs when bridging is wanted:

```python
        # Mode-specific mounts go between the base and config mounts.
        argv += mounts
        if not network and bridge_llm:
            if net_dir is None or not os.path.isdir(net_dir):
                raise ValueError("restricted network requires a valid net_dir socket directory")
            # ... existing socat bridge code unchanged ...
        # (the rest of _build_common_argv unchanged: config mounts, env, namespaces)
```

When `bridge_llm=False` and `network=False`, the sandbox gets `--unshare-net` (restricted network) but no socat bridge and no `net_dir` requirement. Pass `bridge_llm=False` in the new warm-holder builder.

- [ ] **Step 4: Add `warm_holder_argv`.**

```python
def warm_holder_argv(
    self,
    *,
    workspace: "Workspace | None" = None,
    repo_dir: str | None = None,
    inner: str,
    network: bool = False,
) -> list[str]:
    """Build a bwrap argv for a long-lived warm holder running ``inner``.

    Exactly one of ``workspace`` or ``repo_dir`` must be given. The warm holder
    uses the same mount model as the corresponding sandbox mode but runs the
    provided driver script ``inner`` instead of a fixed command, and is
    network-restricted with no LLM bridge.
    """
    home = os.path.expanduser("~")
    if workspace is not None and repo_dir is None:
        mounts = self._workspace_mounts(workspace)
    elif repo_dir is not None and workspace is None:
        mounts = self._single_repo_mounts(repo_dir)
    else:
        raise ValueError("Exactly one of workspace or repo_dir must be provided.")
    return self._build_common_argv(
        home=home,
        mounts=mounts,
        inner=inner,
        network=network,
        bridge_llm=False,
    )
```

- [ ] **Step 5: Add a test for `warm_holder_argv`.**

In `tests/test_sandbox.py`, add, using the existing `workspace` fixture and `_inner_of` helper:

```python
def test_warm_holder_argv_workspace(workspace):
    tool = Sandbox(command=["opencode", "acp"])
    argv = tool.warm_holder_argv(
        workspace=workspace,
        inner='while read line; do echo "$line"; done',
    )
    # agent dir is writable
    assert "--bind" in argv
    assert os.path.join(workspace.directory, ".agent") in argv
    # restricted network, no socat bridge
    assert "--unshare-net" in argv
    assert "socat" not in " ".join(_inner_of(argv))
    assert _inner_of(argv) == 'while read line; do echo "$line"; done'


def test_warm_holder_argv_single_repo(tmp_path):
    tool = Sandbox(command=["opencode", "acp"])
    argv = tool.warm_holder_argv(
        repo_dir=str(tmp_path),
        inner="echo hi",
    )
    assert "--bind" in argv
    assert str(tmp_path) in argv
```

- [ ] **Step 6: Run the sandbox tests.**

Run: `python -m pytest tests/test_sandbox.py -v`
Expected: PASS (including new tests).

- [ ] **Step 7: Commit.**

```bash
git add tkt/sandbox.py tests/test_sandbox.py
git commit -m "feat(sandbox): expose warm-holder argv builder with no LLM bridge"
```

---

### Task 2: Framing helpers and warm driver script

**Files:**

- Create: `tkt/mcp_server.py` (framing helpers + driver builder only, this task)
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: nothing.
- Produces:
  - `encode_field(text: str) -> str` — base64 (no newlines).
  - `decode_field(field: str) -> str` — inverse.
  - `build_driver_script(setup_lines: list[str]) -> str` — the bash driver.
  - `parse_result_line(line: str) -> BashResult`-compatible tuple/frame — reuse in Task 3.

- [ ] **Step 1: Create `tkt/mcp_server.py` with license header and framing helpers.**

Create the file with the full BSD-3-Clause header (copy lines 1-23 of `tkt/__init__.py` verbatim). Then:

```python
from __future__ import annotations

__all__ = ("build_driver_script", "encode_field", "decode_field", "parse_result_line", "BashRequest", "BashResult", "WarmSandbox", "run_server")

import base64
import json
from typing import Any

from pydantic import BaseModel


class BashRequest(BaseModel):
    command: str
    timeout_ms: int | None = None
    description: str | None = None


class BashResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


def encode_field(text: str) -> str:
    """Base64-encode ``text`` with no newlines, so it is safe on one line."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decode_field(field: str) -> str:
    """Inverse of :func:`encode_field`."""
    return base64.b64decode(field.encode("ascii")).decode("utf-8")
```

- [ ] **Step 2: Add `parse_result_line`.**

```python
def parse_result_line(line: str) -> dict[str, Any]:
    """Parse one driver result line into a dict.

    The driver emits 4 space-separated base64 fields: stdout, stderr,
    exit_code, cwd. base64 has no spaces, so splitting on a single space is
    unambiguous.
    """
    parts = line.split(" ")
    if len(parts) != 4:
        raise ValueError(f"Malformed result line: {line!r}")
    stdout, stderr, exit_code, cwd = (decode_field(p) for p in parts)
    return {"stdout": stdout, "stderr": stderr, "exit_code": int(exit_code), "cwd": cwd}
```

- [ ] **Step 3: Add `build_driver_script`.**

```python
SETUP_WORKSPACE_TEMPLATE = "setup -r .agent"
SETUP_SINGLE_REPO_TEMPLATE = "setup -r ."


def build_driver_script(setup_lines: list[str]) -> str:
    """Build the warm-holder driver bash script.

    ``setup_lines`` run once at startup (conda activation + EUPS setup). Then a
    loop reads two base64 lines per request (cwd, then command), runs the
    command in a fresh ``bash -lc`` child, and emits one result line of 4
    space-separated base64 fields (stdout, stderr, exit_code, cwd).
    """
    setup = "\n".join(setup_lines)
    return (
        f"{setup}\n"
        "while IFS= read -r cwd_b64 && IFS= read -r cmd_b64; do\n"
        "    cwd=$(printf '%s' \"$cwd_b64\" | base64 -d)\n"
        "    cmd=$(printf '%s' \"$cmd_b64\" | base64 -d)\n"
        "    cd \"$cwd\" 2>/dev/null || true\n"
        "    out=$(mktemp)\n"
        "    errf=$(mktemp)\n"
        "    bash -lc -- \"$cmd\" >\"$out\" 2>\"$errf\"\n"
        "    rc=$?\n"
        "    cur=$(pwd)\n"
        "    printf '%s %s %s %s\\n' \\\n"
        "        \"$(base64 -w0 < \"$out\")\" \\\n"
        "        \"$(base64 -w0 < \"$errf\")\" \\\n"
        "        \"$(printf '%s' \"$rc\" | base64 -w0)\" \\\n"
        "        \"$(printf '%s' \"$cur\" | base64 -w0)\"\n"
        "    rm -f \"$out\" \"$errf\"\n"
        "done\n"
    )
```

- [ ] **Step 4: Add tests for framing + driver.**

Create `tests/test_mcp_server.py` (license header), then:

```python
import base64

from tkt.mcp_server import build_driver_script, decode_field, encode_field, parse_result_line


def test_encode_decode_roundtrip():
    for text in ("hello", "multi\nline\n", "unicode \u00e9", "quote's \" and $vars"):
        assert decode_field(encode_field(text)) == text


def test_parse_result_line():
    line = " ".join(
        encode_field(field) for field in ("out", "err", "3", "/some/cwd")
    )
    parsed = parse_result_line(line)
    assert parsed == {
        "stdout": "out",
        "stderr": "err",
        "exit_code": 3,
        "cwd": "/some/cwd",
    }


def test_build_driver_script_runs_setup_once():
    script = build_driver_script(["conda activate env", "setup -r .agent"])
    assert "conda activate env" in script
    assert "setup -r .agent" in script
    assert script.count("bash -lc --") == 1
    assert "base64" in script
```

- [ ] **Step 5: Run the new tests.**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: PASS.

- [ ] **Step 6: Commit.**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add framing helpers and warm driver script builder"
```

---

### Task 3: `WarmSandbox` class — lazy spawn, framing, cwd session

**Files:**

- Modify: `tkt/mcp_server.py` (add `WarmSandbox`)
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: Task 1 `warm_holder_argv`, Task 2 framing helpers + `build_driver_script`.
- Produces: `WarmSandbox(sandbox, *, workspace=None, repo_dir=None, cwd: str)`, with `run(command, *, timeout_ms=None) -> BashResult` and `.cwd` property. Lazily spawns the bwrap holder on first `run`.

- [ ] **Step 1: Add `WarmSandbox`.**

```python
import os
import subprocess
import sys


class WarmSandbox:
    """A long-lived bwrap holder that runs commands in fresh child shells.

    Spawns the bwrap holder lazily on first :meth:`run`. The holder runs the
    conda/EUPS setup once; each call runs the command in a fresh ``bash -lc``
    child that inherits the warm environment. The working directory is tracked
    server-side from the end-of-call ``pwd``.
    """

    def __init__(
        self,
        sandbox,
        *,
        workspace=None,
        repo_dir=None,
        cwd: str,
        conda_env: str | None = None,
        timeout_ms: int = 60000,
    ) -> None:
        self._sandbox = sandbox
        self._workspace = workspace
        self._repo_dir = repo_dir
        self._conda_env = conda_env
        self._cwd = cwd
        self._default_timeout_ms = timeout_ms
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def cwd(self) -> str:
        return self._cwd

    def _start(self) -> None:
        from .sandbox import Sandbox

        assert isinstance(self._sandbox, Sandbox)
        setup_lines = self._setup_lines(self._conda_env)
        inner = build_driver_script(setup_lines)
        argv = self._sandbox.warm_holder_argv(
            workspace=self._workspace,
            repo_dir=self._repo_dir,
            inner=inner,
        )
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def _setup_lines(self, conda_env: str | None = None) -> list[str]:
        """Build the one-time setup lines for the warm holder.

        Mirrors ``tkt.sandbox._build_inner_script``: conda is activated only
        when a conda env name is available (``LSST_CONDA_ENV_NAME`` or a
        provided override), and the Rubin environment is set up with EUPS.
        """
        lines: list[str] = []
        if conda_env is not None:
            lines.append(
                "source $CONDA_PREFIX/etc/profile.d/conda.sh "
                "|| source $(dirname $(dirname $(which conda)))/etc/profile.d/conda.sh"
            )
            lines.append(f"conda activate {conda_env}")
        if self._workspace is not None:
            lines.append("setup -r .agent")
        elif self._repo_dir is not None and os.path.isdir(
            os.path.join(self._repo_dir, "ups")
        ):
            lines.append("setup -r .")
        return lines

    def run(self, command: str, *, timeout_ms: int | None = None) -> BashResult:
        if self._proc is None:
            self._start()
        assert self._proc is not None and self._proc.stdin is not None
        assert self._proc.stdout is not None
        if timeout_ms is None:
            timeout_ms = self._default_timeout_ms
        # Write cwd + command as two base64 lines.
        self._proc.stdin.write(
            encode_field(self._cwd) + "\n" + encode_field(command) + "\n"
        )
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("Warm holder exited unexpectedly.")
        frame = parse_result_line(line.strip())
        self._cwd = frame["cwd"]
        return BashResult(
            stdout=frame["stdout"],
            stderr=frame["stderr"],
            exit_code=frame["exit_code"],
        )
```

(Note: `timeout_ms` handling — killing a hung command is deferred to a later task/follow-up; the driver currently runs synchronously. `timeout_ms` is accepted and stored but the child is not yet forcibly killed. This is a known scope note carried in the design; do not block on it. `BashResult.timed_out` stays `False` for now.)

- [ ] **Step 2: Add a test that `run` frames and updates cwd.**

Use `unittest.mock.patch` to fake the subprocess so we don't launch `bwrap`:

```python
from unittest import mock

import pytest

from tkt.mcp_server import BashResult, WarmSandbox, encode_field


def _fake_proc():
    proc = mock.Mock()
    cwd_field = encode_field("/fake/cwd")
    out_field = encode_field("hello out")
    err_field = encode_field("hello err")
    rc_field = encode_field("0")
    proc.stdout.readline.return_value = (
        f"{out_field} {err_field} {rc_field} {cwd_field}\n"
    ).encode()
    proc.stdin = mock.Mock()
    return proc


def test_warm_sandbox_run_frames_command_and_tracks_cwd(tmp_path):
    from tkt.sandbox import Sandbox

    sandbox = Sandbox(command=["opencode", "acp"])  # real Sandbox; _start asserts type
    sandbox.warm_holder_argv = mock.Mock(return_value=["bwrap", "args"])
    with mock.patch("tkt.mcp_server.subprocess.Popen", return_value=_fake_proc()):
        ws = WarmSandbox(sandbox, repo_dir=str(tmp_path), cwd="/start")
        result = ws.run("echo hi")
    assert isinstance(result, BashResult)
    assert result.stdout == "hello out"
    assert result.stderr == "hello err"
    assert result.exit_code == 0
    assert ws.cwd == "/fake/cwd"
    # the written stdin payload encodes the current cwd then the command
    payload = ws._proc.stdin.write.call_args[0][0].decode()
```

- [ ] **Step 3: Run tests.**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: PASS.

- [ ] **Step 4: Commit.**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add lazy WarmSandbox with cwd session tracking"
```

---

### Task 4: FastMCP app, `bash` tool, and `tkt mcp-server` CLI command

**Files:**

- Modify: `tkt/mcp_server.py` (add `run_server`), `tkt/_cli.py` (add `mcp-server` command)
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: Task 2 `BashRequest`/`BashResult`, Task 3 `WarmSandbox`.
- Produces: `run_server(sandbox, *, workspace=None, repo_dir=None, cwd: str, env: Environment) -> None`; CLI command `tkt mcp-server` that detects mode from cwd and calls `run_server`.

- [ ] **Step 1: Add `run_server` to `tkt/mcp_server.py`.**

```python
from mcp.server.fastmcp import FastMCP


def run_server(
    sandbox,
    *,
    cwd: str,
    workspace=None,
    repo_dir=None,
    conda_env: str | None = None,
) -> None:
    """Run the FastMCP stdio server exposing the ``bash`` tool.

    ``sandbox`` is the configured ``tkt.sandbox.Sandbox`` tool. ``cwd`` is the
    project root (Zed spawns this process with cwd = project root). Warm start
    is lazy: the holder spawns on the first ``bash`` call.
    """
    warm = WarmSandbox(
        sandbox,
        workspace=workspace,
        repo_dir=repo_dir,
        cwd=cwd,
        conda_env=conda_env,
    )
    mcp = FastMCP(name="tkt")

    @mcp.tool()
    def bash(request: BashRequest) -> BashResult:
        """Run a shell command inside the tkt sandbox.

        Args:
            request: The command to run and optional timeout/description.
        """
        result = warm.run(request.command, timeout_ms=request.timeout_ms)
        return result

    mcp.run()
```

- [ ] **Step 2: Add the `mcp-server` CLI command to `tkt/_cli.py`.**

Adding after `sandbox_run` (~L392). Mirror `sandbox_run`'s env/config loading and mode detection:

```python
@cli.command("mcp-server", help="Run the MCP stdio server exposing a sandboxed bash tool.")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, writable=True, resolve_path=True),
)
@click.option("--conda-env", type=str, default=None)
@click.option("-v", "--verbose", count=True)
def mcp_server(
    *,
    environment: TextIO | None,
    directory: str | None,
    conda_env: str | None,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    from .mcp_server import run_server
    from .sandbox import Sandbox

    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    cwd = os.path.abspath(".")
    if os.path.isdir(os.path.join(cwd, ".agent")):
        # Workspace mode.
        env = Environment.from_file(environment)
        workspace = Workspace.from_existing(
            ticket=None, directory=directory, environment=env
        )
        tool = env.get_tool("sandbox")
        if tool is None or not isinstance(tool, Sandbox):
            raise click.UsageError("No configured 'sandbox' tool (tkt.sandbox.Sandbox).")
        run_server(tool, cwd=workspace.directory, workspace=workspace, conda_env=conda_env)
    else:
        # Single-repo mode.
        cls, data = Environment.load_config(environment)
        tools = cls.load_tools(data)
        sandbox = tools.get("sandbox")
        if sandbox is None or not isinstance(sandbox, Sandbox):
            raise click.UsageError("No configured 'sandbox' tool (tkt.sandbox.Sandbox).")
        repo_dir = directory if directory is not None else cwd
        run_server(sandbox, cwd=repo_dir, repo_dir=repo_dir, conda_env=conda_env)
```

NOTE: `Workspace.from_existing` accepts `ticket=None` when `directory` is given (it then locates the workspace by walking up to find `tkt.json`). The conda env to activate, if any, is passed via `--conda-env`; the user typically supplies `$LSST_CONDA_ENV_NAME`. If no conda env is given, the holder skips conda activation and only runs the EUPS `setup` (mirroring `tkt.sandbox._build_inner_script`).

- [ ] **Step 3: Add a CLI-level test for mode detection.**

In `tests/test_mcp_server.py`:

```python
def test_mcp_server_run_server_builds_warm_sandbox(tmp_path, monkeypatch):
    from tkt import mcp_server

    captured = {}

    def fake_warm(*args, **kwargs):
        captured["kwargs"] = kwargs
        mock_warm = mock.Mock()
        return mock_warm

    monkeypatch.setattr(mcp_server, "WarmSandbox", fake_warm)
    sandbox = mock.Mock()
    mcp_server.run_server(sandbox, cwd=str(tmp_path), repo_dir=str(tmp_path))
    assert captured["kwargs"]["repo_dir"] == str(tmp_path)
```

- [ ] **Step 4: Run tests + lint + typecheck.**

Run:

```bash
python -m pytest tests/test_mcp_server.py tests/test_sandbox.py -v
ruff check .
ruff format --check .
mypy tkt/
```

Expected: all pass. (Fix any integration detail found in Step 2.)

- [ ] **Step 5: Commit.**

```bash
git add tkt/mcp_server.py tkt/_cli.py tests/test_mcp_server.py
git commit -m "feat(cli): add tkt mcp-server command exposing sandboxed bash tool"
```
