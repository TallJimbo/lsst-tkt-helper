## 1. Add --command option to CLI

- [x] 1.1 Add `--command` click option to `sandbox_run` in `tkt/_cli.py`, parsing
      the string (mirroring config) and storing as `cmd: str | None`
- [x] 1.2 Enforce mutual exclusivity: raise `click.UsageError` when both
      `--command` and `--shell` are given
- [x] 1.3 Pass `command` into the workspace-mode `tool.run(workspace, shell=..., command=...)` call
- [x] 1.4 Pass `command` into the single-repo-mode `sandbox.run_single_repo(repo_dir, shell=..., conda_env=..., command=...)` call

## 2. Thread command through Sandbox

- [x] 2.1 Add `command` parameter to `Sandbox.run()` and pass it into `_build_bwrap_argv()`
- [x] 2.2 Add `command` parameter to `Sandbox.run_single_repo()` and pass it into `_build_single_repo_argv()`
- [x] 2.3 Add `command` parameter to `_build_bwrap_argv()` and `_build_single_repo_argv()` and forward to `_build_inner_script()`
- [x] 2.4 Update `_build_inner_script()` to emit `exec {shlex.join(command)}` when
      `command` is given, otherwise fall back to configured `self._command`

## 3. Verify

- [x] 3.1 Run `ruff check .` and `ruff format --check .`
- [x] 3.2 Run `mypy tkt/`
- [x] 3.3 Manually verify `tkt sandbox-run --command "opencode"` overrides the
      command in a single-repo directory (TTY), and that `--shell --command`
      errors, and that plain `tkt sandbox-run` still emits the configured command
