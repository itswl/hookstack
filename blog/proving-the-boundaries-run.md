# DRAFT — Proving the boundaries actually run

*An outline, not a finished post. The repository already holds the material
(`.agents/notes/`, `docs/containment.md`, `scripts/assert_guards_are_tested.py`);
this is the shape a public write-up would take, and the reason it is worth
writing: it is the one thing here that is hard to copy.*

## The thesis

Most security documentation describes a boundary. This one makes a second,
uncomfortable claim: **that the thing enforcing the boundary is still running**.
The two are different, and the gap between them is where incidents live. A regex
over model output *described* in a README and a regex *executing* on every tool
call produce identical documentation and different worlds.

## The failure this is about

A guard that has quietly stopped catching anything looks exactly like a guard with
nothing to catch. State it once, early; everything else is an instance of it.

## Three instances, from this codebase

1. **The read-only bash guard skipped a whole class of commands.** The AWS rule
   was written as a list of mutating verbs; the guard matched `aws` inside a
   dotted hostname, so a documentation URL was refused and a real mutation was
   missed. A unit test proved the *shape* check refused a string a developer
   typed — which is not the same as proving a real model cannot be steered.

2. **A tool allowlist that said nothing about sockets.** An agent's MCP allowlist
   is enforced where the agent asks for a *tool*. Measured: one plain HTTP POST
   from inside a probe reached the chat client's MCP port and called a tool the
   list did not name. The fix was not a better list; it was a network with no
   route and a gate in another container. Same lesson at a different layer.

3. **A posture check that had been silent for ten days.** The read-only claim is
   measured against a kubeconfig at startup. When `kubectl` was missing from one
   image, the measurement degraded to `unverifiable` — and `unverifiable` read
   like `recorded`, so ten days passed with every gate green and nothing measured.

## The four mechanisms, and what each is for

| mechanism | catches | does not catch |
| --- | --- | --- |
| `assert_guards_are_tested.py` | a boundary whose tests are decorative — it breaks each claim and requires a test to fail | a boundary with no test yet |
| the inverted checker (`assert_watch_signed`) | a checker that has stopped catching anything | a checker pointed at the wrong thing |
| `gen_reference.py` | a knob documented but not generated from code | prose about behaviour |
| `docs/containment.md`'s four lists | a boundary whose limits are unwritten | an operator who overrides a limit on purpose |

## The honest limit

None of this is containment against a genuinely adversarial model — the
read-only credential is, and the page says so in those words. Heuristics catch
the over-eager model, which is the case that happens. Say that plainly; a security
post that oversells loses the reader it was written for.

## Closing

The ask is small and specific: for every guarantee you write down, name the thing
that would tell you it stopped. If there is no such thing, you have a preference,
not a boundary.
