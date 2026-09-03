# tkt: Jira + EUPS + git development tooling

This package provides command-line tools that automate common development tasks that involve a combination of EUPS and git, as used in the Rubin Observatory Data Management system.

It creates EUPS metapackages that correspond to a single Jira ticket, containing git source repositories for multiple packages (generally on the same branch).

At present there is no documentation at all, and integration with Jira is entirely hypothetical; at present the ticket number is just something the user provides.

THIS PACKAGE IS LARGELY AI-AUTHORED AND MAY CONTAIN CODE THAT HAS NOT BEEN
HUMAN-REVIEWED.

## Usage

Run `tkt --help` (or `tkt <command> --help`) for a full list of options. The
commands fall into a few workflows:

**Workspace lifecycle** — create and maintain the EUPS metapackage + git
workspace tied to a Jira ticket (`new`, `update`, `upgrade-metapackage`, `rm`),
usually run inside the workspace directory.

```sh
tkt new TICKET-123 pkg-a pkg-b --tag tickets/TICKET-123
cd <workspace>
tkt update                 # re-sync packages to the branch
```

**Sandboxed agent** — run an LLM agent inside a `bwrap` sandbox, then bring its
work back onto the human-workspace branches.

```sh
tkt sandbox-run            # run the agent (workspace or single-repo mode)
tkt pull-sandbox           # transfer agent work back onto your branches
tkt sandbox-reset          # reset .agent worktrees to the human branches
tkt sandbox-cleanup        # kill orphaned bridge socats left after unclean exit
```

**Harness install** — symlink the Zed or OpenCode agent harnesses (rules + skills)
into place for the editor/agent you use.

```sh
tkt install-zed-agent      # link Zed harness skills + AGENTS.md
# or
tkt install-opencode-agent # link OpenCode harness agents
```

**Model-traffic tracing** — capture and retroactively segment the model traffic a
Zed/OpenCode agent generates.

```sh
tkt trace-proxy --ssh-host HOST --upstream URL   # capture model traffic
cd ~/.tkt/traces                                  # default data root
cat capture.jsonl                                 # live continuous capture
tkt trace-log segment                             # split capture into sessions
tkt trace-log list                                # label each session
```

**Other** — `mcp-server` runs the MCP stdio server that exposes the sandboxed
`bash`, `read`, `grep`, `glob`, and `ls` tools, and `fix-openspec` rewrites
OpenSpec skill files for OpenCode's harness.
