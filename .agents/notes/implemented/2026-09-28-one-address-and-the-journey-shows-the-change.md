---
title: One address for every board, and the journey shows the change itself
status: implemented
date: 2026-09-28
scope: stack
---

## Decision

Each deployment carries a gateway: a plain Caddy proxy on `127.0.0.1:8000`
that serves every board under one origin — the pipe's board at the root, each
brain's console under a prefix with the path stripped (`/plan/`, `/watch/`,
`/work/` on the work deployment; `/judge/`, `/probe/` on the demo). The three
single-file pages address their own service relative to where they are mounted
(`BASE`, read from the page's own path), so the same file works on its own port
and inside the one site. The pipe's console links (`HOOKRELAY_UI_LINKS`) become
site paths and a run's link back to its chain (`HOOKPROBE_RELAY_UI_URL`) the
gateway's address, so a reader never leaves 8000. The proxy holds no token and
injects none.

And the pipe's journey now shows what a report carried besides its prose,
from the payload the pipe already holds: the fenced `diff` block a work
runner's report ends with (coloured), the `blocked` list, the `remediation`
steps, and the whole report under an expander. Most days the front page is the
only page.

## Why

The operator's words: 「我要做到一个网站看全部」. What kept the boards apart
was not their content but four ports. Of the ways to make one site, a proxy in
front is the one that changes no boundary: the pipe still calls no node, no
console opens CORS, no token moves — the browser holds each board's credential
exactly as before, and the three investigators of the work deployment share
one, so it is typed once. Framing the consoles inside the pipe's page (the
zero-change way) is a page inside a page; a read-only aggregator service
(the integrated way) is a fourth service holding every node's token, which the
2026-09-08 note refused for good reasons that still hold. A proxy is the
"one website" without either cost.

The journey's new depth costs nothing in coupling either: the diff, the
blockers and the proposal already travel in the report envelope the pipe
stores — the same fences the investigator lifts with `patches.py`,
`blockers.py` and `remediation.py` — and were simply never drawn.

## Consequences

- One address per deployment. The direct ports stay published for the smoke
  and the scripts (`stack-smoke.sh` speaks to them by port and now also checks
  the gateway reaches all three and serves the console under its prefix).
- The demo compose gains a container (`caddy:2-alpine`, ~15 MB); the
  quickstart file does not, because a Caddyfile is a second file and the
  quickstart's promise is one. The next quickstart can inline it with
  compose's `configs: content:`.
- A console served under a prefix redirects `/` to `ui` relatively; behind
  the gateway `/plan` alone also redirects to `/plan/ui`.
- Exposing the boards to a phone is now one port to protect rather than
  four: put 8000 behind a private network or an authenticating proxy. Not
  done; the cards remain the phone's interface.
- The pipe's page renders fences by regex; a report whose closing fence is
  not on its own line shows as prose, which is exactly how the investigator's
  own lift treats it.
