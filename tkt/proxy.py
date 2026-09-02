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

"""Capture record model and relay handler for the tracing proxy.

Each captured record is a single JSON object with fields:
``time``, ``method``, ``path``, ``upstream_url``, ``request_headers``,
``request_body``, ``status``, ``response_headers``, ``response_body``, and an
optional ``error``.

``ProxyHandler`` is a stdlib ``http.server`` handler that relays each request
to an upstream URL (configured on the server object) and records a capture
record for it.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse

# Header names that urllib manages itself or that must be re-framed for our
# connection-close streaming relay; we do not forward them verbatim.
_SKIP_FORWARD = {
    "host",
    "connection",
    "content-length",
    "keep-alive",
    "transfer-encoding",
    "expect",
    "upgrade",
}


def mask_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of headers with any authorization value masked."""
    masked = {}
    for key, value in headers.items():
        masked[key] = "<redacted>" if key.lower() == "authorization" else value
    return masked


def write_record(file: TextIO, record: dict[str, Any]) -> None:
    """Append a single JSON object on its own line and flush."""
    file.write(json.dumps(record, ensure_ascii=False) + "\n")
    file.flush()


def apply_rewrites(
    body: bytes,
    *,
    user_agent: str,
    rewrite_rules: Sequence[Mapping[str, Any]],
) -> tuple[bytes, bool]:
    """Rewrite a chat-completions request body per the matching rules.

    Parameters
    ----------
    body
        The raw request body.
    user_agent
        The request's ``User-Agent`` header (used for rule matching).
    rewrite_rules
        Rules; each may have optional ``client`` (substring match on
        ``user_agent``), optional ``model`` (exact match on the body's
        ``model``), and ``params`` (key -> value; ``None`` removes the key,
        anything else sets it). Rules apply in order. Non-dict entries are
        skipped.

    Returns
    -------
    tuple
        The (possibly rewritten) body and whether it changed. Unchanged when
        the body is not a JSON object with a ``model`` field.
    """
    if not rewrite_rules or not body:
        return body, False
    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return body, False
    if not isinstance(obj, dict) or "model" not in obj:
        return body, False
    model = obj["model"]
    changed = False
    for rule in rewrite_rules:
        if not isinstance(rule, Mapping):
            continue
        if not _rule_matches(rule, user_agent=user_agent, model=model):
            continue
        for key, value in (rule.get("params") or {}).items():
            if value is None:
                if key in obj:
                    del obj[key]
                    changed = True
            elif obj.get(key) != value:
                obj[key] = value
                changed = True
    if not changed:
        return body, False
    return json.dumps(obj, ensure_ascii=False).encode("utf-8"), True


def _rule_matches(rule: Mapping[str, Any], *, user_agent: str, model: object) -> bool:
    """Return whether ``rule`` applies to a request with this
    user-agent/model.
    """
    client = rule.get("client")
    if client is not None and str(client).lower() not in user_agent.lower():
        return False
    rule_model = rule.get("model")
    if rule_model is not None and rule_model != model:
        return False
    return True


def build_entry(*, method: str, path: str, upstream_url: str, headers: dict[str, str], body: bytes) -> dict:
    """Build a capture record from incoming request fields."""
    return {
        "time": time.time(),
        "method": method,
        "path": path,
        "upstream_url": upstream_url,
        "request_headers": mask_headers(headers),
        "request_body": body.decode("utf-8", "replace"),
    }


class ProxyHandler(BaseHTTPRequestHandler):
    """Relay a request to the configured upstream and record the exchange."""

    protocol_version = "HTTP/1.1"
    server: ProxyServer

    def do_GET(self) -> None:
        self._relay()

    def do_POST(self) -> None:
        self._relay()

    def do_PUT(self) -> None:
        self._relay()

    def do_PATCH(self) -> None:
        self._relay()

    def do_DELETE(self) -> None:
        self._relay()

    def _relay(self) -> None:
        upstream = self.server.upstream  # scheme://host:port, no path

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        url = upstream + self.path
        headers = {key: value for key, value in self.headers.items() if key.lower() not in _SKIP_FORWARD}

        forwarded_body = body
        if self.command == "POST" and self.path.endswith("/chat/completions"):
            rules = getattr(self.server, "rewrite_rules", ())
            if rules:
                new_body, changed = apply_rewrites(
                    body,
                    user_agent=self.headers.get("User-Agent", ""),
                    rewrite_rules=rules,
                )
                if changed:
                    forwarded_body = new_body

        entry = build_entry(
            method=self.command,
            path=self.path,
            upstream_url=url,
            headers=dict(self.headers.items()),
            body=body,
        )
        if forwarded_body != body:
            entry["request_body_forwarded"] = forwarded_body.decode("utf-8", "replace")

        request = urllib.request.Request(url, data=forwarded_body, headers=headers, method=self.command)
        try:
            response = urllib.request.urlopen(request)
            status = response.status
            response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as err:
            response = err
            status = err.code
            response_headers = dict(err.headers.items())
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            self._write(entry)
            self.send_error(502, "Proxy upstream error")
            return

        # Relay status and headers, dropping length/framing fields so we can
        # stream the body and close the connection (handles SSE fine).
        self.send_response(status)
        for key, value in response_headers.items():
            if key.lower() not in _SKIP_FORWARD:
                self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()

        chunks = []
        try:
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            response.close()

        entry["status"] = status
        entry["response_headers"] = response_headers
        entry["response_body"] = b"".join(chunks).decode("utf-8", "replace")
        self._write(entry)

    def _write(self, entry: dict) -> None:
        """Write the record through the server's logger."""
        write_record(self.server.logger, entry)

    def log_message(self, fmt: str, *args: Any) -> None:  # keep capture file clean
        pass


class ProxyServer(ThreadingHTTPServer):
    """HTTP server that carries the upstream URL and capture logger."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        upstream: str,
        logger: TextIO,
        rewrite_rules: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(address, handler)
        self.upstream = upstream.rstrip("/")
        self.logger = logger
        self.rewrite_rules = rewrite_rules


def wait_for_upstream(upstream: str, timeout: float = 5.0) -> bool:
    """Poll until the upstream host:port accepts a connection or timeout."""
    parsed = urlparse(upstream)
    host, port = parsed.hostname, parsed.port
    if host is None or port is None:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def resolve_traces_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the traces dir from TKT_TRACES_DIR, else ~/.tkt/traces."""
    env = os.environ if env is None else env
    return Path(env.get("TKT_TRACES_DIR") or (Path.home() / ".tkt" / "traces"))


def run_proxy(
    listen_port: int,
    upstream: str,
    log_path: str,
    *,
    rewrite_rules: Sequence[Mapping[str, Any]] = (),
) -> None:
    """Run the proxy in the foreground; append captures to log_path."""
    fh = open(log_path, "a", encoding="utf-8")
    server = ProxyServer(
        ("127.0.0.1", listen_port),
        ProxyHandler,
        upstream=upstream,
        logger=fh,
        rewrite_rules=rewrite_rules,
    )
    print(f"tkt proxy listening on 127.0.0.1:{listen_port}, relaying to {upstream}")
    print(f"tkt proxy capture log: {log_path}")
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        fh.close()


def run_with_ssh(
    upstream: str,
    ssh_command: list[str],
    *,
    listen: int,
    log_path: str,
    rewrite_rules: Sequence[Mapping[str, Any]] = (),
) -> None:
    """Run ssh_command in the foreground; a proxy child relays upstream."""
    proxy_cmd = [
        sys.executable,
        "-m",
        "tkt.proxy",
        "--listen",
        str(listen),
        "--upstream",
        upstream,
        "--log",
        log_path,
    ]
    if rewrite_rules:
        proxy_cmd += ["--rewrite-rules", json.dumps(rewrite_rules)]
    child = subprocess.Popen(
        proxy_cmd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Start the tunnel first: ssh establishes the port-forward that
        # ``--upstream`` points at, so waiting before the tunnel exists always
        # fails on a fresh tunnel. Run it in the foreground so stdin/stdout
        # stay attached to the interactive session, then wait for the upstream
        # to become reachable through it.
        ssh_proc = subprocess.Popen(ssh_command)
        if not wait_for_upstream(upstream, timeout=30.0):
            ssh_proc.terminate()
            ssh_proc.wait()
            raise SystemExit(f"upstream {upstream} never became reachable")
        ssh_proc.wait()
    finally:
        child.terminate()
        child.wait()


def main() -> None:
    """CLI entry point for the background proxy child."""
    parser = argparse.ArgumentParser(description="Run the tkt tracing proxy.")
    parser.add_argument("--listen", type=int, required=True, help="local port to listen on")
    parser.add_argument("--upstream", required=True, help="upstream URL to relay to")
    parser.add_argument("--log", required=True, help="path to the capture log file")
    parser.add_argument("--rewrite-rules", default=None, help="JSON array of rewrite rules")
    args = parser.parse_args()
    rewrite_rules: Sequence[Mapping[str, Any]] = ()
    if args.rewrite_rules:
        rewrite_rules = json.loads(args.rewrite_rules)
    run_proxy(args.listen, args.upstream, args.log, rewrite_rules=rewrite_rules)


if __name__ == "__main__":
    main()
