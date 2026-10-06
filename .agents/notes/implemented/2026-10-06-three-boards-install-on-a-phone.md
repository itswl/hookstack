---
title: Three boards install on a phone
status: implemented
date: 2026-10-06
scope: stack
---

## Decision

The judge's board and the investigator's console become web apps the same way
the pipe's board did the day before: icons under `/static`, a service worker
at `/sw.js`, the install row in the settings drawer, the home-screen metas in
the head. The shell is ONE shape: the service worker is the same file in all
three services and the install wiring is a pinned block in all three pages
(`scripts/assert_design.py` keeps both identical). The one page-specific fact
— which path is the page (`/` on two of them, `/ui` on the investigator's) —
reaches the worker in the query of its own URL, from the page that registers
it, so the worker needs no edit per service.

The investigator's manifest is a route rather than a file, registered before
the static mount so it is the one that answers: a deployment runs several of
these nodes, and three icons all called "hookprobe" on one home screen tell
nobody which is which. The name is `HOOKPROBE_AGENT_NAME`, which only that
process knows. The pages take their iOS home-screen title from the manifest
too, so the one place a service names itself is the name on the phone.

The icons come from each favicon's own geometry: `scripts/make_app_icons.py`
now carries the three glyphs (the hook, the scales, the lens) and writes nine
files; the pipe's three came out byte-identical to the ones committed the day
before.

## Why

The decision of 2026-10-05 was "only the pipe's board; the other two are
worked in from a desk". The operator put the investigator's console on a home
screen the next morning and asked why there was no icon. A decision about how
a person works is settled by how they work.

The worker also stopped caching every navigation under the page's key: the
first version put whatever page a navigation returned — a journey page, say —
into the cache slot the board is restored from offline. Now only the page's
own path is cached, and only a 200.

## Consequences

- A browser installs an app only over https; nothing here opens a console to
  the network. The work stack's consoles are on `127.0.0.1:8088`–`8090`
  behind the doors container, so the same tailnet or tunnel that reaches the
  board reaches them.
- The judge's weight ceiling is unchanged; its board shell cost sixteen lines.
- `docs/design-language.md`, `docs/deployments.md` and the front pages no
  longer say the other two boards are not apps.
