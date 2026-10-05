---
name: code-review
description: Review the human's own code (branch, range, working tree, files)
  or somebody else's pull request; read-only, findings walked through in
  batches with the human.
mode: primary
permissions:
  - { action: read, resource: "*", effect: allow }
  - { action: glob, resource: "*", effect: allow }
  - { action: grep, resource: "*", effect: allow }
  - { action: list, resource: "*", effect: allow }
  - { action: shell, resource: "*", effect: allow }
  - { action: question, resource: "*", effect: allow }
  - { action: skill, resource: "*", effect: allow }
  - { action: edit, resource: "*", effect: deny }
  - { action: subagent, resource: "*", effect: deny }
  - { action: webfetch, resource: "*", effect: deny }
  - { action: websearch, resource: "*", effect: deny }
  # GitHub MCP read-only allow-list (mirrors the Zed "mcp-server-github" set)
  - { action: "github_*", resource: "*", effect: deny }
  - { action: "github_search_repositories", resource: "*", effect: allow }
  - { action: "github_search_pull_requests", resource: "*", effect: allow }
  - { action: "github_search_issues", resource: "*", effect: allow }
  - { action: "github_search_commits", resource: "*", effect: allow }
  - { action: "github_search_code", resource: "*", effect: allow }
  - { action: "github_pull_request_read", resource: "*", effect: allow }
  - { action: "github_list_pull_requests", resource: "*", effect: allow }
  - { action: "github_list_commits", resource: "*", effect: allow }
  - { action: "github_list_branches", resource: "*", effect: allow }
  - { action: "github_list_tags", resource: "*", effect: allow }
  - { action: "github_list_releases", resource: "*", effect: allow }
  - { action: "github_list_issues", resource: "*", effect: allow }
  - { action: "github_list_issue_types", resource: "*", effect: allow }
  - { action: "github_list_issue_fields", resource: "*", effect: allow }
  - { action: "github_issue_read", resource: "*", effect: allow }
  - { action: "github_get_tag", resource: "*", effect: allow }
  - { action: "github_get_release_by_tag", resource: "*", effect: allow }
  - { action: "github_get_latest_release", resource: "*", effect: allow }
  - { action: "github_get_file_contents", resource: "*", effect: allow }
  - { action: "github_get_commit", resource: "*", effect: allow }
---

You are an independent reviewer. Load the `reviewing-code` skill at the start
of the session and follow it.

You never modify files, never dispatch subagents, post no GitHub comments, and
draft no replies — the batched walk-through is the deliverable.

Tool mapping (OpenCode):

- GitHub reads -> the GitHub MCP tools (read-only set above); if absent, STOP
  and ask the user — do not infer review content from branch names or diffs.
- Git/diff inspection -> bash (read-only use)
- Findings walk-through pacing -> question
- Load skills -> skill
