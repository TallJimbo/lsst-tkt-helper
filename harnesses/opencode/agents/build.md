---
description:
  "Native Build-mode agent for tkt workspaces: implement with local tooling.
  Superpowers workflow skills are disabled and GitHub MCP access is hidden."
mode: primary
permissions:
  # Superpowers workflow skills belong to the sp-* profiles; native modes run
  # the plain OpenCode flow. Skills are gated per agent by id (the SKILL.md
  # name) via the `skill` permission.
  - { action: skill, resource: brainstorming, effect: deny }
  - { action: skill, resource: finishing-a-development-branch, effect: deny }
  - { action: skill, resource: requesting-code-review, effect: deny }
  - { action: skill, resource: subagent-driven-development, effect: deny }
  - { action: skill, resource: systematic-debugging, effect: deny }
  - { action: skill, resource: test-driven-development, effect: deny }
  - { action: skill, resource: using-superpowers, effect: deny }
  - { action: skill, resource: verification-before-completion, effect: deny }
  - { action: skill, resource: writing-plans, effect: deny }
  - { action: skill, resource: writing-skills, effect: deny }
  # No network tools (sp-build parity; restricted sandboxes have none).
  - { action: webfetch, resource: "*", effect: deny }
  - { action: websearch, resource: "*", effect: deny }
  # GitHub MCP fully hidden here (sp-build parity): read-only GitHub access
  # belongs to the code-review/pr-responder profiles, and agent-level "ask"
  # is fail-open under ACP clients. The global opencode.jsonc denies the
  # GitHub write tools per tool.
  - { action: "github_*", resource: "*", effect: deny }
---
