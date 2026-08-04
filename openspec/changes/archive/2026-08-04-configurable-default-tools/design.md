## Context

Currently the "default" tool list is hardcoded in `_cli.py` on the `new` command's `--tool` option (`default=("zed", "pyright", "sandbox", "precommit", "openspec", "direnv")`). `tkt update` never reconciles a workspace's recorded tools (in `tkt.json`) with configuration.

The config file (`local.json`) has a `tools` dict (all available tools and their definitions) but no notion of defaults, and `Environment`/`RubinEnvironment` have no access to a default list. `Workspace` stores the installed tool names in `tkt.json` and calls `_write_tools(environment)` which iterates `self._tools` and invokes each `Tool.write(...)`.

## Goals / Non-Goals

**Goals:**
- Make the default tool list configurable in the environment config JSON.
- Have `tkt new` start from that configured default and adjust it per invocation with `--add-tool`/`--remove-tool`.
- Have `tkt update` reconcile a workspace's tools against the config: propose missing defaults (single interactive prompt), remove tools no longer configured (with a warning), and leave configured-but-not-default tools in place.
- In `--dry-run`, log instead of prompt/apply.

**Non-Goals:**
- A `--yes`/`--no-tools` non-interactive flag (explicitly out of scope).
- Prompting per tool (single combined prompt).
- Removing tools that are still configured but no longer in the default list (retained).
- Changing the `tools` config section semantics.

## Decisions

### D1. Config schema: add a top-level `default_tools` array

`local.json` gains `"default_tools": ["zed", "pyright", "sandbox", "precommit", "openspec", "direnv"]` — the list moved verbatim from the CLI. Names must correspond to keys of `tools`. We chose a separate key over reusing the `tools` dict's key order because `tools` is a full mapping of *available* tools (many of which are not defaults, and the order is not meaningful for defaults).

### D2. Expose the default list via the `Environment` ABC

Add an abstract property `default_tools: tuple[str, ...]`, implemented in `RubinEnvironment` by reading the required `data["default_tools"]` key. Because the key is required, `from_json_data` raises `KeyError` naming `default_tools` when it is absent — we rely on that rather than defining a fallback list. Routing through the `Environment` ABC keeps `_cli.py` thin and testable and lets both `new` and `update` share the same source of truth.

### D3. `tkt new` computes the tool set from defaults plus add/remove

Replace the single `--tool` option with `--add-tool NAME` and `--remove-tool NAME` (each repeatable). `new` starts from `env.default_tools`, applies every add, then every remove. Removal takes precedence when a name is both added and removed, giving a deterministic outcome.

The old `--tool` was an "override": passing it discarded the whole configured default set, which is confusing when the default is essentially all available tools. Add/remove starts from the defaults and only modifies them, which matches how the option is actually used. Adding a tool that is not configured still fails via `_write_tools`'s `LookupError` naming the tool; removing a non-default/non-configured tool is a no-op.

### D4. `tkt update` reconciliation lives in the CLI command

The `update` CLI command computes the reconciliation using `env` and `workspace`, prompts the user, and passes any confirmed additions into `Workspace.update`. The `Workspace` class gains a way to add tools but stays prompt-free and testable.

Concretely:
- Compute `missing = [t for t in env.default_tools if t not in workspace.tools]` and `stale = [t for t in workspace.tools if env.get_tool(t) is None]`.
- In dry-run: log proposed additions/removals and return without applying.
- Otherwise: warn+log removals; for `stale`, remove from workspace tools; for `missing`, one `click.confirm` listing the tools; on confirm, pass `tools_to_add` to `Workspace.update`.

### D5. Workspace.update accepts tools to add

Add a `tools: Iterable[str] = ()` parameter to `Workspace.update`. It appends to `self._tools` (dedup, preserving order). The existing `_write_tools(environment)` call at the end of `update` then installs the newly added tools. `_write_tools` already raises `LookupError(f"No editor configuration for {name}.")` naming the missing tool, satisfying the spec's "names the tool" requirement.

For dry-run, the CLI does not call `Workspace.update` with additions (it returns early after logging), so no dry-run changes to `update` are needed.

### D6. Removal path

`_write_tools` only iterates `self._tools`, so removing a tool is simply not writing it and persisting the updated list via `_write_description`. Removing the name from `self._tools` means its config is no longer written, which is the correct outcome for a tool no longer configured.

## Risks / Trade-offs

- [User has default_tools referencing a tool not in `tools`] → `_write_tools` raises `LookupError` naming the tool; acceptable per AGENTS.md, and the name is included in the message.
- [Prompt in non-interactive environments] → The user chose interactive-only. A run without a TTY that hits a prompt will fail; that is the accepted trade-off.
- [Order sensitivity] → Newly added tools are appended in `default_tools` order after existing ones; order is preserved deterministically.

## Migration Plan

No deployment: `tkt` is used from a git clone. Update `local.json` to add `default_tools`. Existing workspaces are reconciled on their next `tkt update`.

## Open Questions

None.
