# R2 Batch 2 — Sandboxed `grep`, `glob`, `ls` Tools — Design Handover

**Date:** 2026-09-02
**Status:** Approved by human in conversation (before implementation).
**Implements:** batch 2 (the `Grep`, `Glob`, `LS` tools) of phase R2 in
`docs/zed-agent-roadmap.md`.

## Goal

Add sandboxed `grep`, `glob`, and `ls` MCP tools to the tkt MCP server — the
Claude-Code-shaped search/find/list tools — and fold them into the Zed harness
(skills mapping + `zed-explorer` wording), so the Zed native agent searches and
lists through the sandbox (blocking `$HOME`, honoring workspace mounts) rather
than Zed's native, unsandboxed `grep`/`find_path`/`list_directory`. This is the
second R2 MCP tool batch, following the already-landed `read` tool. OpenCode is
untouched throughout.

## Architecture

Same channel as `read` and `bash`: each tool builds a small command and runs it
through the **existing warm holder** (`WarmSandbox.run`), which gives it the
sandbox's mount model automatically (workspace read-only + `.agent`/git
writable, `$HOME` tmpfs-blocked, `~/.agents/skills` and configured ro/rw
mounts) and returns via the existing base64 framing. The MCP server process runs
host-side, so opening files or listing directories in the server would be
unscoped; running through `warm` is what keeps these tools sandboxed. No new
sandbox infrastructure or mounts are needed.

Because the sandbox bind-mounts `/` read-only (bwrap `--ro-bind / /`), the host's
standard `grep`, `ls`, and `bash` (for globstar) are guaranteed available.

### Result types

Distinct result type per tool, per explicit human preference — even though the
fields happen to coincide (`content: str`, `truncated: bool`); that duplication
is accepted as incidental. They mirror `ReadResult` (content carried directly,
not wrapped in the `BashResult` bash-execution envelope), which is the
Claude-Code-shaped surface: Claude Code's `Grep`/`Glob`/`LS` return their output
directly rather than an exit-code envelope.

### Tool shape and naming

- MCP tool names are **`ls`**, **`glob`**, **`grep`** (bare names, no `tkt:`
  prefix — consistent with the naming decision from the `read` batch).
- Signatures:
  - `ls(path=".")` -> `LSResult`
  - `glob(pattern, path=".")` -> `GlobResult`
  - `grep(pattern, path=".", glob=None, output_mode="content",
    ignore_case=False, line_number=False)` -> `GrepResult`
- `output_mode` for `grep`: `content` (default) | `files` (`-l`) | `matches`
  (`-o`).

### Data flow

1. Host builds a sandbox command from the args (`path`/`pattern` embedded via
   `shlex.quote`; flags assembled in Python).
2. The command runs in the warm holder and its output is returned through the
   existing base64 framing as a `BashResult`.
3. Host maps the `BashResult` to the tool's result type: rc 0 -> `stdout` as
   `content`; rc != 0 -> `stderr` as an error-prefixed `content` (for `ls`/
   `glob`); `grep` additionally normalizes rc 1 (grep's "no matches") to empty
   `content` rather than surfacing it as an error.
4. `content` is capped with the existing `truncate_output(..., _MAX_OUTPUT_CHARS)`
   and `truncated` is set accordingly — same defense-in-depth as `read` (the
   driver already hard-caps each stream at 50 KB and SIGPIPE-kills a runaway
   producer; this bounds the model-facing context).

All formatting/logic after the sandbox lives in host Python, so it is testable
in pure Python with a mocked `warm`, exactly like the existing `read_tool` tests.

## Concretes (verbatim — authoritative for implementation)

### `tkt/mcp_server.py` additions

`__all__` additions:

```python
    "LSResult",
    "GlobResult",
    "GrepResult",
    "build_ls_command",
    "build_glob_command",
    "build_grep_command",
    "ls_tool",
    "glob_tool",
    "grep_tool",
```

New models (after `ReadResult`):

```python
class LSResult(BaseModel):
    """The outcome of one sandboxed ``ls`` call.

    ``content`` is a ``ls -laF``-style listing of ``path``; ``truncated`` is
    True when the output was cut to the model-facing cap.
    """

    content: str
    truncated: bool


class GlobResult(BaseModel):
    """The outcome of one sandboxed ``glob`` call.

    ``content`` is one matching path per line; ``truncated`` is True when the
    output was cut to the model-facing cap.
    """

    content: str
    truncated: bool


class GrepResult(BaseModel):
    """The outcome of one sandboxed ``grep`` call.

    ``content`` holds the matches in the requested ``output_mode``; an empty
    ``content`` means no matches were found (not an error). ``truncated`` is
    True when the output was cut to the model-facing cap.
    """

    content: str
    truncated: bool
```

Command builders (module-level, after `build_read_command`):

```python
def build_ls_command(path: str) -> str:
    """Build the sandbox command that lists ``path``.

    ``ls -laF`` lists all entries in long format with type indicators; ``--``
    guards a path that begins with ``-``. ``path`` is embedded via
    ``shlex.quote``.
    """
    return f"ls -laF -- {shlex.quote(path)}\n"


def build_glob_command(pattern: str, path: str) -> str:
    """Build the sandbox command that finds files matching a glob.

    ``globstar`` makes ``**`` recurse across directories and ``nullglob`` drops
    unmatched patterns, so a no-match yields empty output (rc 0). The pattern is
    assigned to a quoted variable (preventing shell injection) and then
    ``for f in $pattern`` glob-expands it; ``[ -e \"$f\" ]`` filters literals
    that do not exist. ``path`` and ``pattern`` are embedded via
    ``shlex.quote``.
    """
    quoted_path = shlex.quote(path)
    quoted_pattern = shlex.quote(pattern)
    return (
        f"cd {quoted_path} && "
        "shopt -s globstar nullglob && "
        f"pattern={quoted_pattern} && "
        'for f in $pattern; do [ -e "$f" ] && printf \'%s\\n\' "$f"; done\n'
    )


def build_grep_command(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    output_mode: str = "content",
    ignore_case: bool = False,
    line_number: bool = False,
) -> str:
    """Build the sandbox command that searches file contents.

    ``grep -rE -IH`` searches recursively with extended regex, skipping binary
    files (``-I`` avoids the UTF-8 framing failure ``read`` defends against) and
    always prefixing filenames (``-H``). ``--exclude-dir=.git`` skips the
    mounted git dir; ``--include`` (when ``glob`` is given) restricts to
    matching files. ``output_mode`` maps to ``-l`` (files) or ``-o`` (matches);
    ``line_number`` adds ``-n`` (content mode). ``-e`` precedes the pattern so
    patterns beginning with ``-`` work. ``pattern`` and ``path`` are embedded
    via ``shlex.quote``.
    """
    flags = ["-r", "-E", "-I", "-H"]
    if ignore_case:
        flags.append("-i")
    if glob is not None:
        flags.append(f"--include={shlex.quote(glob)}")
    flags.append("--exclude-dir=.git")
    if output_mode == "files":
        flags.append("-l")
    elif output_mode == "matches":
        flags.append("-o")
    elif line_number:
        flags.append("-n")
    flag_str = " ".join(flags)
    return (
        f"grep {flag_str} -e {shlex.quote(pattern)} -- {shlex.quote(path)}\n"
    )
```

Tool helpers (module-level, testable with a mocked `warm`):

```python
def ls_tool(warm: WarmSandbox, *, path: str = ".") -> LSResult:
    """Run one sandboxed ``ls`` against ``warm`` and return an :class:`LSResult`."""
    result = warm.run(build_ls_command(path))
    if result.exit_code != 0:
        err = (result.stderr or result.stdout or "").strip()
        return LSResult(content=f"ls: {err}", truncated=False)
    content, truncated = truncate_output(result.stdout, _MAX_OUTPUT_CHARS)
    return LSResult(content=content, truncated=truncated)


def glob_tool(warm: WarmSandbox, *, pattern: str, path: str = ".") -> GlobResult:
    """Run one sandboxed ``glob`` against ``warm`` and return a :class:`GlobResult`."""
    result = warm.run(build_glob_command(pattern, path))
    if result.exit_code != 0:
        err = (result.stderr or result.stdout or "").strip()
        return GlobResult(content=f"glob: {err}", truncated=False)
    content, truncated = truncate_output(result.stdout, _MAX_OUTPUT_CHARS)
    return GlobResult(content=content, truncated=truncated)


def grep_tool(
    warm: WarmSandbox,
    *,
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    output_mode: str = "content",
    ignore_case: bool = False,
    line_number: bool = False,
) -> GrepResult:
    """Run one sandboxed ``grep`` against ``warm`` and return a :class:`GrepResult`.

    ``grep``'s rc 1 (no matches) is normalized to empty ``content`` rather than
    reported as an error.
    """
    result = warm.run(
        build_grep_command(pattern, path, glob, output_mode, ignore_case, line_number)
    )
    if result.exit_code == 1:
        return GrepResult(content="", truncated=False)
    if result.exit_code != 0:
        err = (result.stderr or result.stdout or "").strip()
        return GrepResult(content=f"grep: {err}", truncated=False)
    content, truncated = truncate_output(result.stdout, _MAX_OUTPUT_CHARS)
    return GrepResult(content=content, truncated=truncated)
```

The MCP tools are registered inside `run_server`, after `read`:

```python
    @mcp.tool()
    def ls(
        path: str = ".",
        description: str | None = None,  # present for human approvals of tool actions
    ) -> LSResult:
        """List files and subdirectories at ``path`` inside the tkt sandbox.

        Equivalent to ``ls -laF`` (all entries, long format, type indicators).
        The sandbox blocks ``$HOME`` (so credentials are never exposed) but
        mounts the workspace and the read-only ``~/.agents/skills`` directory.
        ``description`` is a per-call rationale for the human; it does not
        change behavior.

        Args:
            path: Directory to list (default ".").
            description: Optional human-readable rationale for this call.
        """
        return ls_tool(warm, path=path)

    @mcp.tool()
    def glob(
        pattern: str,
        path: str = ".",
        description: str | None = None,  # present for human approvals of tool actions
    ) -> GlobResult:
        """Find files under ``path`` matching the glob ``pattern`` in the sandbox.

        ``*`` matches within a directory; ``**`` matches recursively across
        directories (bash ``globstar``). Hidden entries require an explicit
        leading dot. Returns one matching path per line. ``description`` is a
        per-call rationale for the human; it does not change behavior.

        Args:
            pattern: The glob pattern to match.
            path: Directory to search under (default ".").
            description: Optional human-readable rationale for this call.
        """
        return glob_tool(warm, pattern=pattern, path=path)

    @mcp.tool()
    def grep(
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        output_mode: str = "content",
        ignore_case: bool = False,
        line_number: bool = False,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> GrepResult:
        """Search file contents under ``path`` for a regex in the sandbox.

        ``output_mode`` is ``content`` (default), ``files`` (paths only), or
        ``matches`` (matched text only). ``ignore_case`` is case-insensitive;
        ``line_number`` prefixes line numbers (content mode); ``glob`` restricts
        to matching file paths. Binary files are skipped. No matches yields an
        empty ``content`` (not an error). ``description`` is a per-call
        rationale for the human; it does not change behavior.

        Args:
            pattern: The regular expression to search for.
            path: Directory to search under (default ".").
            glob: Optional glob restricting which files are searched.
            output_mode: "content", "files", or "matches".
            ignore_case: Case-insensitive search.
            line_number: Prefix line numbers in content mode.
            description: Optional human-readable rationale for this call.
        """
        return grep_tool(
            warm,
            pattern=pattern,
            path=path,
            glob=glob,
            output_mode=output_mode,
            ignore_case=ignore_case,
            line_number=line_number,
        )
```

### `superpowers/skills/using-superpowers/references/zed-tools.md`

Three table rows change (note: this file lives inside the `superpowers`
submodule; see Open items):

```markdown
| Search file contents                                         | `grep`  |
| Find files by name                                           | `glob`  |
| List a directory                                             | `ls`    |
```

(replacing `grep`, `find_path`, `list_directory` respectively).

### `harnesses/zed/skills/zed-explorer/SKILL.md`

Lines ~13-15: the "Investigation discipline" sentence changes

```markdown
- Find files with `find_path` (glob patterns); search contents with `grep`;
  read with the `read` tool; list with `list_directory`; run shell commands with
```

to

```markdown
- Find files with `glob` (glob patterns); search contents with `grep`;
  read with the `read` tool; list with `ls`; run shell commands with
```

### `README.md`

Lines ~57-59 ("Other" block) note that `mcp-server` exposes the sandboxed `bash`
tool; extend to name the read/execute surface:

```markdown
**Other** — `mcp-server` runs the MCP stdio server that exposes the sandboxed
`bash`, `read`, `grep`, `glob`, and `ls` tools, and `fix-openspec` rewrites
OpenSpec skill files for OpenCode's harness.
```

### `docs/zed-agent-roadmap.md`

Mark batch 2 in section 6 R2 as done:

```markdown
2. `Grep`, `Glob`, `LS`. **DONE, 2026-09-02**
```

The target-suite table (section 4) already lists Grep/Glob/LS as "tkt MCP
(sandboxed)" and needs no change.

### Explicitly NOT changed: `harnesses/zed/rules.md`

The existing "Tool changes" note there only exists because the Zed system prompt
*unconditionally* references `read_file` (line 245) and line 39's generic
"use file tools / search tools / terminal commands" phrasing. Verified against
`investigations/zed-src/crates/agent/src/templates/system_prompt.hbs`:

- `grep` and `find_path` are referenced only inside
  `{{#if (contains available_tools 'grep')}}` (lines 60-63). Disabling `grep`
  in the profile (which this batch does) removes that whole guidance block.
- `list_directory` and `glob` appear nowhere in the system prompt — only in the
  tool schema.

So there is no unconditional system-prompt prose naming the tools being
replaced, and no `rules.md` change is motivated for this batch.

## Key decisions log

1. **`grep`/`glob`/`ls` run through the warm holder, not host IO** — same
   rationale as `read`: keeps them sandboxed because the server process is
   host-side.
2. **Distinct result type per tool** (`LSResult`/`GlobResult`/`GrepResult`), even
   though fields coincide — explicit human preference; duplication is incidental
   and accepted.
3. **Return content directly, not the `BashResult` envelope** — Claude Code's
   `Grep`/`Glob`/`LS` return their output directly; `ReadResult` is the
   in-repo analog.
4. **`grep` rc 1 normalized to empty content** — grep's "no matches" is a normal
   outcome, not an error.
5. **`-I` (skip binary) for `grep`** — avoids the UTF-8 framing failure `read`
   defends against; **`--exclude-dir=.git`** skips the mounted git dir.
6. **`glob` via bash globstar + nullglob + `[ -e ]`**, pattern passed through a
   quoted variable so it can't inject shell while still glob-expanding.
7. **No `harnesses/zed/rules.md` change** — the only system-prompt prose naming
   `grep`/`find_path` is gated on `available_tools 'grep'` and disappears when
   the tool is disabled; no prose names `list_directory`/`glob` (verified in the
   Zed source).
8. **Machine-side profile + system-prompt override are human-applied** — the
   Zed agent profile (disable native `grep`/`find_path`/`list_directory`, enable
   MCP `grep`/`glob`/`ls`) and the override live on the human's machine and are
   **not** in this repo; delivered as a paste-ready snippet in the plan.
9. **OpenCode untouched** — coexistence maintained (roadmap Goal 4).

## Open items / assumptions

- **`zed-tools.md` lives in the `superpowers` submodule.** Editing it means
  committing inside the submodule, then bumping the submodule pointer in the
  main repo (convention from `a2e1e84 Update superpowers submodule for Zed tool
  mapping`). The implementing agent must do both, or flag it if the submodule is
  pinned/read-only for this work.
- **Machine-side profile + override** (Zed profile disabling native
  `grep`/`find_path`/`list_directory`, enabling MCP `grep`/`glob`/`ls`; and the
  system-prompt override). The implementing agent must NOT edit them; a
  paste-ready template is delivered in the plan's final chat summary for the
  human.
- **`ls -laF` flag choice** — shows hidden entries and type indicators; a
  deliberate, easily-adjusted default.
