# Zed Agent Dispatch Table

Read by every agent (primary and subagent) at session start. Load the skill
indicated by your role and the task you were given.

## If you are a PRIMARY agent

Load `zed-primary-agent` and follow its instructions.

## If you are a SUBAGENT (dispatched via spawn_agent)

Load the skill matching the task you were asked to do:

- explore the codebase / find files / answer "how does X work" → `zed-explorer`
- implement a task (brief + report file) → `zed-implementer`
- review a task's diff → `zed-reviewer` (scope: task)
- re-review a fix round → `zed-reviewer` (scope: re-review)
- final whole-branch review → `zed-reviewer` (scope: final)

## Harness bug reporting

The harness configuration you're running under is still under development. If
you notice any inconsistencies or unexpected permission blocks (e.g. files you
can access via the `bash` tool but not more precise tools), surface those
issues to the user.

## Tool changes

System prompts may reference a `read_file` tool; use the sandboxed `read` tool instead.
