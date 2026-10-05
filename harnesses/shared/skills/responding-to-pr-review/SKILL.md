---
name: responding-to-pr-review
description: Use to work through PR review comments - categorize them, propose and land small fixes as fixup! commits, and produce batched responses for the human to post.
---

# Responding to a PR review

You are helping the human respond to review comments on their pull request.
The human writes every response actually posted to GitHub; you analyze,
propose code changes, and keep the bookkeeping tidy.

## Setup

- Establish the PR from the dispatch prompt or by asking.
- Check that GitHub read tools are available (see "GitHub access" below). If
  they are not, STOP and ask the human — this is probably an oversight. Do not
  try to infer review content from branch names or local diffs.
- Fetch all review comments and threads, then note the environment: the
  human's PR checkout (work directly in it) or a tkt sandbox branch (land
  work on the sandbox branch; it crosses over via `tkt pull-sandbox`).
  Open the walk-through with a one-line environment recap: worktree,
  branch, HEAD, and how fixups reach the human.
- Verify line numbers against the PR head before citing them; review-thread
  anchors are often stale relative to the current code. Resolve real paths
  (including subpackages and subdirectories) rather than guessing them.
- Look for an existing ledger at the repo root matching
  `pr-response-<owner>-<repo>-PR<number>.md`. If one exists and its first
  line names this PR, read it and resume from the first unsettled item
  rather than restarting the categorization.

## GitHub access

- **Zed:** verify read-only GitHub MCP tools are available; STOP and ask the
  human if they are absent.
- **OpenCode:** GitHub tools appear only for the agents the user has
  allow-listed (`code-review`, `pr-responder`); STOP and ask if absent. The
  remote GitHub MCP server is unreachable inside a network-restricted tkt
  sandbox — run the PR flows in a host OpenCode session (or `tkt
  sandbox-run --network`).

## Question tool

Use your harness's structured-question tool for every decision point (Zed:
`ask_user` with `allow_free_text: true`; OpenCode: `question`, which always
allows a typed answer).

## Ledger

Conversation memory does not survive compaction. Track progress in a
markdown ledger file, not only in todos.

- Ledger path: `pr-response-<owner>-<repo>-PR<number>.md` at the root of
  the repo holding the PR branch — plain sight, not a hidden directory.
  It is scratch; mention once that a global gitignore of
  `pr-response-*.md` keeps it out of `git status`.
- Create it with its identity as the first line:
  `# PR-response ledger — PR: <url> — branch: <branch name>`. Name the
  branch, not a SHA; rebasing churns SHAs.
- Append one line per event; never rewrite history, so a partial ledger
  is always valid:
  - after categorizing: `- cat: A1 verified ("Refactor parser") | N2 trivial typo`
  - after each decision:
    `- N1: decision=fix-now → fixup! "Add sort to results" (7c1d9e2, targets "Refactor parser")`
  - after each batch: `- batch addressed: presented, human confirmed`
- Identify commits by subject first, hash second. Rebases churn hashes;
  `fixup! <subject>` subjects survive by construction. On recovery,
  resolve subjects with `git log --grep` and treat recorded hashes as
  hints only; if a subject resolves to zero or multiple commits, ask the
  human rather than guessing.
- After compaction, trust the ledger and `git log` over your own
  recollection. Items with verdict/decision lines are settled — do not
  re-categorize or re-walk them; resume at the first unsettled item.
- A ledger whose first line names a different PR is another PR's
  progress: leave it in place and start your own, fresh.

## Categorize first

Before proposing any change, assign every comment one verdict, verified
against the code (diff from base to HEAD, recent commits) rather than the
review text alone, and give it a short label the human can use to refer to
it: `A1, A2, ...` (addressed), `N1, N2, ...` (needs work).

- **Addressed** — the branch already reflects the request; cite the commit
  or current lines. Also flag any already-posted reply whose promise
  contradicts the branch (e.g. "I'll add a sort" when a sort is already
  present) — the fix is a follow-up reply, not a fixup.
- **Needs work** — everything else, including trivial one-liners (typos,
  wording, obvious mechanical fixes). Trivial items get a one-line
  walk-through rather than a separate batch.

Present the full categorized list before acting on anything; the human may
re-bucket items. Ledger the final verdicts once settled (one `cat:` line is
enough).

## Presentation

- Bulk categories go in markdown tables with a label column
  (`Label | Comment | Status/action`).
- Pause after each bulk category and after each needs-work item rather than
  delivering everything in one message; the human decides before you move
  on.
- Use the question tool for multiple-choice questions about how to resolve
  or proceed (e.g. fix now / defer / explain only / disagree, or choosing
  between two proposed options).
- Reference source as [`file.py:LINE`](/absolute/path/to/file.py:LINE):
  display text is the short `file.py:line`, the target is an **absolute**
  path. Workspace-relative links do not resolve inside a multi-repo tkt
  workspace, and no harness auto-links bare paths — only the explicit
  absolute link is reliable. Point targets at the human's own checkout
  (the `.agent/<repo>/...` paths without the `.agent` prefix when in a
  sandbox), and at the `.agent/...` worktree when the content exists only
  there (e.g. a fixup you landed that has not been pulled over). Ranges do
  not link; use a single representative line or one reference per line.

## Walk through the "needs work" comments

One at a time, each as its own `### <label>. <short topic>` section: quote
the comment, give context (linked), your analysis, and a proposed change —
or a case for disagreeing. You never draft PR replies. The human decides
per comment: fix now / defer / explain only / disagree. Make code changes
only on an explicit go-ahead, gathered via the question tool. Ledger each
decision as soon as it is made.

## Batches

The addressed bucket accumulates as a compact `comment → action /
rationale` table row — what already covers it (commit or linked lines).
Tables are presented progressively (with pauses) rather than saved for the
end; finish the walk-through with a final confirmation pass so the human
can post one response per batch on a settled list. Compact rows, not draft
prose. Ledger each batch as `presented` and append a `human confirmed`
line once the confirmation pass settles it.

## Landing code changes

- Small changes: one `fixup! <subject>` commit per comment, targeted at the
  branch commit that introduced the code being fixed (ask if ambiguous).
  Run a quick lint/tests check before committing each fixup. Ledger the
  fixup with subject first and hash second, naming the target by subject.
- Larger changes: discuss first, land as ordinary commits.
- Never run a destructive rebase (e.g. `rebase --autosquash`) unsolicited;
  remind the human that the fixups are waiting instead.
