## ADDED Requirements

### Requirement: Sandbox network is restricted by default
The sandbox SHALL, by default, run in an isolated network namespace (`bwrap --unshare-net`) such that only a single configured localhost port is reachable and all other network destinations are unreachable. The reachable port SHALL bridge back to the host's corresponding localhost port so an existing localhost service (e.g. an ssh-tunneled LLM endpoint) remains usable.

#### Scenario: Default sandbox has no external egress
- **WHEN** the sandbox is started without a network opt-in flag
- **THEN** a connection from inside the sandbox to a non-localhost external host fails (unreachable)

#### Scenario: Default sandbox can reach the bridged localhost port
- **WHEN** a service listens on the host's configured localhost port (the default `8080`)
- **THEN** a connection from inside the sandbox to `127.0.0.1:<port>` reaches the host service

#### Scenario: Other localhost ports are not reachable
- **WHEN** a connection is attempted from inside the sandbox to `127.0.0.1` on a port other than the configured one
- **THEN** the connection fails

### Requirement: Full network access is available via an opt-in flag
The `sandbox-run` command SHALL accept a boolean `--network` flag. When present, the sandbox SHALL run with full network access (shared host network namespace, no bridge), matching previous behavior.

#### Scenario: Opting in grants full network
- **WHEN** `sandbox-run --network` is invoked
- **THEN** the sandbox runs in the host network namespace and external connections succeed

#### Scenario: Flag omitted yields restricted network
- **WHEN** `sandbox-run` is invoked without `--network`
- **THEN** the sandbox runs in the restricted (isolated) network mode described above

### Requirement: Bridge port is configurable
The port the restricted sandbox bridges SHALL default to `8080` and SHALL be overridable through the `Sandbox` tool configuration without code changes.

#### Scenario: Bridge uses the configured port
- **WHEN** the `Sandbox` is configured with a bridge port `N`
- **THEN** the restricted sandbox reaches the host's localhost port `N`

### Requirement: Bridge lifecycle is cleaned up on exit
The host-side bridge process and its shared socket SHALL be torn down when the sandbox exits, and the sandbox's exit status SHALL be preserved.

#### Scenario: Normal sandbox exit cleans up the bridge
- **WHEN** the sandbox runs to completion
- **THEN** the host-side `socat` process is terminated, the shared socket and temporary directory are removed, and `tkt sandbox-run` exits with the sandbox's exit status

### Requirement: Sandbox network restriction applies in both modes
The default restricted network SHALL apply to both workspace mode and single-repo mode sandboxes.

#### Scenario: Single-repo sandbox is also restricted
- **WHEN** the sandbox is run in single-repo mode without `--network`
- **THEN** it is network-restricted in the same way as workspace mode

### Requirement: Runtime does not require elevated privileges
The restricted-network sandbox SHALL operate without root or a setuid helper at runtime.

#### Scenario: Unprivileged restricted sandbox starts
- **WHEN** a non-root user starts the restricted sandbox
- **THEN** the sandbox starts and reaches the bridged localhost port without requesting elevated privileges
