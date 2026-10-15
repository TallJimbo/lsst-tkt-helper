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

"""Host-side MCP server reached from a restricted sandbox over the bridge
unix socket (``net-*/session.sock``).  Exposes session tools ONLY.
Spawned per connection by socat; holds no state (truth lives on disk).
"""

from __future__ import annotations

from typing import NoReturn

from mcp.server.fastmcp import FastMCP

from ._session_tools import build_session_tools


def run_session_server(workspace: str) -> NoReturn:
    """Run the session-tool MCP server over stdio until EOF."""
    mcp = FastMCP(name="tkt-session")
    build_session_tools(mcp, workspace=str(workspace))
    mcp.run()  # stdio; EOF on socket close ends the process
    raise SystemExit(0)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="tkt.session_server")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    run_session_server(args.workspace)
