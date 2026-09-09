# Design: `tkt probe-llm` — capability prober for Zed's `openai_compatible` settings

Date: 2026-09-08
Status: approved (design reviewed in chat; v2)

## Problem

Local LLMs are consumed by Zed through an OpenAI-compatible endpoint on a
port-forwarded port (typically `localhost:8080`). Zed's settings for such
models require fields whose correct values usually cannot be found in the
model's documentation: the exact `api_url` (does the server want `/v1` or
not?), the context window, max output tokens, image support, reasoning-mode
handling, and which request parameters the server rejects. Today the workflow
is `tkt trace-proxy` + manual inspection of captured exchanges. This tool
probes the endpoint directly and emits a ready-to-paste Zed
`settings.json` block.

## Ground truth: Zed's `openai_compatible` schema

From `investigations/zed-src/crates/settings_content/src/language_model.rs`
(`OpenAiCompatibleSettingsContent`, `OpenAiCompatibleAvailableModel`,
`OpenAiCompatibleModelCapabilities`):

```rust
OpenAiCompatibleSettingsContent {
    api_url: String,                       // Zed POSTs to {api_url}/chat/completions
    available_models: Vec<AvailableModel>,
    custom_headers: Option<HashMap<String, String>>,
}

OpenAiCompatibleAvailableModel {
    name: String,
    display_name: Option<String>,
    max_tokens: u64,                                 // context window
    max_output_tokens: Option<u64>,
    max_completion_tokens: Option<u64>,
    reasoning_effort: Option<ReasoningEffort>,
    capabilities: OpenAiCompatibleModelCapabilities,
}

OpenAiCompatibleModelCapabilities (defaults in parens):
    tools: bool (true)
    images: bool (false)
    parallel_tool_calls: bool (false)
    prompt_cache_key: bool (false)
    chat_completions: bool (true)
    interleaved_reasoning: bool (false)
    max_tokens_parameter: bool (false)
```

Semantics verified in `.../provider/open_ai_compatible.rs`, `open_ai.rs`, and
`.../provider/api_compatible.rs`:

- **URL construction is dead-reckoned**: `format!("{api_url}/chat/completions")`
  (or `/responses` when `chat_completions == false`). No `/v1` detection, no
  trailing-slash normalization. The emitted `api_url` therefore must include
  the full working prefix (`http://localhost:8080/v1` for vLLM-style servers,
  `http://localhost:8080` for others).
- **API key**: Zed reads env var `{PROVIDER-KEY}_API_KEY` (upper-snake of the
  settings map key) and always sends `Authorization: Bearer <key>`;
  `openai_compatible` errors with `NoApiKey` if unset. A dummy key is a valid
  configuration for servers that ignore auth.
- **`chat_completions: true`** (default) = Zed uses chat/completions;
  `false` = Zed uses the responses API. A server implementing either/both is
  fine; the only reason to set `false` is a server that *lacks*
  chat/completions. For local servers, emit `true` unless chat/completions
  fails while `/responses` works.
- **`max_tokens_parameter`**: `true` = Zed sends `max_tokens`, `false` =
  `max_completion_tokens`, for the same output-limit value. Which name the
  server prefers is cosmetic; the flag only needs to avoid a param that gets
  *rejected*.
- **`interleaved_reasoning`**: stream events carry reasoning deltas
  interleaved with content deltas (`reasoning` / `reasoning_content` fields).
  Only meaningful for reasoning models.
- **`prompt_cache_key`**: when `true`, Zed includes the `prompt_cache_key`
  parameter (with `cache_control` accepted as the equivalent in some
  providers).
- **`reasoning_effort`** (model field): when set (including `none`), Zed sends
  a static `reasoning_effort` parameter; `none` also enables the
  "thinking off" path. A server that rejects the parameter must leave the
  field unset.

## Design decisions (agreed in chat)

1. **Schema-driven scope**: the probe matrix is exactly the set of fields in
   the Zed schema (plus the URL/auth discovery needed to use them), not a
   generic "llm benchmark".
2. **Assume tool calls work**: `capabilities.tools` is emitted as `true`
   without probing. `parallel_tool_calls` is *not probed at all* and is
   emitted as `false` (Zed's default): acceptance (HTTP 200) proves nothing
   about whether the feature is honored, so the probe would only learn the
   flag's failure mode — a rejected parameter, which cannot happen if Zed
   never sends it. `prompt_cache_key` stays at the Zed default unless a
   probe's reject list says otherwise.
3. **Metadata-first, small synthetic prompts only**: context window and
   max-output numbers come from server metadata endpoints when present; when
   absent they are emitted as explicit placeholders and flagged *unknown* in
   the report — never guessed. No oversized-prompt empirical tests.
4. **Output**: human-readable report + ready-to-paste `openai_compatible`
   settings block; `--json` for the machine-readable full report (including
   the reject list and per-probe notes). The user applies their own overrides
   afterwards.
5. **Single model per run**: `--model` is required; no mass-probing of
   `/models` results.
6. **No new dependencies**: stdlib `urllib` (+ `http.server` stubs in tests),
   matching `tkt/proxy.py`.
7. **No tiebreaker probes**: if one of `max_tokens`/`max_completion_tokens`
   is rejected, the flag follows the survivor; if both are accepted, emit the
   default (`false`) and note the ambiguity in the report.

## Interface stubs

New CLI command in `tkt/_cli.py`:

```python
@cli.command("probe-llm")
@click.argument("model")
@click.option("--url", default="http://localhost:8080", show_default=True,
              help="Base URL of the OpenAI-compatible server.")
@click.option("--api-key", default=None,
              help="Bearer token to send (default: 'dummy', since Zed always sends one).")
@click.option("--timeout", default=30.0, show_default=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("-v", "--verbose", count=True)
def probe_llm(...) -> None: ...
```

New module `tkt/llmprobe.py`:

```python
@dataclass
class ProbeResult:
    """Outcome of one probe: status is one of 'ok', 'unsupported', 'error',
    'unknown'; `detail` carries the reason / server error text."""
    probe: str
    status: str
    detail: str | None = None

@dataclass
class ServerProfile:
    """Everything needed to fill OpenAiCompatibleSettingsContent for one
    model, plus the raw probe record for the report."""
    base_url: str                     # working prefix incl. /v1 if required
    model: str
    context_window: int | None       # -> max_tokens (None => placeholder)
    max_output_tokens: int | None     # -> max_output_tokens
    reasoning_effort: str | None      # accepted effort kw, e.g. "none"; None => omit
    capabilities: OpenAiCompatibleCapabilities
    probe_results: list[ProbeResult]
    rejected_params: dict[str, str]   # param -> server error text

def run_probes(base_url: str, model: str, *, api_key: str = "dummy",
               timeout: float = 30.0) -> ServerProfile: ...

def zed_settings_block(profile: ServerProfile) -> dict:
    """Return the dict for `[language_models.<provider>].available_models`
    entries, with placeholder values for unknown numbers."""

def render_report(profile: ServerProfile) -> str:
    """Human-readable check/warn/fail report."""
```

Internal probe ordering (first failure of a dependency skips its dependents
and marks them `unknown`): URL/auth discovery → sanity (non-streaming,
streaming) → parameters (`max_tokens` vs `max_completion_tokens`,
`reasoning_effort`, reject-list scan) → `images` → reasoning deltas in the
stream.

## Probe details

- **URL/auth discovery**: GET `/models`, then `/v1/models` (then
  `/api/v0/models` as a last resort, e.g. LM Studio). The first prefix
  returning a usable model list wins and defines `base_url`. Auth: run
  sanity with the given key; if 401, report clearly (Zed will fail the same
  way).
- **Sanity**: 1 non-streaming + 1 streaming chat completion ("Reply with OK").
  Streaming: parse SSE; do not require full framing, only that deltas arrive.
- **Numbers**: consult metadata endpoints generically — accept
  `max_model_len`, `context_length`, `n_ctx`, `context_window`,
  `max_output_tokens`/`default_max_output_tokens` when found anywhere in
  `/models`, `/v1/models`, or (llama.cpp) `/props` payloads. Values must be
  attributed (shown in the report where they came from).
- **Parameter acceptance**: for each candidate param, send the small
  completion with only that param added (e.g. `temperature: 0.5`,
  `top_p: 0.95`, `reasoning_effort: "low"`, `prompt_cache_key: "tkt"`;
  `parallel_tool_calls` is never sent). A 4xx marks the param rejected with its
  error text; acceptance of unknown params should be assumed possible
  (silently ignored), i.e. "accepted" never means "honored".
- **Images**: 1×1 PNG data URL in a `content` parts array; probe only after
  sanity passes.
- **Reasoning deltas**: inspect the sanity streaming reply and a
  reasoning_effort probe's stream for `reasoning`/`reasoning_content`
  deltas.

## Output format

Default: human-readable report (✓/✗/⚠ per schema field with one-line
reasons, unknowns called out), then the Zed block as JSON with a
short comment list of caveats printed before it (e.g. "max_tokens is a
placeholder — set the real context window"). `--json`: the full
`ServerProfile` serialization only.

## Error handling

Per `tkt`'s philosophy (users are developers; tracebacks are fine): network
and parse errors surface as exceptions for the CLI to print (no retry storms;
one retry on connection reset for the URL-discovery step is enough). A probe
that fails never aborts the run; it records `error`/`unknown` and continues,
so one broken capability still yields a usable settings block. After
successful URL discovery, however, network-level failures (unreachable
socket) inside an individual probe do abort the run, matching the current
implementation.

## Testing

`tests/test_llmprobe.py` with scripted `ThreadingHTTPServer` stubs (style of
existing tests): one fake server profile per test case
(vLLM-flavored `/v1` server; bare-prefix server; reject-happy server;
reasoning-capable streamer; metadata-less server), asserting
`run_probes` results and the exact generated Zed block, including
placeholder behavior for unknown context windows.
