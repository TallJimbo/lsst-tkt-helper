# tkt: Jira + EUPS + git development tooling

This package provides command-line tools that automate common development tasks that involve a combination of EUPS and git, as used in the Rubin Observatory Data Management system.

It creates EUPS metapackages that correspond to a single Jira ticket, containing git source repositories for multiple packages (generally on the same branch).

At present there is no documentation at all, and only one command (`tkt new`), which creates a new metapackage from scratch.
Integration with Jira is entirely hypothetical; at present the ticket number is just something the user provides.
