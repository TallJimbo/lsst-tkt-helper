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
# 3. Neither the name of the copyright holder nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
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

import os
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = str(Path(__file__).resolve().parents[1])
PLUGIN = str(Path(REPO) / "harnesses/opencode/plugin/plannotator-wake.js")

# Stub host session server: speaks MCP framing over a unix socket and always
# reports one review ("rev1") with settled feedback. Its handshake gate mirrors
# the real session server (FastMCP): ``initialize`` with missing or empty
# params is rejected as invalid (-32602), and ``tools/call`` is refused
# (-32600) until the ``notifications/initialized`` notification arrives. The
# v1-era empty-params flow must fail loudly here — the historical stub
# accepted it, hiding the tick failure seen against the real server. Used by
# the wake-plugin probes below; the probe side controls delivery by faking the
# plugin context (session.prompt + event.subscribe).
STUB_SERVER = """
import net from "node:net";
import { createInterface } from "node:readline";
const fence = (obj) => "```json\\n" + JSON.stringify(obj) + "\\n```";
const srv = net.createServer((c) => {
  let initOk = false, ready = false;
  const err = (id, code, message) => c.write(JSON.stringify(
    { jsonrpc: "2.0", id, error: { code, message } }) + "\\n");
  createInterface({ input: c }).on("line", (l) => {
    let r; try { r = JSON.parse(l); } catch { return; }
    if (r.method === "initialize") {
      const p = r.params;
      if (!p || !p.protocolVersion || !p.clientInfo)
        return err(r.id, -32602, "Invalid request parameters");
      initOk = true;
      return c.write(JSON.stringify({ jsonrpc: "2.0", id: r.id, result: {
        protocolVersion: "2024-11-05", capabilities: {},
        serverInfo: { name: "stub" } } }) + "\\n");
    }
    if (r.method === "notifications/initialized") { ready = initOk; return; }
    if (r.method === "tools/call") {
      if (!ready) return err(r.id, -32600, "Server not ready");
      const action = r.params?.arguments?.action;
      const text = action === "status" ? fence([{ id: "rev1" }])
                                       : fence({ type: "feedback", text: "fix it" });
      return c.write(JSON.stringify({ jsonrpc: "2.0", id: r.id, result: {
        content: [{ type: "text", text }] } }) + "\\n");
    }
  });
});
srv.listen(process.argv[2], () => console.log("ready"));
"""


def _serve(tmp_path, script_body):
    """Start a node stub server on a unix socket; wait for it to appear."""
    server = tmp_path / "stub.mjs"
    server.write_text(textwrap.dedent(script_body))
    sock = str(tmp_path / "s.sock")
    srv = subprocess.Popen(["node", str(server), sock], stdout=subprocess.DEVNULL)
    for _ in range(50):
        if os.path.exists(sock):
            return srv, sock
        time.sleep(0.1)
    srv.terminate()
    raise RuntimeError("stub server socket never appeared")


# Pushable async-iterable queue for the probe harnesses: the plugin's event
# capability's subscribe hands back an ASYNC ITERABLE of events (optional
# {signal}-style options in, iterable out; NOT a callback sink), so the fake
# contexts return this iterable and the probe pushes fake OpenCode events
# ({type, properties: {sessionID, ...}}) into it. An iterable that has never
# pushed leaves next() pending forever: the watcher simply waits.
QUEUE_JS = """
const mkq = () => {
  const q = [];
  let wake = null;
  return {
    push: (v) => { q.push(v); const w = wake; wake = null; w?.(); },
    iterable: {
      [Symbol.asyncIterator]: () => ({
        next: () => new Promise((resolve) => {
          const step = () => {
            if (q.length) resolve({ value: q.shift(), done: false });
            else wake = step;
          };
          step();
        }),
      }),
    },
  };
};
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_socket_client_roundtrip(tmp_path):
    """Client half speaks MCP framing over the socket against a stub server.

    Pins the handshake contract: empty-params ``initialize`` is rejected
    (-32602), ``tools/call`` before ``notifications/initialized`` is rejected
    (-32600), and the full handshake (real params, then the id-less notify)
    serves ``tools/call``.
    """
    srv, sock = _serve(tmp_path, STUB_SERVER)
    client = tmp_path / "client.mjs"
    client.write_text(
        f'import {{ SessionSocketClient, parseResult }} from "{tmp_path}/plugin.js";\n'
        "const sock = process.argv[2];\n"
        'const die = (w) => { console.error("BAD: " + w); process.exit(1); };\n'
        "// Regression pin: the v1-era empty-params initialize must fail.\n"
        "const bad = new SessionSocketClient(sock);\n"
        "await bad.connect();\n"
        'try { await bad.rpc("initialize", {}); die("empty-params initialize accepted"); }\n'
        "catch (e) { if (!String(e.message).includes('-32602')) die('empty-params err: ' + e.message); }\n"
        "bad.close();\n"
        "const c = new SessionSocketClient(sock);\n"
        "await c.connect();\n"
        'await c.rpc("initialize", { protocolVersion: "2024-11-05", capabilities: {},\n'
        '  clientInfo: { name: "probe", version: "1" } });\n'
        "// Regression pin: tools/call before the initialized notification must fail.\n"
        'try { await c.callTool("plannotator", { action: "status" });\n'
        '  die("tools/call served before initialized"); }\n'
        "catch (e) { if (!String(e.message).includes('-32600')) die('ready-gate err: ' + e.message); }\n"
        'await c.notify("notifications/initialized", {});\n'
        'const t = await c.callTool("plannotator", { action: "status" });\n'
        "const rec = parseResult(t);\n"
        'if (!(Array.isArray(rec) && rec[0]?.id === "rev1")) die("status content " + t);\n'
        'const fb = parseResult("```json\\n{\\"type\\":\\"feedback\\",\\"text\\":\\"x\\"}\\n```");\n'
        'if (fb.type !== "feedback" || fb.text !== "x") { console.error("BADPARSE"); process.exit(1); }\n'
        "c.close(); process.exit(0);\n"
    )
    (tmp_path / "plugin.js").write_text(open(PLUGIN).read())
    try:
        subprocess.run(["node", str(client), sock], check=True, timeout=20)
    finally:
        srv.terminate()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_module_export_shape(tmp_path):
    """The installed OpenCode's plugin loader contract: default export with
    `id` and `setup`.

    Also pins the named exports the other test in this file imports.
    """
    (tmp_path / "plugin.mjs").write_text(open(PLUGIN).read())
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        textwrap.dedent("""
        import wake, { SessionSocketClient, parseResult, PlannotatorWake }
          from "__PLUGIN_DIR__/plugin.mjs";
        const die = (why) => { console.error("BAD: " + why); process.exit(1); };
        if (typeof wake !== "object" || wake === null) die("default not an object");
        if (wake.id !== "tkt-plannotator-wake") die("default id");
        if (wake.setup !== PlannotatorWake || typeof wake.setup !== "function") die("default setup");
        if (typeof SessionSocketClient !== "function") die("SessionSocketClient export");
        if (typeof parseResult !== "function") die("parseResult export");
        if (typeof PlannotatorWake !== "function") die("PlannotatorWake export");
        process.exit(0);
    """).replace("__PLUGIN_DIR__", str(tmp_path))
    )
    subprocess.run(["node", str(probe), str(tmp_path)], check=True, timeout=20)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_setup_undefined_ctx_boot_log(tmp_path):
    """setup(undefined) must not throw and must write a boot debug line.

    Pins the crash-proof contract: the loader may call setup with any
    argument or none, and the boot line is the only evidence the plugin ran.
    """
    (tmp_path / "plugin.mjs").write_text(open(PLUGIN).read())
    log = tmp_path / "wake-debug.log"
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        textwrap.dedent(f"""
        import wake from "{tmp_path}/plugin.mjs";
        await wake.setup(undefined);
        process.exit(0);
        """)
    )
    env = {k: v for k, v in os.environ.items() if k != "TKT_SESSION_SOCKET"}
    env["TKT_WAKE_DEBUG_LOG"] = str(log)
    subprocess.run(["node", str(probe)], check=True, timeout=20, env=env)
    text = log.read_text()
    assert "boot sock=none ctxKeys=[none]" in text, text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_real_session_server_handshake(tmp_path):
    """The client's handshake works against the real session server.

    The decisive pin against the actual FastMCP server rather than a stub:
    spawns ``python -m tkt.session_server --workspace <ws>`` on a unix socket
    the same way the sandbox bridge does (socat fork per connection) and
    drives ``SessionSocketClient`` through initialize -> notify(initialized)
    -> tools/call, checking the fenced status block comes back. Also pins
    that the server rejects the empty-params initialize.
    """
    if shutil.which("socat") is None:
        pytest.skip("socat not available")
    ws = tmp_path / "ws"
    ws.mkdir()
    sock = str(tmp_path / "session.sock")
    cmd = "SYSTEM:" + shlex.join([sys.executable, "-m", "tkt.session_server", "--workspace", str(ws)])
    env = {**os.environ, "PYTHONPATH": REPO}
    srv = subprocess.Popen(
        ["socat", f"UNIX-LISTEN:{sock},fork", cmd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    client = tmp_path / "client.mjs"
    client.write_text(
        f'import {{ SessionSocketClient, parseResult }} from "{tmp_path}/plugin.js";\n'
        "const sock = process.argv[2];\n"
        'const die = (w) => { console.error("BAD: " + w); process.exit(1); };\n'
        "// The real server rejects the empty-params initialize outright.\n"
        "const bad = new SessionSocketClient(sock);\n"
        "await bad.connect();\n"
        'try { await bad.rpc("initialize", {}, 5000); die("empty-params initialize accepted"); }\n'
        "catch (e) { if (!String(e.message).includes('-32602')) die('empty-params err: ' + e.message); }\n"
        "bad.close();\n"
        "// And answers the full handshake with the fenced status block.\n"
        "const c = new SessionSocketClient(sock);\n"
        "await c.connect();\n"
        'await c.rpc("initialize", { protocolVersion: "2024-11-05", capabilities: {},\n'
        '  clientInfo: { name: "probe", version: "1" } });\n'
        'await c.notify("notifications/initialized", {});\n'
        'const t = await c.callTool("plannotator", { action: "status" });\n'
        'if (!t.includes("```json")) die("no fenced status block: " + t);\n'
        "const status = parseResult(t);\n"
        'if (!Array.isArray(status)) die("status block not a JSON array: " + t);\n'
        "c.close(); process.exit(0);\n"
    )
    (tmp_path / "plugin.js").write_text(open(PLUGIN).read())
    for _ in range(50):
        if os.path.exists(sock):
            break
        time.sleep(0.1)
    try:
        subprocess.run(["node", str(client), sock], check=True, timeout=60, env=env, cwd=REPO)
    finally:
        srv.terminate()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_inject_retry_after_failure(tmp_path):
    """A failed inject logs `inject err` and is retried, not deduped.

    The next tick must retry a failed inject. Runs the real plugin against
    the stub session server with a fake plugin context (session.prompt +
    event.subscribe) whose prompt throws once; setInterval is patched to a
    fast cadence so the retry lands within the test timeout.
    """
    (tmp_path / "plugin.mjs").write_text(open(PLUGIN).read())
    srv, sock = _serve(tmp_path, STUB_SERVER)
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        textwrap.dedent(f"""
        import wake from "{tmp_path}/plugin.mjs";
        const realInterval = globalThis.setInterval;
        globalThis.setInterval = (fn, ms) => realInterval(fn, 20);  // fast ticks
        {QUEUE_JS}
        let injects = 0, lastBody = null;
        const q = mkq();
        const ctx = {{
          session: {{
            prompt: async (body) => {{
              injects++;
              lastBody = body;
              if (injects === 1) throw new Error("boom");
            }},
          }},
          event: {{ subscribe: () => q.iterable }},
        }};
        await wake.setup(ctx);
        q.push({{ type: "session.idle", properties: {{ sessionID: "live1" }} }});
        const deadline = Date.now() + 10000;
        // Poll with setTimeout: awaiting a Timeout object (thenable interval)
        // misbehaves on some Node versions.
        while (injects < 2 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 50));
        if (injects < 2) {{ console.error("BAD: no retry inject"); process.exit(1); }}
        if (lastBody?.sessionID !== "live1" || typeof lastBody?.text !== "string") {{
          console.error("BAD prompt body: " + JSON.stringify(lastBody)); process.exit(1);
        }}
        process.exit(0);
        """)
    )
    log = tmp_path / "wake-debug.log"
    env = {**os.environ, "TKT_SESSION_SOCKET": sock, "TKT_WAKE_DEBUG_LOG": str(log)}
    try:
        subprocess.run(["node", str(probe), sock], check=True, timeout=30, env=env)
    finally:
        srv.terminate()
    text = log.read_text()
    assert "watch session live1" in text, text
    assert "inject err: boom" in text, text
    assert "inject ok rev1" in text, text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_inject_no_target_skip_then_retries(tmp_path):
    """No session id observed must log `inject skip` and NOT consume the
    seen-map.

    With no event fired yet the same feedback must stay retryable: once an
    event names a session, a later tick posts it (`inject ok`) via
    session.prompt addressed to that id.
    """
    (tmp_path / "plugin.mjs").write_text(open(PLUGIN).read())
    srv, sock = _serve(tmp_path, STUB_SERVER)
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        textwrap.dedent(f"""
        import fs from "node:fs";
        import wake from "{tmp_path}/plugin.mjs";
        const realInterval = globalThis.setInterval;
        globalThis.setInterval = (fn, ms) => realInterval(fn, 20);  // fast ticks
        const log = process.env.TKT_WAKE_DEBUG_LOG;
        {QUEUE_JS}
        const prompts = [];
        const q = mkq();  // yields nothing until pushed: the watcher just waits
        const ctx = {{
          session: {{ prompt: async (body) => {{ prompts.push(body); }} }},
          event: {{ subscribe: () => q.iterable }},
        }};
        await wake.setup(ctx);
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        await sleep(300);  // many 20 ms ticks with no session id observed
        const before = fs.readFileSync(log, "utf8");
        if (!before.includes("inject skip: no session id observed")) {{
          console.error("BAD: no inject skip line"); process.exit(1);
        }}
        if (before.split("inject skip").length - 1 !== 1) {{
          console.error("BAD: skip line not rate-limited per id"); process.exit(1);
        }}
        if (before.includes("inject ok")) {{
          console.error("BAD: posted without a session id"); process.exit(1);
        }}
        if (prompts.length) {{
          console.error("BAD: prompt called without a session id"); process.exit(1);
        }}
        q.push({{ type: "session.idle", properties: {{ sessionID: "s1" }} }});
        const deadline = Date.now() + 1500;
        while (!fs.readFileSync(log, "utf8").includes("inject ok") && Date.now() < deadline)
          await sleep(50);
        if (!fs.readFileSync(log, "utf8").includes("inject ok")) {{
          console.error("BAD: feedback lost after no-target ticks"); process.exit(1);
        }}
        if (prompts[0]?.sessionID !== "s1" ||
            !String(prompts[0]?.text).startsWith("Plannotator:")) {{
          console.error("BAD prompt body: " + JSON.stringify(prompts[0])); process.exit(1);
        }}
        process.exit(0);
        """)
    )
    log = tmp_path / "wake-debug.log"
    env = {**os.environ, "TKT_SESSION_SOCKET": sock, "TKT_WAKE_DEBUG_LOG": str(log)}
    try:
        subprocess.run(["node", str(probe), sock], check=True, timeout=15, env=env)
    finally:
        srv.terminate()
    text = log.read_text()
    assert "inject skip: no session id observed" in text, text
    assert "watch session s1" in text, text
    assert "inject ok rev1" in text, text
    assert text.index("inject skip") < text.index("watch session s1"), text
    assert text.index("watch session s1") < text.index("inject ok rev1"), text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_inject_missing_prompt_capability_skip_then_retries(tmp_path):
    """A context lacking the session capability must NOT count as posted.

    Optional chaining on a missing `session` capability short-circuits to
    undefined, so `inject` must guard the prompt being a function: with a
    target id observed but no capability, the skip line must name the
    capability reason, `inject ok` must not appear, and the feedback must
    stay retryable — once the capability appears, the same feedback posts
    via session.prompt.
    """
    (tmp_path / "plugin.mjs").write_text(open(PLUGIN).read())
    srv, sock = _serve(tmp_path, STUB_SERVER)
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        textwrap.dedent(f"""
        import fs from "node:fs";
        import wake from "{tmp_path}/plugin.mjs";
        const realInterval = globalThis.setInterval;
        globalThis.setInterval = (fn, ms) => realInterval(fn, 20);  // fast ticks
        const log = process.env.TKT_WAKE_DEBUG_LOG;
        {QUEUE_JS}
        const prompts = [];
        const q = mkq();
        const ctx = {{ event: {{ subscribe: () => q.iterable }} }};  // no session
        await wake.setup(ctx);
        q.push({{ type: "session.idle", properties: {{ sessionID: "s1" }} }});
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        await sleep(300);  // many 20 ms ticks with a target but no capability
        const before = fs.readFileSync(log, "utf8");
        if (!before.includes("inject skip: no prompt capability")) {{
          console.error("BAD: no capability skip line"); process.exit(1);
        }}
        if (before.includes("inject ok")) {{
          console.error("BAD: deduped without a prompt capability"); process.exit(1);
        }}
        if (prompts.length) {{
          console.error("BAD: prompt called without the capability"); process.exit(1);
        }}
        ctx.session = {{ prompt: async (body) => {{ prompts.push(body); }} }};
        const deadline = Date.now() + 1500;
        while (!fs.readFileSync(log, "utf8").includes("inject ok") && Date.now() < deadline)
          await sleep(50);
        if (!fs.readFileSync(log, "utf8").includes("inject ok")) {{
          console.error("BAD: feedback lost while capability was missing"); process.exit(1);
        }}
        if (prompts[0]?.sessionID !== "s1" ||
            !String(prompts[0]?.text).startsWith("Plannotator:")) {{
          console.error("BAD prompt body: " + JSON.stringify(prompts[0])); process.exit(1);
        }}
        process.exit(0);
        """)
    )
    log = tmp_path / "wake-debug.log"
    env = {**os.environ, "TKT_SESSION_SOCKET": sock, "TKT_WAKE_DEBUG_LOG": str(log)}
    try:
        subprocess.run(["node", str(probe), sock], check=True, timeout=15, env=env)
    finally:
        srv.terminate()
    text = log.read_text()
    assert "watch session s1" in text, text
    assert "inject skip: no prompt capability" in text, text
    assert "inject ok rev1" in text, text
    assert text.index("inject skip: no prompt capability") < text.index("inject ok rev1"), text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_watcher_iterates_subscribe_events(tmp_path):
    """The event watcher iterates what ``subscribe()`` returns.

    The plugin facade's event capability takes optional {signal}-style
    options and returns an async iterable of events — it is NOT a callback
    sink (the callback form silently never fired in the live sandbox). Push
    one session.idle event through a fake iterable: the watcher must log
    `watch start` then `watch session <id>`, and a later tick must inject
    into that session via session.prompt.
    """
    (tmp_path / "plugin.mjs").write_text(open(PLUGIN).read())
    srv, sock = _serve(tmp_path, STUB_SERVER)
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        textwrap.dedent(f"""
        import fs from "node:fs";
        import wake from "{tmp_path}/plugin.mjs";
        const realInterval = globalThis.setInterval;
        globalThis.setInterval = (fn, ms) => realInterval(fn, 20);  // fast ticks
        const log = process.env.TKT_WAKE_DEBUG_LOG;
        {QUEUE_JS}
        const prompts = [];
        const q = mkq();
        const ctx = {{
          session: {{ prompt: async (body) => {{ prompts.push(body); }} }},
          event: {{ subscribe: () => q.iterable }},
        }};
        await wake.setup(ctx);
        q.push({{ type: "session.idle", properties: {{ sessionID: "s1" }} }});
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        const deadline = Date.now() + 5000;
        while (!fs.readFileSync(log, "utf8").includes("inject ok") && Date.now() < deadline)
          await sleep(50);
        const text = fs.readFileSync(log, "utf8");
        if (!text.includes("inject ok")) {{
          console.error("BAD: event stream never reached the watcher\\n" + text);
          process.exit(1);
        }}
        if (prompts[0]?.sessionID !== "s1" ||
            !String(prompts[0]?.text).startsWith("Plannotator:")) {{
          console.error("BAD prompt body: " + JSON.stringify(prompts[0])); process.exit(1);
        }}
        process.exit(0);
        """)
    )
    log = tmp_path / "wake-debug.log"
    env = {**os.environ, "TKT_SESSION_SOCKET": sock, "TKT_WAKE_DEBUG_LOG": str(log)}
    try:
        subprocess.run(["node", str(probe), sock], check=True, timeout=20, env=env)
    finally:
        srv.terminate()
    text = log.read_text()
    assert "watch start" in text, text
    assert "watch session s1" in text, text
    assert "inject ok rev1" in text, text
    assert text.index("watch start") < text.index("watch session s1"), text
    assert text.index("watch session s1") < text.index("inject ok rev1"), text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_watch_missing_subscribe_capability(tmp_path):
    """A context without the event capability logs a watch err, stays healthy.

    No `event` at all must yield `watch err: no subscribe capability` (not a
    crash, not silence): ticks keep running and take the skip path, since no
    session id can ever be observed.
    """
    (tmp_path / "plugin.mjs").write_text(open(PLUGIN).read())
    srv, sock = _serve(tmp_path, STUB_SERVER)
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        textwrap.dedent(f"""
        import fs from "node:fs";
        import wake from "{tmp_path}/plugin.mjs";
        const realInterval = globalThis.setInterval;
        globalThis.setInterval = (fn, ms) => realInterval(fn, 20);  // fast ticks
        const log = process.env.TKT_WAKE_DEBUG_LOG;
        const prompts = [];
        const ctx = {{ session: {{ prompt: async (b) => {{ prompts.push(b); }} }} }};  // no event
        await wake.setup(ctx);
        const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
        await sleep(300);  // many ticks: all must take the skip path, no crash
        const text = fs.readFileSync(log, "utf8");
        if (!text.includes("watch err: no subscribe capability")) {{
          console.error("BAD: no watch err line"); process.exit(1);
        }}
        if (!text.includes("inject skip: no session id observed")) {{
          console.error("BAD: ticks did not reach the skip path"); process.exit(1);
        }}
        if (text.includes("inject ok")) {{
          console.error("BAD: posted without any event capability"); process.exit(1);
        }}
        if (prompts.length) {{
          console.error("BAD: prompt called without a session id"); process.exit(1);
        }}
        process.exit(0);
        """)
    )
    log = tmp_path / "wake-debug.log"
    env = {**os.environ, "TKT_SESSION_SOCKET": sock, "TKT_WAKE_DEBUG_LOG": str(log)}
    try:
        subprocess.run(["node", str(probe), sock], check=True, timeout=15, env=env)
    finally:
        srv.terminate()
    text = log.read_text()
    assert "watch err: no subscribe capability" in text, text
    assert "inject skip: no session id observed" in text, text
    assert "inject ok" not in text, text


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_debug_log_opt_in_no_default_write(tmp_path):
    """No TKT_WAKE_DEBUG_LOG means no filesystem writes at all.

    The debug log is opt-in diagnostics (live acceptance passed; the
    default /tmp sink is gone): setup runs with the session socket set —
    so the plugin boots and ticks, not returning early — with the env var
    removed, and the historical default path must be left untouched
    (absent before / absent after, or identical size).
    """
    (tmp_path / "plugin.mjs").write_text(open(PLUGIN).read())
    srv, sock = _serve(tmp_path, STUB_SERVER)
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        textwrap.dedent(f"""
        import wake from "{tmp_path}/plugin.mjs";
        await wake.setup({{
          session: {{ prompt: async () => {{}} }},
          event: {{ subscribe: () => ({{ [Symbol.asyncIterator]: () => ({{
            next: () => new Promise(() => {{}}),
          }}) }}) }},
        }});
        await new Promise((r) => setTimeout(r, 100));  // let boot land
        process.exit(0);
        """)
    )
    default_log = Path("/tmp/tkt-wake-debug.log")

    def size_or_none():
        return default_log.stat().st_size if default_log.exists() else None

    before = size_or_none()
    env = {k: v for k, v in os.environ.items() if k != "TKT_WAKE_DEBUG_LOG"}
    env["TKT_SESSION_SOCKET"] = sock
    try:
        subprocess.run(["node", str(probe)], check=True, timeout=15, env=env)
    finally:
        srv.terminate()
    assert size_or_none() == before, (
        f"default debug log changed ({before} -> {size_or_none()}): dbg writes without TKT_WAKE_DEBUG_LOG"
    )
