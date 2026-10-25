# Agent Instructions

## Sandboxing and Harness Inconsistencies

Agents are almost always run in a `bwrap` sandbox with no network and `/home`
mounted as `tmpfs` (with real mounts layered on top).

Extensions to your harness are under frequent development. If tools don't
behave as expected or interact badly with the sandbox, ALWAYS report the issue,
and DO NOT attempt a workaround without human permission. Improving your
harness through feedback is a side goal for every project.

## Coding style preferences

These are not hard rules, and they all have an implied "within reason" limit:

- Inline most single-use, single-line private helper functions.
  Consider inlining simple private functions with few callers, especially if
  they're not establishing important shared behavior.

- Avoid assigning property values to local variables.

- Avoid single-use local variables with simple definitions.

## Prose style guidelines

### The reader

Everything you write into code is read by **a developer who finds this code in
two years via `git blame`**. They did not see the chat that produced it. They
do not know what the code looked like before, what was tried and rejected, or
what the prompt said. They know Python, C++, etc, they know the stack, they can
read the diff.

That reader is the _only_ audience. Not the person in the current conversation,
not the reviewer of this PR, not yourself.

### The two tests

Before writing any comment, docstring line, or commit body, apply both:

1. **Deletion test**: if I delete this, what does the reader lose? If the
   answer is "nothing" or "they'd have to read the next line", delete it.
2. **Stranger test**: can the reader act on this without the chat log? If it
   only makes sense to someone who watched the work happen, cut it or rewrite it
   as a fact about the code.

Don't use a paragraph where a sentence works. Don't use a sentence where the
code already says it.

### Never write these

These are the specific failure modes reviewers flag. All are chat leaking into
the repo.

| Category                 | Example to never write                                                  |
| ------------------------ | ----------------------------------------------------------------------- |
| Conversation artifacts   | `# As requested, switched to a dict here`                               |
| Review/process narration | `# Addressed reviewer feedback on the loop`                             |
| Spec/plan references     | `# Fills in stub from task 4`                                           |
| Thinking out loud        | `# We need to be careful here, so let's first check bounds`             |
| Diff narration           | `# Changed from a list to a set for speed`                              |
| Restating the code       | `# Increment the counter` above `counter += 1`                          |
| Self-assessment          | `# This is a robust, comprehensive solution`                            |
| Defensive justification  | `# This is correct because the loop always terminates`                  |
| Temporal language        | `# New approach:` / `# Now we use astropy` / `# Updated to handle None` |
| Usage docs in commits    | how to call the API, which belongs in a docstring                       |

**Temporal language deserves emphasis.** Code has no memory of the diff that
created it. "New", "now", "updated", "previously", "instead of" are meaningless
to the reader; there is no old version on their screen. Describe what the code
_is_, not how it got that way. The one place change-relative language belongs is
the commit message, which is the only artifact that is _about_ a change.

## Test fixture guidelines

Fixture realism and legibility are top priorities. Prefer real
data/configuration to synthetic data and synthetic data to mocking, and ask the
human about the properties of the data insted of guessing or extrapolating.

A complex but realistic fixture (e.g. a multi-task pipeline that exhibits many
edge cases) used by many tests is often a better choice than a multitude of
smaller per-test fixtures, even if it adds some extra coupling:

- Humans who read the code have experience understanding complex production
  scenarios and data, allowing them to more easily understand your tests by
  analogy.

- Realistic scenarios often have important invariants that aren't guaranteed by
  code _now_ but might be in the future, so overly-synthetic tests are fragile.

- Realistically complex fixtures make it easy to add new tests in the future.
