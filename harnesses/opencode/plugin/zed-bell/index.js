// harnesses/opencode/plugin/zed-bell/index.js
// Server half of the zed-bell directory plugin: a strict no-op that only
// exists so the directory resolves a server entrypoint (a discovered plugin
// directory without one fails to load) and the server reports it as
// TUI-capable, which is how the TUI finds tui.js in the same directory.
// It must never write to stdout: in TUI mode the server process is
// `opencode serve --stdio`, whose stdout is the protocol socket to the TUI,
// not the terminal. The bell lives in tui.js, which runs in the TUI process.
export default { id: "tkt-zed-bell", setup: () => {} };
