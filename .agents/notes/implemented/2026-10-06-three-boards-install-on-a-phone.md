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

## Corrected again the same evening

The first screenshot back showed the dark strip for what it was: a band
floating above the header, and the clock dark — on this phone the status bar's
glyphs follow the page's `color-scheme`, not a fixed white, so the strip was
built on a four-year-old table. The operator's other installed board does it
plainly and has for a while: the header grows by the inset and draws under the
bar in its own colour, nothing painted for the bar. Ported here, with its
harder-won half: the safe-area paddings read four variables that default to
`env()` and that the install block overwrites from a measurement, because an
installed app with a translucent bar can draw under it and report every inset
as zero; where it does, the bar's height is assumed from the screen's shape
(44pt and 34pt on a tall phone, 20pt and none on an older one), once per size
change. `--statusbar` is gone; `scripts/assert_design.py` asks for the
variables and the measurement instead.

The screenshot after that showed the header beneath the clock, nothing to tap:
the measured inset had come out zero and the script had written that zero over
a CSS `env()` that, the earlier band proved, works on this phone. So the
measurement only ever adds — a probe that reads nothing leaves the stylesheet's
`env()` alone — it runs again at load, on return and on a size change, the
assumed height is by the screen's size where the phone is known (48pt on the
operator's) and by its shape otherwise, and the settings drawer prints what the
phone was given, measured or assumed, so the next screenshot carries numbers.

The header rule did not reach the rest of the edge. The drawers took the inset
as their own padding, so the strip under the clock was the drawer's background
above a head of another colour, and the settings drawer's body ended above the
home indicator instead of scrolling under it; the dialog and the toasts kept
clear of nothing. Now the insets go on the parts: the drawer head grows by the
top inset in its own colour, the bodies pad their end by the bottom one, the
dialog is padded by all four and scrolls inside itself, the toasts clear the
indicator and the notch. And the two habits the operator's other installed
board undoes: the grey flash over a tapped control, and text enlarged when the
phone is turned. All in the shared blocks, so the pipe's board, the judge's and
the investigator's change together; measured headless on all three — the
judge from a scratch instance — as an installed app whose insets read zero and
as one whose insets are reported, both themes, with a drawer open: the header
clears the bar, no control is left under it, nothing overflows.

Then the pipe's board was right and the investigator's was not: under the
investigator's console a light, opaque status bar stayed light when the page
went dark, and the drawer read "top 0px, measured". The two pages serve the
same head byte for byte. The difference was the icons: iOS keeps the status-bar
style an icon was ADDED with, whatever the page says later, and the console's
icon dated from the day the head said `default`; the pipe's had been added
again since. Nothing a page does reaches that bar. So the install block now
recognises it — installed, portrait on a phone, nothing reported above the
page and the page shorter than the screen — and the settings drawer says what
only the reader can do: remove the icon and add it again from Safari. Measured
headless in the three states: an icon with an opaque bar (the hint, no extra
padding), a translucent one reporting nothing (48px assumed, no hint), and a
browser tab (the install button).

## The tab strip on a phone

The strip slid sideways on a phone: the pipe's six tabs ran 131px past a 414px
screen, the console's thirteen cells 511px with eight of them off it, and the
operator found the sliding ugly. Now, in the shared tab shell, a phone gets one
row of equal cells with the icon over the name, the count as a badge on the
icon. The console's groups collapse to one cell each that opens the group's
first page (the label carries that page and an icon), and the group of the page
on screen unfolds its pages as a second row, a segmented control drawn from two
new tokens valued per theme, because no pair of the existing ones keeps the
selected segment lighter than its track in both. Rejected: two rows of six for
the console's eleven pages (every page pays for a launcher grid at the top) and
a tab bar fixed at the bottom (a fixed bar fights the composer and the
keyboard, and the operator's other installed board took its own out).

Measured headless at 375 and 414 in both languages, both themes: no cell off
the screen, nothing overflowing, no name cut, one row on the pipe's and the
judge's boards and on the console's top-level pages, two when a group is open.
At desk width the probe's page is pixel-identical below the header before and
after.

## Every view, both languages

A sweep of every view and drawer of the three boards at 375px in both
languages found two more overflows, both in the pipe's English: the channel
table on Deliveries (116px past its card: six headers that never wrap) and a
help table whose first column holds status pills (36px). Fixed in two layers.
The shared components let a table's headers and the pills in its cells wrap on
a phone and pull its cells closer, which is enough for the help table and is a
floor for every other table on the three boards. The channel table and the
dead-letter table are six columns a phone cannot hold even then, so on a phone
the pipe draws each row as a small block, the column names carried by
data-label from the same words as the header row it hides. The dead-letter
table had no rows to measure, so the check rewrote the status response to
carry one dead letter and one open breaker. Afterwards the sweep — 62
measurements — is clean, and at desk width the Deliveries view is
pixel-identical before and after.
