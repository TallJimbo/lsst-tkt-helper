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

import io
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tkt.proxy import (
    _SKIP_FORWARD,
    ProxyHandler,
    build_entry,
    mask_headers,
    resolve_traces_dir,
    run_proxy,
    wait_for_upstream,
    write_record,
)


class _UpstreamHandler(BaseHTTPRequestHandler):
    """Echo a known status/body per path; 404 for ``/missing``."""

    def do_GET(self) -> None:
        if self.path == "/missing":
            self.send_response(404)
            body = b"not found"
        else:
            self.send_response(200)
            body = b"hello from upstream"
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Echo", "yes")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:  # keep test output clean
        pass


class _ProxyServer(ThreadingHTTPServer):
    """Minimal stand-in for Task 3's ``ProxyServer`` (upstream + logger)."""

    def __init__(self, address, upstream: str, logger: io.StringIO) -> None:
        super().__init__(address, ProxyHandler)
        self.upstream = upstream
        self.logger = logger


def _reserve_free_port() -> int:
    """Return a port that is currently free (best-effort, no listener)."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_mask_headers_redacts_authorization_case_insensitive() -> None:
    """Authorization is masked regardless of header-case."""
    out = mask_headers(
        {"Authorization": "Bearer sekrit", "authorization": "lower", "content-type": "application/json"}
    )
    assert out["Authorization"] == "<redacted>"
    assert out["authorization"] == "<redacted>"
    assert out["content-type"] == "application/json"


def test_write_record_appends_one_json_object_per_line(tmp_path) -> None:
    """Each record is appended as one JSON object on its own line."""
    path = tmp_path / "capture.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        write_record(fh, {"a": 1})
        write_record(fh, {"b": "two"})
    lines = path.read_text(encoding="utf-8")
    assert [json.loads(line) for line in lines.splitlines()] == [{"a": 1}, {"b": "two"}]


def test_skip_forward_contains_framing_fields() -> None:
    """Framing headers are excluded from the forwarded set."""
    assert {
        "host",
        "connection",
        "content-length",
        "keep-alive",
        "transfer-encoding",
        "expect",
        "upgrade",
    } <= _SKIP_FORWARD


def test_build_entry_masks_authorization_and_decodes_body() -> None:
    """build_entry masks authorization and decodes the request body."""
    rec = build_entry(
        method="POST",
        path="/api/v1/chat/completions",
        upstream_url="http://localhost:8080/api/v1/chat/completions",
        headers={"Authorization": "Bearer xyz", "Content-Type": "application/json"},
        body=b'{"m":[]}',
    )
    assert rec["request_headers"]["Authorization"] == "<redacted>"
    assert rec["request_body"] == '{"m":[]}'


def test_relay_forwards_and_records(tmp_path) -> None:
    """A live request is relayed to upstream and recorded (200 and 404)."""
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}"

    log_path = tmp_path / "capture.jsonl"
    logger = open(log_path, "a", encoding="utf-8")
    proxy = _ProxyServer(("127.0.0.1", 0), upstream_url, logger)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"

    try:
        resp = urllib.request.urlopen(f"{proxy_url}/ok", timeout=10)
        assert resp.status == 200
        assert resp.read() == b"hello from upstream"
        assert resp.headers.get("X-Echo") == "yes"

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"{proxy_url}/missing", timeout=10)
        assert excinfo.value.code == 404
    finally:
        proxy.shutdown()
        proxy.server_close()
        upstream.shutdown()
        upstream.server_close()
        logger.close()

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    by_path = {rec["path"]: rec for rec in records}
    assert by_path["/ok"]["status"] == 200
    assert by_path["/ok"]["response_body"] == "hello from upstream"
    assert by_path["/missing"]["status"] == 404
    assert by_path["/missing"]["response_body"] == "not found"


def test_relay_upstream_error_returns_502_and_records_error(tmp_path) -> None:
    """A refusing upstream yields HTTP 502 and an error record."""
    dead_port = _reserve_free_port()

    log_path = tmp_path / "capture.jsonl"
    logger = open(log_path, "a", encoding="utf-8")
    proxy = _ProxyServer(("127.0.0.1", 0), f"http://127.0.0.1:{dead_port}", logger)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"

    try:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"{proxy_url}/any", timeout=10)
        assert excinfo.value.code == 502
    finally:
        proxy.shutdown()
        proxy.server_close()
        logger.close()

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert "error" in records[-1]


def test_resolve_traces_dir_uses_env(monkeypatch, tmp_path) -> None:
    """TKT_TRACES_DIR takes precedence over the default location."""
    monkeypatch.setenv("TKT_TRACES_DIR", str(tmp_path / "traces"))
    assert resolve_traces_dir() == tmp_path / "traces"


def test_resolve_traces_dir_defaults_to_home(monkeypatch, tmp_path) -> None:
    """Without TKT_TRACES_DIR the default is ~/.tkt/traces."""
    monkeypatch.delenv("TKT_TRACES_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_traces_dir() == tmp_path / ".tkt" / "traces"


def test_wait_for_upstream_returns_true_when_open() -> None:
    """wait_for_upstream reports True for an accepting port."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert wait_for_upstream(f"http://127.0.0.1:{port}", timeout=1.0) is True
    finally:
        srv.close()


def test_run_proxy_starts_and_serves(tmp_path) -> None:
    """run_proxy binds, relays a request, and appends a capture record."""
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}"

    listen_port = _reserve_free_port()
    log_path = tmp_path / "capture.jsonl"
    proxy_thread = threading.Thread(
        target=run_proxy,
        args=(listen_port, upstream_url, str(log_path)),
        daemon=True,
    )
    proxy_thread.start()

    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", listen_port), timeout=0.25):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("proxy did not start listening")

        resp = urllib.request.urlopen(f"http://127.0.0.1:{listen_port}/ok", timeout=10)
        assert resp.status == 200
        assert resp.read() == b"hello from upstream"
    finally:
        upstream.shutdown()
        upstream.server_close()

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records and records[-1]["path"] == "/ok"
    assert records[-1]["status"] == 200
