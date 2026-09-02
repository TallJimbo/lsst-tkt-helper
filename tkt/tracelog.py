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

"""Capture parsing, session detection, and title extraction.

Analyzes the flat `capture.jsonl` of proxy exchanges (one JSON object per line)
into discrete conversation sessions, either by an explicit `x-session-id`
header (OpenCode) or by content-based connected-component tracking (Zed, which
sends no session header and may interleave a primary conversation with
empty-context `spawn_agent` subagents).
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path

# Look-back window (in records) when searching for the earlier record a request
# continues, and the minimum `_continue_score` for a link to be accepted. Both
# are baselines to be tuned against the golden fixtures.
_PREFIX_WINDOW = 20
_CONTINUE_THRESHOLD = 0.5


def iter_records(path: str) -> Iterator[dict]:
    """Yield each JSON object from a `capture.jsonl` in order."""
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
    """Return the session id header value, or None when absent.

    Checks `x-session-id` then `x-parent-session-id`, case-insensitively.
    """
    return _header(record, "x-session-id") or _header(record, "x-parent-session-id")


def is_title_request(record: dict) -> bool:
    """Report whether a user message asks for a title generation.

    Only the last user turn counts, because the title-gen prompt is always the
    newest user message; a mid-history user turn mentioning ``title`` must not
    flag a record. The shared system boilerplate (which may mention ``title``)
    is skipped anyway since it is not a user turn.
    """
    try:
        msgs = json.loads(record.get("request_body", "{}")).get("messages", [])
    except (ValueError, AttributeError):
        return False
    for m in reversed(msgs):
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str) and ("title" in content.lower() or "Generate a title" in content):
            return True
        return False
    return False


def extract_title(record: dict) -> str | None:
    """Concatenate SSE delta.content chunks from a title request response."""
    parts: list[str] = []
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


def _fingerprint(msg: dict) -> tuple[str, str]:
    """Map a message to a comparable `(role, content_normalized)` pair."""
    role = str(msg.get("role", ""))
    content = msg.get("content")
    if isinstance(content, str):
        return (role, content[:80])
    # Content-list form (e.g. Zed's ``[{type: text, text: ...}]``) and other
    # non-str payloads are collapsed to a marker so the turn boundary survives.
    return (role, "<non-str>")


def _record_turns(record: dict) -> list[tuple[str, str]]:
    """Fingerprint request messages, skipping leading system boilerplate."""
    try:
        msgs = json.loads(record.get("request_body", "{}")).get("messages", [])
    except (ValueError, AttributeError):
        return []
    turns: list[tuple[str, str]] = []
    for i, m in enumerate(msgs):
        if i == 0 and m.get("role") == "system":
            continue
        turns.append(_fingerprint(m))
    return turns


def _first_user_text(record: dict) -> str | None:
    """Return the first user message's text content, or None."""
    try:
        msgs = json.loads(record.get("request_body", "{}")).get("messages", [])
    except (ValueError, AttributeError):
        return None
    for m in msgs:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None


def _fallback_label(records: list[dict], entries: list[int]) -> str:
    """Label a session that has no usable title: its opening user message.

    Entries are visited in time order (already sorted by the caller); the first
    user message that yields text is used, with whitespace collapsed and
    truncated, so the label stays a single readable line. Falls back to
    ``(untitled)`` when no such message exists.
    """
    for i in entries:
        text = _first_user_text(records[i])
        if text:
            return " ".join(text.split())[:80]
    return "(untitled)"


def _continue_score(a: list[tuple[str, str]], b: list[tuple[str, str]]) -> float:
    """Score how well b's history re-presents a's (``a`` earlier, ``b`` later).

    Returns the fraction of the *shorter* history that b carries over: b's
    leading turns must match a contiguous *tail* of a's turns, so both a
    monotonic growth (``a`` a strict prefix of ``b``, score 1.0) and a
    within-session head-trim that drops early turns while keeping a's tail
    (``b`` picks up somewhere in the middle of ``a`` and continues) still score
    high. Two conversations sharing nothing but the (excluded) system prompt
    score 0.0.
    """
    if not a or not b:
        return 0.0
    shared = min(len(a), len(b))
    matched = 0
    for k in range(1, shared + 1):
        if a[len(a) - k :] == b[:k]:
            matched = k
    if matched == 0:
        return 0.0
    return matched / shared


def _build_components(records: list[dict]) -> list[list[int]]:
    """Group capture indices into connected conversation-lineage components.

    Each record is linked to the best earlier record (within the look-back
    window) that it continues; connected components are the sessions. A record
    that continues nothing opens a new component. Title-gen requests never
    split the owning component: if one matches nothing it is attached to the
    most recent component instead.
    """
    turns = [_record_turns(r) for r in records]
    parent = list(range(len(records)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    for i in range(len(records)):
        best_j, best_score = -1, 0.0
        for j in range(max(0, i - _PREFIX_WINDOW), i):
            score = _continue_score(turns[j], turns[i])
            if score > best_score:
                best_j, best_score = j, score
        if best_score >= _CONTINUE_THRESHOLD and best_j != -1:
            union(best_j, i)
        elif is_title_request(records[i]) and i > 0:
            # A title-gen with no continued history still belongs to the
            # conversation it was generated for; fold it into the prior record
            # so it never opens (or splits into) its own session.
            union(i - 1, i)

    comps: dict[int, list[int]] = {}
    for i in range(len(records)):
        comps.setdefault(find(i), []).append(i)
    return list(comps.values())


def _session(
    records: list[dict],
    entries: list[int],
    *,
    sid: str | None,
) -> dict:
    """Build one session record from capture indices."""
    entries = sorted(entries, key=lambda i: records[i].get("time", 0))
    label: str | None = sid
    if label is None:
        for i in entries:
            if is_title_request(records[i]):
                title = extract_title(records[i])
                if title:
                    label = title
                    break
    if label is None:
        label = _fallback_label(records, entries)
    start = records[entries[0]].get("time")
    end = records[entries[-1]].get("time")
    client_ua = _header(records[entries[0]], "user-agent")
    # Content (Zed) sessions carry no header id; synthesize a stable, unique
    # one so pin/show/unpin and session filenames work. It is deterministic
    # (survives re-segmentation) because it derives only from the session start
    # time and its earliest capture index. Header (x-session-id) sessions keep
    # their real id.
    resolved_id = sid if sid is not None else f"zed-{int(start or 0)}-{min(entries)}"
    return {
        "id": resolved_id,
        "entries": entries,
        "label": label,
        "start": start,
        "end": end,
        "client_ua": client_ua,
    }


def segment(captures: list[dict]) -> list[dict]:
    """Split a list of capture records into session records.

    Records carrying an `x-session-id`/`x-parent-session-id` header are grouped
    consecutively by id; all others go through the content-based
    connected-component detection. Each returned session has ``id``,
    ``entries`` (indices into ``captures``, possibly non-contiguous),
    ``label``, ``start``, ``end``, and ``client_ua``.
    """
    sessions: list[dict] = []
    content_idxs: list[int] = []
    i = 0
    while i < len(captures):
        sid = session_id(captures[i])
        if sid is not None:
            j = i
            group: list[int] = []
            while j < len(captures) and session_id(captures[j]) == sid:
                group.append(j)
                j += 1
            sessions.append(_session(captures, group, sid=sid))
            i = j
        else:
            while i < len(captures) and session_id(captures[i]) is None:
                content_idxs.append(i)
                i += 1

    if content_idxs:
        content_records = [captures[k] for k in content_idxs]
        for comp in _build_components(content_records):
            indices = [content_idxs[pos] for pos in comp]
            sessions.append(_session(captures, indices, sid=None))

    sessions.sort(key=lambda s: min(s["entries"]))
    return sessions


def _session_file(root: Path, session: dict) -> Path:
    start = session["start"]
    import datetime

    ts = datetime.datetime.fromtimestamp(start).strftime("%Y%m%dT%H%M%S")
    return root / "sessions" / f"{ts}_{session['id']}.jsonl.gz"


def _meta_file(session_file: Path) -> Path:
    return session_file.with_suffix("").with_suffix(".meta.json")  # .jsonl.gz -> .jsonl -> .meta.json


def write_session_files(root: Path, sessions: list[dict], captures: list[dict]) -> list[Path]:
    """Write each session's exchanges and paired meta file under ``sessions/``.

    Each session's ``entries`` index into ``captures``; the gzipped exchange
    JSONL and the sibling ``.meta.json`` (``label``, ``id``, ``start``,
    ``end``, ``client_ua``, ``pinned``) are written together. Returns the
    session files.

    Re-running ``segment`` against the same cumulative capture must not
    reset an already-pinned session: an existing meta with ``pinned: True``
    is preserved, while new sessions (and un-pinned ones) default to
    ``pinned: False``.
    """
    out: list[Path] = []
    for session in sessions:
        session_file = _session_file(root, session)
        session_file.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(session_file, "wt", encoding="utf-8") as fh:
            for idx in session["entries"]:
                fh.write(json.dumps(captures[idx]) + "\n")
        pinned = False
        meta_path = _meta_file(session_file)
        if meta_path.exists():
            try:
                pinned = bool(json.loads(meta_path.read_text(encoding="utf-8")).get("pinned", False))
            except ValueError:
                pass
        meta_path.write_text(
            json.dumps(
                {
                    "label": session.get("label"),
                    "id": session.get("id"),
                    "start": session.get("start"),
                    "end": session.get("end"),
                    "client_ua": session.get("client_ua"),
                    "pinned": pinned,
                }
            ),
            encoding="utf-8",
        )
        out.append(session_file)
    return out


def list_sessions(root: Path) -> list[dict]:
    """Load the paired ``.meta.json`` for each session file."""
    sessions: list[dict] = []
    sessions_dir = root / "sessions"
    if not sessions_dir.exists():
        return sessions
    for session_file in sorted(sessions_dir.glob("*.jsonl.gz")):
        meta = json.loads(_meta_file(session_file).read_text(encoding="utf-8"))
        meta["_session_file"] = session_file
        sessions.append(meta)
    return sessions


def pin_session(root: Path, session_id: str, pinned: bool = True) -> None:
    """Mark a session pinned (or unpinned) and rewrite its meta file."""
    for session in list_sessions(root):
        if session.get("id") == session_id:
            session["pinned"] = pinned
            meta = {k: v for k, v in session.items() if not k.startswith("_")}
            _meta_file(session["_session_file"]).write_text(json.dumps(meta), encoding="utf-8")
            return
    raise KeyError(session_id)


def _read_exchanges(session_file: Path) -> list[dict]:
    with gzip.open(session_file, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def show_session(root: Path, session_id: str, raw: bool = False) -> None:
    """Print a session's exchanges, or its raw JSON when ``raw`` is true."""
    for session in list_sessions(root):
        if session.get("id") == session_id:
            exchanges = _read_exchanges(session["_session_file"])
            if raw:
                print(json.dumps(exchanges))
            else:
                for exchange in exchanges:
                    print(json.dumps(exchange, indent=2))
            return
    raise KeyError(session_id)


def prune(root: Path, horizon_days: int = 30, keep: int = 20) -> list[Path]:
    """Delete unpinned sessions older than ``horizon_days`` or beyond ``keep``.

    The ``.jsonl.gz`` and its paired ``.meta.json`` are removed together.
    ``keep`` caps the number of sessions retained (by ``start``, newest first);
    ``horizon_days`` removes anything older than that window regardless.
    Returns the deleted session-file paths.
    """
    import datetime

    sessions = list_sessions(root)
    cutoff = datetime.datetime.now().timestamp() - horizon_days * 86400
    kept: set[int] = set()
    for session in sorted(
        (s for s in sessions if s.get("start") is not None),
        key=lambda s: s["start"],
        reverse=True,
    )[:keep]:
        kept.add(id(session))

    deleted: list[Path] = []
    for session in sessions:
        if session.get("pinned"):
            continue
        start = session.get("start")
        too_old = start is None or start < cutoff
        beyond_keep = id(session) not in kept
        if not (too_old or beyond_keep):
            continue
        session_file = session["_session_file"]
        for path in (session_file, _meta_file(session_file)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        deleted.append(session_file)
    return deleted
