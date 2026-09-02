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

"""Unit tests for the tracing-proxy capture analysis helpers."""

import gzip
import json
from pathlib import Path

from tkt.tracelog import (
    extract_title,
    is_title_request,
    list_sessions,
    pin_session,
    prune,
    segment,
    session_id,
    show_session,
    write_session_files,
)


def _rec(**over) -> dict:
    base = {
        "time": 1.0,
        "method": "POST",
        "path": "/api/v1/chat/completions",
        "upstream_url": "http://localhost:8080",
        "request_headers": {},
        "request_body": "{}",
        "status": 200,
        "response_headers": {},
        "response_body": "",
    }
    base.update(over)
    return base


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def _body(msgs: list[dict]) -> str:
    return json.dumps({"messages": msgs})


def _sse(*contents: str) -> str:
    lines = [f'data: {{"choices":[{{"delta":{{"content":{json.dumps(c)}}}}}]}}\n' for c in contents]
    return "".join(lines) + "data: [DONE]\n"


def test_session_id_reads_header_case_insensitive() -> None:
    """Read the session id header regardless of case."""
    r = _rec(request_headers={"X-Session-Id": "ses_abc"})
    assert session_id(r) == "ses_abc"


def test_session_id_none_when_absent() -> None:
    """Return None when no session id header is present."""
    assert session_id(_rec()) is None


def test_is_title_request_matches_generate_title() -> None:
    """Detect a title-generation prompt in the request messages."""
    body = '{"messages":[{"role":"user","content":"Generate a title for this conversation: foo"}]}'
    assert is_title_request(_rec(request_body=body)) is True


def test_is_title_request_ignores_system_message() -> None:
    """The system message mentioning 'title' must not flag a record."""
    body = _body([_msg("system", "Generate a title for this conversation"), _msg("user", "Hi")])
    assert is_title_request(_rec(request_body=body)) is False


def test_extract_title_concatenates_delta_content() -> None:
    """Concatenate the SSE delta.content chunks into the title."""
    sse = (
        'data: {"choices":[{"delta":{"content":"Allow "}}]}\n'
        'data: {"choices":[{"delta":{"content":"additional"}}]}\n'
        "data: [DONE]\n"
    )
    assert extract_title(_rec(response_body=sse)) == "Allow additional"


def test_segment_groups_monotonic_growth_as_one_session() -> None:
    """A single growing conversation segments into one session."""
    m1 = [_msg("system", "SYS"), _msg("user", "What is the capital?")]
    m2 = m1 + [_msg("assistant", "Paris."), _msg("user", "And its river?")]
    m3 = m2 + [_msg("assistant", "The Seine.")]
    recs = [
        _rec(request_body=_body(m1), time=0.0),
        _rec(request_body=_body(m2), time=1.0),
        _rec(request_body=_body(m3), time=2.0),
    ]
    sessions = segment(recs)
    assert len(sessions) == 1
    assert sessions[0]["entries"] == [0, 1, 2]


def test_segment_within_session_head_trim_stays_one_session() -> None:
    """A head-trim resume that keeps the tail stays one session."""
    m0 = [_msg("system", "SYS"), _msg("user", "u1")]
    m1 = m0 + [_msg("assistant", "a1"), _msg("user", "u2")]
    m2 = m1 + [_msg("assistant", "a2")]
    # Keeps the tail (u2, a2), drops early turns, and adds u3.
    m3 = [_msg("user", "u2"), _msg("assistant", "a2"), _msg("user", "u3")]
    recs = [
        _rec(request_body=_body(m0), time=0.0),
        _rec(request_body=_body(m1), time=1.0),
        _rec(request_body=_body(m2), time=2.0),
        _rec(request_body=_body(m3), time=3.0),
    ]
    sessions = segment(recs)
    assert len(sessions) == 1
    assert sessions[0]["entries"] == [0, 1, 2, 3]


def test_segment_two_fresh_conversations_are_separate_sessions() -> None:
    """Two unrelated conversations do not merge into one session."""
    a1 = [_msg("system", "SYS"), _msg("user", "Melting point of iron?")]
    a2 = a1 + [_msg("assistant", "1538 C.")]
    b1 = [_msg("system", "SYS"), _msg("user", "Velocity of light?")]
    b2 = b1 + [_msg("assistant", "299792458 m/s.")]
    recs = [_rec(request_body=_body(m), time=float(i)) for i, m in enumerate([a1, a2, b1, b2])]
    sessions = segment(recs)
    assert len(sessions) == 2
    assert sessions[0]["entries"] == [0, 1]
    assert sessions[1]["entries"] == [2, 3]


def test_segment_groups_opencode_by_session_id() -> None:
    """Group records carrying an ``x-session-id`` header into sessions."""
    body = _body([_msg("system", "SYS"), _msg("user", "hi")])

    def oc(rid: str) -> dict:
        return _rec(request_headers={"x-session-id": rid}, request_body=body)

    captures = [oc("ses_a"), oc("ses_a"), oc("ses_b"), oc("ses_b")]
    sessions = segment(captures)
    assert len(sessions) == 2
    assert set(sessions[0]["entries"]) == {0, 1}
    assert set(sessions[1]["entries"]) == {2, 3}


def test_segment_interleaved_subagents_are_distinct_sessions() -> None:
    """Primary plus parallel fresh subagents stay separate sessions."""
    prim = [_msg("system", "SYS"), _msg("user", "Primary task: refactor module")]
    sub1 = [_msg("system", "SYS"), _msg("user", "Subagent 1: run the test suite")]
    sub2 = [_msg("system", "SYS"), _msg("user", "Subagent 2: lint the code base")]
    recs = [
        _rec(request_body=_body(prim), time=0.0),
        _rec(request_body=_body(sub1), time=1.0),
        _rec(request_body=_body(prim + [_msg("assistant", "sure")]), time=2.0),
        _rec(request_body=_body(sub2), time=3.0),
        _rec(request_body=_body(sub1 + [_msg("assistant", "done")]), time=4.0),
        _rec(
            request_body=_body(prim + [_msg("assistant", "sure"), _msg("assistant", "ok")]),
            time=5.0,
        ),
    ]
    sessions = segment(recs)
    assert len(sessions) == 3
    assert set(sessions[0]["entries"]) == {0, 2, 5}
    assert set(sessions[1]["entries"]) == {1, 4}
    assert set(sessions[2]["entries"]) == {3}


def test_is_title_request_only_last_user_turn_counts() -> None:
    """Only the last user turn is checked for title-generation prompts."""
    # A mid-history user turn mentioning 'title' but a clean final turn is not
    # a title request.
    notitle = _body(
        [
            _msg("system", "SYS"),
            _msg("user", "fix the page title"),
            _msg("assistant", "ok"),
            _msg("user", "now run the tests"),
        ]
    )
    assert is_title_request(_rec(request_body=notitle)) is False
    # A title-gen prompt as the final user turn is flagged.
    istitle = _body(
        [
            _msg("system", "SYS"),
            _msg("user", "fix it"),
            _msg("user", "Generate a title for this conversation: x"),
        ]
    )
    assert is_title_request(_rec(request_body=istitle)) is True


def test_segment_zed_content_sessions_get_synthetic_ids() -> None:
    """Content (Zed) sessions get a non-None synthetic id."""
    m1 = [_msg("system", "SYS"), _msg("user", "What is the capital?")]
    m2 = m1 + [_msg("assistant", "Paris."), _msg("user", "And its river?")]
    recs = [
        _rec(request_body=_body(m1), time=100.25),
        _rec(request_body=_body(m2), time=100.5),
    ]
    sessions = segment(recs)
    assert len(sessions) == 1
    assert sessions[0]["id"] is not None
    assert sessions[0]["id"] == f"zed-{int(100.25)}-0"


def test_segment_content_session_is_pinnable_and_showable(tmp_path) -> None:
    """pin_session/show_session work via a content session's synthetic id."""
    m1 = [_msg("system", "SYS"), _msg("user", "Query the database")]
    m2 = m1 + [_msg("assistant", "Done")]
    recs = [
        _rec(request_body=_body(m1), time=200.0),
        _rec(request_body=_body(m2), time=200.1),
    ]
    sessions = segment(recs)
    assert len(sessions) == 1
    sid = sessions[0]["id"]
    assert sid is not None
    write_session_files(tmp_path, sessions, recs)
    # pin should succeed (not raise KeyError) and mark the meta pinned.
    pin_session(tmp_path, sid, pinned=True)
    assert list_sessions(tmp_path)[0]["pinned"] is True
    # show should print the exchanges without raising.
    show_session(tmp_path, sid)


def test_write_session_files_two_content_sessions_no_collision(tmp_path) -> None:
    """Two content sessions produce distinct, non-colliding file paths."""
    s1 = [_msg("system", "SYS"), _msg("user", "Task one")]
    s2 = [_msg("system", "SYS"), _msg("user", "Task two")]
    recs = [
        _rec(request_body=_body(s1), time=300.0),
        _rec(request_body=_body(s2), time=301.0),
    ]
    sessions = segment(recs)
    assert len(sessions) == 2
    paths = write_session_files(tmp_path, sessions, recs)
    assert len(paths) == len(set(paths))  # distinct, no filename collision


def test_segment_title_request_labels_and_does_not_split() -> None:
    """A title-gen request labels its session without splitting it."""
    m1 = [_msg("system", "SYS"), _msg("user", "Fix the parser bug")]
    m2 = m1 + [_msg("assistant", "Looking")]
    m3 = [
        _msg("user", "Fix the parser bug"),
        _msg("assistant", "Looking"),
        _msg("user", "Generate a title for this conversation: x"),
    ]
    recs = [
        _rec(request_body=_body(m1), time=0.0),
        _rec(request_body=_body(m2), time=1.0),
        _rec(request_body=_body(m3), time=2.0, response_body=_sse("Parser bug fix")),
    ]
    sessions = segment(recs)
    assert len(sessions) == 1
    assert sessions[0]["label"] == "Parser bug fix"
    assert sessions[0]["entries"] == [0, 1, 2]


def test_write_session_files_and_list_roundtrip(tmp_path) -> None:
    """Write session files and read them back via list_sessions."""
    captures = [{"time": 1.0, "path": "/api/v1/chat/completions"}]
    sessions = [
        {"id": "s1", "entries": [0], "label": "My title", "start": 1.0, "end": 1.0, "client_ua": "Zed"}
    ]
    paths = write_session_files(tmp_path, sessions, captures)
    assert len(paths) == 1
    gz = paths[0]
    with gzip.open(gz, "rt", encoding="utf-8") as fh:
        assert json.loads(fh.read())["path"] == "/api/v1/chat/completions"
    meta = gz.with_suffix("").with_suffix(".meta.json")
    assert json.loads(meta.read_text())["label"] == "My title"
    listed = list_sessions(tmp_path)
    assert listed[0]["label"] == "My title"


def test_rewrite_preserves_pinned_state(tmp_path) -> None:
    """Re-segmenting must not reset an explicitly pinned session."""
    sessions = [
        {"id": "s1", "entries": [0], "label": "t", "start": 1.0, "end": 1.0, "client_ua": "Zed"},
        {"id": "s2", "entries": [1], "label": "u", "start": 2.0, "end": 2.0, "client_ua": "Zed"},
    ]
    captures = [{"time": 1.0, "path": "/a"}, {"time": 2.0, "path": "/b"}]
    write_session_files(tmp_path, sessions, captures)
    pin_session(tmp_path, "s1", pinned=True)

    # Simulate re-running `tkt trace-log segment` over the same captures.
    write_session_files(tmp_path, sessions, captures)

    by_id = {s["id"]: s for s in list_sessions(tmp_path)}
    # The pinned session survives re-segmentation still pinned.
    assert by_id["s1"]["pinned"] is True
    # The never-pinned session stays unpinned.
    assert by_id["s2"]["pinned"] is False


def test_pin_and_prune_exempts_pinned(tmp_path) -> None:
    """Prune skips a pinned session even when old and beyond keep."""
    s = [{"id": "keep", "entries": [0], "label": "pinned", "start": 1.0, "end": 1.0, "client_ua": "Zed"}]
    write_session_files(tmp_path, s, [{"time": 1.0}])
    pin_session(tmp_path, "keep", pinned=True)
    # horribly old and not within keep -> should still survive because pinned
    deleted = prune(tmp_path, horizon_days=0, keep=0)
    # prune should skip pinned
    assert "keep" not in [Path(d).name for d in deleted]
    assert list_sessions(tmp_path)[0]["pinned"] is True


def test_prune_removes_both_files_for_unpinned(tmp_path) -> None:
    """Prune removes both the gz and meta files for an unpinned session."""
    s = [{"id": "old", "entries": [0], "label": "old", "start": 1.0, "end": 1.0, "client_ua": "Zed"}]
    (sf,) = write_session_files(tmp_path, s, [{"time": 1.0}])
    mf = sf.with_suffix("").with_suffix(".meta.json")
    assert sf.exists() and mf.exists()
    deleted = prune(tmp_path, horizon_days=0, keep=0)
    assert sf in deleted
    assert not sf.exists()
    assert not mf.exists()
    assert list_sessions(tmp_path) == []
