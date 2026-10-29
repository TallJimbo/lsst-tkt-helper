---
description:
  "Native Plan-mode agent for tkt workspaces: read-only exploration and
  planning. Superpowers workflow skills are disabled; approve the plan in
  conversation or review plan files via the host-side Plannotator tools."
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
  # No network tools (sp-design parity; restricted sandboxes have none).
  - { action: webfetch, resource: "*", effect: deny }
  - { action: websearch, resource: "*", effect: deny }
  # GitHub MCP: read-only search/fetch, matching sp-design. The global
  # opencode.jsonc additionally denies the GitHub write tools per tool.
  - { action: "github_*", resource: "*", effect: deny }
  - { action: github_search_repositories, resource: "*", effect: allow }
  - { action: github_search_pull_requests, resource: "*", effect: allow }
  - { action: github_search_code, resource: "*", effect: allow }
  - { action: github_get_file_contents, resource: "*", effect: allow }
---
