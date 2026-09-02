# Proxy Request-Rewrite (Zed/OpenCode Sampling Reconciliation) — Design Handover

**Date:** 2026-09-02
**Status:** Approved by human in conversation (before implementation).
**Builds on:** E3 tracing proxy (`2026-09-02-tracing-proxy-design.md`, implemented as `tkt/proxy.py`).

## Goal

Add **configurable request-body rewriting** to the existing `tkt trace-proxy`
capture proxy, so that requests from the Zed native agent can be rewritten to
match the sampling configuration OpenCode uses for the same model. This lets the
user reconcile coherence differences between Zed and OpenCode that the earlier
`investigations/bad-thinking` trace showed:

- **Zed sends:** `"temperature": 1.0`, no `top_p`, `max_completion_tokens`, no
  `reasoning_effort`.
- **OpenCode sends:** `"top_p": 0.95`, no `temperature`, `reasoning_effort`.

OpenCode only applies `top_p: 0.95` to a specific model set (including
`deepseek-ai/DeepSeek-V4-Flash-0731`), so the rewrite must be **model-gated** and
**configurable**, not a blanket rule.

## Decisions (signed off in conversation)

- **Scope of reconciliation:** Sampling only — drop `temperature`, set
  `top_p: 0.95`. Do **not** touch `reasoning_effort` or token limits.
- **Config location:** a new top-level `"proxy": { "rewrite": [...] }` key in
  `local.json`. The proxy only needs this key, so `_cli.py` reads it directly
  with `read_json_file` (json5-tolerant) rather than loading a full `Environment`.
- **Config shape:** a list of rewrite rules:
  ```json
  "proxy": {
    "rewrite": [
      {
        "client": "zed",                                // optional: substring match on User-Agent
        "model": "deepseek-ai/DeepSeek-V4-Flash-0731",  // optional: exact model match
        "params": { "temperature": null, "top_p": 0.95 }
      }
    ]
  }
  ```
  Rule semantics: `null` value → remove the key; number/bool → set it. A rule
  matches when `client` (if given) is a case-insensitive substring of the
  request `User-Agent` and `model` (if given) equals the request `model`.
  Multiple rules apply in order. Omit both matchers to apply to all
  chat-completions requests. Only keys listed in `params` are touched.
- **Scope of rewrite application:** only `POST` to a path ending in
  `/chat/completions` with a JSON body that is an object containing `model`.
  Other methods, paths, and bodies pass through untouched.
- **Trace fidelity (capture log):** keep the **original** client body as
  `request_body` and, when a rewrite changed the body, add
  `request_body_forwarded` holding the rewritten body actually sent upstream.
  An unchanged request records no `request_body_forwarded`.
- **Client targeting:** handled by the `client` matcher in each rule (e.g.
  `"client": "zed"`). There is **no** hardcoded Zed detection; if the user wants
  blanket rewriting they omit `client`.
- **SSL / framing:** irrelevant here — the proxy relays via urllib and already
  excludes `content-length` from forwarding (`_SKIP_FORWARD`), so a rewritten
  body of different length is re-framed safely by `urllib.request`.

## Architecture

- `tkt/proxy.py` (stdlib-only — JSON via stdlib `json`, no new deps):
  - `apply_rewrites(body, *, user_agent, rewrite_rules) -> (bytes, bool)` —
    pure function: returns the (possibly rewritten) body and a `changed` flag.
  - `_rule_matches(rule, *, user_agent, model) -> bool` — matcher, package-private.
  - `ProxyServer` gains an optional `rewrite_rules` attribute (default `()`).
  - `ProxyHandler._relay` applies rewrites for chat-completions POSTs and records
    `request_body_forwarded` when changed.
  - `run_proxy(..., *, rewrite_rules=())` and `run_with_ssh(..., rewrite_rules=())`
    thread rules through; the ssh child relay gets them via a `--rewrite-rules`
    (JSON string) CLI arg on `python -m tkt.proxy`.
  - `main()` gains `--rewrite-rules` (JSON string, optional).
- `tkt/_cli.py`:
  - `trace_proxy` gains `--environment` (envvar `TKT_ENVIRONMENT`, `click.File`).
  - Reads the `proxy.rewrite` rules via `read_json_file` and passes the rule
    list down to `run_proxy`/`run_with_ssh`.
- Tests in `tests/test_proxy.py` (existing file, extended).

### Stdlib boundary (important)

`tkt/proxy.py` must remain **stdlib-only** (global constraint inherited from the
tracing-proxy design). `local.json` may contain JSON5-isms (trailing commas), so
it is read with `read_json_file` in `_cli.py`, and only the **plain rule list**
(JSON-serializable `list[dict]`) crosses the boundary — passed as a Python list
in-process, or as a `json.dumps` string for the ssh child process.

## Error handling

- Malformed or non-JSON chat-completions bodies are left untouched (logged as-is),
  mirroring the proxy's existing tolerance.
- A missing/empty `proxy.rewrite` config means no rewriting (current behavior).
- Malformed rule entries are skipped per-rule; a bad `params` value type is
  coerced to its JSON representation (None removes, others set).

## Global constraints

- Python 3.13; dependencies only `click`, `GitPython`, `pyyaml`, `json5` — **no
  new third-party dependencies** (stdlib only for `tkt/proxy.py`).
- Every `.py` file carries the BSD-3-Clause header exactly as in existing modules
  (only touched files are `tkt/proxy.py`, `tkt/_cli.py`, `tests/test_proxy.py`;
  no new `.py` files).
- Must pass before each commit and at the end: `ruff check .`,
  `ruff format --check .`, `mypy tkt/`.
- `tkt` is not pip-distributed; no packaging config.
- Must not change existing proxy behavior when no rewrite rules are configured
  (all existing `tests/test_proxy.py` pass unchanged).
- Do not touch `investigations/` originals.
