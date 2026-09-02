# E3 — Model-Degradation Tracing Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Productize the throwaway `investigations/bad-thinking/zed-agent-request-proxy/proxy.py` into a continuously-running tracing tool (`tkt trace-proxy` to capture, `tkt trace-log` to segment/label/pin/prune), gathering a large session-linked dataset of Zed-native-agent model traffic for a future degradation investigation.

**Architecture:** A capture-only reverse proxy appends one JSON object per model exchange to a continuous `capture.jsonl`. An offline `trace-log` tool retroactively segments the capture into sessions (OpenCode via `x-session-id`; Zed via a content-based conversation-reset detector), labels each session with the auto-generated conversation title extracted from the SSE stream, and manages per-session gzipped files with pin/prune retention. Stdlib-only.

**Tech Stack:** Python 3.13, click, stdlib (`http.server`, `urllib`, `json`, `gzip`, `subprocess`, `secrets`). No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-09-02-tracing-proxy-design.md`

## Global Constraints

- Python 3.13; dependencies only `click`, `GitPython`, `pyyaml`, `json5` — no new third-party dependencies.
- Every `.py` file carries the BSD-3-Clause header exactly as in existing modules (preserve it verbatim).
- Must pass before each commit and at the end: `ruff check .`, `ruff format --check .`, `mypy tkt/`.
- `tkt` is not pip-distributed; do not add packaging config.
- OpenCode workflow must keep working throughout (coexistence); these commands are additive and must not affect existing behavior.
- Do not edit `investigations/` (git-ignored scratch). Regression fixtures are **copies** under `tests/fixtures/`.
- The old prototype `investigations/bad-thinking/zed-agent-request-proxy/` is left in place (not moved/deleted).
- Data root defaults to `~/.tkt/traces/`; overridable via `TKT_TRACES_DIR` env or `--traces-dir`.
- Default session label fallback is the session id/timebase; default retention horizon 30 days, default `--keep` 20 most-recent unpinned sessions.
- `Authorization` header (case-insensitive) is always masked to `<redacted>` in captured records.

---

### Task 1: Capture record model + JSON masking + appends

**Files:**

- Create: `tkt/proxy.py`
- Test: `tests/test_proxy.py`

**Interfaces:**

- Consumes: nothing (pure data).
- Produces: `mask_headers(headers: dict[str, str]) -> dict[str, str]`, `write_record(file, record: dict) -> None`, and the captured-record schema documented in the module docstring. Later tasks consume `mask_headers` and `write_record`; `write_record` writes one JSON object + `\n` and flushes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_proxy.py` with the license header, then:

```python
from tkt.proxy import mask_headers, write_record


def test_mask_headers_redacts_authorization_case_insensitive() -> None:
    out = mask_headers({"Authorization": "Bearer sekrit", "content-type": "application/json"})
    assert out["Authorization"] == "<redacted>"
    assert out["authorization"] == "<redacted>"
    assert out["content-type"] == "application/json"


def test_write_record_appends_one_json_object_per_line(tmp_path) -> None:
    path = tmp_path / "capture.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        write_record(fh, {"a": 1})
        write_record(fh, {"b": "two"})
    lines = path.read_text(encoding="utf-8")
    import json

    assert [json.loads(l) for l in lines.splitlines()] == [{"a": 1}, {"b": "two"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_proxy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tkt.proxy'`.

- [ ] **Step 3: Write minimal implementation**

Create `tkt/proxy.py` with the license header and module docstring describing the capture-record schema (fields: `time`, `method`, `path`, `upstream_url`, `request_headers`, `request_body`, `status`, `response_headers`, `response_body`, optional `error`):

```python
from __future__ import annotations

from typing import Any, TextIO


def mask_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of headers with any authorization value masked."""
    masked = {}
    for key, value in headers.items():
        masked[key] = "<redacted>" if key.lower() == "authorization" else value
    return masked


def write_record(file: TextIO, record: dict[str, Any]) -> None:
    """Append a single JSON object on its own line and flush."""
    import json

    file.write(json.dumps(record, ensure_ascii=False) + "\n")
    file.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_proxy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tkt/proxy.py tests/test_proxy.py
git commit -m "feat(tracing): add capture record model and header masking"
```

---

### Task 2: Proxy relay HTTP handler

**Files:**

- Modify: `tkt/proxy.py`
- Test: `tests/test_proxy.py`

**Interfaces:**

- Consumes: `mask_headers`, `write_record` from Task 1.
- Produces: `_SKIP_FORWARD: set[str]`, `ProxyHandler(BaseHTTPRequestHandler)` with class attribute `server` typed as `ProxyServer` (see Task 3), and `relay_response_record(...)`-style helpers as needed. Task 3 (the server/lifecycle) builds on `ProxyHandler`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_proxy.py`:

```python
from unittest import mock

from tkt.proxy import ProxyHandler, _SKIP_FORWARD


def test_skip_forward_contains_framing_fields() -> None:
    assert {"host", "connection", "content-length", "keep-alive",
            "transfer-encoding", "expect", "upgrade"} <= _SKIP_FORWARD


def test_handler_masks_authorization_in_record() -> None:
    from tkt.proxy import _SKIP_FORWARD

    import json

    rec = {}
    handler = mock.create_autospec(ProxyHandler)
    handler.headers = {
        "Authorization": "Bearer xyz",
        "Content-Type": "application/json",
    }
    # use the module function via the handler's _build_entry path indirectly:
    from tkt.proxy import build_entry

    rec = build_entry(
        method="POST", path="/api/v1/chat/completions",
        upstream_url="http://localhost:8080/api/v1/chat/completions",
        headers=dict(handler.headers.items()), body=b'{"m":[]}',
    )
    assert rec["request_headers"]["Authorization"] == "<redacted>"
    assert rec["request_body"] == '{"m":[]}'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_proxy.py::test_handler_masks_authorization_in_record -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

Add to `tkt/proxy.py` a `build_entry` function plus the `ProxyHandler` class that relays the request to upstream and buffers the full response:

```python
_SKIP_FORWARD = {
    "host",
    "connection",
    "content-length",
    "keep-alive",
    "transfer-encoding",
    "expect",
    "upgrade",
}


def build_entry(
    *, method: str, path: str, upstream_url: str, headers: dict[str, str], body: bytes
) -> dict:
    """Build a capture record from incoming request fields."""
    return {
        "time": __import__("time").time(),
        "method": method,
        "path": path,
        "upstream_url": upstream_url,
        "request_headers": mask_headers(headers),
        "request_body": body.decode("utf-8", "replace"),
    }
```

Then add a `ProxyHandler` (a small subclass overriding `do_*` and `_relay`). Keep it faithful to the prototype: read `Content-Length` bytes, build headers forwarding all but `_SKIP_FORWARD`, open the upstream request, relay status/headers with `Connection: close`, stream the body into `chunks` while forwarding to the client, then append status/response to the record and `write_record`. On `HTTPError`, relay it as a normal response; on any other exception, write the record with an `error` field and `send_error(502)`. Override `log_message` to no-op (keep the capture file clean). A complete reference implementation is in the design doc's prototype; transcribe it, restructured around `build_entry`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_proxy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tkt/proxy.py tests/test_proxy.py
git commit -m "feat(tracing): add relay proxy handler"
```

---

### Task 3: Proxy server + ssh co-invocation

**Files:**

- Modify: `tkt/proxy.py`
- Test: `tests/test_proxy.py`

**Interfaces:**

- Consumes: `ProxyHandler` (Task 2).
- Produces: `ProxyServer(ThreadingHTTPServer)` (exposes `.logger` as a writable file-like and `.upstream`); `run_proxy(listen_port, upstream, log_path) -> None` (foreground, Ctrl-C friendly, flushes on exit); `wait_for_upstream(upstream, timeout) -> bool`; `resolve_traces_dir(config) -> Path` (env `TKT_TRACES_DIR` else `~/.tkt/traces`); `run_with_ssh(upstream, ssh_command: list[str], *, listen: int, log_path: str) -> None` (spawns the proxy as a background child, runs `ssh_command` in the foreground, tears the child down on ssh exit). Task 4's CLI calls these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_proxy.py`:

```python
from pathlib import Path

from tkt.proxy import resolve_traces_dir


def test_resolve_traces_dir_uses_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TKT_TRACES_DIR", str(tmp_path / "traces"))
    assert resolve_traces_dir() == tmp_path / "traces"


def test_resolve_traces_dir_defaults_to_home(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("TKT_TRACES_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_traces_dir() == tmp_path / ".tkt" / "traces"


def test_wait_for_upstream_returns_true_when_open(monkeypatch) -> None:
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        from tkt.proxy import wait_for_upstream

        assert wait_for_upstream(f"http://127.0.0.1:{port}", timeout=1.0) is True
    finally:
        srv.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_proxy.py::test_resolve_traces_dir_uses_env -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

Add to `tkt/proxy.py`:

```python
import os
import socket
from http.server import ThreadingHTTPServer
from pathlib import Path


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, *, upstream, logger):
        super().__init__(address, handler)
        self.upstream = upstream.rstrip("/")
        self.logger = logger


def wait_for_upstream(upstream: str, timeout: float = 5.0) -> bool:
    """Poll until the upstream host:port accepts a connection or timeout."""
    from urllib.parse import urlparse

    parsed = urlparse(upstream)
    host, port = parsed.hostname, parsed.port
    if host is None or port is None:
        return False
    deadline = __import__("time").time() + timeout
    while __import__("time").time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            __import__("time").sleep(0.1)
    return False


def resolve_traces_dir(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("TKT_TRACES_DIR") or (Path.home() / ".tkt" / "traces"))
```

Then `run_proxy(listen_port, upstream, log_path)` that opens the log in append mode, builds a `ProxyServer(("127.0.0.1", listen_port), ProxyHandler, upstream=upstream, logger=fh)`, prints startup lines, and `serve_forever()` in a `try` that closes the log on `KeyboardInterrupt`/`SystemExit`. And `run_with_ssh(upstream, ssh_command, *, listen, log_path)`:

1. Start a `subprocess.Popen` running this same module's proxy as a background child (`sys.executable -m tkt.proxy --listen <port> --upstream <upstream> --log <path>`, or re-enter through a `main()`), detached so it survives independently (use `start_new_session=True`).
2. `wait_for_upstream(upstream)`.
3. Run `ssh_command` in the foreground (inherit stdio) via `subprocess.run`.
4. On return, terminate the proxy child and wait for it.

(Note: rely on a `main()`/`argparse`-style entry from the prototype, or add a `__main__` block, to support the background child; keep it stdlib-only.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_proxy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tkt/proxy.py tests/test_proxy.py
git commit -m "feat(tracing): add proxy server, ssh co-invocation, traces dir resolution"
```

---

### Task 4: `tkt trace-proxy` CLI command + `tkt/proxy.py` `main()`

**Files:**

- Modify: `tkt/_cli.py`, `tkt/proxy.py`
- Test: `tests/test_tools.py` (CLI registration smoke) — extend an existing test file rather than adding a new one, per repo convention.

**Interfaces:**

- Consumes: `run_proxy`, `run_with_ssh`, `resolve_traces_dir`, `wait_for_upstream` (Task 3).
- Produces: a `trace-proxy` Click command on the `cli` group with options `--listen` (int, default 8090), `--upstream` (str, required), `--traces-dir`, `--ssh-host` (str, optional), `-v/--verbose`. Also `proxy.py` gains a `main()`/`if __name__ == "__main__"` entry usable by the background child (as referenced in Task 3).

- [ ] **Step 1: Write the failing test**

In `tests/test_tools.py`, add:

```python
def test_trace_proxy_command_exists() -> None:
    from tkt._cli import cli
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli, ["trace-proxy", "--help"])
    assert result.exit_code == 0
    assert "trace-proxy" in result.output
```

(Adjust to whatever CLI-testing convention `tests/test_tools.py` already uses — check it first.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL (no `trace-proxy` command).

- [ ] **Step 3: Write the CLI command + module main**

In `tkt/_cli.py`, add (matching the `mcp-server` command style):

```python
@cli.command(
    "trace-proxy",
    help=(
        "Run the long-lived model-traffic capture proxy. Appends one JSON "
        "object per exchange to the continuous capture file. With --ssh-host, "
        "runs an interactive ssh session in the foreground (whose config tunnel "
        "is the upstream) with the proxy managed as a background child."
    ),
)
@click.option("--listen", type=int, default=8090, help="Local listen port (default 8090).")
@click.option("--upstream", required=True, help="Upstream scheme://host:port with no path.")
@click.option("--traces-dir", type=click.Path())
@click.option("--ssh-host", default=None, help="Run an interactive ssh session to this host.")
@click.option("-v", "--verbose", count=True)
def trace_proxy(*, listen: int, upstream: str, traces_dir: str | None, ssh_host: str | None, verbose: int) -> None:
    _setup_logging(verbose)
    from .proxy import resolve_traces_dir, run_proxy, run_with_ssh

    root = resolve_traces_dir()
    if traces_dir:
        root = Path(traces_dir)
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "capture.jsonl"
    if ssh_host:
        run_with_ssh(upstream, ["ssh", ssh_host], listen=listen, log_path=str(log_path))
    else:
        run_proxy(listen, upstream, str(log_path))
```

(Add any missing `from pathlib import Path` import to `_cli.py` if not already present. This is the canonical `run_with_ssh(upstream, ssh_command, *, listen, log_path)` call site; Task 3 defines it.)

In `tkt/proxy.py`, add a `main()` (argparse) and `if __name__ == "__main__": main()` that parses `--listen`, `--upstream`, `--log` and calls `run_proxy`, so the Task 3 background child (`python -m tkt.proxy ...`) works. Adjust `run_with_ssh`'s signature to accept `listen` and `log_path` if the Task 3 version used positional-only args.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools.py tests/test_proxy.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check tkt/proxy.py tkt/_cli.py tests/test_proxy.py tests/test_tools.py
ruff format --check tkt/proxy.py tkt/_cli.py tests/test_proxy.py tests/test_tools.py
mypy tkt/
git add tkt/proxy.py tkt/_cli.py tests/test_tools.py
git commit -m "feat(tracing): add tkt trace-proxy command"
```

---

### Task 5: Capture parsing + conversation-reset session detection + label extraction

**Files:**

- Create: `tkt/tracelog.py`
- Test: `tests/test_tracelog.py`

**Interfaces:**

- Consumes: nothing from proxy (pure functions over capture records).
- Produces (all in `tkt/tracelog.py`, all stdlib-only):
  - `iter_records(path) -> Iterator[dict]` — lazily yields JSON objects from a `capture.jsonl`.
  - `session_id(record) -> str | None` — the `x-session-id`/`x-parent-session-id` request header value, case-insensitive, else `None`.
  - `is_title_request(record) -> bool` — True if any request message content (str) contains `title` / `Generate a title`.
  - `_fingerprint(msg: dict) -> tuple` — `(role, content_normalized)` where content is the string's first 80 chars, or a marker for non-str content.
  - `extract_title(record) -> str | None` — concatenated SSE `delta.content` from a title request's `response_body`.
  - `segment(captures: list[dict]) -> list[dict]` — returns a list of session records: `{"id", "entries": list[int] (indices into captures), "label", "start", "end", "client_ua"}`. Groups by `session_id` when present else by multi-conversation / connected-component content detection (robust to parallel subagents). Uses helpers `_continue_score(a, b) -> float` and `_build_components(records) -> list[list[int]]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tracelog.py` with license header, then:

```python
from tkt.tracelog import extract_title, is_title_request, session_id


def _rec(**over) -> dict:
    base = {
        "time": 1.0, "method": "POST", "path": "/api/v1/chat/completions",
        "upstream_url": "http://localhost:8080",
        "request_headers": {}, "request_body": "{}",
        "status": 200, "response_headers": {}, "response_body": "",
    }
    base.update(over)
    return base


def test_session_id_reads_header_case_insensitive() -> None:
    r = _rec(request_headers={"X-Session-Id": "ses_abc"})
    assert session_id(r) == "ses_abc"


def test_session_id_none_when_absent() -> None:
    assert session_id(_rec()) is None


def test_is_title_request_matches_generate_title() -> None:
    body = '{"messages":[{"role":"user","content":"Generate a title for this conversation: foo"}]}'
    assert is_title_request(_rec(request_body=body)) is True


def test_extract_title_concatenates_delta_content() -> None:
    sse = (
        'data: {"choices":[{"delta":{"content":"Allow "}}]}\n'
        'data: {"choices":[{"delta":{"content":"additional"}}]}\n'
        'data: [DONE]\n'
    )
    assert extract_title(_rec(response_body=sse)) == "Allow additional"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracelog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tkt.tracelog'`.

- [ ] **Step 3: Write minimal implementation**

Create `tkt/tracelog.py` with the license header. Implement:

```python
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


def iter_records(path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _header(record: dict, name: str) -> str | None:
    headers = record.get("request_headers", {}) or {}
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def session_id(record: dict) -> str | None:
    return _header(record, "x-session-id") or _header(record, "x-parent-session-id")


def is_title_request(record: dict) -> bool:
    try:
        msgs = json.loads(record.get("request_body", "{}")).get("messages", [])
    except (ValueError, AttributeError):
        return False
    for m in msgs:
        content = m.get("content")
        if isinstance(content, str) and ("title" in content.lower() or "Generate a title" in content):
            return True
    return False


def extract_title(record: dict) -> str | None:
    parts = []
    for line in record.get("response_body", "").splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            obj = json.loads(line[6:])
        except ValueError:
            continue
        for choice in obj.get("choices", []):
            content = choice.get("delta", {}).get("content")
            if isinstance(content, str):
                parts.append(content)
    text = "".join(parts).strip()
    return text or None
```

Then `segment(captures: list[dict]) -> list[dict]`:

- If `session_id(record)` is present, group consecutive records with the same id.
- Otherwise, use **multi-conversation / connected-component tracking**:
  - For each record, build its fingerprint list from the request `messages`, **excluding the
    leading system message (shared boilerplate)** so it cannot over-merge fresh conversations.
  - Define `continues(prev_fps, cur_fps) -> bool`: True when `cur_fps` re-presents `prev_fps`
    (i.e. `prev_fps` is a prefix of `cur_fps`, or a long suffix of `cur_fps` matches `prev_fps`)
    — weighted over the user/assistant turns, allowing within-session trims/shrinks.
  - Link each record to the best earlier record (within a look-back window of, say, the last 20
    records) that it `continues`; build a graph and take **connected components as sessions**.
    A record that continues nothing (empty-context subagent start, fresh conversation) opens a
    new component.
  - This is order-robust: a primary and parallel subagents interleave in the capture but each
    request is matched to its own lineage, so they form distinct sessions rather than fragments
    or merges. Resumed-after-pause stays one session (continues the same history).
  - Title-gen records: attach to the component they continue (or the most recent one), set its
    `label` via `extract_title` if not set, and never trigger a split.
  - Attach `start`/`end` (first/last `time`), `client_ua` (from request headers), `label`
    (fallback `session_id`/timebase).
- Return the list of session records with non-contiguous `entries` indices allowed (a session's
  exchanges may be interleaved with other sessions in the capture; order within a session file
  is chronological by `time`).

**The overlap/`continues` formula and look-back window are baselines to be tuned against the
golden fixtures (Task 8).** Make `segment` and its helpers (`_continue_score(a, b) -> float`,
`_build_components(records) -> list[list[int]]`) refactor-friendly so the thresholds/window are
easy to adjust.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracelog.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tkt/tracelog.py tests/test_tracelog.py
git commit -m "feat(tracing): add capture parsing, session detection, and title extraction"
```

---

### Task 6: Session file I/O + pin/prune

**Files:**

- Modify: `tkt/tracelog.py`
- Test: `tests/test_tracelog.py`

**Interfaces:**

- Consumes: `segment`, `iter_records`, `extract_title`, `session_id` (Task 5); `resolve_traces_dir` (Task 3).
- Produces:
  - `write_session_files(root: Path, sessions: list[dict], captures: list[dict]) -> list[Path]` — for each session, writes `sessions/<date>_<n>.jsonl.gz` (gzipped JSONL of that session's `captures[i]` exchanges) and `sessions/<date>_<n>.meta.json` (metadata incl. `label`, `id`, `start`, `end`, `client_ua`, `pinned: False`); returns the session-file paths.
  - `list_sessions(root: Path) -> list[dict]` — loads `.meta.json` for each session file.
  - `pin_session(root, session_id, pinned=True) -> None`, `show_session(root, session_id, raw=False)`.
  - `prune(root, horizon_days=30, keep=20) -> list[Path]` — deletes unpinned session files (both `.jsonl.gz` + `.meta.json`) older than horizon and beyond `keep` most recent; returns deleted paths.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tracelog.py`:

```python
import gzip, json
from pathlib import Path

from tkt.tracelog import (
    list_sessions, pin_session, prune, write_session_files,
)


def test_write_session_files_and_list_roundtrip(tmp_path) -> None:
    captures = [{"time": 1.0, "path": "/api/v1/chat/completions"}]
    sessions = [{"id": "s1", "entries": [0], "label": "My title",
                 "start": 1.0, "end": 1.0, "client_ua": "Zed"}]
    paths = write_session_files(tmp_path, sessions, captures)
    assert len(paths) == 1
    gz = paths[0]
    with gzip.open(gz, "rt", encoding="utf-8") as fh:
        assert json.loads(fh.read())["path"] == "/api/v1/chat/completions"
    meta = gz.with_suffix("").with_suffix(".meta.json")
    assert json.loads(meta.read_text())["label"] == "My title"
    listed = list_sessions(tmp_path)
    assert listed[0]["label"] == "My title"


def test_pin_and_prune_exempts_pinned(tmp_path) -> None:
    s = [{"id": "keep", "entries": [0], "label": "pinned", "start": 1.0, "end": 1.0, "client_ua": "Zed"}]
    write_session_files(tmp_path, s, [{"time": 1.0}])
    pin_session(tmp_path, "keep", pinned=True)
    # horribly old and not within keep -> should still survive because pinned
    deleted = prune(tmp_path, horizon_days=0, keep=0)
    # prune should skip pinned
    assert "keep" not in [Path(d).name for d in deleted]
    assert list_sessions(tmp_path)[0]["pinned"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracelog.py::test_write_session_files_and_list_roundtrip -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write minimal implementation**

Add to `tkt/tracelog.py`:

```python
import gzip
from pathlib import Path


def _session_file(root: Path, session: dict) -> Path:
    start = session["start"]
    import datetime

    ts = datetime.datetime.fromtimestamp(start).strftime("%Y%m%dT%H%M%S")
    return root / "sessions" / f"{ts}_{session['id']}.jsonl.gz"


def _meta_file(session_file: Path) -> Path:
    return session_file.with_suffix("").with_suffix(".meta.json")  # .jsonl.gz -> .jsonl -> .meta.json
```

`_meta_file`: `session_file` is `.../<name>.jsonl.gz`; `.with_suffix("")` gives `.../<name>.jsonl`; `.with_suffix(".meta.json")` gives `.../<name>.meta.json`. Then implement `write_session_files` (mkdir `sessions`, gzip-write each session's exchanges to `_session_file`, write meta JSON to `_meta_file`), `list_sessions` (scan `sessions/*.jsonl.gz`, read paired `_meta_file`), `pin_session` (load meta, set `pinned`, rewrite), `show_session` (print the session's exchanges from the gz, or raw JSON when `raw=True`), and `prune` (for each session, if not pinned and (older than `horizon_days` from now or not among the `keep` newest by `start`), delete both files). The `.meta.json` and `.jsonl.gz` must be written/removed together.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracelog.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tkt/tracelog.py tests/test_tracelog.py
git commit -m "feat(tracing): add session file I/O and pin/prune"
```

---

### Task 7: `tkt trace-log` CLI command (segment/list/show/pin/unpin)

**Files:**

- Modify: `tkt/_cli.py`
- Test: `tests/test_tools.py`

**Interfaces:**

- Consumes: `iter_records`, `segment`, `write_session_files`, `list_sessions`, `pin_session`, `show_session`, `prune`; `resolve_traces_dir`.
- Produces: a `trace-log` Click command group on `cli` with subcommands `segment`, `list`, `show <id>`, `pin <id>`, `unpin <id>`, and options `--traces-dir`, `--horizon-days` (default 30), `--keep` (default 20), `-v/--verbose`.

- [ ] **Step 1: Write the failing test**

In `tests/test_tools.py`:

```python
def test_trace_log_command_exists() -> None:
    from tkt._cli import cli
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(cli, ["trace-log", "--help"])
    assert result.exit_code == 0
    assert "trace-log" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL (no `trace-log` command).

- [ ] **Step 3: Write the CLI command group**

In `tkt/_cli.py`, add a `trace-log` Click group with subcommands (use `@cli.group("trace-log")` + `@trace_log.command(...)`). Structure the segment subcommand:

```python
@cli.group(
    "trace-log",
    help="Retroactively segment, label, list, show, pin, and prune captured model traffic.",
)
@click.option("--traces-dir", type=click.Path())
@click.option("-v", "--verbose", count=True)
@click.pass_context
def trace_log(ctx, *, traces_dir, verbose) -> None:
    _setup_logging(verbose)
    from .proxy import resolve_traces_dir
    from pathlib import Path

    root = resolve_traces_dir()
    if traces_dir:
        root = Path(traces_dir)
    ctx.ensure_object(dict)
    ctx.obj["root"] = root
```

Then the subcommands:

```python
@trace_log.command("segment")
@click.option("--horizon-days", type=int, default=30)
@click.option("--keep", type=int, default=20)
@click.pass_context
def trace_log_segment(ctx, *, horizon_days, keep) -> None:
    from .tracelog import iter_records, segment, write_session_files, prune

    root = ctx.obj["root"]
    captures = list(iter_records(root / "capture.jsonl"))
    sessions = segment(captures)
    write_session_files(root, sessions, captures)
    removed = prune(root, horizon_days=horizon_days, keep=keep)
    click.echo(f"segmented {len(sessions)} session(s); pruned {len(removed)}")
```

Add `list` (print a table: SESSION / LABEL / START / EXCH / DURATION / CLIENT / PINNED), `show <session_id>` (`--raw` flag), `pin <session_id>`, and `unpin <session_id>` subcommands wired to `list_sessions`, `show_session`, `pin_session`. Import lazily inside the functions (repo convention). Each command gets a `--traces-dir` passthrough via the group context.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tools.py tests/test_tracelog.py tests/test_proxy.py -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
ruff check tkt/_cli.py tests/test_tools.py
ruff format --check tkt/_cli.py tests/test_tools.py
mypy tkt/
git add tkt/_cli.py tests/test_tools.py
git commit -m "feat(tracing): add tkt trace-log command group"
```

---

### Task 8: Synthetic session-detection fixtures + real-log validation

**Files:**

- Create: `tests/fixtures/zed-single-session.jsonl`, `tests/fixtures/zed-two-sessions.jsonl`, `tests/fixtures/opencode-sessions.jsonl` (small, content-free)
- Modify: `tests/test_tracelog.py`

**Interfaces:**

- Consumes: `iter_records`, `session_id`, `extract_title`, `is_title_request`, `segment` (Task 5).

**Rationale:** The full real captures under `investigations/` are large (5.9MB–17MB) and contain real LLM user prompts; committing them to the repo is undesirable (the roadmap treats `investigations/` as git-ignored scratch). So the committed fixtures are **small, hand-built, content-free** JSONL that replicate the structural signals: same-session monotonic growth, the within-session shrink/divergence (entry 2→3 in 1a), a title-gen request at session start, a fresh-conversation boundary, and OpenCode `x-session-id` grouping. The **real** `agent-capture-1a.log` is used as an uncommitted local validation to tune the detector threshold.

- [ ] **Step 1: Write the fixture builder as a test-mode helper**

Define the three fixtures inline as Python lists of capture dicts (content placeholder strings), written to `tests/fixtures/*.jsonl` in a fixture-creator step. Each exchange uses the record shape from Task 1. Example Zed single-session fixture (system + first user, then growth, then a title-gen with an SSE body, then a within-session shrink):

```python
# structure sketch (implement as write-the-records helper in the test module)
def _zed_grow(n: int, first: str) -> list[dict]:
    msgs = [{"role": "system", "content": "You are a coding agent."}]
    out = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"turn {i}"})
        msgs.append({"role": "assistant", "content": f"answer {i}"})
        out.append({"time": 100.0 + i, "method": "POST",
                    "path": "/api/v1/chat/completions",
                    "request_headers": {"user-agent": "Zed/1.18.0"},
                    "request_body": json.dumps({"messages": list(msgs)}),
                    "status": 200, "response_headers": {},
                    "response_body": 'data: {"choices":[{"delta":{"content":"ok"}}]}\ndata: [DONE]\n'})
    return out
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_tracelog.py`:

```python
from tkt.tracelog import iter_records, segment


def test_segment_groups_monotonic_growth_into_one_session(tmp_path) -> None:
    # single Zed session: 6 growing exchanges -> 1 session
    recs = _zed_grow(6, "first task")
    captures = write_fixture(tmp_path, "zed-single-session.jsonl", recs)
    assert len(segment(captures)) == 1


def test_segment_detects_fresh_conversation_boundary(tmp_path) -> None:
    # first Zed session (grows to 4), then a *new* conversation with a reset
    # history (different first user task, non-continuing) -> 2 sessions
    a = _zed_grow(4, "first task")
    b = _zed_grow(3, "totally different task")
    captures = a + b
    assert len(segment(captures)) == 2


def test_segment_ignores_title_request_as_boundary(tmp_path) -> None:
    # a title-gen exchange in the middle must NOT split the session
    recs = _zed_grow(3, "task")
    # insert a title-gen record between index 1 and 2
    title = {"time": 102.5, "method": "POST", "path": "/api/v1/chat/completions",
             "request_headers": {}, "request_body": json.dumps(
                 {"messages": [{"role": "user",
                                "content": "Generate a title for this conversation: x"}]}),
             "status": 200, "response_headers": {},
             "response_body": 'data: {"choices":[{"delta":{"content":"Great title"}}]}\ndata: [DONE]\n'}
    captures = recs[:2] + [title] + recs[2:]
    sessions = segment(captures)
    assert len(sessions) == 1
    assert sessions[0]["label"] == "Great title"


def test_segment_groups_opencode_by_session_id(tmp_path) -> None:
    # two different x-session-id groups -> 2 sessions, even with title-gen in each
    def oc(rid: str) -> dict:
        return {"time": 1.0, "method": "POST", "path": "/api/v1/chat/completions",
                "request_headers": {"x-session-id": rid},
                "request_body": json.dumps({"messages": [{"role": "user", "content": "hi"}]}),
                "status": 200, "response_headers": {}, "response_body": ""}
    captures = [oc("ses_a"), oc("ses_a"), oc("ses_b"), oc("ses_b")]
    assert len(segment(captures)) == 2


def test_segment_parallel_subagents_are_own_sessions(tmp_path) -> None:
    # primary + two fresh-context subagents interleaved -> 3 distinct sessions,
    # not fragments or merges. Each subagent starts with empty context (its own
    # system + first user task) and grows independently.
    def fresh(topic: str, blocks: int) -> list[dict]:
        msgs = [{"role": "system", "content": "Shared Zed system prompt"}]
        out = []
        for i in range(blocks):
            msgs.append({"role": "user", "content": f"{topic} turn {i}"})
            msgs.append({"role": "assistant", "content": f"{topic} answer {i}"})
            out.append({"time": 200.0 + i, "method": "POST",
                        "path": "/api/v1/chat/completions",
                        "request_headers": {"user-agent": "Zed/1.18.0"},
                        "request_body": json.dumps({"messages": list(msgs)}),
                        "status": 200, "response_headers": {}, "response_body": ""})
        return out

    primary = fresh("primary", 3)      # times 200,201,202
    sub_a = fresh("sub-A", 2)          # share the *same* system prompt
    sub_b = fresh("sub-B", 2)
    # interleave chronologically: primary, subA, subB, primary, subA, subB, primary
    interleaved = [primary[0], sub_a[0], sub_b[0],
                   primary[1], sub_a[1], sub_b[1],
                   primary[2]]
    sessions = segment(interleaved)
    assert len(sessions) == 3, f"expected 3 sessions, got {len(sessions)}"
    # each session contains only its own topic's exchanges
    for s in sessions:
        bodies = [interleaved[i]["request_body"] for i in s["entries"]]
        assert all("primary" in b for b in bodies) \
            or all("sub-A" in b for b in bodies) \
            or all("sub-B" in b for b in bodies)
```

Place `_zed_grow` and a `write_fixture(path, name, recs) -> list[dict]` helper (writes the records to `path/name.jsonl` in the JSONL format and returns the parsed list) at module scope in `tests/test_tracelog.py`.

- [ ] **Step 3: Run the tests; implement/tune `segment`**

Run: `python -m pytest tests/test_tracelog.py -v`
Expected: the four tests pass. `test_segment_detects_fresh_conversation_boundary` is the key one: the detector must split on a non-continuing history (the second conversation starts with a fresh first user task and its history does not grow from the first) yet keep the title-gen and monotonic-growth cases as single sessions. Tune the overlap formula/threshold in `tkt/tracelog.py::segment` until all four pass.

- [ ] **Step 4: Local (uncommitted) real-log validation to tune the threshold**

```bash
python - <<'PY'
import sys
from tkt.tracelog import iter_records, segment
caps = list(iter_records("investigations/bad-thinking/agent-capture-1a.log"))
s = segment(caps)
print("sessions:", len(s))   # expect 1
for x in s:
    print("label:", x.get("label"))
PY
```

Expected: `sessions: 1` and label containing `Allow additional port`. If not, adjust the threshold again (this is the real-data tuning gate). This step is run locally and is **not** committed; it confirms the synthetic-tuned detector generalizes to real traffic.

- [ ] **Step 5: Verify all tests pass + lint + commit**

Run: `python -m pytest tests/ -v` (all pass). Then:

```bash
ruff check .
ruff format --check .
mypy tkt/
git add tests/test_tracelog.py tests/fixtures
git commit -m "test(tracing): session detection fixtures and real-log threshold validation"
```

---

### Task 9: Documentation + final review

**Files:**

- Modify: `docs/zed-agent-roadmap.md`, `docs/superpowers/plans/2026-09-02-tracing-proxy.md` (review pass)
- Test: none (docs)

**Interfaces:**

- Consumes: the completed `tkt trace-proxy` / `tkt trace-log` commands.
- Produces: roadmap note that E3 is done; a short usage note in the roadmap's emergent section (§9) pointing at the two commands and the data layout.

- [ ] **Step 1: Update the roadmap**

In `docs/zed-agent-roadmap.md` §9 (Emergent work), update E3 to mark it done and add a one-paragraph summary of the two commands, the data layout (`~/.tkt/traces/`: `capture.jsonl` + `sessions/*.jsonl.gz` + `*.meta.json`), the labeling scheme (auto-title), and the pin/prune workflow.

- [ ] **Step 2: Final lint + full test run**

```bash
ruff check .
ruff format --check .
mypy tkt/
python -m pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 3: Self-review commit**

Read back `tkt/proxy.py` and `tkt/tracelog.py` for correctness and consistency with the design doc. Confirm the two commands appear in `tkt --help`. Confirm no changes to `investigations/` and no new third-party deps.

```bash
git add docs/zed-agent-roadmap.md
git commit -m "docs(roadmap): mark E3 tracing proxy done"
```

---
