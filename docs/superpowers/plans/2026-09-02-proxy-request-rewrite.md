# Proxy Request-Rewrite (Zed/OpenCode Sampling Reconciliation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable, model-gated request-body rewriting to `tkt trace-proxy` so Zed's chat-completions requests can be rewritten to match OpenCode's sampling config (drop `temperature`, set `top_p`).

**Architecture:** A pure rewrite function in `tkt/proxy.py` transforms the chat-completions body per rules configured in `local.json` (`proxy.rewrite`); the relay records the original body as `request_body` plus `request_body_forwarded` when changed. `_cli.py`'s `trace_proxy` reads `local.json` and threads the plain rule list down to `run_proxy`/`run_with_ssh`; the ssh child receives it as a `--rewrite-rules` JSON CLI arg. `tkt/proxy.py` stays stdlib-only (stdlib `json`).

**Tech Stack:** Python 3.13, stdlib `http.server`/`urllib`/`json`, `click` (CLI), `pytest`.

**Spec:** `docs/superpowers/specs/2026-09-02-proxy-request-rewrite-design.md` — this plan argues from that spec; executors read both.

## Global Constraints

(From the spec; every task's requirements implicitly include these.)
- Python 3.13; dependencies only `click`, `GitPython`, `pyyaml`, `json5` — **no new third-party dependencies**; `tkt/proxy.py` stdlib-only.
- Every touched `.py` file keeps its existing BSD-3-Clause header; do not add new `.py` files without a header.
- Must pass before each commit and at the end: `ruff check .`, `ruff format --check .`, `mypy tkt/`.
- No packaging config changes; `tkt` is not pip-distributed.
- No rewrite behavior when no rules are configured (all existing `tests/test_proxy.py` tests keep passing).
- Do not touch `investigations/` originals.
- Run tests with `python -m pytest`.

---
### Task 1: Pure rewrite logic (`apply_rewrites`, `_rule_matches`)

**Files:**
- Modify: `tkt/proxy.py:50` (add `Sequence` to the `collections.abc` import) and add two functions near the top, after `write_record` (after line 79).
- Test: `tests/test_proxy.py`

**Interfaces:**
- Consumes: nothing (stdlib `json`).
- Produces: `apply_rewrites(body: bytes, *, user_agent: str, rewrite_rules: Sequence[Mapping[str, Any]]) -> tuple[bytes, bool]` and `_rule_matches(rule: Mapping[str, Any], *, user_agent: str, model: object) -> bool`. Task 2's relay and Task 4's CLI both call `apply_rewrites`; Task 2 depends on the `getattr` default-on-missing-server-attribute pattern (tested here implicitly via the non-matching/no-rules cases).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_proxy.py` (after the existing imports, add `apply_rewrites` and `_rule_matches` to the `from tkt.proxy import (...)` list):

```python
def test_apply_rewrites_sets_and_removes_params() -> None:
    body = b'{"model":"m","temperature":1.0}'
    rules = [{"client": "zed", "params": {"temperature": None, "top_p": 0.95}}]
    out, changed = apply_rewrites(body, user_agent="Zed/1.18.0", rewrite_rules=rules)
    assert changed is True
    assert json.loads(out) == {"model": "m", "top_p": 0.95}


def test_apply_rewrites_skips_nonmatching_client() -> None:
    body = b'{"model":"m","temperature":1.0}'
    rules = [{"client": "zed", "params": {"top_p": 0.95}}]
    out, changed = apply_rewrites(body, user_agent="OpenCode", rewrite_rules=rules)
    assert changed is False
    assert out == body


def test_apply_rewrites_respects_model_gate() -> None:
    body = b'{"model":"other","temperature":1.0}'
    rules = [{"model": "deepseek-ai/DeepSeek-V4-Flash-0731", "params": {"top_p": 0.95}}]
    out, changed = apply_rewrites(body, user_agent="anything", rewrite_rules=rules)
    assert changed is False


def test_apply_rewrites_leaves_non_chat_json_untouched() -> None:
    rules = [{"params": {"top_p": 0.95}}]
    for body in (b"not json", b"[1, 2]", b'{"no_model":true}'):
        out, changed = apply_rewrites(body, user_agent="x", rewrite_rules=rules)
        assert changed is False
        assert out == body


def test_apply_rewrites_noop_when_no_rules() -> None:
    body = b'{"model":"m","temperature":1.0}'
    out, changed = apply_rewrites(body, user_agent="x", rewrite_rules=())
    assert changed is False
    assert out == body


def test_rule_matches_client_case_insensitive() -> None:
    rule = {"client": "Zed", "model": "m"}
    assert _rule_matches(rule, user_agent="zed/1.2", model="m") is True
    assert _rule_matches(rule, user_agent="opencode", model="m") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_proxy.py -k "apply_rewrites or rule_matches" -v`
Expected: FAIL with `ImportError: cannot import name 'apply_rewrites'`.

- [ ] **Step 3: Implement**

In `tkt/proxy.py`, change the import at line 50:

```python
from collections.abc import Mapping, Sequence
```

Add these functions immediately after `write_record` (after line 79):

```python
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
        anything else sets it). Rules apply in order.

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
    """Return whether ``rule`` applies to a request with this user-agent/model."""
    client = rule.get("client")
    if client is not None and str(client).lower() not in user_agent.lower():
        return False
    rule_model = rule.get("model")
    if rule_model is not None and rule_model != model:
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_proxy.py -k "apply_rewrites or rule_matches" -v`
Expected: PASS (all 6 new tests).

- [ ] **Step 5: Run lint on the touched files**

Run: `ruff check tkt/proxy.py tests/test_proxy.py && ruff format --check tkt/proxy.py tests/test_proxy.py`
Expected: no errors. Fix any style issues (e.g. the doc length / position of the single-line docstring on `_rule_matches` if ruff flags it).

- [ ] **Step 6: Commit**

```bash
git add tkt/proxy.py tests/test_proxy.py
git commit -m "feat(proxy): add pure request-body rewrite helpers"
```

---
### Task 2: Apply rewrites in the relay with `request_body_forwarded`

**Files:**
- Modify: `tkt/proxy.py:115-132` (`ProxyHandler._relay`).
- Test: `tests/test_proxy.py`

**Interfaces:**
- Consumes: `apply_rewrites` from Task 1.
- Produces: `ProxyHandler._relay` reads `self.server.rewrite_rules` via `getattr(self.server, "rewrite_rules", ())` (so a server object without that attribute — like an older stand-in — behaves as "no rules"). Records `request_body_forwarded` on the capture entry only when the forwarded body differs from the client body. Task 3 then sets that attribute on `ProxyServer`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_proxy.py` (after the existing `_UpstreamHandler`, add an echo handler; reuse the existing `_ProxyServer` stand-in and `_reserve_free_port`/`_EchoUpstreamHandler`):

```python
class _EchoPostHandler(BaseHTTPRequestHandler):
    """Echo the POST body back and record it for assertion."""

    received: list[bytes] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).received.append(body)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:
        pass


def test_relay_rewrites_chat_completions_and_records_forwarded(tmp_path) -> None:
    _EchoPostHandler.received = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _EchoPostHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}"

    log_path = tmp_path / "capture.jsonl"
    logger = open(log_path, "a", encoding="utf-8")
    proxy = _ProxyServer(("127.0.0.1", 0), upstream_url, logger)
    proxy.rewrite_rules = [{"client": "zed", "params": {"temperature": None, "top_p": 0.95}}]
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"

    try:
        req = urllib.request.Request(
            f"{proxy_url}/api/v1/chat/completions",
            data=b'{"model":"m","temperature":1.0}',
            headers={"User-Agent": "Zed/1.2", "Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        resp.read()
    finally:
        proxy.shutdown()
        proxy.server_close()
        upstream.shutdown()
        upstream.server_close()
        logger.close()

    assert json.loads(_EchoPostHandler.received[-1]) == {"model": "m", "top_p": 0.95}
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    entry = records[-1]
    assert json.loads(entry["request_body"]) == {"model": "m", "temperature": 1.0}
    assert json.loads(entry["request_body_forwarded"]) == {"model": "m", "top_p": 0.95}


def test_relay_does_not_record_forwarded_for_unchanged_request(tmp_path) -> None:
    _EchoPostHandler.received = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _EchoPostHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}"

    log_path = tmp_path / "capture.jsonl"
    logger = open(log_path, "a", encoding="utf-8")
    proxy = _ProxyServer(("127.0.0.1", 0), upstream_url, logger)
    proxy.rewrite_rules = [{"client": "zed", "params": {"top_p": 0.95}}]
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"

    try:
        # Non-Zed user-agent: rule does not match, body unchanged.
        req = urllib.request.Request(
            f"{proxy_url}/api/v1/chat/completions",
            data=b'{"model":"m","temperature":1.0}',
            headers={"User-Agent": "OpenCode", "Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        resp.read()
    finally:
        proxy.shutdown()
        proxy.server_close()
        upstream.shutdown()
        upstream.server_close()
        logger.close()

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert "request_body_forwarded" not in records[-1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_proxy.py -k "relay_rewrites or relay_does_not_record" -v`
Expected: FAIL — the forwarded body still contains `temperature` and no `top_p` (rewrite not applied yet).

- [ ] **Step 3: Implement**

In `ProxyHandler._relay`, replace the block that computes `headers` and `entry` and builds `request` (lines ~122-132):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_proxy.py -k "relay" -v`
Expected: PASS (both new relay tests + all pre-existing relay tests still pass).

- [ ] **Step 5: Run full proxy test file and lint**

Run: `python -m pytest tests/test_proxy.py -q && ruff check tkt/proxy.py && ruff format --check tkt/proxy.py`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add tkt/proxy.py tests/test_proxy.py
git commit -m "feat(proxy): rewrite chat-completions bodies and record forwarded body"
```

---
### Task 3: Thread `rewrite_rules` through `ProxyServer`, `run_proxy`, `run_with_ssh`, `main`

**Files:**
- Modify: `tkt/proxy.py` — `ProxyServer.__init__` (lines 188-198), `run_proxy` (line 223), `run_with_ssh` (lines 237-270), `main` (lines 273-280).
- Test: `tests/test_proxy.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ProxyServer.__init__(..., *, upstream, logger, rewrite_rules: Sequence[Mapping[str, Any]] = ())`; `run_proxy(listen_port, upstream, log_path, *, rewrite_rules=())`; `run_with_ssh(upstream, ssh_command, *, listen, log_path, rewrite_rules=())` (forwards rules to the child as a `--rewrite-rules` JSON CLI arg); `main()` accepts `--rewrite-rules`. Backward-compatible: `run_proxy` still callable with 3 positional args (used by pre-existing `test_run_proxy_starts_and_serves`).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_proxy.py`:

```python
def test_run_with_ssh_forwards_rewrite_rules(monkeypatch) -> None:
    popen_calls: list[list[str]] = []

    class _FakePopen:
        def __init__(self, cmd, **kwargs) -> None:
            popen_calls.append(cmd)

        def terminate(self) -> None:
            pass

        def wait(self) -> None:
            pass

    monkeypatch.setattr("tkt.proxy.subprocess.Popen", _FakePopen)
    monkeypatch.setattr("tkt.proxy.wait_for_upstream", lambda upstream, timeout=30.0: True)

    run_with_ssh(
        "http://localhost:8080",
        ["ssh", "host"],
        listen=8090,
        log_path="/tmp/capture.jsonl",
        rewrite_rules=[{"client": "zed", "params": {"top_p": 0.95}}],
    )

    proxy_cmd = next(cmd for cmd in popen_calls if "--listen" in cmd)
    idx = proxy_cmd.index("--rewrite-rules")
    assert json.loads(proxy_cmd[idx + 1]) == [{"client": "zed", "params": {"top_p": 0.95}}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_proxy.py::test_run_with_ssh_forwards_rewrite_rules -v`
Expected: FAIL with `TypeError: run_with_ssh() got an unexpected keyword argument 'rewrite_rules'`.

- [ ] **Step 3: Implement**

`ProxyServer.__init__` — add the param and store it:

```python
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
```

`run_proxy` — add the keyword param and pass it through:

```python
def run_proxy(
    listen_port: int,
    upstream: str,
    log_path: str,
    *,
    rewrite_rules: Sequence[Mapping[str, Any]] = (),
) -> None:
    fh = open(log_path, "a", encoding="utf-8")
    server = ProxyServer(
        ("127.0.0.1", listen_port),
        ProxyHandler,
        upstream=upstream,
        logger=fh,
        rewrite_rules=rewrite_rules,
    )
```

`run_with_ssh` — add the param and forward it to the child:

```python
def run_with_ssh(
    upstream: str,
    ssh_command: list[str],
    *,
    listen: int,
    log_path: str,
    rewrite_rules: Sequence[Mapping[str, Any]] = (),
) -> None:
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
```

`main()` — add the argument and parse it:

```python
    parser.add_argument("--rewrite-rules", default=None, help="JSON array of rewrite rules")
    args = parser.parse_args()
    rewrite_rules: Sequence[Mapping[str, Any]] = ()
    if args.rewrite_rules:
        rewrite_rules = json.loads(args.rewrite_rules)
    run_proxy(args.listen, args.upstream, args.log, rewrite_rules=rewrite_rules)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_proxy.py -v`
Expected: PASS — new ssh test plus all pre-existing proxy tests (incl. `test_run_proxy_starts_and_serves`, which still calls `run_proxy` with 3 positional args).

- [ ] **Step 5: Lint and type-check**

Run: `ruff check tkt/proxy.py && ruff format --check tkt/proxy.py && mypy tkt/proxy.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add tkt/proxy.py tests/test_proxy.py
git commit -m "feat(proxy): thread rewrite rules through server and CLI entrance"
```

---
### Task 4: CLI `trace_proxy` reads rewrite rules from `local.json`

**Files:**
- Modify: `tkt/_cli.py:442-457` (`trace_proxy` command).
- Test: `tests/test_proxy.py`

**Interfaces:**
- Consumes: `run_proxy`/`run_with_ssh` (Task 3) with their `rewrite_rules=` kwarg; `tkt.utils.read_json_file`.
- Produces: `trace_proxy` accepts `--environment` (envvar `TKT_ENVIRONMENT`, `click.File()`), reads the `proxy.rewrite` key of `local.json`, and passes the resulting `list[dict]` as `rewrite_rules` to `run_proxy`/`run_with_ssh`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_proxy.py` (add `import` of the click runner at the top of the test file; also import `cli` inside the test to avoid import-time cost):

```python
def test_trace_proxy_reads_rewrite_rules_from_env(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / "local.json"
    env_file.write_text(
        json.dumps({"proxy": {"rewrite": [{"client": "zed", "params": {"temperature": None, "top_p": 0.95}}]}}),
        encoding="utf-8",
    )
    captured: dict = {}
    fake_run_proxy = lambda *a, **kw: captured.update(kw)
    monkeypatch.setattr("tkt.proxy.run_proxy", fake_run_proxy)

    from click.testing import CliRunner
    from tkt._cli import cli

    result = CliRunner().invoke(
        cli,
        [
            "trace-proxy",
            "--environment",
            str(env_file),
            "--upstream",
            "http://127.0.0.1:1",
            "--listen",
            "8090",
            "--traces-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["rewrite_rules"] == [{"client": "zed", "params": {"temperature": None, "top_p": 0.95}}]


def test_trace_proxy_defaults_to_no_rules(tmp_path, monkeypatch) -> None:
    captured: dict = {}
    fake_run_proxy = lambda *a, **kw: captured.update(kw)
    monkeypatch.setattr("tkt.proxy.run_proxy", fake_run_proxy)

    from click.testing import CliRunner
    from tkt._cli import cli

    result = CliRunner().invoke(
        cli,
        [
            "trace-proxy",
            "--upstream",
            "http://127.0.0.1:1",
            "--listen",
            "8090",
            "--traces-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["rewrite_rules"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_proxy.py -k "trace_proxy" -v`
Expected: FAIL with `Error: No such option: --environment` (option not yet added).

- [ ] **Step 3: Implement**

In `tkt/_cli.py`, extend the `trace_proxy` decorator with the `--environment` option and update the function body. Replace the decorator block (lines 440-448) to add the option:

```python
@click.option("--ssh-host", default=None, help="Run an interactive ssh session to this host.")
@click.option("--environment", envvar="TKT_ENVIRONMENT", type=click.File())
@click.option("-v", "--verbose", count=True)
def trace_proxy(
    *,
    listen: int,
    upstream: str,
    traces_dir: str | None,
    ssh_host: str | None,
    environment: TextIO | None,
    verbose: int,
) -> None:
```

Replace the function body:

```python
    _setup_logging(verbose)
    from .proxy import resolve_traces_dir, run_proxy, run_with_ssh
    from .utils import read_json_file

    rewrite_rules: list[Any] = []
    if environment is not None:
        data = read_json_file(environment.name)
        rewrite_rules = list((data.get("proxy") or {}).get("rewrite") or [])

    root = resolve_traces_dir()
    if traces_dir:
        root = Path(traces_dir)
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "capture.jsonl"
    if ssh_host:
        run_with_ssh(
            upstream,
            ["ssh", ssh_host],
            listen=listen,
            log_path=str(log_path),
            rewrite_rules=rewrite_rules,
        )
    else:
        run_proxy(listen, upstream, str(log_path), rewrite_rules=rewrite_rules)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_proxy.py -k "trace_proxy" -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full test suite and lint**

Run: `python -m pytest -q && ruff check . && ruff format --check . && mypy tkt/`
Expected: all pass, no lint/type errors.

- [ ] **Step 6: Commit**

```bash
git add tkt/_cli.py tests/test_proxy.py
git commit -m "feat(cli): read proxy rewrite rules from environment config"
```

---
## Self-Review Notes

- **Spec coverage:** Applying rewrites (Task 2), pure logic (Task 1), plumbing to server/ssh-child/main (Task 3), and `local.json` config reading in the CLI (Task 4) cover every spec requirement. Trace-fidelity (`request_body` original + `request_body_forwarded`) is in Task 2. Stdlib-only boundary (rules cross as plain list / JSON string; `read_json_file` lives in `_cli.py`) is honored in Tasks 3-4.
- **Placeholder scan:** Every step has concrete code or an exact command; no TBD/`implement later` placeholders.
- **Type consistency:** `Sequence[Mapping[str, Any]]` is the consistent type for `rewrite_rules` across `apply_rewrites`, `ProxyServer`, `run_proxy`, `run_with_ssh`, and `main`; `apply_rewrites -> tuple[bytes, bool]`; `_rule_matches -> bool`. The `--rewrite-rules` CLI arg is `json.dumps`-encoded on the way out (Task 3) and `json.loads`-decoded in `main` (Task 3); `_cli.py` produces the plain `list[dict]` (Task 4), which Task 3's `json.dumps` accepts.
