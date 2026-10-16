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

"""Integration tests for tkt.remote_mcp_proxy: verbatim tool relay and
host-side Authorization injection, driven over real stdio against a
real streamable-HTTP upstream.
"""

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

UPSTREAM = str(Path(__file__).parent / "fake_github_mcp.py")
TOKEN = "ghp_test-s3cret"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def upstream_url():
    """Run the fake upstream on a free port; yield its MCP URL."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, UPSTREAM, str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                break
        except OSError:
            time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError("fake upstream did not come up")
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


def _drive_proxy(url: str, token_file: Path, extra: list[str] | None = None):
    """Run the proxy over stdio; return (tools, whoami result)."""

    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "tkt.remote_mcp_proxy",
                "--url",
                url,
                "--token-file",
                str(token_file),
                *(extra or []),
            ],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                result = await session.call_tool("whoami", {})
                return tools, result

    return asyncio.run(go())


def test_proxy_relays_tools_verbatim(upstream_url, tmp_path):
    """The proxy relays upstream tool names and schemas verbatim."""
    token = tmp_path / "tok"
    token.write_text(TOKEN + "\n")  # trailing newline must be tolerated
    tools, _ = _drive_proxy(upstream_url, token)
    assert [t.name for t in tools] == ["whoami"]
    assert tools[0].description == "Return the Authorization header received by this server."


def test_proxy_injects_default_bearer_header(upstream_url, tmp_path):
    """The default header template sends ``Bearer <token>`` upstream."""
    token = tmp_path / "tok"
    token.write_text(TOKEN + "\n")
    _, result = _drive_proxy(upstream_url, token)
    assert result.content[0].text == f"Bearer {TOKEN}"


def test_proxy_honors_header_template(upstream_url, tmp_path):
    """``--header`` templates substitute ``{token}`` with the file contents."""
    token = tmp_path / "tok"
    token.write_text(TOKEN)
    _, result = _drive_proxy(upstream_url, token, extra=["--header", "token {token}"])
    assert result.content[0].text == f"token {TOKEN}"


def test_proxy_missing_token_file_exits_nonzero(upstream_url, tmp_path):
    """An unreadable token file is a fatal startup error."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tkt.remote_mcp_proxy",
            "--url",
            upstream_url,
            "--token-file",
            str(tmp_path / "absent"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "absent" in proc.stderr or "absent" in proc.stdout


def test_proxy_empty_token_file_exits_nonzero(upstream_url, tmp_path):
    """A whitespace-only token file is a fatal startup error."""
    token = tmp_path / "tok"
    token.write_text("   \n")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tkt.remote_mcp_proxy",
            "--url",
            upstream_url,
            "--token-file",
            str(token),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
