---
name: math-display
description: Use when relaying nontrivial mathematics to the human — any formula where unicode prose would be insufficient (nested fractions, multi-line derivations, matrices, cases) — or whenever the human names rendered math; offer the brainstorming visual companion and render with KaTeX rather than pasting raw LaTeX.
---

# math-display: rendered math via the visual companion

The terminal never renders math; the human's browser tab does. When a
formula needs LaTeX grammar to be readable, put it in the browser and keep
chat for prose and plain-text source.

## Trigger

Unicode is sufficient for single symbols and flat expressions
(`α ≈ 0.30″`, `E = mc²`, `SNR = F/σ`): answer in chat. Anything needing
LaTeX grammar — nested fractions, stacked sub/superscripts, aligned
multi-line derivations, matrices, integrals with limits, cases — goes to
the companion. When in doubt between the two, offer.

## Consent

- **You noticed the need**: offer, don't start. One short offer message
  ("this reads better rendered — want it in a browser tab?") and wait.
- **The human asked**: loading or naming this skill, or asking for
  rendered math, is consent. Start the companion and push the first
  screen on your next turn; do not re-offer.

## Server

In tkt restricted runs start the host-side session MCP tool
`brainstorm_server` with `action="start"` and `project_dir` set to the
workspace. Never launch `start-server.sh` from a sandboxed shell: the
sandbox is network-isolated, so its loopback never reaches the human's
browser and detached processes there are fragile. If the session tools
are missing (e.g. `--network` runs), stop and report — an in-sandbox
server is unreachable by design, so don't hunt for a port.

From the start result, keep `screen_dir` and `state_dir`. Push each
screen as a **new** HTML file in `screen_dir` — never reuse a filename
(the server serves the newest by mtime; iterations get `-v2` suffixes).
Hand the human the complete `url`, `?key=…` included, on every screen.
Check `action="status"` before referring to a URL from an earlier turn;
restart with the same `project_dir` reuses the port and open tabs
reconnect. The human's clicks land in `state_dir/events`.

## Keep the conversation on the page

The page is the conversation, extended every turn, not an illustration beside
it or a document you maintain. Your reply that accompanies displayed math —
explanation, derivation steps, interpretation, conclusions — belongs on the
page next to the formulas it describes, not left in the chat terminal. While
the human has to reply in chat, you quote their replies on the companion page
to keep the conversation flow.

The thread is one document in identity and append-only in body: each turn,
copy the last pushed file, append the human's quoted reply and your
response, and write it as the next version (`thread.html`, `thread-v2.html`,
`thread-v3.html`, …). The never-reuse-a-filename rule is what makes the
discipline checkable — the version suffix is the thread's revision number,
earlier versions survive on disk as its history, and editing a pushed file
in place is the rewrite-a-document failure mode this section forbids. A
page that does not carry the transcript forward is a rewrite by another
name: it drops the flow the human has been reading.

## Questions

When discussing mathematical results, do not use multiple-choice
question tools — this overrides other skill and agent-profile text that
directs otherwise. Ask genuinely open questions in prose, on the page
when they concern the math itself. Never manufacture a follow-up
question to offer next steps when none is obviously open.

## Round trip

The human drafts their math in the page's reply-preview sandbox and
pastes it into the terminal — the reply direction reaches you as plain
text, and formatting it is your job, never theirs. Parse the source
before echoing it: a free index under a bound `\sum_i`, an undefined sum
range, unbalanced braces, or an obvious typo is worth naming. When you
suspect a mistake, render what they wrote _and_ what they likely meant,
each with its source, and ask which one to carry on with.

## Notation

Reuse symbols and conventions already present in the project and the
conversation: name a quantity as the repo or the human's own messages
name it. For a variant of a quantity, reach for a modifier before a new
letter — `\hat f` for the Fourier transform of `f`, `\mathbf{A}` or
`A_{ij}` for the matrix representation of operator `A`, `f_i` for
samples `f(x_i)` of a function already called `f`. A small abuse of
notation, declared in a sentence where it occurs, beats an explosion of
new symbols that each need a definition.

## KaTeX recipe

Write **full documents** — the file begins with `<!DOCTYPE` (or
`<html>`), and the server serves it verbatim apart from its injected
helper script. Pin the KaTeX version. Load css, `katex.min.js`, and
`contrib/auto-render.min.js` from jsdelivr, then render on `$$`/`$`
delimiters with `throwOnError: false`. Scale formula blocks for
readability (display math around `1.2–1.4em`, `overflow-x: auto`).

Every math page carries two fixtures, both in the stub below: the
self-check banner and, at the bottom, a **reply-preview sandbox** — a
textarea whose LaTeX the human drafts their next reply in, live-rendered
so they see what they are about to send. A page that omits the render
call, or whose CDN fetch failed, displays raw LaTeX with no error
signal — and raw source in a styled page is indistinguishable from a
deliberate choice, so only the human notices:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <link
      rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css"
    />
    <script
      defer
      src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"
    ></script>
    <script
      defer
      src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"
    ></script>
    <style>
      .reply {
        border-left: 3px solid #999;
        margin: 0.8rem 0;
        padding: 0.2rem 0.8rem;
        color: #444;
      }
      .who {
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #888;
        margin: 0;
      }
    </style>
  </head>
  <body>
    <div id="math-check">Rendering…</div>
    <h2>Thread: PSF fit</h2>
    <p>
      The fit minimizes $\chi^2(\sigma)$ over the pixel grid, and the FWHM
      follows as $2\sqrt{2\ln 2}\,\sigma$.
    </p>
    <blockquote class="reply">
      <p class="who">Human</p>
      <p>Work in the transform domain: $\sum_i \hat{f}_i = \alpha^2$</p>
    </blockquote>
    <p>Your convention closes the sum:</p>
    <div>$$\sum_i \hat{f}_i = \alpha^2.$$</div>
    <textarea
      id="reply-src"
      placeholder="draft your reply in LaTeX"
    ></textarea>
    <div id="reply-preview"></div>
    <script>
      window.addEventListener("load", function () {
        var el = document.getElementById("math-check");
        if (!window.katex || !window.renderMathInElement) {
          el.textContent =
            "FAIL: KaTeX did not load — the browser cannot reach the CDN.";
          return;
        }
        renderMathInElement(document.body, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
          ],
          throwOnError: false,
        });
        var n = document.querySelectorAll(".katex").length;
        el.textContent =
          n > 0 && !/\$\$/.test(document.body.innerText)
            ? "PASS: " + n + " formulas rendered."
            : "FAIL: rendering incomplete — do not trust this screen.";
        var src = document.getElementById("reply-src"),
          prev = document.getElementById("reply-preview");
        src.addEventListener("input", function () {
          try {
            prev.innerHTML = katex.renderToString(src.value, {
              displayMode: true,
              throwOnError: true,
            });
          } catch (e) {
            prev.innerHTML = "parse error: " + e.message;
          }
        });
      });
    </script>
  </body>
</html>
```

The human's judgment of fidelity comes from seeing the math, so show the
LaTeX source beneath each displayed formula when correctness matters. Quote
the human's replies verbatim — their own words and their own LaTeX, rendered
as-is — so the page reads as the full exchange; a reply you paraphrased is
a reply they did not send.

## Limits

- The CDN fetch happens in the human's browser, not the sandbox, so it
  needs the browser's internet. Offline: vendor `katex.min.css`,
  `katex.min.js`, and `auto-render.min.js` into `screen_dir` next to the
  screens and reference them by relative URL (the server serves files
  from the content directory).
- The browser → agent channel is clicks recorded in `state_dir/events`.
  The reply-preview sandbox is a drafting aid: nothing typed there
  reaches you directly — the human pastes its contents into the
  terminal, and the text arrives as a chat message.
