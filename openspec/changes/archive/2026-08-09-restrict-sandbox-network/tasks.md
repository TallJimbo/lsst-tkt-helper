## 1. Sandbox core: network restriction

- [x] 1.1 Add a `network` toggle (default restricted) to `Sandbox.__init__`, `from_json_data`, and the `_build_common_argv`/`_build_bwrap_argv`/`_build_single_repo_argv` builders; when restricted, add `--unshare-net` and remove the "intentionally absent" comment
- [x] 1.2 Add a configurable bridge port to `Sandbox` (default `8080`) and thread it through the bwrap builders
- [x] 1.3 Add bridge setup logic: create a per-run temporary socket directory (e.g. `~/.local/state/tkt/`), bind-mount it read-write into the sandbox at the same path in both workspace and single-repo builders
- [x] 1.4 Prepend the in-sandbox bridge command to the inner script for restricted mode: `socat TCP4-LISTEN:<port>,fork,reuseaddr UNIX-CONNECT:<sockdir>/llm.sock`
- [x] 1.5 Ensure the `--network` (full) path reproduces today's behavior: shared host network namespace, no `--unshare-net`, no bridge, no socket mount

## 2. CLI / lifecycle

- [x] 2.1 Add `--network` boolean flag to the `sandbox-run` command and pass it through the `Sandbox.run` / `run_single_repo` calls
- [x] 2.2 Restructure `sandbox-run` restricted path to supervise: start the host-side `socat` (`UNIX-LISTEN:<sockdir>/llm.sock,fork TCP4:127.0.0.1:<port>`) as a child, run `bwrap` as a child, wait on it, then terminate the host `socat`, remove the socket and temp dir, and exit with bwrap's status
- [x] 2.3 Handle kill/`--die-with-parent` so the host `socat` and socket are not orphaned when the sandbox is terminated abnormally
- [x] 2.4 Update the `sandbox-run` help text and AGENTS.md template (if applicable) to reflect the restricted-by-default network and the `--network` opt-in

## 3. Testing

- [x] 3.1 Add unit tests asserting the restricted vs. `--network` argv construction (presence/absence of `--unshare-net`, socket mount, and bridge command) for both workspace and single-repo builders
- [x] 3.2 Add a test for the bridge port default and override
- [x] 3.3 Verify end-to-end with `socat` present: restricted sandbox can reach the bridged localhost port and cannot reach an external host; `--network` restores full access
- [x] 3.4 Run `ruff check .`, `ruff format --check .`, and `mypy tkt/` and fix any failures

## 4. Docs / config

- [x] 4.1 Document `socat` as a runtime prerequisite for the restricted sandbox (no packaging changes)
- [x] 4.2 Note the breaking default (restricted network) in the README/proposal; update any config examples that rely on full network to include `--network`
