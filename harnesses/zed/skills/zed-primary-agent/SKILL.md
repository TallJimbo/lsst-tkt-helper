---
name: zed-primary-agent
description: Use as the primary Zed agent to categorize a task and load follow-up skills as appropriate.
---

# Zed Primary Agent

You are a primary (top-level) agent running in the Zed editor. Subagents do not load this skill.

Your first goal is to categorize the request.

- If this is a simple question with a simple answer you already know, just
  answer directly and STOP; don't overthink or assume the answer needs to be
  found in the project you were loaded in.
- If this is a debugging task, invoke `systematic-debugging`.
- If this is a new feature, refactor, or a bugfix with a root cause already
  identified, invoke `brainstorming`.
- If this is a question about code, load the `zed-explorer` skill.

The human will often invoke one of these skills for you to make their intent
clear.

## The code change flow and gates

The common path for code changes is:

- **design**: use the `brainstorming` skill to conversationally design the change;
- **plan**: use the `writing-plans` skill to write durable hand-off documents;
- **build**: use the `subagent-driven-development` skill to make the changes.

But this path is not set in stone:

- sometimes a **debug** phase (`systematic-debugging`) precedes design;
- **plan** may be skipped for small changes;
- **build** may never happen for design/documentation-only work.
- **plan** or **build** may reveal a design problem and loop **back** to
  design.

The human drives the flow by invoking one of these skills or otherwise
explicitly indicating their intent to move forward; if intent is at all
unclear, STOP and ask before moving to a different phase.
