---
title: The board installs on a phone
status: implemented
date: 2026-10-05
scope: hookrelay
---

## Decision

The pipe's board becomes a web app: a manifest and icons under `/static`, a
service worker at `/sw.js`, and an install row in the settings drawer. The
worker keeps the shell — the page, the manifest, the icons — and never the
data: `/status`, `/live`, `/timeline` and the rest are not touched by it, so
offline the app shows the page and the page says the pipe is out of reach,
which is the truth. Everything is linked with the page's own `BASE`, and the
manifest's start URL and scope are relative, so a board served under a path
prefix installs under it without being told. The icons are rendered from the
favicon's geometry by `scripts/make_app_icons.py` and committed.

Only the pipe's board. The judge's and the investigator's pages stay as they
are: the pipe's board is the one attention entry, and the other two are
worked in from a desk.

*Corrected 2026-10-06.* The operator put the investigator's console on a home
screen the next day and found no icon. All three boards install now, with one
shell; see
[2026-10-06-three-boards-install-on-a-phone.md](2026-10-06-three-boards-install-on-a-phone.md).

## Why

The operator reads on the phone. The card loop covers what needs a ruling; the
board is where the rest is read, and a tab in a phone browser loses its tokens
and its place. An installed app keeps both, and keeps them in that browser and
nowhere else — which is the login that the earlier attempt at a one-address
gateway never solved and the reason it was withdrawn (2026-09-29).

Not cached: the data. A worker that answered `/status` from a cache would show
a board that looks current and is not, and the page already has the honest
sentence for a pipe it cannot reach. The shell is network-first with the cached
copy as the fallback, so a deploy is seen on the next open and an old worker
can never pin an old page: `/sw.js` is served no-cache.

## Consequences

- A browser installs an app only over https. The work stack's board is on
  `127.0.0.1:8100` and nothing here opens it to the network; reaching it from a
  phone is a tailnet or a tunnel, the operator's choice, documented in
  deployments.md. Over plain http the page is exactly what it was, and the
  settings drawer says why there is no install button.
- hookrelay gains one route (`/sw.js`) and one static mount, documented in its
  README; the component gate parses the worker and the manifest the way it
  parses the page's inline script. The weight ceiling held (+26 before, +10
  after).
- The install row's words are in both languages; the shared blocks the design
  checker pins are untouched, so the other two pages did not change.
