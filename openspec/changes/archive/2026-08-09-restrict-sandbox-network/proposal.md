## Why

An LLM agent running inside the `tkt` sandbox currently has **unrestricted network access**: the sandbox shares the host's network namespace and `--unshare-net` is deliberately omitted. A capable agent can reach arbitrary hosts — a data-exfiltration and command-and-control risk. The agent only ever needs to reach the LLM, which it does through a **localhost port** (the `localhost:8080` endpoint backed by the user's ssh port-forward). We want the sandbox restricted by default so the only reachable endpoint is that localhost LLM port, with an explicit opt-in to restore today's full access.

## What Changes

- Restrict the sandbox's network **by default**: isolate it with `bwrap --unshare-net` (an isolated loopback; verified: external destinations report "Network is unreachable") and bridge only the LLM port back to the host so the existing ssh-tunneled `localhost` LLM endpoint keeps working.
- Add a `--network` boolean flag to `sandbox-run`. **Absent = restricted** (default). **Present = full network**, preserving today's behavior (shared host network namespace, no bridge).
- Introduce a `Sandbox` bridge: a host-side `socat` and an in-sandbox `socat` connected over a shared unix socket, forwarding exactly one port (default `localhost:8080`) from the sandbox's isolated loopback to the host's `localhost:8080` (the ssh tunnel). No root or setuid required at runtime (empirically validated).
- Make `sandbox-run` supervise the bridge's lifecycle: start the host-side `socat`, run `bwrap`, then tear down `socat` and the socket when the sandbox exits, preserving the bwrap exit code.
- **BREAKING (behavior change):** the sandbox no longer has full network by default; callers must pass `--network` to opt back in.

## Capabilities

### New Capabilities
- `sandbox-network`: the sandbox's network model — the default restricted mode (isolated loopback + single localhost LLM port bridged via `socat`), the `--network` opt-in granting full access, and the bridge's port/socket configuration.

### Modified Capabilities
<!-- No existing capability spec covers sandbox networking (the existing sandbox-* specs cover reset, command override, and single-repo mode). None of their requirements change. -->

## Impact

- `tkt/sandbox.py`: `Sandbox.run`, `_build_common_argv`, and new bridge setup/teardown logic; `Sandbox` gains a "full network" toggle.
- `tkt/_cli.py`: `sandbox-run` gains the `--network` flag and supervises the sandbox/bridge lifecycle (currently it `exec`s into `bwrap`).
- Dependencies: requires `socat` on the host (already installed). No packaging changes (`tkt` is not pip-distributed).
- Behavior: the default sandbox loses general egress; commands that legitimately need network (e.g. `pip install`, git fetch to remotes) must be run with `--network`.
