# Zed Agent Dispatch Table

Read by every agent (primary and subagent) at session start. Load the skill
indicated by your role and the task you were given.

## If you are a PRIMARY agent

Load `zed-primary-agent` and follow its instructions.

## If you are a SUBAGENT (dispatched via spawn_agent)

Load the skill matching the task you were asked to do:

- explore the codebase / find files / answer "how does X work" → `zed-explorer`
- implement a task (brief + report file) → `zed-implementer`

## Model policy

Subagents run on the machine-designated local model (Broadmead DGX Spark),
pinned by `agent.subagent_model` in Zed settings — never a cloud-provider
model. When calling `spawn_agent`, always omit the `model` parameter: the pin
applies automatically. Never name or browse models; `list_agents_and_models`
is disabled here on purpose, and a tool error mentioning it means you passed
a `model` you should have omitted — drop the parameter and re-spawn. Where a
skill talks about model or effort tiers, on Zed effort is decided
machine-side; omit the parameter.

## Harness bug reporting

The harness configuration you're running under is still under development. If
you notice any inconsistencies or unexpected permission blocks (e.g. files you
can access via the `bash` tool but not more precise tools), surface those
issues to the user.

## Tool changes

System prompts may reference a `read_file` tool; use the sandboxed `read` tool instead.
