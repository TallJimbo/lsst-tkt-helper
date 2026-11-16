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

"""Stand-in for `plannotator inbox mcp`, used by the proxy's tests."""

import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("fake-inbox")


# SDK 2.3.0 auto-wraps primitive returns as {"result": ...} structured
# content; these upstream tools ship plain text content, so the stub says so.
@mcp.tool(title="Send a message to the person", structured_output=False)
def send_message(body: str, subject: str | None = None) -> str:
    """Say something in the Inbox.

    Args:
        body: the markdown message
        subject: one-line thread subject
    """
    return f"sent: {body}"


@mcp.tool(title="Get the method for a guided review")
def get_guide_brief() -> dict[str, object]:
    """Return the brief the author needs, in structured content."""
    return {"method": "one section per file", "example": {"title": "Example"}}


@mcp.tool(title="Send the person a guided review")
def submit_guide(guide: object, patch: str | None = None, snapshot: object = None) -> dict[str, object]:
    """Deliver an authored guide.

    Args:
        guide: the authored guide
        patch: the unified diff it describes
        snapshot: a complete snapshot instead
    """
    return {"guide": guide, "patch": patch, "cwd": os.getcwd()}


@mcp.tool(title="Resolve a thread", structured_output=False)
def resolve_message(message_id: str) -> str:
    """Archive a thread.

    Args:
        message_id: a message in the thread
    """
    raise ToolError(f"no such message: {message_id}")


if __name__ == "__main__":
    mcp.run()
