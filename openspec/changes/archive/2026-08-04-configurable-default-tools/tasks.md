## 1. Environment: expose required default tools

- [x] 1.1 Add abstract `default_tools` property to `Environment` in `tkt/_environment.py`.
- [x] 1.2 Remove the `_DEFAULT_TOOLS` fallback constant from `tkt/_environment.py`.
- [x] 1.3 Implement `default_tools` in `RubinEnvironment` reading the required `data["default_tools"]` key so that a missing key raises an error naming it.

## 2. Config: add default_tools to local.json

- [x] 2.1 Add a `default_tools` key to `local.json` with the current default list.

## 3. CLI: new starts from defaults with add/remove options

- [x] 3.1 In `_cli.py`, replace the `new --tool` option with repeatable `--add-tool` and `--remove-tool` options.
- [x] 3.2 After loading the environment in `new`, compute the tool set as `env.default_tools` plus each `--add-tool`, minus each `--remove-tool` (removal takes precedence when a name is both added and removed), and pass it to `Workspace.new`.

## 4. Workspace: accept tools on update

- [x] 4.1 Add a `tools: Iterable[str] = ()` parameter to `Workspace.update`.
- [x] 4.2 Append to `self._tools` preserving order and deduping against existing entries.
- [x] 4.3 Ensure the existing `_write_tools(environment)` call at the end of `update` installs newly added tools.

## 5. CLI: update reconciliation logic

- [x] 5.1 In `_cli.py` `update`, compute `missing` (default tools not in workspace.tools) and `stale` (workspace tools with `env.get_tool(t) is None`).
- [x] 5.2 In dry-run, log proposed additions/removals and return without prompting or applying.
- [x] 5.3 Warn and remove each stale tool from the workspace.
- [x] 5.4 For missing tools, single `click.confirm` listing proposed additions; on confirm, pass additions to `Workspace.update`.
- [x] 5.5 When `env.default_tools` references a tool not configured, ensure `_write_tools` raises an error naming it (existing behavior; verify message contains the name).

## 6. Verification

- [x] 6.1 Run `ruff check .`, `ruff format --check .`, and `mypy tkt/`.
- [x] 6.2 Sanity-check `tkt new` and `tkt update` behavior manually against a scratch workspace (including `--add-tool`/`--remove-tool`).
- [x] 6.3 Run `openspec validate --change configurable-default-tools` (or `openspec doctor`/`status`) to confirm artifacts are consistent.
- [x] 6.4 Add a `direnv-tool` delta spec and update the proposal's Modified Capabilities to reflect the `--tool` → `default_tools` change.
