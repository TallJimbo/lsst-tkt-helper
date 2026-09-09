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

"""Probe a local OpenAI-compatible LLM server for Zed settings.

Every probe records a ``ProbeResult`` so the emitted Zed settings block is
traceable to a concrete test; unknown values stay unknown rather than
being guessed.
"""

from __future__ import annotations

import json
import typing
import urllib.error
import urllib.request
from dataclasses import dataclass, field

OK = "ok"
UNSUPPORTED = "unsupported"
ERROR = "error"
UNKNOWN = "unknown"

_SANITY_PROMPT = "Reply with the single word OK and nothing else."
_MODEL_CANDIDATE_PREFIXES = ("", "/v1", "/api/v0")


@dataclass
class ProbeResult:
    """Outcome of a single capability probe."""

    probe: str
    status: str
    detail: str | None = None


@dataclass
class Capabilities:
    """Zed ``OpenAiCompatibleModelCapabilities`` knob by knob."""

    tools: bool = True
    images: bool = False
    parallel_tool_calls: bool = False
    prompt_cache_key: bool = False
    chat_completions: bool = True
    interleaved_reasoning: bool = False
    max_tokens_parameter: bool = False


@dataclass
class ServerProfile:
    """Everything needed to fill a Zed ``openai_compatible`` block."""

    api_url: str
    model: str
    context_window: int | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None
    capabilities: Capabilities = field(default_factory=Capabilities)
    rejected_params: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    probe_results: list[ProbeResult] = field(default_factory=list)


class ProbeConnectionError(Exception):
    """The server could not be reached or exposes no usable endpoint."""


def _http(
    url: str,
    *,
    data: dict[str, typing.Any] | None = None,
    api_key: str | None = None,
    timeout: float = 30.0,
) -> tuple[int, bytes]:
    """Send one request and return (status, body).

    HTTP errors (4xx/5xx) are returned, not raised. Unreachable sockets
    propagate as ``URLError``/``OSError``; callers that must not abort wrap
    them in ``ProbeConnectionError``.
    """
    headers = {"Content-Type": "application/json"}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def _error_text(payload: bytes) -> str:
    """Extract a short human-readable error string from a response body."""
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return payload.decode("utf-8", "replace")[:200]
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return str(data["error"].get("message", data["error"]))[:200]
    return str(data)[:200]


def _chat_once(
    base_url: str,
    model: str,
    extra: dict[str, typing.Any],
    *,
    api_key: str,
    timeout: float = 30.0,
) -> tuple[int, bytes]:
    """POST one minimal chat completion with ``extra`` merged into the body."""
    body: dict[str, typing.Any] = {
        "model": model,
        "messages": [{"role": "user", "content": _SANITY_PROMPT}],
        "stream": False,
    }
    body.update(extra)
    status, payload = _http(
        base_url + "/chat/completions",
        data=body,
        api_key=api_key,
        timeout=timeout,
    )
    return status, payload


def _stream_chat(
    base_url: str,
    model: str,
    extra: dict[str, typing.Any],
    *,
    api_key: str,
    timeout: float = 30.0,
) -> tuple[int, list[dict[str, typing.Any]]]:
    """POST one streaming chat completion and collect SSE data events.

    Unlike ``_http``/``_chat_once``, HTTP errors are NOT converted here;
    callers wrap network failures if they cannot themselves abort the run.
    """
    body: dict[str, typing.Any] = {
        "model": model,
        "messages": [{"role": "user", "content": _SANITY_PROMPT}],
        "stream": True,
    }
    body.update(extra)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    events: list[dict[str, typing.Any]] = []
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[len("data:") :].strip()
                if chunk == "[DONE]":
                    break
                try:
                    events.append(json.loads(chunk))
                except ValueError:
                    continue
    except urllib.error.HTTPError as err:
        return err.code, []
    return 200, events


def probe_sanity(
    base_url: str,
    model: str,
    *,
    api_key: str,
    timeout: float = 30.0,
) -> tuple[list[ProbeResult], list[dict[str, typing.Any]]]:
    """Check chat completion and SSE streaming; return results, events."""
    results: list[ProbeResult] = []
    status, payload = _chat_once(base_url, model, {}, api_key=api_key, timeout=timeout)
    if status != 200:
        text = _error_text(payload)
        results.append(ProbeResult("chat-completions", ERROR, f"HTTP {status}: {text}"))
        results.append(ProbeResult("streaming", ERROR, f"skipped: chat failed (HTTP {status})"))
        return results, []
    results.append(ProbeResult("chat-completions", OK))
    try:
        stream_status, events = _stream_chat(base_url, model, {}, api_key=api_key, timeout=timeout)
    except (urllib.error.URLError, OSError) as exc:
        results.append(ProbeResult("streaming", ERROR, f"{type(exc).__name__}: {exc}"))
        return results, []
    if stream_status != 200 or not events:
        streaming_status = ERROR if stream_status >= 500 else UNSUPPORTED
        detail = f"HTTP {stream_status}, {len(events)} events"
        results.append(ProbeResult("streaming", streaming_status, detail))
        return results, []
    results.append(ProbeResult("streaming", OK, f"{len(events)} SSE events"))
    return results, events


def discover_base_url(
    base_url: str,
    model: str,
    *,
    api_key: str,
    timeout: float = 30.0,
) -> tuple[str, list[ProbeResult]]:
    """Pin down the exact ``api_url`` Zed must use.

    Zed dead-reckons ``{api_url}/chat/completions``, so the returned prefix
    must match the server's real path layout. Try ``/models`` behind known
    prefixes first, then fall back to a minimal chat completion.
    """
    root = base_url.rstrip("/")
    results: list[ProbeResult] = []
    for prefix in _MODEL_CANDIDATE_PREFIXES:
        url = root + prefix + "/models"
        status, payload = _http(url, api_key=api_key, timeout=timeout)
        if status == 200:
            try:
                json.loads(payload)
            except (ValueError, TypeError):
                continue
            results.append(ProbeResult("models-discovery", OK, url))
            return root + prefix, results
        results.append(ProbeResult("models-discovery", UNSUPPORTED, f"{url}: HTTP {status}"))
    # No usable model listing: infer the prefix from chat completions.
    for prefix in ("", "/v1"):
        status, payload = _chat_once(root + prefix, model, {}, api_key=api_key, timeout=timeout)
        if status not in (404, 405):
            results.append(
                ProbeResult(
                    "models-discovery",
                    OK,
                    f"{root}{prefix} (inferred from chat completions, HTTP {status})",
                )
            )
            return root + prefix, results
        results.append(
            ProbeResult("models-discovery", UNSUPPORTED, f"{root}{prefix}/chat/completions: HTTP {status}")
        )
    raise ProbeConnectionError(f"no working endpoint found under {root}")


_CONTEXT_WINDOW_KEYS = (
    "max_model_len",
    "context_length",
    "n_ctx",
    "context_window",
    "max_context_length",
)
_MAX_OUTPUT_KEYS = ("max_output_tokens", "default_max_output_tokens", "max_completion_tokens")
_METADATA_SUFFIXES = ("/models", "/props", "/api/v0/models")


def _scan_metadata(value: typing.Any, keys: tuple[str, ...]) -> tuple[str, int] | None:
    """Depth-first search for the first of `keys` with a plausible int value.

    Plausible means an int (not bool) in ``[1, 2**31)``; timestamps like
    ``created`` are excluded by key name, not value.
    """
    if isinstance(value, dict):
        for key in keys:
            found = value.get(key)
            if isinstance(found, int) and not isinstance(found, bool) and 0 < found < 2**31:
                return key, found
        for item in value.values():
            hit = _scan_metadata(item, keys)
            if hit is not None:
                return hit
    elif isinstance(value, list):
        for item in value:
            hit = _scan_metadata(item, keys)
            if hit is not None:
                return hit
    return None


def probe_metadata(
    base_url: str,
    model: str,
    *,
    api_key: str,
    timeout: float = 30.0,
) -> tuple[int | None, int | None, ProbeResult]:
    """Harvest context window / max output from server metadata endpoints."""
    window = output = None
    sources: list[str] = []
    for suffix in _METADATA_SUFFIXES:
        status, payload = _http(base_url + suffix, api_key=api_key, timeout=timeout)
        if status != 200:
            continue
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            continue
        hit = _scan_metadata(data, _CONTEXT_WINDOW_KEYS)
        if hit is not None and window is None:
            window = hit[1]
            sources.append(f"{suffix}#{hit[0]}")
        hit = _scan_metadata(data, _MAX_OUTPUT_KEYS)
        if hit is not None and output is None:
            output = hit[1]
            sources.append(f"{suffix}#{hit[0]}")
        if window is not None and output is not None:
            break
    if sources:
        return window, output, ProbeResult("metadata", OK, "from " + ", ".join(sources))
    return window, output, ProbeResult("metadata", UNKNOWN, "no metadata endpoint reported sizes")


def _param_result(param: str, value: typing.Any, status: int, payload: bytes) -> ProbeResult:
    """Build a ProbeResult for one single-parameter acceptance probe."""
    name = f"param:{param}"
    if status == 200:
        return ProbeResult(name, OK, f"accepted (may be ignored): {value!r}")
    text = _error_text(payload)
    if 400 <= status < 500:
        return ProbeResult(name, UNSUPPORTED, f"HTTP {status}: {text}")
    return ProbeResult(name, ERROR, f"HTTP {status}: {text}")


def probe_parameters(
    profile: ServerProfile,
    *,
    api_key: str,
    timeout: float = 30.0,
) -> list[ProbeResult]:
    """Probe single request parameters; mutate the profile in place.

    "accepted" never means "honored": servers may silently ignore unknown
    parameters, so acceptance is informational while rejections are facts.
    """
    results: list[ProbeResult] = []

    def attempt(param: str, value: typing.Any) -> ProbeResult:
        status, payload = _chat_once(
            profile.api_url,
            profile.model,
            {param: value},
            api_key=api_key,
            timeout=timeout,
        )
        result = _param_result(param, value, status, payload)
        results.append(result)
        return result

    # Output-limit parameter: whichever name the server does not reject
    # decides the Zed flag. Both accepted keeps Zed's default (false).
    statuses = {param: attempt(param, 32).status for param in ("max_completion_tokens", "max_tokens")}
    if statuses["max_tokens"] == OK and statuses["max_completion_tokens"] != OK:
        profile.capabilities.max_tokens_parameter = True
    elif statuses["max_tokens"] != OK and statuses["max_completion_tokens"] != OK:
        profile.notes.append("neither max_tokens nor max_completion_tokens accepted; setting default flag")
    elif OK == statuses["max_tokens"] == statuses["max_completion_tokens"]:
        profile.notes.append(
            "both max_tokens and max_completion_tokens accepted; output limit may not be enforced"
        )

    value: typing.Any
    for param, value in (("temperature", 0.5), ("top_p", 0.95)):
        result = attempt(param, value)
        if result.status == UNSUPPORTED:
            profile.rejected_params[param] = result.detail or ""

    # reasoning_effort: accepted? can thinking be switched off?
    low_result = attempt("reasoning_effort", "low")
    if low_result.status == UNSUPPORTED:
        profile.rejected_params["reasoning_effort"] = low_result.detail or ""
    elif low_result.status == ERROR:
        profile.notes.append(
            f"reasoning_effort probe hit a server error ({low_result.detail}); "
            "cannot determine thinking-off support"
        )
    else:
        none_result = attempt("reasoning_effort", "none")
        if none_result.status == OK:
            profile.reasoning_effort = "none"
        elif none_result.status == UNSUPPORTED:
            profile.notes.append(
                "reasoning_effort accepted but 'none' rejected; Zed cannot switch this model's thinking off"
            )
        else:
            profile.notes.append(
                f"reasoning_effort 'none' probe hit a server error ({none_result.detail}); "
                "cannot determine thinking-off support"
            )

    # Knobs Zed sends only when the corresponding capability flag is on.
    # parallel_tool_calls is not probed: acceptance (200) proves nothing
    # about honoring, and the Zed flag's only failure mode — a rejected
    # parameter — is covered by nobody sending it at all. Emitted false.
    for param, value in (("prompt_cache_key", "tkt-probe"),):
        result = attempt(param, value)
        if result.status == UNSUPPORTED:
            profile.rejected_params[param] = result.detail or ""

    return results


# fmt: off
_PROBE_IMAGE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)
# fmt: on


def probe_images(
    profile: ServerProfile,
    *,
    api_key: str,
    timeout: float = 30.0,
) -> ProbeResult:
    """Send a tiny image as a content part and see whether it is accepted."""
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": _SANITY_PROMPT},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{_PROBE_IMAGE_PNG}"},
            },
        ],
    }
    status, payload = _chat_once(
        profile.api_url, profile.model, {"messages": [message]}, api_key=api_key, timeout=timeout
    )
    if status == 200:
        profile.capabilities.images = True
        return ProbeResult("images", OK, "image content part accepted")
    text = _error_text(payload)
    if 400 <= status < 500:
        return ProbeResult("images", UNSUPPORTED, f"HTTP {status}: {text}")
    return ProbeResult("images", ERROR, f"HTTP {status}: {text}")


def _delta_has_reasoning(events: list[dict[str, typing.Any]]) -> bool:
    """Return whether any stream delta carries reasoning content."""
    for event in events:
        if not isinstance(event, dict):
            continue
        for choice in event.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if isinstance(delta, dict) and ("reasoning" in delta or "reasoning_content" in delta):
                return True
    return False


def probe_reasoning_deltas(
    profile: ServerProfile,
    stream_events: list[dict[str, typing.Any]],
    *,
    api_key: str,
    timeout: float = 30.0,
) -> ProbeResult:
    """Look for reasoning deltas; retry the stream with reasoning_effort once.

    Reasoning models often emit reasoning deltas only when the request has a
    reasoning_effort set, so pass ``low`` (the lowest commonly-accepted
    effort) on the retry when the parameter was accepted earlier.
    """
    events = list(stream_events)
    if not _delta_has_reasoning(events):
        effort_accepted = any(
            result.probe == "param:reasoning_effort" and result.status == OK
            for result in profile.probe_results
        )
        extra = {"reasoning_effort": "low"} if effort_accepted else {}
        try:
            _, more = _stream_chat(profile.api_url, profile.model, extra, api_key=api_key, timeout=timeout)
            events.extend(more)
        except (urllib.error.URLError, OSError) as exc:
            profile.probe_results.append(
                ProbeResult("interleaved-reasoning-retry", ERROR, f"{type(exc).__name__}: {exc}")
            )
    if not events:
        return ProbeResult("interleaved-reasoning", UNKNOWN, "no stream events captured")
    if _delta_has_reasoning(events):
        profile.capabilities.interleaved_reasoning = True
        return ProbeResult("interleaved-reasoning", OK, "stream deltas carry reasoning content")
    return ProbeResult("interleaved-reasoning", UNSUPPORTED, "no reasoning deltas in streamed replies")


CONTEXT_WINDOW_PLACEHOLDER = 256000


def probe_responses_api(
    base_url: str,
    model: str,
    *,
    api_key: str,
    timeout: float = 30.0,
) -> bool:
    """Return whether the OpenAI responses API answers for this model."""
    status, _ = _http(
        base_url + "/responses",
        data={"model": model, "input": _SANITY_PROMPT, "stream": False},
        api_key=api_key,
        timeout=timeout,
    )
    return status == 200


def run_probes(
    base_url: str,
    model: str,
    *,
    api_key: str = "dummy",
    timeout: float = 30.0,
) -> ServerProfile:
    """Run the full probe sequence and return the filled profile.

    When the sanity chat probe fails, the probes that POST to
    ``/chat/completions`` (parameters, images, reasoning deltas) are not
    fired at the dead endpoint; each is recorded as UNKNOWN with a
    "skipped: chat/completions failed" detail. GET-based metadata probes
    still run, and ``chat_completions`` is resolved from ``/responses`` in
    both skip branches.
    """
    profile = ServerProfile(api_url="", model=model)
    profile.api_url, discovery = discover_base_url(base_url, model, api_key=api_key, timeout=timeout)
    profile.probe_results.extend(discovery)

    sanity_results, stream_events = probe_sanity(profile.api_url, model, api_key=api_key, timeout=timeout)
    profile.probe_results.extend(sanity_results)
    chat_ok = any(result.probe == "chat-completions" and result.status == OK for result in sanity_results)
    if chat_ok:
        profile.capabilities.chat_completions = True
    elif probe_responses_api(profile.api_url, model, api_key=api_key, timeout=timeout):
        profile.capabilities.chat_completions = False
        profile.probe_results.append(
            ProbeResult("responses-api", OK, "chat/completions failed; /responses works")
        )
    else:
        profile.probe_results.append(
            ProbeResult(
                "responses-api",
                UNSUPPORTED,
                "chat/completions failed and /responses unusable",
            )
        )

    window, output, metadata = probe_metadata(profile.api_url, model, api_key=api_key, timeout=timeout)
    profile.context_window = window
    profile.max_output_tokens = output
    profile.probe_results.append(metadata)

    if chat_ok:
        profile.probe_results.extend(probe_parameters(profile, api_key=api_key, timeout=timeout))
        profile.probe_results.append(probe_images(profile, api_key=api_key, timeout=timeout))
        profile.probe_results.append(
            probe_reasoning_deltas(profile, stream_events, api_key=api_key, timeout=timeout)
        )
    else:
        for probe in ("param-probes", "images", "interleaved-reasoning"):
            profile.probe_results.append(ProbeResult(probe, UNKNOWN, "skipped: chat/completions failed"))
    return profile


def zed_settings_block(
    profile: ServerProfile,
    *,
    provider_id: str = "local-llm",
) -> dict[str, typing.Any]:
    """Emit the ``language_models`` settings fragment for this profile."""
    capabilities = profile.capabilities
    entry: dict[str, typing.Any] = {
        "name": profile.model,
        "max_tokens": (
            profile.context_window if profile.context_window is not None else CONTEXT_WINDOW_PLACEHOLDER
        ),
        "capabilities": {
            "tools": capabilities.tools,
            "images": capabilities.images,
            "parallel_tool_calls": capabilities.parallel_tool_calls,
            "prompt_cache_key": capabilities.prompt_cache_key,
            "chat_completions": capabilities.chat_completions,
            "interleaved_reasoning": capabilities.interleaved_reasoning,
            "max_tokens_parameter": capabilities.max_tokens_parameter,
        },
    }
    if profile.max_output_tokens is not None:
        entry["max_output_tokens"] = profile.max_output_tokens
    if profile.reasoning_effort is not None:
        entry["reasoning_effort"] = profile.reasoning_effort
    return {"language_models": {provider_id: {"api_url": profile.api_url, "available_models": [entry]}}}


def api_key_env_var(provider_id: str) -> str:
    """Zed reads keys from ``{PROVIDER-ID upper-snake}_API_KEY``."""
    return provider_id.upper().replace("-", "_") + "_API_KEY"


_ICONS = {OK: "✓", UNSUPPORTED: "✗", ERROR: "!", UNKNOWN: "?"}


def render_report(profile: ServerProfile, *, provider_id: str = "local-llm") -> str:
    """Human-readable probe report followed by the Zed settings block."""
    lines = [f"Probing {profile.model} at {profile.api_url}", "", "Probes:"]
    for result in profile.probe_results:
        suffix = f" ({result.detail})" if result.detail else ""
        lines.append(f"  {_ICONS[result.status]} {result.probe}: {result.status}{suffix}")
    lines += ["", "Settings values:"]
    lines.append(f"  api_url: {profile.api_url}")
    lines.append(f"  max_tokens (context window): {profile.context_window!r}")
    lines.append(f"  max_output_tokens: {profile.max_output_tokens!r}")
    lines.append(f"  reasoning_effort: {profile.reasoning_effort!r}")
    if profile.context_window is None:
        lines += [
            "",
            f"NOTE: context window unknown; the settings block uses the placeholder "
            f"{CONTEXT_WINDOW_PLACEHOLDER} — set it to the model's real window.",
        ]
    if profile.rejected_params:
        lines += ["", "Rejected parameters (server returned 4xx):"]
        for param in sorted(profile.rejected_params):
            lines.append(f"  {param}: {profile.rejected_params[param]}")
    if profile.notes:
        lines += ["", "Notes:"] + [f"  {note}" for note in profile.notes]
    lines += ["", "Zed settings block:", ""]
    lines.append(json.dumps(zed_settings_block(profile, provider_id=provider_id), indent=4))
    env_var = api_key_env_var(provider_id)
    lines += [
        "",
        f"API key: Zed reads it from the {env_var} environment variable.",
        'Probes used the "dummy" key; set the env var in Zed\'s environment.',
    ]
    return "\n".join(lines)
