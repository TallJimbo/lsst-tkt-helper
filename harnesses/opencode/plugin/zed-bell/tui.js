// harnesses/opencode/plugin/zed-bell/tui.js
// TUI half of the zed-bell directory plugin: emits a terminal bell when
// OpenCode needs attention, so Zed Terminal Threads fire their agent
// notification (pop-up + optional sound; see Zed docs
// ai/terminal-threads.md "OpenCode Notifications"). It must run in the TUI
// process: the server process's stdout is the stdio protocol socket, so a
// bell written there never reaches a terminal. Grounded in the vendored
// dev tree: the TUI host requires `export default { id, setup }`
// (packages/tui/src/plugin/context.tsx isPlugin) and calls setup with a
// Context whose `data` capability carries the OpenCodeEvent stream
// (packages/plugin/src/tui/context.ts): data.listen(handler) receives every
// event wrapped as { details }, while data.on(type, handler) receives the
// event directly; both return an unsubscribe function. Prefer listen (one
// subscription for the whole attention set) with a per-type on fallback,
// and unwrap defensively so either delivery shape rings. No ACP guard: there
// is no TUI under ACP. Keep the never-throw posture:
// every host call is capability-guarded and wrapped, because a broken
// listener must never crash the TUI. setup returns the unsubscribe cleanup
// (returning a truthy non-function would make the host's teardown throw).
export const ZedBellTui = async (ctx) => {
  const attention = new Set([
    "session.idle", // v1 spelling (Zed's snippet)
    "session.execution.succeeded", // observed terminal events on 2.0.26
    "session.execution.failed",
    "permission.asked", // permission prompt (present in 2.0.26)
  ]);
  const bell = () => {
    try {
      process.stdout.write("\x07");
    } catch {
      /* stdout unavailable: no bell */
    }
  };
  try {
    const data = ctx?.data;
    const ring = (payload) => {
      // listen delivers { details: event }; on delivers the event itself.
      const event = payload?.details ?? payload;
      if (event && attention.has(event.type)) bell();
    };
    if (typeof data?.listen === "function") {
      const off = data.listen((payload) => {
        try {
          ring(payload);
        } catch {
          /* never crash the TUI */
        }
      });
      return () => {
        try {
          if (typeof off === "function") off();
        } catch {
          /* cleanup must not throw */
        }
      };
    }
    if (typeof data?.on === "function") {
      const offs = [];
      for (const type of attention) {
        try {
          const off = data.on(type, (event) => {
            try {
              ring(event);
            } catch {
              /* never crash the TUI */
            }
          });
          if (typeof off === "function") offs.push(off);
        } catch {
          /* one bad subscription must not stop the rest */
        }
      }
      return () => {
        for (const off of offs) {
          try {
            off();
          } catch {
            /* cleanup must not throw */
          }
        }
      };
    }
  } catch {
    /* no usable event stream: no bells, but never crash the TUI */
  }
  return undefined;
};

export default { id: "tkt-zed-bell-tui", setup: ZedBellTui };
