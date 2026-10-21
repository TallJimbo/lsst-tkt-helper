# Remove the Plannotator wake plugin — design spec

Date: 2026-10-20
Status: approved in chat (Gate 1)
Supersedes: the wake layer of `docs/superpowers/specs/2026-10-12-host-session-services-design.md`
(§3.6 and related plan Task 8). The rest of that design (host session
services, bridge socket, MCP session tools, registry) is unchanged.

## Problem

`harnesses/opencode/plugin/plannotator-wake.js` polls the host session server
over the bridge socket and injects feedback into the live OpenCode session via
`ctx.session.prompt({sessionID, text})` (default `resume:true`).

Under ACP this spawns a detached headless agent run. Root cause (verified
against `investigations/opencode-src`, findings summarized here):

- The plugin's `session.prompt` is byte-for-byte the same HTTP endpoint the ACP
  turn uses (`POST /api/session/:sessionID/prompt`); with default `resume` it
  durably admits an inbox row and calls `SessionExecution.wake(sessionID)`
  (`packages/core/src/session/session.ts`, `run-coordinator.ts`).
- ACP subscriptions are **per `session/prompt` request**
  (`packages/cli/src/acp/turn.ts`): between client prompts the session is idle
  from the ACP layer's perspective. A wake ring then starts a fresh server-side
  execution fiber with **no ACP subscriber** — output lands only in durable
  history; the ACP client never sees it and has no turn to resume. This is the
  observed "headless background agent" bug.
- ACP has no server→client user-message injection at all: user-role content
  flows only client→agent; `session.inbox.delivered` has no relay case in
  `translate.ts`. The server can only coalesce a steer into an *active* turn
  (racing the started-gate) or spawn an orphan successor run.
- No ACP-mode flag exists on the plugin ctx. The only signal is
  `process.env.OPENCODE_CLIENT === "acp"`
  (`packages/cli/src/commands/handlers/acp.ts` sets it; inherited by the
  spawned server), which describes the launching *process*, not per-session
  ownership — a fragile gate.

## Decision

**Remove the wake layer entirely** (considered and rejected: gating injection on
`OPENCODE_CLIENT=acp` — near-dead code with a process-level detector that is
wrong for shared servers; `resume:false` queueing — injects a phantom user
message beside the human's next prompt and wakes nothing). The user is mostly
on ACP; even a fixed plugin would be inert there.

**The pull path becomes the expected path everywhere:** after `open` (or any
review launch), hand the URL verbatim and end the turn. Feedback is *not
pushed*. When the human prods (or when the agent needs the outcome), call
`plannotator` `action='status'` for session ids and `action='result'` for each
outcome. Never busy-poll on a timer.

## Changes

### 1. Delete

- `harnesses/opencode/plugin/plannotator-wake.js`
- `tests/test_wake_plugin_client.py` (entire file)

### 2. `tkt/install.py`

Installed copies live in `~/.config/opencode/plugin/` and OpenCode autoloads
them, so deleting the source alone would leave every existing host install
still loading the plugin. Use the existing legacy-removal machinery:

```python
PLUGIN_NAMES: tuple[str, ...] = ("zed-bell",)

# tkt-owned plugin artifacts from older installs that live in the host plugin
# dir but must be *removed* on install (they would otherwise keep loading
# alongside their replacements).
LEGACY_PLUGIN_NAMES: tuple[str, ...] = ("zed-bell.js", "plannotator-wake.js")
```

- Remove the now-unused `wake_plugin_source()` / `wake_plugin_dest()` helpers
  (their only non-test consumer is the sandbox warning removed in §3).
- `install_opencode_agent` docstring: drop "wake +" from the plugin-copy
  sentence (zed-bell only).
- `plugin_source`/`plugin_dest`/`_install_plugin` are generic over `name` and
  stay as-is.

### 3. `tkt/sandbox.py`

- Delete `_warn_if_wake_plugin_stale()` (lines ~116–133), its call site in
  `_build_bwrap_argv` (~1012–1017, including the wake-specific comment block;
  the surrounding session-bridge env block stays), the
  `from .install import wake_plugin_dest, wake_plugin_source` import, and the
  `filecmp` import (used only there).
- `opencode_config_content` docstring: keep the "env-config `plugin` array is
  NOT honored" fact (still explains why *zed-bell* is a copied file), drop the
  wake-plugin tail.

### 4. Instruction surfaces (pull becomes the documented path)

`tkt/AGENTS.md.in` — replace (lines ~48–60, keeping surrounding structure):

> Hand the human any URL the tool returns **verbatim** and end your
> turn — feedback arrives later as `Plannotator:`-prefixed messages or when you
> ask the `plannotator` tool for the session `result`.

with:

> Hand the human any URL the tool returns **verbatim** and end your
> turn — feedback is not pushed to you. When the human prods you (or when you
> need the outcome), call the `plannotator` tool's `status` action to list
> session ids and `result` to fetch each outcome.

`tkt/_session_tools.py` — `plannotator_inbox` docstring: drop
"or as injected ``Plannotator:`` messages"; replies arrive via `wait`/`read`.
Also (added at review): the `plannotator` `open` docstring's
"(feedback arrives later, or via ``action=\"result\"`` …)" becomes
"(feedback is not pushed to you — pull it with ``action=\"result\"`` once the
human says it is in)".

`superpowers/skills/subagent-driven-development/SKILL.md` (Human browser review
section, ~line 498) — replace:

> One session at a time: end your turn after each launch and wait for the
> `Plannotator:` message; never poll it.

with:

> One session at a time: end your turn after each launch; when your human
> partner prods you with the outcome, pull it with the `plannotator` tool's
> `result` action — never busy-poll on a timer.

This lands as a commit in the `superpowers/` submodule (TallJimbo fork) with a
pin bump in tkt2; submodule commit precedes the tkt2 pin-bump commit.

`tkt/_sessionmanagers.py` — comments/docstrings only: the `status` listing
"for the wake layer" becomes "so agents can route `result` calls" (~lines
573–585). No behavior change (registry id/target routing stays; it is what
agent-side `result` calls use).

### 5. Docs & stray comment

- Root `AGENTS.md` OpenCode-integration paragraph (~line 152): plugin copies are
  just the zed-bell directory plugin (drop the wake-plugin mention).
- `harnesses/opencode/plugin/zed-bell/tui.js` header comment (~line 16): drop
  "of plannotator-wake.js" from "Keep the never-throw posture of …" (keep the
  posture statement).
- Historical specs/plans under `docs/superpowers/` are not edited (they record
  what was built).

## Not touched

Session-server bridge (`session.sock`, socat, `tkt.session_server`), MCP
session tools and their actions, the registry/status sweep, brainstorming
companion delivery (always pull/`session`-tool based), remote-MCP proxying.

## Testing

- Delete `tests/test_wake_plugin_client.py`.
- `tests/test_install.py`: remove the wake fixture (`// wake plugin v1`) and
  wake-specific assertions/imports (`wake_plugin_source`/`dest`); **add** a
  test that a pre-existing
  `~/.config/opencode/plugin/plannotator-wake.js` copy is *removed* by
  `install_opencode_agent` (mirrors the existing `zed-bell.js` legacy test);
  keep the stale-copy-replaced-by-legacy semantics correct (a lexists() file of
  any kind is removed).
- `tests/test_sandbox.py`: remove the two wake-stale-warning tests
  (~lines 525–580).
- `tests/test_session_managers.py`: comment wording only.
- Gate: `python -m pytest` all green; `ruff check .`, `ruff format --check .`,
  `mypy tkt/`.

## Git workflow

Feature branch; first commit the spec (and the plan, once sp-plan runs).
Ordering: superpowers submodule commit → tkt2 pin bump + code/test/doc commits.
Suggest ending with a single squashed commit on main per repo convention
(the submodule commit necessarily stays separate).
