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

## The status bar, the same afternoon

The first screenshots from a real phone showed the pipe's board light under a
black status bar and dark under a white one. The head had one `theme-color`
meta rewritten by script and the iOS status-bar style `black-translucent`.
Now: two `theme-color` metas split by the system's preference, which the
install block rewrites to the shown theme's surface colour (read from the
design tokens, not copied) because a media query cannot see a pick; and the
status-bar style is `default`, since `black-translucent` paints white text
over a light header and is read once, at launch. `scripts/assert_design.py`
holds both as a rule for all three heads. The probe console's budget chip also
moved to its own line at phone width: with it the header's first line was
wider than the phone and the page scrolled sideways.

## Consequences

- A browser installs an app only over https; nothing here opens a console to
  the network. The work stack's consoles are on `127.0.0.1:8088`–`8090`
  behind the doors container, so the same tailnet or tunnel that reaches the
  board reaches them.
- The judge's weight ceiling is unchanged; its board shell cost sixteen lines.
- `docs/design-language.md`, `docs/deployments.md` and the front pages no
  longer say the other two boards are not apps.

## The flight recorder on a phone, two days later

The next real screenshot (2026-10-08) was the investigator's Audit tab: the
rows ran out of their card, the page had zoomed out to fit them, and the
last column was one character to a line. The table is four columns of
monospace and three of them do not break — time, session, tool — and
together those three are wider than a phone. A table cannot shrink below its
unbreakable cells, so it took the page with it; the fourth column, allowed
to break anywhere, was squeezed to nothing. iOS then shrinks the layout to
fit the widest thing, which is why the header looked narrow too.

Decision: at phone width each call is a small block — time and session on
one line, the tool on the next, the detail wrapped under them — written as
page-local rules on the audit table, not in the shared components block,
because the other two pages have no such table. Two cheaper fixes were
rejected: a sideways scroller inside the card keeps the border honest but
hides the detail column, which is the one a reader opened the tab for; and
lifting the no-wrap alone leaves four cramped columns on a 375px screen.

Measured headless with device emulation against the live watcher with its
real audit data: the page was 625px wide at a 414px viewport before, 414px
after, at 375px too, in both languages; no element on any tab spills past
its card. The pipe's board was surveyed the same way and has nothing of
this class. The rendering recipe is an operator-side script, not a gate
step: it needs a desktop Chrome and a running stack.

## The status bar, corrected two days later

The `default` style turned out to follow the device's appearance, not the
page's, and an installed iOS app reads its status bar once, at launch, and
ignores `theme-color` rewrites after — so the fix above held the launch state
and nothing else: a board switched to the other theme kept the old bar until
relaunch (operator, 2026-10-08). The bar is translucent now and the header
paints the strip under the clock itself, growing by the safe-area inset; the
drawer, which covers the header when open, does the same; `viewport-fit=cover`
pairs with the `env(safe-area-inset-*)` paddings on the sides, the bottom, the
composer and the toasts. iOS draws the clock white over a translucent bar
whatever the page shows, so the light theme's strip is the dark surface — the
operator chose this over keeping a launch-only bar and over giving up the
home-screen app for Safari, whose own bar tints live. The rule above, "default
and never black-translucent", is withdrawn; `scripts/assert_design.py` now
requires the opposite and the CSS half that goes with it.

The same day, every field types at 16px on a phone: iOS zooms the page in to a
field whose text is smaller when it takes focus, and does not zoom back out.
