# Copyright 2020-2026 Jim Bosch
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import Any, TextIO

import click

from ._environment import Environment
from ._workspace import Workspace
from .openspec import OpenSpec


def _setup_logging(verbose: int) -> None:
    logging.basicConfig(
        level={0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}[verbose],
        format="%(message)s",
    )


def _classify_tools(
    workspace_tools: Iterable[str], default_tools: Iterable[str], get_tool: Any
) -> tuple[list[str], list[str], list[str]]:
    """Split the workspace's tools into missing, stale, and non-default.

    Returns (missing, stale, nondefault): missing defaults are default tools
    absent from the workspace; stale tools are no longer configured in the
    environment at all; nondefault tools are configured but no longer defaults
    (candidates for removal on migration).
    """
    workspace_tools = list(workspace_tools)
    missing = [t for t in default_tools if t not in workspace_tools]
    stale = [t for t in workspace_tools if get_tool(t) is None]
    nondefault = [t for t in workspace_tools if get_tool(t) is not None and t not in default_tools]
    return missing, stale, nondefault


@click.group()
def cli() -> None:
    pass


@cli.command(
    "fix-openspec",
    help=(
        "Rewrite OpenSpec skill files under DIR for OpenCode's harness. "
        "Standalone (no environment required); DIR defaults to the current "
        "directory. Exits with status 2 if no SKILL.md is found."
    ),
)
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    required=False,
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def fix_openspec(directory: str | None, *, dry_run: bool = False, verbose: int = 0) -> None:
    _setup_logging(verbose)
    if directory is None:
        directory = os.path.abspath(os.curdir)
    result = OpenSpec.fix_skills(directory, dry_run=dry_run)
    if result.files_found == 0:
        click.echo(f"No SKILL.md files found under {directory}.", err=True)
        raise SystemExit(2)
    verb = "would be" if dry_run else "were"
    click.echo(f"done: {result.files_changed} file(s) {verb} updated, {len(result.warnings)} warning(s)")


@cli.command(
    "install-zed-agent",
    help=(
        "Symlink the Zed harness skills into ~/.agents/skills and rules.md into "
        "~/.config/zed/AGENTS.md. Warns about (and, with --yes, removes) stale "
        "entries under ~/.agents/skills."
    ),
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("--yes", "yes", is_flag=True, help="Remove stale entries without prompting.")
@click.option("-v", "--verbose", count=True)
def install_zed_agent(*, dry_run: bool = False, yes: bool = False, verbose: int = 0) -> None:
    _setup_logging(verbose)
    from .install import install_zed_agent as _install_zed

    confirm = (lambda msg: True) if yes else click.confirm
    _install_zed(dry_run=dry_run, confirm=confirm)


@cli.command(
    "install-opencode-agent",
    help=(
        "Symlink the OpenCode harness agents dir into ~/.config/opencode/agents, replacing any stale symlink."
    ),
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def install_opencode_agent(*, dry_run: bool = False, verbose: int = 0) -> None:
    _setup_logging(verbose)
    from .install import install_opencode_agent as _install_opencode

    _install_opencode(dry_run=dry_run)


@cli.command()
@click.argument("ticket")
@click.argument("packages", nargs=-1)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, writable=True, resolve_path=True),
)
@click.option("-t", "--tag", type=str)
@click.option("--metapackage")
@click.option("--workspace-eups-product")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option(
    "--add-tool",
    "add_tools",
    multiple=True,
    default=(),
    type=str,
    help="Add a tool to the new workspace in addition to the configured defaults.",
)
@click.option(
    "--remove-tool",
    "remove_tools",
    multiple=True,
    default=(),
    type=str,
    help="Remove a tool from the new workspace relative to the configured defaults.",
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def new(
    ticket: str,
    packages: Iterable[str],
    *,
    directory: str | None,
    tag: str | None,
    metapackage: str | None,
    workspace_eups_product: str | None,
    environment: TextIO | None,
    add_tools: Iterable[str] = (),
    remove_tools: Iterable[str] = (),
    dry_run: bool = False,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    else:
        env = Environment.from_file(environment)
    tools = env.default_tools
    for name in add_tools:
        if name not in tools:
            tools = (*tools, name)
    for name in remove_tools:
        tools = tuple(t for t in tools if t != name)
    Workspace.new(
        ticket=ticket,
        packages=packages,
        directory=directory,
        metapackage=metapackage,
        tag=tag,
        workspace_eups_product=workspace_eups_product,
        tools=tools,
        environment=env,
        dry_run=dry_run,
    )


@cli.command()
@click.argument("packages", nargs=-1)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, writable=True, resolve_path=True),
)
@click.option("--ticket")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def update(
    packages: Iterable[str],
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    dry_run: bool = False,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    else:
        env = Environment.from_file(environment)
    workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
    missing, stale, nondefault = _classify_tools(workspace.tools, env.default_tools, env.get_tool)
    if dry_run:
        for t in missing:
            logging.warning(f"Would ask to add missing default tool {t}.")
        for t in stale:
            logging.warning(f"Would remove unconfigured tool {t}.")
        for t in nondefault:
            logging.warning(f"Would prompt to remove non-default tool {t}.")
        workspace.update(packages=packages, environment=env, dry_run=True)
        return
    for t in stale:
        logging.warning(f"Removing tool {t} because it is no longer configured.")
    workspace.remove_tools(stale)
    for t in nondefault:
        if click.confirm(f"Remove tool {t}? It is no longer a default (this also cleans up its artifacts)."):
            tool = env.get_tool(t)
            if tool is not None:
                tool.remove(workspace.directory)
            workspace.remove_tools([t])
    additions: list[str] = []
    if missing:
        if click.confirm("Missing default tools: " + ", ".join(missing) + ". Add them?"):
            additions = missing
    workspace.update(
        packages=packages,
        tools=additions,
        environment=env,
        dry_run=dry_run,
    )


@cli.command("upgrade-metapackage")
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, writable=True, resolve_path=True),
)
@click.option("--ticket")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option("-t", "--tag", type=str)
@click.option("--metapackage")
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def upgrade_metapackage(
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    tag: str | None,
    metapackage: str | None,
    dry_run: bool = False,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    else:
        env = Environment.from_file(environment)
    workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
    workspace.upgrade_metapackage(
        metapackage=metapackage,
        tag=tag,
        environment=env,
        dry_run=dry_run,
    )


@cli.command("rm")
@click.argument("ticket")
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, writable=True, resolve_path=True),
)
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option("-v", "--verbose", count=True)
def rm(
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    else:
        env = Environment.from_file(environment)
    workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
    workspace.remove()


@cli.command(
    "sandbox-run",
    help=(
        "Run the LLM agent sandbox. Autodetects mode from the current directory: "
        "if a `.agent` subdirectory is present it runs in workspace mode; otherwise "
        "it treats the current directory as a single git repository (single-repo mode)."
    ),
)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
)
@click.option("--ticket")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option(
    "--shell",
    is_flag=True,
    help="Drop into an interactive shell in the sandbox instead of launching the agent command.",
)
@click.option(
    "--conda-env",
    type=str,
    help="Activate this conda environment inside the sandbox before anything else.",
)
@click.option(
    "--command",
    "cmd",
    type=str,
    help=(
        "Override the configured final command with this (shlex-split string). "
        "Mutually exclusive with --shell."
    ),
)
@click.option(
    "--network",
    is_flag=True,
    default=None,
    help=(
        "Run with full, unrestricted network access (shared host network "
        "namespace). By default the sandbox is network-restricted: only the "
        "bridged localhost LLM port is reachable. If not given, the sandbox "
        "tool's configured 'network' value is used."
    ),
)
@click.option("-v", "--verbose", count=True)
def sandbox_run(
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    shell: bool = False,
    conda_env: str | None = None,
    cmd: str | None = None,
    network: bool | None = None,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    if cmd is not None and shell:
        raise click.UsageError("--command and --shell are mutually exclusive.")
    from .sandbox import Sandbox

    cwd = os.path.abspath(".")
    if os.path.isdir(os.path.join(cwd, ".agent")):
        # Workspace mode: existing behavior.
        env = Environment.from_file(environment)
        workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
        tool = env.get_tool("sandbox")
        if tool is None:
            raise click.UsageError("No 'sandbox' tool configured in the tkt environment.")
        if not isinstance(tool, Sandbox):
            raise click.UsageError(
                f"Configured 'sandbox' tool is {type(tool).__name__}, not tkt.sandbox.Sandbox."
            )
        tool.run(workspace, shell=shell, command=cmd, network=network)
    else:
        # Single-repo mode: treat the CWD as the repository root.
        cls, data = Environment.load_config(environment)
        tools = cls.load_tools(data)
        sandbox = tools.get("sandbox")
        if sandbox is None:
            raise click.UsageError("No 'sandbox' tool configured in the tkt environment.")
        if not isinstance(sandbox, Sandbox):
            raise click.UsageError(
                f"Configured 'sandbox' tool is {type(sandbox).__name__}, not tkt.sandbox.Sandbox."
            )
        repo_dir = directory if directory is not None else cwd
        sandbox.run_single_repo(repo_dir, shell=shell, conda_env=conda_env, command=cmd, network=network)


@cli.command("mcp-server", help="Run the MCP stdio server exposing a sandboxed bash tool.")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
)
@click.option("--conda-env", type=str, default=None)
@click.option("-v", "--verbose", count=True)
def mcp_server(
    *,
    environment: TextIO | None,
    directory: str | None,
    conda_env: str | None,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    from .mcp_server import run_server
    from .sandbox import Sandbox

    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    cwd = os.path.abspath(".")
    if os.path.isdir(os.path.join(cwd, ".agent")):
        # Workspace mode.
        env = Environment.from_file(environment)
        workspace = Workspace.from_existing(ticket=None, directory=directory, environment=env)
        tool = env.get_tool("sandbox")
        if tool is None or not isinstance(tool, Sandbox):
            raise click.UsageError("No configured 'sandbox' tool (tkt.sandbox.Sandbox).")
        run_server(tool, cwd=workspace.directory, workspace=workspace, conda_env=conda_env)
    else:
        # Single-repo mode.
        cls, data = Environment.load_config(environment)
        tools = cls.load_tools(data)
        sandbox = tools.get("sandbox")
        if sandbox is None or not isinstance(sandbox, Sandbox):
            raise click.UsageError("No configured 'sandbox' tool (tkt.sandbox.Sandbox).")
        repo_dir = directory if directory is not None else cwd
        run_server(sandbox, cwd=repo_dir, repo_dir=repo_dir, conda_env=conda_env)


@cli.command(
    "pull-sandbox",
    help=(
        "Transfer work from `.agent` worktrees onto the human-workspace "
        "branches. Committed agent work is classified and fast-forwarded or "
        "snapshotted + interactively rebased; uncommitted work is transferred "
        "as unstaged changes. Use -f/--finish to finalize an in-progress sync "
        "or -a/--abort to cancel it. Use -s/--skip-uncommitted or "
        "-o/--only-uncommitted to process one side of a mixed package."
    ),
)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
)
@click.option("--ticket")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option(
    "-s",
    "--skip-uncommitted",
    is_flag=True,
    help="Transfer committed work only, deferring any dirty agent worktree.",
)
@click.option(
    "-o",
    "--only-uncommitted",
    is_flag=True,
    help="Transfer uncommitted work only (committed side assumed reconciled).",
)
@click.option(
    "-f",
    "--finish",
    is_flag=True,
    help="Finalize an in-progress pull-sandbox sync across all packages.",
)
@click.option(
    "-a",
    "--abort",
    is_flag=True,
    help="Cancel an in-progress pull-sandbox sync across all packages.",
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def pull_sandbox(
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    skip_uncommitted: bool = False,
    only_uncommitted: bool = False,
    finish: bool = False,
    abort: bool = False,
    dry_run: bool = False,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if skip_uncommitted and only_uncommitted:
        raise click.UsageError("--skip-uncommitted and --only-uncommitted are mutually exclusive.")
    if finish and abort:
        raise click.UsageError("--finish and --abort are mutually exclusive.")
    if (finish or abort) and (skip_uncommitted or only_uncommitted):
        raise click.UsageError(
            "--finish/--abort cannot be combined with --skip-uncommitted/--only-uncommitted."
        )
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    from .pull import Pull

    env = Environment.from_file(environment)
    workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
    if abort:
        Pull.abort(workspace, dry_run=dry_run)
    elif finish:
        Pull.finish(workspace, dry_run=dry_run)
    else:
        Pull.run(
            workspace,
            skip_uncommitted=skip_uncommitted,
            only_uncommitted=only_uncommitted,
            dry_run=dry_run,
        )


@cli.command(
    "sandbox-reset",
    help=(
        "Reset all `.agent` worktrees to the state of the corresponding "
        "human-workspace branch. Uncommitted work is saved to the git stash; "
        "unmerged agent commits are saved to a timestamped backup branch "
        "(`<branch>-saved-<timestamp>`) before the reset."
    ),
)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
)
@click.option("--ticket")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option("-v", "--verbose", count=True)
def sandbox_reset(
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    from .sandbox import Sandbox

    env = Environment.from_file(environment)
    workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
    tool = env.get_tool("sandbox")
    if tool is None:
        raise click.UsageError("No 'sandbox' tool configured in the tkt environment.")
    if not isinstance(tool, Sandbox):
        raise click.UsageError(
            f"Configured 'sandbox' tool is {type(tool).__name__}, not tkt.sandbox.Sandbox."
        )
    tool.reset(workspace)


@cli.command(
    "sandbox-cleanup",
    help=(
        "Kill orphaned bridge socats left behind by sandbox sessions that "
        "shut down uncleanly. A bridge socat is stale when the shared net-* "
        "directory it references no longer exists (removed on clean shutdown), "
        "so live sandboxes are never touched. Use -n to preview."
    ),
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def sandbox_cleanup(*, dry_run: bool = False, verbose: int = 0) -> None:
    _setup_logging(verbose)
    from .sandbox import cleanup_stale_bridges

    killed, dirs = cleanup_stale_bridges(dry_run=dry_run)
    verb = "would kill" if dry_run else "killed"
    click.echo(f"done: {verb} {killed} stale bridge socat(s)")
    for net in dirs:
        logging.info(f"  {net}")
