# E3 — Long-Running Model-Degradation Tracing Proxy — Design Handover

**Date:** 2026-09-02
**Status:** Approved by human in conversation (before implementation).
**Implements:** E3 in `docs/zed-agent-roadmap.md`.

## Goal

Turn the throwaway debugging proxy (`investigations/bad-thinking/zed-agent-request-proxy/proxy.py`)
into a productized, continuously-running tracing tool that gathers a large dataset of
Zed-native-agent model traffic for a future degradation investigation (incorrect/non-use of
thinking tags, repeated tool-use mistakes). Two user-facing capabilities:

1. `tkt trace-proxy` — run the capture proxy long-lived (foreground, optionally co-invoking the
   ssh tunnel), appending every model exchange to a single continuous capture file.
2. `tkt trace-log` — retroactively segment the raw capture into _sessions_, label them with the
   auto-generated conversation titles, list/show them, and let the user pin/retain the ones that
   "went off the rails" while pruning the rest — so normal daily use accumulates a bounded,
   session-linked, reviewable corpus.

## Architecture

Two new tkt modules plus CLI wiring, all **stdlib-only** (`http.server`, `urllib`, `json`, `gzip`,
`subprocess`), mirroring how `tkt/mcp_server.py` is wired as a command in `tkt/_cli.py`:

- `tkt/proxy.py` — the capture proxy server: exact relay of the current prototype's forwarding
  semantics, but structured as a reusable class, plus the captured-exchange record model and the
  foreground/lifecycle machinery shared by the command.
- `tkt/tracelog.py` — the offline analysis: reads the capture JSONL, groups it into sessions,
  extracts labels, writes per-session gzipped files + metadata, and implements list/show/pin/prune.

The old `investigations/bad-thinking/zed-agent-request-proxy/` stays in place as the historical
prototype; we do not move or delete it.

### The two-command split

The proxy is a **capture-only** tool: it appends one JSON object per exchange to a continuous
`capture.jsonl` and does **no** session segmentation or rotation live. Segmentation is
**retroactive**, done offline by `tkt trace-log`. Rationale (from conversation): the correct Zed
session boundary is a _conversation-reset_ signal, which is easiest and most reliably detected by
scanning the accumulated history after the fact rather than deciding in real time; and idle-time
gaps are a poor boundary heuristic because a paused-and-resumed session must remain one session.
This also means nothing is ever dropped during capture (a big capture is fine), and session
"revealing" only happens when the user runs `trace-log` on the accumulated capture.

## Decisions

1. **Two focused modules** (`tkt/proxy.py`, `tkt/tracelog.py`) rather than one large file, matching
   the repo's one-responsibility-per-file pattern (`pull.py`, `sandbox.py`).
2. **Session detection:**
   - **OpenCode**: group by the `x-session-id` header when present (authoritative and trivially
     correct across its many per-turn title-gen requests). Fall back to the content-based detector
     if absent.
   - **Zed** (no session header): retroactive content-based detector — a request starts a new
     session when its message history does _not_ continue the running conversation ("replays a
     fresh beginning"). Title-gen requests are **part of** their session (never a boundary), and
     their extracted title labels the session.
3. **Labeling:** detect the title-gen request by its first-user message (prompt contains
   `title` / `Generate a title` — verified in real logs: Zed emits
   `Generate a concise 3-7 word title...`, OpenCode `Generate a title for this conversation: ...`);
   extract the concatenated SSE `delta.content` chunks from that request's `response_body`;
   use the result as the session label; fall back to session-id/timestamp if absent. (Verified on
   real captures: yields e.g. `Allow additional port in sandbox-run`,
   `Write/edit permission conflict in sp-plan agent`.)
4. **Data layout:**
   - Default root `~/.tkt/traces/`, overridable via `TKT_TRACES_DIR` env or `--traces-dir`.
   - Continuously appended capture: `<root>/capture.jsonl`.
   - Segmented outputs under `<root>/sessions/`: `<date>_<n>.jsonl.gz` (gzipped JSONL of that
     session's exchanges) and `<date>_<n>.meta.json` (label, session id, start time, exchange
     count, duration, client UA, pinned flag).
5. **Pin/prune:** pinned sessions are exempt from pruning. Unpinned sessions are pruned when older
   than a retention horizon and/or beyond a `--keep` most-recent count. Pruning only ever applies
   to segmented session files, never the raw capture.
6. **CLI wiring:** `tkt trace-proxy` and `tkt trace-log` (with `segment|list|show|pin|unpin`
   subcommands) in `tkt/_cli.py`, following the `mcp-server` command pattern.
7. **ssh co-invocation (interactive):** `tkt trace-proxy --ssh-host HOST --upstream URL` starts the
   proxy as a **background child**, then runs `ssh HOST` **in the foreground** as a normal
   interactive session (whose config-established tunnel is what `--upstream` points at). When the
   ssh session exits, tkt tears down the proxy child and exits. Without `--ssh-host`, the proxy
   runs in the foreground standalone (the current prototype's behavior); the user brings up the
   tunnel themselves.
8. **Subagents are their own sessions.** Detecting a `spawn_agent` subagent (which starts with
   empty context and may run in parallel with the primary) requires multi-conversation tracking;
   the detector splits subagent conversations into their own sessions rather than merging them
   into the parent (best for isolating a misbehaving subagent). Labeling may fall back to
   id/timebase for subagent sessions lacking a title-gen.

## Data / data flow

### Capture record (one JSON object per exchange, appended to `capture.jsonl`)

```json
{
  "time": 1788111567.0,
  "method": "POST",
  "path": "/api/v1/chat/completions",
  "upstream_url": "http://localhost:8080/api/v1/chat/completions",
  "request_headers": {
    "content-type": "application/json",
    "authorization": "<redacted>",
    "...": "..."
  },
  "request_body": "{...}", // decoded utf-8, lossy
  "status": 200,
  "response_headers": { "...": "..." },
  "response_body": "data: {...}\ndata: [DONE]\n"
}
```

- `request_headers`/`response_headers` are dicts of the raw header fields.
- `authorization` (case-insensitive) is always masked to `<redacted>`.
- `request_body`/`response_body` are the full decoded bodies (the response is the entire buffered
  SSE stream), matching the prototype.

### Proxy relay semantics (carried over from the prototype)

- Forwards `GET`/`POST`/`PUT`/`PATCH`/`DELETE` for any incoming path verbatim to
  `--upstream + path` (`--upstream` is `scheme://host:port` with no path).
- Skips re-framing headers: `host`, `connection`, `content-length`, `keep-alive`,
  `transfer-encoding`, `expect`, `upgrade`.
- Relays response status/headers; on `HTTPError` treats it as a normal relayed response; on any
  other upstream error logs the record with an `error` field and sends `502`.
- Buffers the full body before returning so each exchange is written **atomically** (single JSON
  object) even for SSE. `Connection: close` to the client.
- Write-ahead: the record is written to the log file before the response stream is flushed to the
  client is NOT required for correctness here, but the record is always written once, in order.

### Session detection algorithm (Zed, content-based)

Given the ordered exchanges from `capture.jsonl`:

```
session_id(exchange) =
    if x-session-id header present:  return that header
    else: content-based grouping
```

For content-based grouping, process exchanges and assign each to a conversation lineage using **multi-conversation / connected-component tracking** (robust to `spawn_agent` subagents, which start with empty context and may run in parallel with the primary). A message fingerprint is `(role, content_normalized)` where `content_normalized` is the message text's first ~80 chars (or a marker for content-list form).

**Edge rule — “B continues A”:** request B continues request A when B's message history re-presents A's running history (B's messages have A's history as a prefix). The overlap is computed **over the user/assistant turns, excluding the (shared, long) system prompt boilerplate**, so two fresh conversations that share only the system prompt do **not** merge.

**Graph/component step:** build a directed edge from request A to a later request B when B continues A, linking each request to the best earlier request (within a look-back window — not just the immediately-previous one, since parallel streams interleave). Take **connected components as sessions**. This is order-robust: each request is matched to its own lineage, so parallel subagent streams separate cleanly; resumed-after-pause stays one session because it continues the same history.

**Subagents:** a subagent's empty-context first request extends no prior history (its only overlap is the shared system boilerplate, which is excluded), so it opens **its own session/component** — subagents are deliberately split out rather than merged into the parent, per design (best for isolating a misbehaving subagent). A subagent session that has no title-gen request gets the fallback label (id/timebase).

**Title-gen requests** are classified as _continuations_ (they carry the session label but never trigger a split), and their extracted title labels the owning session.

**This heuristic is the crux and must be validated against real data before it is trusted.**
The plan pins a golden expectation: `investigations/bad-thinking/agent-capture-1a.log` (the Zed
capture) must segment into **exactly 1 session** labeled `Allow additional port in sandbox-run`,
and `agent-capture-2.log` (OpenCode) into **exactly 1 session** (grouped by `x-session-id`). The
implementation is developed TDD against these fixtures plus synthetic unit cases (same-session
growth, within-session shrink, title-gen in middle, fresh-conversation boundary, resumed-after-pause,
**and a parallel-subagent interleaving case: primary + two fresh subagents interleaved must yield 3
distinct sessions, not fragments or merges**).

### Label extraction

For a title-gen exchange, walk the SSE `response_body` (`data: `-prefixed lines, skipping
`[DONE]`), parse each JSON chunk, and concatenate every `choices[].delta.content` string; strip
whitespace. Use that string as the session label.

## Testing strategy

- Unit (TDD): detection (synthetic short JSONL sequences), label extraction (a constructed SSE
  body), masking, record writing/rotation-free appends, gzip session-file writing, meta
  round-trip, list/show formatting, pin/unpin, prune selection (horizon + keep, pinned exempt).
- Fixture/regression: the real capture logs under `investigations/bad-thinking/` are _copied_ into
  `tests/fixtures/` (small subset; do not edit originals) and used as golden inputs for the
  detector/label assertions above.
- The proxy's HTTP relay is tested with `http.server`-based lightweight fake upstream +
  `urllib`-driven requests, or the framing-level pure-Python tests used by `test_mcp_server.py`.

## Global constraints

- Python 3.13; dependencies only `click`, `GitPython`, `pyyaml`, `json5` — **no new third-party
  dependencies** (stdlib only for the proxy/analysis).
- Every `.py` file carries the BSD-3-Clause header exactly as in existing modules.
- Must pass before each commit and at the end: `ruff check .`, `ruff format --check .`,
  `mypy tkt/`.
- `tkt` is not pip-distributed; no packaging config.
- OpenCode workflow must keep working throughout (coexistence); these are additive commands and
  must not affect existing behavior.
- Do not touch `investigations/` originals (git-ignored scratch space); regression fixtures are
  copies under `tests/fixtures/`.
- The old prototype under `investigations/bad-thinking/zed-agent-request-proxy/` is left in place.
