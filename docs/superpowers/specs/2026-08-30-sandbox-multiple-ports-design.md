# sandbox: bridge multiple LLM ports (design handover)

Date: 2026-08-30

## Problem

The user now runs their local model through a proxy on a different port than
the single LLM port the sandbox currently bridges, and wants `tkt sandbox-run`
to allow access to that new port **in addition to** the old one.

Currently the `Sandbox` tool bridges exactly one port. Its `port` config entry
(default `_DEFAULT_PORT = 8080`) is a single int. In restricted mode
(`network: false`) the sandbox is isolated with `--unshare-net`, and a pair of
`socat` forwarders makes the LLM endpoint reachable on that one port:

- **Host side** (`_start_bridge`): `socat UNIX-LISTEN:<net_dir>/llm.sock,fork`
  relays to `TCP4:127.0.0.1:<port>`.
- **Sandbox side** (`_build_common_argv`): `socat TCP4-LISTEN:<port>,fork,reuseaddr`
  relays to `UNIX-CONNECT:<net_dir>/llm.sock`.

There is no way to expose a second port.

## Design decision (D1): `port` becomes a single int or a list of ints

Change the `port` configuration entry to accept either a single integer
(backward compatible with existing configs) or a list of integers. Internally
normalize to a tuple and store as `self._ports`. The total set of bridged ports
is exactly that tuple; there is no separate "primary" vs "additional" notion.

```python
# A single int stays supported:
{"port": 8080}            # -> self._ports == (8080,)
{"port": [8080, 8000]}    # -> self._ports == (8080, 8000)
```

A new module-level helper normalizes and validates:

```python
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

`__init__` stores `self._ports = _normalize_ports(port)` (replacing the old
`self._port = int(port)`). `from_json_data` drops its inline `port` type check
and lets `_normalize_ports` (via `cls(...)`) raise on bad input; `vc_port`
validation stays inline and unchanged.

## Design decision (D2): emit one bridge per port, on a per-port socket

Each port gets its own unix socket so the forwarders do not collide on a single
`llm.sock`. Socket / log names are disambiguated by port:

- Host: `socat UNIX-LISTEN:<net_dir>/llm-<port>.sock,fork`
  -> `TCP4:127.0.0.1:<port>`, log `host-llm-<port>.log`.
- Sandbox: `socat TCP4-LISTEN:<port>,fork,reuseaddr`
  -> `UNIX-CONNECT:<net_dir>/llm-<port>.sock`, log `sandbox-llm-<port>.log`.

`_DEFAULT_PORTS = (_DEFAULT_PORT,)`. The build methods
(`_build_bwrap_argv`, `_build_single_repo_argv`, `_build_common_argv`) take
`ports: Sequence[int] = _DEFAULT_PORTS` in place of `port: int = _DEFAULT_PORT`,
and `_build_common_argv` prepends one in-sandbox socat per port. `_start_bridge`
launches one host socat per port and waits for **all** sockets to appear before
starting the sandbox. The `vc_port` reverse bridge is unchanged (single port).

The `_start_bridge` startup/wait loop and early-exit error handling generalize
to the list of per-port sockets/logs. `_bridge_net_dir` / `_iter_bridge_socat_pids`
/ `cleanup_stale_bridges` are unaffected: they match on the `state/tkt/net-`
marker in any argv arg and extract the `net-<id>` dir, independent of the
socket filename.

### In-sandbox socat construction (`_build_common_argv`)

```python
for p in ports:
    sock_path = shlex.quote(os.path.join(net_dir, f"llm-{p}.sock"))
    log_path = shlex.quote(os.path.join(net_dir, f"sandbox-llm-{p}.log"))
    inner = (
        f"socat TCP4-LISTEN:{p},fork,reuseaddr "
        f"UNIX-CONNECT:{sock_path} >{log_path} 2>&1 &\n" + inner
    )
```

### Host-side socat construction (`_start_bridge`)

```python
sock_paths: list[str] = []
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
                start_new_session=True,
                preexec_fn=_set_pdeathsig,
            )
        )
# ... then wait until all(sock_paths) exist or a proc exited ...
```

## Scope

- `tkt/sandbox.py`: `_normalize_ports`, `_DEFAULT_PORTS`, `Sandbox.__init__`,
  `Sandbox.from_json_data`, `Sandbox.run`, `Sandbox.run_single_repo`,
  `Sandbox._start_bridge`, `Sandbox._build_bwrap_argv`,
  `Sandbox._build_single_repo_argv`, `Sandbox._build_common_argv`.
- `tests/test_sandbox.py`: update existing port/argv tests to the list
  semantics and per-port socket names; add tests for list parsing and for
  multiple ports producing one socat each (host and sandbox sides).
- No change to `local.json` (the user will configure their proxy port); no new
  dependencies.
