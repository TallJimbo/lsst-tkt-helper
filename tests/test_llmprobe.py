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

import json
import socket
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

from tkt import llmprobe

CHAT_OK = json.dumps(
    {
        "id": "cmpl",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
).encode("utf-8")

MODELS = {"object": "list", "data": [{"id": "test-model", "object": "model"}]}


class _ScriptedHandler(BaseHTTPRequestHandler):
    """Routes are [(status, bytes)] tuples or callables.

    Each callable takes the request body and returns (status, bytes).
    """

    routes: dict[str, Any] = {}

    def log_message(self, format: str, *args: Any) -> None:
        """Silence access logs, which would otherwise be written to
        ``sys.stderr`` and leak into CLI output captured by Click's
        test runner.
        """

    def do_GET(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def _route(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        route = self.routes.get(f"{self.command} {self.path}")
        if route is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status, payload = route(body) if callable(route) else route
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def scripted_server(routes: dict[str, Any]):
    """Run a scripted HTTP server and yield its local base URL."""
    handler = type("_Handler", (_ScriptedHandler,), {"routes": routes})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join()


def reject_param(name: str, message: str):
    """Callable route: 400 when the request contains `name`, else a 200 OK."""

    def handler(body: bytes):
        if name in json.loads(body):
            return 400, json.dumps({"error": {"message": message}}).encode("utf-8")
        return 200, CHAT_OK

    return handler


def test_discover_finds_v1_prefix():
    """A server exposing only ``/v1/models`` resolves to ``{base}/v1``."""
    with scripted_server(
        {
            "GET /v1/models": (200, json.dumps(MODELS).encode("utf-8")),
            "GET /models": (404, b""),
        }
    ) as base:
        api_url, results = llmprobe.discover_base_url(base, "test-model", api_key="dummy")
    assert api_url == base + "/v1"
    assert [result.probe for result in results] == ["models-discovery", "models-discovery"]
    assert [result.status for result in results] == [
        llmprobe.UNSUPPORTED,
        llmprobe.OK,
    ]


def test_discover_chat_fallback_when_models_missing():
    """With no model listing, the prefix is inferred from chat completions."""
    with scripted_server(
        {
            "GET /models": (404, b""),
            "GET /v1/models": (404, b""),
            "GET /api/v0/models": (404, b""),
            "POST /v1/chat/completions": (200, CHAT_OK),
        }
    ) as base:
        api_url, results = llmprobe.discover_base_url(base, "test-model", api_key="dummy")
        assert api_url == base + "/v1"
        assert results[-1].detail is not None and "inferred" in results[-1].detail


def test_discover_raises_when_unreachable_pathway():
    """A server with no working endpoint raises ``ProbeConnectionError``."""
    with scripted_server({}) as base:
        try:
            llmprobe.discover_base_url(base, "test-model", api_key="dummy")
        except llmprobe.ProbeConnectionError:
            pass
        else:
            raise AssertionError("expected ProbeConnectionError")


SSE_OK = (
    b'data: {"choices": [{"index": 0, "delta": {"content": "OK"}}]}\n\n'
    b'data: {"choices": [{"index": 0, "delta": {}}]}\n\n'
    b"data: [DONE]\n\n"
)


def test_probe_sanity_ok_captures_stream_events():
    """Both sanity probes succeed and stream events are captured."""
    with scripted_server(
        {
            "POST /v1/chat/completions": lambda body: (200, SSE_OK)
            if json.loads(body).get("stream")
            else (200, CHAT_OK),
        }
    ) as base:
        url = base + "/v1"
        results, events = llmprobe.probe_sanity(url, "test-model", api_key="dummy")
    statuses = {(result.probe, result.status) for result in results}
    assert ("chat-completions", llmprobe.OK) in statuses
    assert ("streaming", llmprobe.OK) in statuses
    assert len(events) == 2
    assert events[0]["choices"][0]["delta"]["content"] == "OK"


def test_probe_sanity_chat_failure_short_circuits():
    """A chat failure marks both probes ERROR and yields no stream events."""
    with scripted_server({"POST /chat/completions": (500, b"internal")}) as base:
        results, events = llmprobe.probe_sanity(base, "test-model", api_key="dummy")
    results_by_probe = {result.probe: result for result in results}
    assert results_by_probe["chat-completions"].status == llmprobe.ERROR
    assert results_by_probe["streaming"].status == llmprobe.ERROR
    assert "HTTP 500" in results_by_probe["streaming"].detail
    assert events == []


def test_probe_sanity_stream_5xx_is_error():
    """A 5xx from the streaming probe is ERROR, not UNSUPPORTED."""
    with scripted_server(
        {
            "POST /chat/completions": lambda body: (500, b"internal")
            if json.loads(body).get("stream")
            else (200, CHAT_OK),
        }
    ) as base:
        results, events = llmprobe.probe_sanity(base, "test-model", api_key="dummy")
    streaming = {result.probe: result for result in results}["streaming"]
    assert streaming.status == llmprobe.ERROR
    assert "500" in (streaming.detail or "")
    assert events == []


def test_probe_metadata_reads_model_and_props():
    """A ``/props`` endpoint supplies ``n_ctx`` for the context window."""
    props = {"default_generation_settings": {"n_ctx": 4096}}
    with scripted_server(
        {
            "GET /v1/models": (200, json.dumps(MODELS).encode("utf-8")),
            "GET /v1/props": (200, json.dumps(props).encode("utf-8")),
        }
    ) as base:
        url = base + "/v1"
        window, output, result = llmprobe.probe_metadata(url, "test-model", api_key="dummy")
    assert window == 4096
    assert output is None
    assert result.status == llmprobe.OK
    assert "n_ctx" in (result.detail or "")


def test_probe_metadata_dict_style_models_list():
    """A vLLM-style model listing carries ``max_model_len`` per entry."""
    models = {"data": [{"id": "test-model", "max_model_len": 8192}]}
    with scripted_server({"GET /v1/models": (200, json.dumps(models).encode("utf-8"))}) as base:
        window, output, result = llmprobe.probe_metadata(base + "/v1", "test-model", api_key="dummy")
    assert window == 8192
    assert output is None
    assert result.status == llmprobe.OK


def test_probe_metadata_unknown_when_absent():
    """No size keys anywhere yields ``(None, None)`` and UNKNOWN status."""
    with scripted_server({"GET /v1/models": (200, json.dumps(MODELS).encode("utf-8"))}) as base:
        window, output, result = llmprobe.probe_metadata(base + "/v1", "test-model", api_key="dummy")
    assert window is None and output is None
    assert result.status == llmprobe.UNKNOWN


def _param_profile(url: str) -> llmprobe.ServerProfile:
    return llmprobe.ServerProfile(api_url=url, model="test-model")


def test_probe_parameters_flag_flips_on_rejected_max_completion():
    """A ``max_completion_tokens`` rejection flips the Zed flag to true."""
    routes = {
        "POST /chat/completions": lambda body: reject_param("max_completion_tokens", "Unknown parameter")(
            body
        ),
    }
    with scripted_server(routes) as base:
        profile = _param_profile(base)
        results = llmprobe.probe_parameters(profile, api_key="dummy")
    assert profile.capabilities.max_tokens_parameter is True
    names = {r.probe: r.status for r in results}
    assert names["param:max_completion_tokens"] == llmprobe.UNSUPPORTED
    assert names["param:max_tokens"] == llmprobe.OK


def test_probe_parameters_both_accepted_keeps_default():
    """Both names accepted keeps the default flag and records a note."""
    with scripted_server({"POST /chat/completions": (200, CHAT_OK)}) as base:
        profile = _param_profile(base)
        results = llmprobe.probe_parameters(profile, api_key="dummy")
    assert profile.capabilities.max_tokens_parameter is False
    assert any("both max_tokens" in note for note in profile.notes)
    assert {r.status for r in results} <= {llmprobe.OK, llmprobe.UNSUPPORTED}


def test_probe_parameters_records_reject_list_and_reasoning_off():
    """Sampling rejects fill ``rejected_params``; 'none' refusal noted."""

    def route(body: bytes):
        params = json.loads(body)
        if "temperature" in params or "top_p" in params:
            return 400, json.dumps({"error": {"message": "rejected"}}).encode()
        if params.get("reasoning_effort") == "low":
            return 200, CHAT_OK
        if params.get("reasoning_effort") == "none":
            return 400, json.dumps({"error": {"message": "never"}}).encode()
        return 200, CHAT_OK

    with scripted_server({"POST /chat/completions": route}) as base:
        profile = _param_profile(base)
        results = llmprobe.probe_parameters(profile, api_key="dummy")
    assert set(profile.rejected_params) == {"temperature", "top_p"}
    assert profile.reasoning_effort is None
    assert any("thinking off" in note for note in profile.notes)
    effort_results = [r for r in results if r.probe == "param:reasoning_effort"]
    assert [r.status for r in effort_results] == [llmprobe.OK, llmprobe.UNSUPPORTED]


def test_probe_parameters_reasoning_none_accepted_sets_effort():
    """Both effort levels accepted means thinking can be switched off."""

    def route(body: bytes):
        params = json.loads(body)
        if params.get("reasoning_effort") in ("low", "none"):
            return 200, CHAT_OK
        return 200, CHAT_OK

    with scripted_server({"POST /chat/completions": route}) as base:
        profile = _param_profile(base)
        llmprobe.probe_parameters(profile, api_key="dummy")
    assert profile.reasoning_effort == "none"


def test_probe_parameters_reasoning_effort_500_records_error_note():
    """A 5xx on the reasoning_effort routes is an error note, not a reject."""

    def route(body: bytes):
        if json.loads(body).get("reasoning_effort"):
            return 500, json.dumps({"error": {"message": "boom"}}).encode("utf-8")
        return 200, CHAT_OK

    with scripted_server({"POST /chat/completions": route}) as base:
        profile = _param_profile(base)
        llmprobe.probe_parameters(profile, api_key="dummy")
    assert "reasoning_effort" not in profile.rejected_params
    assert profile.reasoning_effort is None
    assert any("reasoning_effort" in note and "server error" in note for note in profile.notes)
    assert not any("thinking off" in note for note in profile.notes)


SSE_REASONING = (
    b'data: {"choices": [{"index": 0, "delta": {"reasoning_content": "hmm"}}]}\n\n'
    b'data: {"choices": [{"index": 0, "delta": {"content": "OK"}}]}\n\n'
    b"data: [DONE]\n\n"
)


def test_probe_images_flag_flip():
    """An accepted image part flips ``images``; a 400 leaves it off."""

    def route(body: bytes):
        params = json.loads(body)
        content = params["messages"][0]["content"]
        if isinstance(content, list) and any(part.get("type") == "image_url" for part in content):
            return 200, CHAT_OK
        return 400, json.dumps({"error": {"message": "images not supported"}}).encode()

    with scripted_server({"POST /chat/completions": route}) as base:
        profile = _param_profile(base)
        result = llmprobe.probe_images(profile, api_key="dummy")
        assert result.status == llmprobe.OK
        assert profile.capabilities.images is True
    with scripted_server({"POST /chat/completions": (400, b'{"error":{}}')}) as base:
        profile = _param_profile(base)
        result = llmprobe.probe_images(profile, api_key="dummy")
        assert result.status == llmprobe.UNSUPPORTED
        assert profile.capabilities.images is False


def test_delta_has_reasoning_detects_reasoning_content():
    """``reasoning_content`` in a delta is detected; plain text is not."""
    events = [{"choices": [{"delta": {"reasoning_content": "thinking"}}]}]
    assert llmprobe._delta_has_reasoning(events) is True
    assert llmprobe._delta_has_reasoning([{"choices": [{"delta": {"content": "x"}}]}]) is False


def test_delta_has_reasoning_skips_non_dict_choices():
    """Non-dict choice entries (e.g. ``null``) are skipped, not fatal."""
    assert llmprobe._delta_has_reasoning([{"choices": [None]}]) is False
    mixed = [{"choices": [None, {"delta": {"reasoning": "thinking"}}]}]
    assert llmprobe._delta_has_reasoning(mixed) is True


def test_probe_reasoning_deltas_uses_extra_stream_when_sanity_clean():
    """A clean sanity stream triggers one extra stream with effort set."""
    with scripted_server(
        {
            "POST /chat/completions": lambda body: (200, SSE_REASONING)
            if json.loads(body).get("stream") and json.loads(body).get("reasoning_effort")
            else (200, SSE_OK),
        }
    ) as base:
        profile = _param_profile(base)
        profile.probe_results.append(llmprobe.ProbeResult("param:reasoning_effort", llmprobe.OK))
        sanity_events = [{"choices": [{"delta": {"content": "OK"}}]}]
        result = llmprobe.probe_reasoning_deltas(profile, sanity_events, api_key="dummy")
    assert result.status == llmprobe.OK
    assert profile.capabilities.interleaved_reasoning is True
    assert not any(r.probe == "interleaved-reasoning-retry" for r in profile.probe_results)


def test_probe_reasoning_deltas_unknown_without_events():
    """No events captured at all leaves the capability UNKNOWN."""
    with scripted_server({}) as base:
        profile = _param_profile(base)
        result = llmprobe.probe_reasoning_deltas(profile, [], api_key="dummy", timeout=0.2)
    assert result.status == llmprobe.UNKNOWN


def test_probe_reasoning_deltas_records_retry_network_death():
    """A network failure on the retry stream is recorded before continuing."""
    # Reserve (then release) a port so connections to it are refused.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    profile = _param_profile(f"http://127.0.0.1:{port}")
    result = llmprobe.probe_reasoning_deltas(profile, [], api_key="dummy", timeout=2.0)
    retry = [r for r in profile.probe_results if r.probe == "interleaved-reasoning-retry"]
    assert len(retry) == 1
    assert retry[0].status == llmprobe.ERROR
    assert result.status == llmprobe.UNKNOWN


def reject_image_parts(body: bytes):
    """Callable route: 400 for image content parts, else a 200 CHAT_OK."""
    params = json.loads(body)
    content = params["messages"][0]["content"]
    if isinstance(content, list) and any(part.get("type") == "image_url" for part in content):
        return 400, json.dumps({"error": {"message": "images not supported"}}).encode()
    return 200, CHAT_OK


FULL_SERVER = {
    "GET /v1/models": (
        200,
        json.dumps({"data": [{"id": "test-model", "max_model_len": 8192}]}).encode("utf-8"),
    ),
    "POST /v1/chat/completions": reject_image_parts,
    "POST /v1/responses": (404, b""),
}


def test_run_probes_produces_full_profile():
    """The full probe run fills api_url, sizes, and capability flags."""
    with scripted_server(FULL_SERVER) as base:
        profile = llmprobe.run_probes(base, "test-model", api_key="dummy")
    assert profile.api_url == base + "/v1"
    assert profile.capabilities.chat_completions is True
    assert profile.capabilities.tools is True
    assert profile.context_window == 8192
    assert profile.capabilities.images is False


def test_run_probes_falls_back_to_responses_api():
    """A 404 chat endpoint with a working /responses marks chat off."""
    routes = {
        "GET /v1/models": (200, json.dumps(MODELS).encode("utf-8")),
        "POST /v1/chat/completions": (404, b""),
        "POST /v1/responses": (200, json.dumps({"status": "completed"}).encode("utf-8")),
    }
    with scripted_server(routes) as base:
        profile = llmprobe.run_probes(base, "test-model", api_key="dummy")
    assert profile.capabilities.chat_completions is False


CHAT_401 = (401, json.dumps({"error": {"message": "bad key"}}).encode("utf-8"))
MODELS_JSON = json.dumps(MODELS).encode("utf-8")

CHAT_FAIL_RESPONSES_OK = {
    "GET /v1/models": (200, MODELS_JSON),
    "POST /v1/chat/completions": CHAT_401,
    "POST /v1/responses": (200, json.dumps({"status": "completed"}).encode("utf-8")),
}

CHAT_FAIL_RESPONSES_DEAD = {
    "GET /v1/models": (200, MODELS_JSON),
    "POST /v1/chat/completions": CHAT_401,
    "POST /v1/responses": (404, b""),
}

SKIPPED_PROBES = ("param-probes", "images", "interleaved-reasoning")


def test_run_probes_skip_dependent_probes_when_chat_fails():
    """A failing sanity chat gates the chat-based probes as UNKNOWN."""
    with scripted_server(CHAT_FAIL_RESPONSES_OK) as base:
        profile = llmprobe.run_probes(base, "test-model", api_key="dummy")
    assert profile.capabilities.chat_completions is False
    by_probe = {r.probe: r for r in profile.probe_results}
    for probe in SKIPPED_PROBES:
        result = by_probe[probe]
        assert result.status == llmprobe.UNKNOWN
        assert result.detail == "skipped: chat/completions failed"
    assert "temperature" not in profile.rejected_params
    assert "top_p" not in profile.rejected_params


def test_run_probes_skip_dependent_probes_when_responses_also_dead():
    """The gate applies even when /responses is also unusable."""
    with scripted_server(CHAT_FAIL_RESPONSES_DEAD) as base:
        profile = llmprobe.run_probes(base, "test-model", api_key="dummy")
    by_probe = {r.probe: r for r in profile.probe_results}
    assert by_probe["responses-api"].status == llmprobe.UNSUPPORTED
    for probe in SKIPPED_PROBES:
        assert by_probe[probe].status == llmprobe.UNKNOWN
        assert by_probe[probe].detail == "skipped: chat/completions failed"
    assert "temperature" not in profile.rejected_params
    assert "top_p" not in profile.rejected_params


def test_zed_settings_block_shape_and_placeholder():
    """An empty profile gets the placeholder window and default flags."""
    profile = llmprobe.ServerProfile(api_url="http://localhost:8080/v1", model="m")
    block = llmprobe.zed_settings_block(profile, provider_id="local-llm")
    entry = block["language_models"]["local-llm"]["available_models"][0]
    assert entry["max_tokens"] == llmprobe.CONTEXT_WINDOW_PLACEHOLDER
    assert entry["capabilities"]["images"] is False
    assert entry["capabilities"]["tools"] is True
    assert "max_output_tokens" not in entry
    assert "reasoning_effort" not in entry
    assert block["language_models"]["local-llm"]["api_url"] == "http://localhost:8080/v1"
    assert llmprobe.api_key_env_var("local-llm") == "LOCAL_LLM_API_KEY"


def test_zed_settings_block_filled_profile():
    """A filled profile keeps measured values and optional keys."""
    profile = llmprobe.ServerProfile(
        api_url="http://localhost:8080",
        model="m",
        context_window=4096,
        max_output_tokens=2048,
        reasoning_effort="none",
        capabilities=llmprobe.Capabilities(
            images=True, interleaved_reasoning=True, max_tokens_parameter=True
        ),
    )
    block = llmprobe.zed_settings_block(profile)
    entry = block["language_models"]["local-llm"]["available_models"][0]
    assert entry["max_tokens"] == 4096
    assert entry["max_output_tokens"] == 2048
    assert entry["reasoning_effort"] == "none"
    assert entry["capabilities"]["interleaved_reasoning"] is True


def test_render_report_mentions_placeholder_and_block():
    """The report shows the placeholder, env var, and settings block."""
    profile = llmprobe.ServerProfile(api_url="http://x", model="m")
    report = llmprobe.render_report(profile, provider_id="local-llm")
    assert "256000" in report
    assert "LOCAL_LLM_API_KEY" in report
    assert '"available_models"' in report


def test_probe_llm_cli_smoke():
    """The --json command prints a parseable probe report and nothing else."""
    import json as json_module

    from click.testing import CliRunner

    from tkt import _cli

    with scripted_server(FULL_SERVER) as base:
        runner = CliRunner()
        result = runner.invoke(
            _cli.cli,
            ["probe-llm", "test-model", "--url", base, "--json"],
            catch_exceptions=False,
        )
    assert result.exit_code == 0
    document = json_module.loads(result.output)
    assert document["api_url"] == base + "/v1"
    assert document["capabilities"]["chat_completions"] is True
