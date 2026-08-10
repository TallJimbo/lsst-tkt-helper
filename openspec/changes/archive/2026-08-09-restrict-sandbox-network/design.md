## Context

The `tkt` sandbox (`tkt/sandbox.py`) builds a `bwrap` invocation in `_build_common_argv`; today it does **not** unshare the network namespace (a comment says `--unshare-net` is "intentionally absent — OpenCode needs to reach the LLM API"). The sandbox therefore has full, unrestricted egress. `sandbox-run` (`tkt/_cli.py`) locates the configured `Sandbox` tool and calls `Sandbox.run(...)`, which builds the argv and `os.execvp`s straight into `bwrap`.

The LLM is reached through a localhost port: `~/.config/opencode/opencode.jsonc` defines the active provider `rubin-dm-01` with `baseURL: http://localhost:8080/api/v1`, itself backed by the user's ssh port-forward (outside the sandbox). This is the only endpoint the agent legitimately needs.

This change is security-motivated: an autonomous LLM agent with full egress is a data-exfiltration / C2 vector. Empirically verified on this machine (unprivileged, no sudo):

- `bwrap --unshare-net` yields an isolated loopback; external destinations report `Network is unreachable`.
- A host-side `socat` bridging a shared unix socket to host `localhost` + an in-sandbox `socat` bridging that socket to the sandbox's own `localhost` port successfully carries traffic, with external access blocked. No root/setuid needed at runtime.

## Goals / Non-Goals

**Goals:**
- Default sandbox network = isolated loopback + the single LLM localhost port bridged to the host, so the existing ssh tunnel keeps working.
- `--network` flag on `sandbox-run` to opt back into today's full/ shared-host-network behavior.
- Runtime is fully unprivileged; no per-run root; no setuid helper.
- Clean bridge lifecycle (no orphaned `socat` / socket after the sandbox exits) with the bwrap exit code preserved.

**Non-Goals:**
- Per-host/IP allowlists or fine-grained egress policy beyond "one localhost port vs. all" (out of scope).
- Hardening other mount/credential aspects of the sandbox.
- Changing _how_ the LLM port-forward is set up (it stays outside the sandbox, as today).
- A cheap DNS-files fallback as the default mechanism (see Decisions).

## Decisions

### 1. Mechanism: `bwrap --unshare-net` + `socat` unix-socket bridge
Restrict via true network-isolation namespaces rather than name-resolution tricks.

- Host side (parent of `bwrap`, in the host netns): `socat UNIX-LISTEN:<dir>/llm.sock,fork TCP4:127.0.0.1:<port>` — connects as a *client* to the existing ssh tunnel.
- Sandbox side (in the isolated netns, prepended to the inner script): `socat TCP4-LISTEN:<port>,fork,reuseaddr UNIX-CONNECT:<dir>/llm.sock`.
- Tool traffic: sandbox `127.0.0.1:<port>` → sandbox socat → shared unix socket → host socat → host `127.0.0.1:<port>` (ssh tunnel) → LLM.
- Socket path lives in a directory bind-mounted read-write into the sandbox at the same path.

**Alternatives considered:**
- *Limited `/etc/hosts` + `/etc/resolv.conf` in the sandbox.* Rejected as the default: these only affect **name resolution**, not connectivity. Raw-IP connections, alternative resolvers (DNS-over-HTTPS), and discovered proxies all still work; it does not deliver "only a localhost port is reachable". It remains a possible cheap defense-in-depth layer later, but not the mechanism.
- *`iptables`/`nftables` egress rules scoped to the sandbox.* Rejected: needs root per-run, and scoping only the sandbox's processes is fragile (the sandbox runs as the user's UID, so `--uid-owner` would block the whole session; cgroup matching with `bwrap --unshare-cgroup-try` is finicky).
- *Own netns (`ip netns`) + veth + DNAT for one port.* Rejected: effective, but requires root and NAT plumbing; the chosen approach is unprivileged and has a simpler mental model.

### 2. Port to bridge: default `localhost:8080`, configurable
The LLM port the bridge exposes defaults to `8080` (the `rubin-dm-01` provider's `localhost:8080`). It is exposed as a `Sandbox` configuration knob so a different environment can change it without code edits.

### 3. Socket directory: dedicated per-run directory, bind-mounted rw
Neither `$HOME` (tmpfs in the sandbox) nor `/tmp` (tmpfs in the sandbox) is shared, so the bridge creates a dedicated host directory per run (e.g. `tempfile.mkdtemp` under `~/.local/state/tkt/`), bind-mounts it read-write into the sandbox at the same path, and removes it on teardown. This works uniformly for **both** workspace mode (which has `.agent/`) and single-repo mode (which does not), avoiding mode-specific socket paths.

### 4. Lifecycle: `sandbox-run` supervises the sandbox and bridge
Today `Sandbox.run` `os.execvp`s into `bwrap`, leaving no parent to clean up. For the restricted path `sandbox-run` instead runs the host `socat` and `bwrap` as children, waits for `bwrap`, then reaps the host `socat`, removes the socket and temp dir, and exits with bwrap's status. The `--network` (full) path may keep the current spawn/exec behavior; a supervisory path is also acceptable for uniformity.

### 5. Flag shape: boolean `--network`
`--network` (present) = full network (today's behavior); absent = restricted (default). This flips the default, which is the intended security posture.

## Risks / Trade-offs

- **Breaking default change** (sandbox loses general egress) → clearly documented in the proposal and flag help; users pass `--network` when they need `pip install`, git fetch to remotes, or the external `portkey` provider.
- **Orphaned host `socat` / leftover socket after a user kills the sandbox** → `sandbox-run` supervises and tears down on normal exit; `--die-with-parent` and trap handling reduce leaks from kills.
- **`socat` is a new runtime dependency** → already present on this host; document it as a prerequisite (no packaging changes since `tkt` is used from a git clone).
- **IPv4/IPv6 bind mismatch** (socat defaulted to `[::]` in testing) → both bridge sockets explicitly use `TCP4`/`TCP4-LISTEN` to bind IPv4 `127.0.0.1` predictably.
- **LLM port changes** (different provider/port) → configurable `port`; migration is a one-line config change.

## Migration Plan

- No rollout of existing installs; this is a behavior default flip shipped with the next tool update. Users who relied on full network pass `--network`.
- Rollback: revert to the previous default (no `--unshare-net`) if the bridge proves unreliable; the flag/bridge code can remain dormant.

## Open Questions

- ~~Socket-directory root (`~/.local/state/tkt/` vs. `$XDG_RUNTIME_DIR`)~~ **Resolved**: the bridge uses `~/.local/state/tkt/net-*` (created via `tempfile.mkdtemp` under the user's state dir). It is unprivileged and gives a fresh `0700` directory per run.
- ~~Whether the `--network` (full) path should also route through the supervisor** ~~ **Resolved**: the full path keeps today's `os.execvp` (there is no bridge to clean up). The `--network` CLI flag is tri-state (`bool | None`): absent defers to the sandbox's configured `network` value, `--network` forces full access. This keeps the config `network` knob functional (e.g. a trusted host can default a workspace to full network).
