# tkt: Jira + EUPS + git development tooling

This package provides command-line tools that automate common development tasks that involve a combination of EUPS and git, as used in the Rubin Observatory Data Management system.

It creates EUPS metapackages that correspond to a single Jira ticket, containing git source repositories for multiple packages (generally on the same branch).

At present there is no documentation at all, and integration with Jira is entirely hypothetical; at present the ticket number is just something the user provides.

## Sandbox networking

The `tkt sandbox-run` sandbox is network-**restricted by default**: it runs in an
isolated network namespace and only a single localhost port (the bridged LLM
endpoint) is reachable.  Pass `--network` to `tkt sandbox-run` to restore full,
unrestricted network access (this was the historical default).

The restricted mode requires `socat` on the host to bridge the LLM port into
the sandbox; install it with your package manager (e.g. `apt install socat`).
The bridged port defaults to `localhost:8080` and can be changed via the
`sandbox` tool's `port` config entry in `local.json`.
