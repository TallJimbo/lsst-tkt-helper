# sandbox-reset

## 1. Core reset logic in `Sandbox`

- [x] 1.1 Add `Sandbox.reset(self, workspace: Workspace) -> None` in
      `tkt/sandbox.py` that iterates `workspace.packages` and, for each package
      with an existing `.agent/<pkg>` worktree, calls a per-package helper
      `_reset_agent_worktree(agent_package_dir, human_branch, package)`; skip
      packages with no `.agent/<pkg>` (log info, like `Sandbox.write`).
- [x] 1.2 In the per-package helper, detect uncommitted work: staged/unstaged
      changes (`git diff --quiet` / `git diff --cached --quiet`) or untracked or
      ignored files. If dirty, run `git stash push --all -m "tkt reset backup: <package>"`.
- [x] 1.3 Detect unmerged commits with `git rev-list <human-branch>..<agent-HEAD>`
      non-empty. If present, build the backup branch name
      `<agent-branch>-saved-<timestamp>` where `<agent-branch>` is the active
      branch name and `timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")`, and
      run `git branch <backup-name> <agent-HEAD>`.
- [x] 1.4 Run `git reset --hard <human-branch>` then `git clean -fdx` in the
      worktree, and log what was stashed / which backup branch was created.

## 2. CLI command

- [x] 2.1 Add `tkt sandbox-reset` command in `tkt/_cli.py` mirroring
      `sandbox-run`: options `-d/--directory` (click.Path exists, resolve_path),
      `--ticket`, `--environment` (envvar `TKT_ENVIRONMENT`), `-v/--verbose`.
- [x] 2.2 Implement the handler: require `--environment` or `TKT_ENVIRONMENT`;
      load env via `Environment.from_file`, workspace via
      `Workspace.from_existing(ticket=..., directory=..., environment=env)`, fetch
      `env.get_tool("sandbox")` and validate it is a `tkt.sandbox.Sandbox`
      (raise `click.UsageError` otherwise), then call `tool.reset(workspace)`.
- [x] 2.3 Export `Sandbox` (including `reset`) from `tkt/__init__.py` if not
      already exported.

## 3. Docs & cleanup

- [x] 3.1 Run `ruff check .`, `ruff format --check .`, and `mypy tkt/`; fix
      any issues so all pass.
- [x] 3.2 Add a brief note to `tkt/AGENTS.md.in` and the `sandbox-reset` help
      text about the command saving work to the stash/backup branches before
      resetting.
- [x] 3.3 If desired, add pytest coverage under `tests/` for the reset behavior
      (stash on dirty worktree, backup branch on unmerged commits, no backup when
      in sync).
