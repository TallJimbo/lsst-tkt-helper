# Copyright 2020 Jim Bosch
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

import re
import logging
from typing import (
    Any,
    Iterable,
    Optional,
    TextIO,
    Tuple,
)

import click

from ._workspace import Workspace
from ._environment import Environment


class KeySetParamType(click.ParamType):

    name = "k=v"

    _REGEX = re.compile(r"(?P<key>[a-zA-Z0-9_]+)=(?P<value>\S+)")

    def convert(self, value: str, param: Any, ctx: Any) -> Tuple[str, str]:
        m = self._REGEX.match(value)
        if m is not None:
            return (m.group("key"), m.group("value"))
        self.fail(f"Expected a string of the form 'k=v', got '{value}'.", param, ctx)


def _setup_logging(verbose: int) -> None:
    logging.basicConfig(
        level={0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}[verbose],
        format="%(message)s",
    )


@click.group()
def cli() -> None:
    pass


@cli.command()
@click.argument("ticket")
@click.argument("packages", nargs=-1)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, writable=True, resolve_path=True),
)
@click.option(
    "-b", "--branch", "branches", type=KeySetParamType(), multiple=True, default=()
)
@click.option("-t", "--tag", type=str)
@click.option("--metapackage")
@click.option("--workspace-eups-product")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def new(
    ticket: str,
    packages: Iterable[str],
    *,
    directory: Optional[str],
    branches: Iterable[Tuple[str, str]],
    tag: Optional[str],
    metapackage: Optional[str],
    workspace_eups_product: Optional[str],
    environment: Optional[TextIO],
    dry_run: bool = False,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        env = Environment.minimal()
    else:
        env = Environment.from_file(environment)
    Workspace.new(
        ticket=ticket,
        packages=packages,
        directory=directory,
        branches=dict(branches),
        metapackage=metapackage,
        tag=tag,
        workspace_eups_product=workspace_eups_product,
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
    "-b", "--branch", "branches", type=KeySetParamType(), multiple=True, default=()
)
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
    ticket: Optional[str],
    directory: Optional[str],
    branches: Iterable[Tuple[str, str]],
    environment: Optional[TextIO],
    dry_run: bool = False,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        env = Environment.minimal()
    else:
        env = Environment.from_file(environment)
    workspace = Workspace.from_existing(
        ticket=ticket, directory=directory, environment=env
    )
    workspace.update(
        packages=packages,
        branches=dict(branches),
        environment=env,
        dry_run=dry_run,
    )
