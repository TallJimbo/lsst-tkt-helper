# sandbox: bridge multiple LLM ports — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `tkt sandbox-run` bridge more than one LLM port into the network-restricted sandbox, so a model reachable via a different localhost port than the historical default stays reachable **alongside** the old port.

**Architecture:** Change the `Sandbox` tool's `port` config from a single int to "an int or a list of ints", normalized to a tuple `self._ports`. Then emit one `socat` bridge pair per port (host-side and in-sandbox), each on its own per-port unix socket, so the restricted sandbox can reach every configured port via localhost. The reverse `vc_port` companion bridge is unchanged.

**Tech Stack:** Python 3.13, stdlib (`subprocess`, `os`, `shlex`); tests with `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-30-sandbox-multiple-ports-design.md`

## Global Constraints

- All `.py` files include the existing BSD-3-Clause license header — preserve it; do not alter existing headers.
- Python 3.13; dependencies are `click`, `GitPython`, `pyyaml`, `json5`. No new third-party deps.
- Ruff: line-length 110, doc-length 79, numpy docstring convention. Run `ruff check .` and `ruff format --check .` before committing.
- mypy: run `mypy tkt/` before committing.
- Tests: `python -m pytest`.
- Do not add packaging configuration; do not scaffold a project.
- Do not modify `local.json` (the user configures their own proxy port).
- Prefer simple solutions; this tool's users can handle tracebacks.

---

### Task 1: Accept `port` as an int or a list, normalized to `self._ports`

**Files:**
- Modify: `tkt/sandbox.py` — add `_normalize_ports` near the `_DEFAULT_PORT`/`_DEFAULT_VC_PORT` constants (after line 67); change `Sandbox.__init__` (lines 202-219) and `Sandbox.from_json_data` (lines 221-251) and the class docstring `port` entry (lines 188-192).
- Test: `tests/test_sandbox.py`

**Interfaces:**
- Consumes: existing `_DEFAULT_PORT = 8080`, `_DEFAULT_VC_PORT = 8081`.
- Produces: `def _normalize_ports(port: int | Sequence[int]) -> tuple[int, ...]`; `Sandbox._ports: tuple[int, ...]` and a temporary `Sandbox._port: int` alias (`_ports[0]`, kept so Task 1 is green on its own; Task 2 removes it).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sandbox.py`:

```python
def test_from_json_data_accepts_list_or_int_port():
    """from_json_data parses port as a single int or a list of ints."""
    single = Sandbox.from_json_data({"command": [], "port": 9999})
    assert single._ports == (9999,)
    multi = Sandbox.from_json_data({"command": [], "port": [8080, 8000]})
    assert multi._ports == (8080, 8000)
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "port": [8080, "8080"]})
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "port": []})
```

- [ ] **Step 2: Run the new test to verify it FAILS**

Run: `python -m pytest tests/test_sandbox.py::test_from_json_data_accepts_list_or_int_port -v`

Expected: FAIL — `Sandbox` has no attribute `_ports` (AttributeError), and `port: [8080, "8080"]` / `port: []` do not raise.

- [ ] **Step 3: Add the `_normalize_ports` helper**

In `tkt/sandbox.py`, after the `_DEFAULT_VC_PORT = 8081` block (line 67), add:

```python
_DEFAULT_PORTS = (_DEFAULT_PORT,)


def _normalize_ports(port: int | Sequence[int]) -> tuple[int, ...]:
    """Return ``port`` as a tuple of ints, accepting an int or a sequence.

    Raises ``ValueError`` if ``port`` is a non-integer scalar or a sequence
    containing a non-integer.
    """
    if isinstance(port, bool):
        raise ValueError(f"'port' must be an integer or list of integers, got {port!r}.")
    if isinstance(port, int):
        return (port,)
    result = []
    for p in port:
        if isinstance(p, bool) or not isinstance(p, int):
            raise ValueError(f"'port' must contain only integers, got {port!r}.")
        result.append(int(p))
    if not result:
        raise ValueError("'port' must include at least one port.")
    return tuple(result)
```

Note: `Sequence` is already imported (used for `command: Sequence[str]`).

- [ ] **Step 4: Update the class docstring `port` entry**

In `tkt/sandbox.py`, replace the `port` entry in the class docstring (lines 188-192):

```python
    port
        Host port, or sequence of host ports, bridged into the sandbox's
        isolated network namespace so tools can reach the LLM via localhost.
        Default :data:`_DEFAULT_PORT`. Each port maps to the same port on the
        host's loopback (the ssh tunnel's listen port).  Only used when
        ``network`` is ``False``.
```

- [ ] **Step 5: Update `Sandbox.__init__`**

In `tkt/sandbox.py`, change the signature and body of `__init__` (lines 202-219) from:

```python
    def __init__(
        self,
        *,
        command: Sequence[str],
        mounts_ro: Sequence[str] = (),
        mounts_rw: Sequence[str] = (),
        env: dict[str, str] | None = None,
        network: bool = False,
        port: int = _DEFAULT_PORT,
        vc_port: int = _DEFAULT_VC_PORT,
    ):
        self._command = tuple(command)
        self._mounts_ro = tuple(mounts_ro)
        self._mounts_rw = tuple(mounts_rw)
        self._env = dict(env) if env is not None else {}
        self._network = bool(network)
        self._port = int(port)
        self._vc_port = int(vc_port)
```

to:

```python
    def __init__(
        self,
        *,
        command: Sequence[str],
        mounts_ro: Sequence[str] = (),
        mounts_rw: Sequence[str] = (),
        env: dict[str, str] | None = None,
        network: bool = False,
        port: int | Sequence[int] = _DEFAULT_PORT,
        vc_port: int = _DEFAULT_VC_PORT,
    ):
        self._command = tuple(command)
        self._mounts_ro = tuple(mounts_ro)
        self._mounts_rw = tuple(mounts_rw)
        self._env = dict(env) if env is not None else {}
        self._network = bool(network)
        self._ports = _normalize_ports(port)
        self._port = self._ports[0]  # temporary alias; removed in Task 2
        self._vc_port = int(vc_port)
```

- [ ] **Step 6: Update `Sandbox.from_json_data`**

In `tkt/sandbox.py`, in `from_json_data` (lines 235-237), replace the inline `port` parsing and validation:

```python
        port = data.pop("port", _DEFAULT_PORT)
        if isinstance(port, bool) or not isinstance(port, int):
            raise ValueError(f"'port' must be an integer, got {port!r}.")
```

with:

```python
        port = data.pop("port", _DEFAULT_PORT)
```

`port` is passed to `cls(...)` (unchanged), where `__init__` -> `_normalize_ports`
now performs the validation and raises `ValueError`. `vc_port` validation stays as
it is.

- [ ] **Step 7: Update the existing single-port test to check `_ports`**

In `tests/test_sandbox.py`, in `test_bridge_port_default_and_override` (around line 208), change:

```python
    custom = Sandbox.from_json_data({"command": [], "port": 9999})
    assert custom._port == 9999
```

to:

```python
    custom = Sandbox.from_json_data({"command": [], "port": 9999})
    assert custom._ports == (9999,)
```

- [ ] **Step 8: Run the sandbox test suite**

Run: `python -m pytest tests/test_sandbox.py -v`

Expected: all PASS — the new list test passes, and all existing tests (including the `port`/`vc_port` override tests, which still use single ports and the unchanged build signatures) remain green. `test_bridge_lifecycle` still passes because `__init__` still accepts and stores a single port via `_ports`.

- [ ] **Step 9: Run lint and type checks**

Run: `ruff check . && ruff format --check . && mypy tkt/`

Expected: all three pass; apply `ruff format` if it reports diffs on the changed files.

- [ ] **Step 10: Commit**

```bash
git add tkt/sandbox.py tests/test_sandbox.py
git commit -m "feat: accept sandbox port config as an int or list of ints"
```

---

### Task 2: Bridge one socat per port (host and sandbox sides)

**Files:**
- Modify: `tkt/sandbox.py` — `Sandbox.run` (line 394), `Sandbox.run_single_repo` (line 452), `Sandbox._start_bridge` (lines 461-547), `_build_bwrap_argv` signature (lines 577-580), `_build_single_repo_argv` signature (lines 621-624), `_build_common_argv` signature and restricted-net block (lines 641-693).
- Modify: `tests/test_sandbox.py` — update argv tests to `ports=` / per-port sockets; update `test_bridge_lifecycle` to two ports; add a multi-port argv test.

**Interfaces:**
- Consumes: `Sandbox._ports: tuple[int, ...]` from Task 1; `_DEFAULT_PORTS = (_DEFAULT_PORT,)` from Task 1.
- Produces: build methods taking `ports: Sequence[int] = _DEFAULT_PORTS`; `_start_bridge` launching one host socat per port (each on `llm-<port>.sock`) and waiting for all sockets; removes the temporary `Sandbox._port` alias.

- [ ] **Step 1: Update the existing argv tests to `ports=` and per-port socket names**

In `tests/test_sandbox.py`, update `test_restricted_argv_isolates_network_and_bridges` (lines 153-166). Change the `_build_bwrap_argv` call and assertions:

```python
def test_restricted_argv_isolates_network_and_bridges(workspace, sandbox, tmp_path):
    """Restricted mode isolates net and bridges the port and companion port."""
    net_dir = tmp_path / "net"
    net_dir.mkdir()
    argv = sandbox._build_bwrap_argv(
        workspace, shell=False, network=False, net_dir=str(net_dir), ports=(8080,)
    )
    assert "--unshare-net" in argv
    assert ("--bind", str(net_dir), str(net_dir)) in [tuple(argv[i : i + 3]) for i in range(len(argv) - 2)]
    inner = _inner_of(argv)
    assert "socat TCP4-LISTEN:8080,fork,reuseaddr UNIX-CONNECT:" in inner
    assert f"{net_dir}/llm-8080.sock" in inner
    # Reverse bridge: host -> sandbox for the visual companion.
    assert f"socat UNIX-LISTEN:{net_dir}/vc.sock,fork TCP4:127.0.0.1:8081" in inner
    assert f"{net_dir}/vc.sock" in inner
```

Update `test_single_repo_network_modes` (lines 177-193): change the restricted call

```python
    restricted = sandbox._build_single_repo_argv(
        str(tmp_path), shell=False, network=False, net_dir=str(net_dir), port=8080
    )
```

to use `ports=(8080,)`. The `vc.sock` assertion stays unchanged.

Update `test_bridge_port_default_and_override` (lines 201-213): change both `_build_bwrap_argv` calls to pass `ports=(8080,)` and `ports=(9999,)` instead of `port=...`, and update the inner-script assertions:

```python
    default = Sandbox(command=[])
    inner = _inner_of(
        default._build_bwrap_argv(workspace, shell=False, network=False, net_dir=str(net_dir), ports=(8080,))
    )
    assert "TCP4-LISTEN:8080" in inner
    assert f"UNIX-LISTEN:{net_dir}/vc.sock,fork TCP4:127.0.0.1:8081" in inner

    custom = Sandbox.from_json_data({"command": [], "port": 9999})
    assert custom._ports == (9999,)
    inner = _inner_of(
        custom._build_bwrap_argv(workspace, shell=False, network=False, net_dir=str(net_dir), ports=(9999,))
    )
    assert "TCP4-LISTEN:9999" in inner
```

- [ ] **Step 2: Run the updated argv tests to verify they FAIL**

Run: `python -m pytest tests/test_sandbox.py::test_restricted_argv_isolates_network_and_bridges tests/test_sandbox.py::test_single_repo_network_modes tests/test_sandbox.py::test_bridge_port_default_and_override -v`

Expected: FAIL — build methods still take `port=` (TypeError for `ports=`), and the in-sandbox socat still references `llm.sock` rather than `llm-<port>.sock`.

- [ ] **Step 3: Change build-method signatures to `ports`**

In `tkt/sandbox.py`, change `port: int = _DEFAULT_PORT` to `ports: Sequence[int] = _DEFAULT_PORTS` in all three build methods:

- `_build_bwrap_argv` (line 579)
- `_build_single_repo_argv` (line 623)
- `_build_common_argv` (line 649)

- [ ] **Step 4: Emit one in-sandbox socat per port in `_build_common_argv`**

In `tkt/sandbox.py`, in `_build_common_argv` (lines 684-693), replace the single-port LLM socat line and its comment:

```python
            sock_path = shlex.quote(os.path.join(net_dir, "llm.sock"))
            log_path = shlex.quote(os.path.join(net_dir, "sandbox-socat.log"))
            vc_sock_path = shlex.quote(os.path.join(net_dir, "vc.sock"))
            vc_log_path = shlex.quote(os.path.join(net_dir, "sandbox-vc-socat.log"))
            inner = (
                f"socat TCP4-LISTEN:{port},fork,reuseaddr "
                f"UNIX-CONNECT:{sock_path} >{log_path} 2>&1 &\n"
                f"socat UNIX-LISTEN:{vc_sock_path},fork "
                f"TCP4:127.0.0.1:{vc_port} >{vc_log_path} 2>&1 &\n" + inner
            )
```

with:

```python
            # Start one in-sandbox socat per bridged port so the agent's
            # localhost:<port> reaches the host bridge, plus one for the host's
            # localhost:<vc_port> companion server.  Background them (the inner
            # script ends with `exec`), and redirect logs into the shared
            # net_dir.
            for p in ports:
                sock_path = shlex.quote(os.path.join(net_dir, f"llm-{p}.sock"))
                log_path = shlex.quote(os.path.join(net_dir, f"sandbox-llm-{p}.log"))
                inner = (
                    f"socat TCP4-LISTEN:{p},fork,reuseaddr "
                    f"UNIX-CONNECT:{sock_path} >{log_path} 2>&1 &\n" + inner
                )
            vc_sock_path = shlex.quote(os.path.join(net_dir, "vc.sock"))
            vc_log_path = shlex.quote(os.path.join(net_dir, "sandbox-vc-socat.log"))
            inner = (
                f"socat UNIX-LISTEN:{vc_sock_path},fork "
                f"TCP4:127.0.0.1:{vc_port} >{vc_log_path} 2>&1 &\n" + inner
            )
```

- [ ] **Step 5: Update `run` and `run_single_repo` to pass `ports`**

In `tkt/sandbox.py`, in `run` (line 394) change `port=self._port` to `ports=self._ports`; in `run_single_repo` (line 452) change `port=self._port` to `ports=self._ports`.

- [ ] **Step 6: Remove the temporary `_port` alias**

In `tkt/sandbox.py`, in `__init__` (from Task 1), delete the line:

```python
        self._port = self._ports[0]  # temporary alias; removed in Task 2
```

- [ ] **Step 7: Generalize `_start_bridge` to one host socat per port**

In `tkt/sandbox.py`, in `_start_bridge` (lines 487-547), replace the single LLM socket/log setup and socat launch:

```python
        procs: list[subprocess.Popen[Any]] = []
        try:
            with open(log, "w", encoding="utf-8") as f:
                procs.append(
                    subprocess.Popen(
                        ["socat", f"UNIX-LISTEN:{sock},fork", f"TCP4:127.0.0.1:{self._port}"],
                        stdout=f,
                        stderr=subprocess.STDOUT,
                        # Isolate each forwarder in its own process group (so
                        # we can signal its forked children during teardown)
                        # and arrange for it to be SIGTERM'd if we are killed
                        # abruptly.
                        start_new_session=True,
                        preexec_fn=_set_pdeathsig,
                    )
                )
```

with one socat per port:

```python
        procs: list[subprocess.Popen[Any]] = []
        sock_paths: list[str] = []
        try:
            for p in self._ports:
                sock = os.path.join(net_dir, f"llm-{p}.sock")
                log = os.path.join(net_dir, f"host-llm-{p}.log")
                sock_paths.append(sock)
                with open(log, "w", encoding="utf-8") as f:
                    procs.append(
                        subprocess.Popen(
                            ["socat", f"UNIX-LISTEN:{sock},fork", f"TCP4:127.0.0.1:{p}"],
                            stdout=f,
                            stderr=subprocess.STDOUT,
                            # Isolate each forwarder in its own process group
                            # (so we can signal its forked children during
                            # teardown) and arrange for it to be SIGTERM'd if
                            # we are killed abruptly.
                            start_new_session=True,
                            preexec_fn=_set_pdeathsig,
                        )
                    )
```

Also update the variable definitions at the top of `_start_bridge` (lines 482-485): remove the now-unused single `sock` and `log` locals (`llm.sock` / `host-socat.log`). Keep `vc_sock` / `vc_log`.

- [ ] **Step 8: Update the socket-wait loop and early-exit handling in `_start_bridge`**

In `tkt/sandbox.py`, replace the wait loop (lines 521-543):

```python
            # Wait for the LLM listener to create its socket before the
            # sandbox's socat tries to connect, so there is no startup race.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if os.path.exists(sock):
                    break
                if any(p.poll() is not None for p in procs):
                    break
                time.sleep(0.05)
            if any(p.poll() is not None for p in procs):
                detail = ""
                try:
                    with open(log, encoding="utf-8") as f:
                        tail = f.read().strip()
                    if not tail:
                        with open(vc_log, encoding="utf-8") as f:
                            tail = f.read().strip()
                    detail = f": {tail}" if tail else ""
                except OSError:
                    pass
                raise RuntimeError(f"host socat for sandbox bridge exited early{detail}")
            if not os.path.exists(sock):
                raise RuntimeError(f"host socat did not create socket {sock}")
```

with:

```python
            # Wait for all LLM listeners to create their sockets before the
            # sandbox's socats try to connect, so there is no startup race.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if all(os.path.exists(s) for s in sock_paths):
                    break
                if any(p.poll() is not None for p in procs):
                    break
                time.sleep(0.05)
            if any(p.poll() is not None for p in procs):
                detail = ""
                logs = [os.path.join(net_dir, f"host-llm-{p}.log") for p in self._ports] + [vc_log]
                try:
                    for lp in logs:
                        if os.path.exists(lp):
                            with open(lp, encoding="utf-8") as f:
                                tail = f.read().strip()
                            if tail:
                                detail = f": {tail}"
                                break
                except OSError:
                    pass
                raise RuntimeError(f"host socat for sandbox bridge exited early{detail}")
            if not all(os.path.exists(s) for s in sock_paths):
                missing = ", ".join(s for s in sock_paths if not os.path.exists(s))
                raise RuntimeError(f"host socat did not create socket(s) {missing}")
```

- [ ] **Step 9: Add a multi-port argv test**

Append to `tests/test_sandbox.py`:

```python
def test_multiple_ports_emit_one_socat_each(workspace, sandbox, tmp_path):
    """A list of ports produces one in-sandbox socat per port."""
    net_dir = tmp_path / "net"
    net_dir.mkdir()
    argv = sandbox._build_bwrap_argv(
        workspace, shell=False, network=False, net_dir=str(net_dir), ports=(8080, 8000)
    )
    inner = _inner_of(argv)
    assert "socat TCP4-LISTEN:8080,fork,reuseaddr UNIX-CONNECT:" in inner
    assert "socat TCP4-LISTEN:8000,fork,reuseaddr UNIX-CONNECT:" in inner
    assert f"{net_dir}/llm-8080.sock" in inner
    assert f"{net_dir}/llm-8000.sock" in inner
```

- [ ] **Step 10: Update `test_bridge_lifecycle` to two ports**

In `tests/test_sandbox.py`, change `test_bridge_lifecycle` (lines 252-267) to use a two-port sandbox and assert one socket per port and one extra socat:

```python
@pytest.mark.skipif(shutil.which("socat") is None, reason="socat not installed")
def test_bridge_lifecycle(tmp_path, monkeypatch):
    """The host-side bridges start one socat per port and tear down cleanly."""
    monkeypatch.setenv("HOME", str(tmp_path))  # state dir lands under tmp_path
    sandbox = Sandbox(command=[], port=[18089, 18088], vc_port=18090)

    net_dir, procs = sandbox._start_bridge()
    try:
        assert os.path.isdir(net_dir)
        assert os.path.exists(os.path.join(net_dir, "llm-18089.sock"))
        assert os.path.exists(os.path.join(net_dir, "llm-18088.sock"))
        assert len(procs) == 3  # one socat per LLM port + one for vc
        assert all(proc.poll() is None for proc in procs)  # all socats alive
    finally:
        sandbox._stop_bridge(net_dir, procs)

    assert all(proc.poll() is not None for proc in procs)  # socats terminated
    assert not os.path.exists(net_dir)  # socket dir removed
```

- [ ] **Step 11: Run the sandbox test suite**

Run: `python -m pytest tests/test_sandbox.py -v`

Expected: all PASS, including the new `test_multiple_ports_emit_one_socat_each` and the updated `test_bridge_lifecycle`. `test_from_json_data_accepts_list_or_int_port` still passes (repeat self-check).

- [ ] **Step 12: Run lint and type checks**

Run: `ruff check . && ruff format --check . && mypy tkt/`

Expected: all three pass; apply `ruff format` if it reports diffs on the changed files.

- [ ] **Step 13: Commit**

```bash
git add tkt/sandbox.py tests/test_sandbox.py
git commit -m "feat: bridge multiple LLM ports into the restricted sandbox"
```
