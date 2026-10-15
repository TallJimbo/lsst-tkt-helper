// harnesses/opencode/plugin/plannotator-wake.js
// In-sandbox wake layer: polls the host session server over the bridge unix
// socket for new Plannotator feedback and injects `Plannotator:` messages.
// The session server is a real MCP server, so every connection must complete
// the handshake before any tools/call: `initialize` carrying protocol
// version, capabilities, and client info (empty params are rejected as
// invalid request parameters), followed by the id-less
// `notifications/initialized` notification. Injection uses the plugin
// context's `session` capability: a plain-text prompt addressed by sessionID
// (no session list on this capability). The target id comes from the
// `event` capability: its subscribe takes optional {signal}-style options
// and returns an async iterable of events, which a fire-and-forget watcher
// iterates until the stream ends, remembering the session id most recently
// seen (checked at the event root and one nesting level down, under either
// spelling). With no id observed yet, or without a usable prompt
// capability, feedback is skipped (with the reason logged) and retried on
// later ticks.
// Pure-MCP client half is exported for tests; only the OpenCode plugin half
// touches the plugin API. Loaded by OpenCode's global plugin autoload from
// ~/.config/opencode/plugin/ as a real copied file (tkt install-opencode-agent
// copies it; the installed OpenCode's plugin loader silently skips symlinks);
// inert without TKT_SESSION_SOCKET (i.e. in host runs). Diagnostics are
// opt-in: with TKT_WAKE_DEBUG_LOG set to a path, every event also appends
// one line there so a silently dead wake layer stays diagnosable from
// inside the sandbox; unset, the plugin never touches the filesystem.
// Log-write failures are swallowed.
import fs from "node:fs";
import net from "node:net";

const dbg = (line) => {
  // Best-effort diagnostics, opt-in: writes ONLY when TKT_WAKE_DEBUG_LOG
  // names a path (no default sink; unset means fully silent). One short
  // line per event (no data payloads); never let a log failure propagate.
  const path = process.env.TKT_WAKE_DEBUG_LOG;
  if (!path) return;
  try { fs.appendFileSync(path, `${new Date().toISOString()} ${line}\n`); } catch { /* ignore */ }
};

export class SessionSocketClient {
  constructor(socketPath) { this.path = socketPath; this.buf = ""; this.nextId = 1; this.pending = new Map(); }
  connect() {
    return new Promise((resolve, reject) => {
      this.sock = net.connect(this.path);
      this.sock.on("connect", resolve);
      this.sock.on("error", reject);
      // Always settle pending rpcs on close (host server stall/restart): the
      // next tick reconnects fresh rather than hanging or leaking sockets.
      this.sock.on("close", () => this._rejectPending(new Error("socket closed")));
      this.sock.on("data", (d) => this._onData(d));
    });
  }
  _onData(chunk) {
    this.buf += chunk.toString("utf8");
    let idx;
    while ((idx = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, idx).trim();
      this.buf = this.buf.slice(idx + 1);
      if (!line) continue;
      let msg; try { msg = JSON.parse(line); } catch { continue; }
      const p = this.pending.get(msg.id);
      if (p) { this.pending.delete(msg.id); msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result); }
    }
  }
  rpc(method, params, timeoutMs = 20_000) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        this.sock.destroy();
        reject(new Error(`rpc ${method} timed out`));
      }, timeoutMs);
      timer.unref?.();
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this.sock.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  }
  notify(method, params) {
    // JSON-RPC notification: written without registering a pending id, so no
    // response is awaited (the handshake's notifications/initialized gets
    // none). Losing it on a half-open socket surfaces as the next rpc's
    // timeout/rejection, which the tick already handles.
    this.sock.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n");
  }
  _rejectPending(err) {
    for (const { reject } of this.pending.values()) reject(err);
    this.pending.clear();
  }
  async callTool(name, args) {
    const res = await this.rpc("tools/call", { name, arguments: args });
    return (res?.content ?? []).map((c) => c.text ?? "").join("\n");
  }
  close() { this.sock?.end(); }
}

export const parseResult = (s) =>
  JSON.parse(String(s).replace(/^```json\n?/m, "").replace(/\n?```$/m, ""));

export const PlannotatorWake = async (ctx) => {
  // Boot line first, synchronously, before anything that can throw: the
  // loader may call setup with any argument (or none), so tolerate ctx fully.
  const ctxKeys = ctx && typeof ctx === "object" ? Object.keys(ctx).join(",") : "none";
  dbg(`boot sock=${process.env.TKT_SESSION_SOCKET ? "set" : "none"} ctxKeys=[${ctxKeys}]`);
  const sockPath = process.env.TKT_SESSION_SOCKET;
  if (!sockPath) return {}; // host runs: inert
  const seen = new Map(); // session id -> last feedback text seen
  const skipped = new Set(); // ids already logged as "inject skip" (log-only; never gates retry)
  // The plugin context's event capability is the only way to learn which
  // OpenCode session to inject into. Its subscribe takes optional
  // {signal}-style options and RETURNS an async iterable of events — it is
  // not a callback sink — so a fire-and-forget async watcher (setup never
  // awaits it) iterates that stream until it ends and remembers the session
  // id most recently seen in any event. Events carry the id at the root and
  // one nesting level down (properties/data), under either spelling, so
  // probe all of those defensively.
  let watchId = null; // most recent session id observed via events
  const watched = new Set(); // ids already logged as "watch session"
  const observe = (ev) => {
    if (!ev || typeof ev !== "object") return;
    for (const src of [ev, ev.properties, ev.data]) {
      if (!src || typeof src !== "object") continue;
      const id = src.sessionID ?? src.sessionId;
      if (typeof id === "string" && id) {
        watchId = id;
        if (!watched.has(id)) {
          watched.add(id);
          dbg(`watch session ${id}`);
        }
        return;
      }
    }
  };
  const watch = async () => {
    // Rejection-proof: everything (subscribe itself included) sits inside
    // the try, so a broken stream can never take down the plugin — at worst
    // no session ids are observed and injections stay on the skip path.
    try {
      if (typeof ctx?.event?.subscribe !== "function") {
        dbg("watch err: no subscribe capability");
        return;
      }
      const it = ctx.event.subscribe();
      if (!it || typeof it[Symbol.asyncIterator] !== "function") {
        dbg("watch err: subscribe returned no async iterable");
        return;
      }
      dbg("watch start");
      for await (const ev of it) observe(ev);
      dbg("watch end");
    } catch (e) {
      dbg(`watch err: ${e?.message ?? e}`);
    }
  };
  watch(); // fire-and-forget; never awaited by setup
  // Post one message into the live OpenCode session via the plugin context's
  // session capability (in-process server): the session most recently seen
  // in events gets it, as a plain-text prompt.
  // Returns true only when the message was actually posted; otherwise it
  // returns the skip reason (a string) so the caller keeps the feedback
  // unseen for retry and logs why. The prompt must be a function: optional
  // chaining alone would short-circuit a missing `session` capability to
  // undefined and let `await undefined` masquerade as a successful post.
  const inject = async (text) => {
    if (!watchId) return "no session id observed";
    if (typeof ctx?.session?.prompt !== "function") return "no prompt capability";
    await ctx.session.prompt({ sessionID: watchId, text });
    return true;
  };
  const timer = setInterval(async () => {
    let c;
    try {
      c = new SessionSocketClient(sockPath);
      await c.connect();
      // Real MCP handshake (see header): proper initialize params, then the
      // initialized notification before any tools/call.
      await c.rpc("initialize", {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "plannotator-wake", version: "1" },
      });
      await c.notify("notifications/initialized", {});
      const sessions = parseResult(await c.callTool("plannotator", { action: "status" }));
      for (const s of sessions) {
        const id = s.id ?? s.session;
        if (!id) continue;
        try {
          const out = await c.callTool("plannotator", { action: "result", session: id });
          const rec = parseResult(out);
          if (rec.type === "feedback" && seen.get(id) !== rec.text) {
            try {
              const posted = await inject("Plannotator: feedback on review " + id + ":\n\n" + rec.text);
              if (posted === true) {
                // Dedupe only after a successful inject: a failed one must stay
                // visible so the next tick retries rather than losing it forever.
                seen.set(id, rec.text);
                dbg(`inject ok ${id}`);
              } else if (!skipped.has(id)) {
                // Nothing posted (skip reason string): retry next tick; log once per id only.
                skipped.add(id);
                dbg(`inject skip: ${posted}`);
              }
            } catch (e) {
              dbg(`inject err: ${e?.message ?? e}`);
            }
          }
        } catch { /* one bad session (e.g. unparseable result) must not stop the rest */ }
      }
    } catch (e) {
      dbg(`tick err: ${e?.message ?? e}`); /* host tools unreachable: retry next tick */
    } finally { c?.close(); }
  }, 30_000);
  timer.unref?.();
  return {};
};

// The installed OpenCode's plugin loader contract: the module must export a
// default definition with an `id` and an `effect` or `setup` function. The
// named exports above stay for tests (they import them directly).
export default { id: "tkt-plannotator-wake", setup: PlannotatorWake };
