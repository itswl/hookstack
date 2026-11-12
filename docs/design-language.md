# The three pages, one product

hookrelay's ledger, hookjudge's ledger and hookprobe's console are three
single-file pages: no build step, no bundler, no shared asset served from a
fourth place. That is deliberate — a board that cannot render while another
service is down is not a board, and an operator debugging an outage should not
be served a page that needs the outage to be over.

The price of that choice is duplication, and duplication drifts. It already
had: three palettes, three type stacks, and four independent poll timers
between them. So the shared parts are copied **verbatim** between the pages,
and `scripts/assert_design.py` compares them byte for byte in `ci-stack` and
in `scripts/stack-smoke.sh`. A red design check is the contract talking.

## The shared blocks

Twelve delimited regions, identical in all three files:

| Block | Delimiters | What it holds |
| --- | --- | --- |
| Design tokens | `── hookstack design tokens …` / `── end design tokens ──` | Both sets of colours, depth, glass and the two motion curves, the mono, UI and number font stacks, the header controls' own styling |
| Live control markup | `<span class="rc">` … the first `</span>` | The light/dark button, ↻, and the connection indicator |
| Live control script | `── live control …` / `── end live control ──` | One streaming connection, capped reconnect backoff, refetch on wake |
| Token wiring | `── hookstack token wiring …` / `── end token wiring ──` | The one way a page asks for a token and keeps it |
| Tab shell | `── hookstack tab shell …` / `── end tab shell ──` | The tab strip every page is navigated by, which is the tab bar on a phone |
| Theme first paint | `── hookstack theme …` / `── end theme ──` | The snippet in `<head>` that picks light or dark before anything is drawn |
| Theme wiring | `── theme wiring …` / `── end theme wiring ──` | The button, the reader's pick, and a system that changes its mind |
| Components | `── hookstack components …` / `── end components ──` | Everything a board is built from: the top bar, buttons and fields, the status pill, the headline and its numbers, the list row, the drawer, the dialog, the toast |
| Dialogs markup | `── hookstack dialogs …` / `── end dialogs ──` | The one dialog and the toast stack every page asks with |
| Kit script | `── hookstack kit …` / `── end kit ──` | The helpers the components are drawn by: the words in two languages, dates and durations, icons, the dialog, the toast, the clipboard |
| Install script | `── install on a phone …` / `── end install ──` | The manifest, the icons, the service worker, the browser's bar colour and the safe-area insets |
| App shell script | `── hookstack app shell …` / `── end app shell ──` | What a phone gets besides: the large title and the header's small one, a group's pages under the title, the search behind its button, the pull that refreshes an installed board |

Copy a block wholesale when changing it, in all three files, in one commit.

## Tokens

```
              dark      light
--bg          #0b0e14   #f4f6f9   page
--surface     #11151d   #ffffff   cards, inputs, raised rows
--border      #1f2530   #dde2ea   every 1px line
--border-soft #171c26   #eaedf2   inner divisions
--text        #d7dde6   #1b2230   body
--muted       #8b94a3   #5b6677   secondary
--accent      #4c8dff   #2160d6   links, focus, the one primary action
--ok          #3dd68c   #137a3e   delivered, sent, recovered
--warn        #f5a524   #9a5800   queued, running, medium
--bad         #e5484d   #c9262c   dead letters, failures, high and critical
```

Two rules that keep a board readable: **colour is earned by state**, so a
healthy page is quiet and grey; and `--mono` carries anything a machine wrote
(ids, keys, commands, payloads) while `--ui` carries prose. `"PingFang SC"`
stays in the UI stack on purpose — alert titles arrive in whatever language
the monitoring system speaks and are rendered here verbatim. A number a person
counts (alerts, verdicts, dollars, the tab counts) is set in `--num`, the
system's rounded face where it has one, with figures of one width so a count
that changes does not shuffle what is beside it; an id stays in `--mono`.

Since 2026-11-12 the tokens also carry depth and motion, valued per mode like
the colours. `--elev` lifts a card off the page (a light top edge in the dark, a
soft shadow on white) and `--elev-up` is the same card under a pointer.
`--glass` and `--hairline` are the bars a phone keeps on screen — the header and
the tab bar — frosted, so what scrolls under them stays faintly there.
`--ease` is the phone's own sheet curve (fast out, long settle) for everything
that slides, and `--spring` a small overshoot for the tab bar's pill. Colour is
still earned by state, and the overview's sentence spends it as light: its card
glows from the corner in the colour of what it says, green when nothing needs
anyone, amber or red when something does — and then its badge sends out a slow
ring. Whoever asked the system for less motion gets all of it standing still.

hookprobe's console additionally aliases its older token names
(`--panel`, `--line`, `--dim`, `--green`, `--amber`, `--red`) onto the shared
set, so its existing rules keep working without a sweeping rewrite.

## Two modes

Every page comes in dark and light, and follows the system's setting until the
reader picks the other one. The tokens carry both sets of values under one set
of names, and `data-theme` on `<html>` says which applies. A snippet in
`<head>` sets it before anything is drawn — the reader's pick if there is one,
otherwise what `prefers-color-scheme` asks for — so a phone in light mode never
flashes a dark page. On a desk the button beside ↻ switches; switching back to
what the system uses forgets the pick, so the same button is the way back to
following the system. Every board's settings drawer offers the three choices by
name, and on a phone it is the way, since the header there leaves the button
out. The pick is kept in the browser under one key, per board, like the tokens.

Every text colour in the light set clears 4.5:1 on white and on its own 10%
tint, which is how a pill is drawn. The dark set is unchanged.

A page's own colours follow the same rule: named through a variable, with a
value in each mode, in a `:root { … }` and `:root[data-theme="light"] { … }`
pair of the page's own. `assert_design.py` fails on any colour literal in a
page's CSS outside those definitions, because a literal is the same grey on a
white page as on a dark one; white, for text on a filled accent, is the one
exception. Making the light mode found eighteen such literals on the
investigator's console and fifteen on the pipe's board.

## Getting around

All three boards are laid out the same way, because they are read the same way.
A sticky top bar carries the name, one search, the language, the settings and
the header controls (light or dark, refresh, live). Under it, one tab strip,
pinned like the tokens. Every overview opens with **one sentence that says
whether anything needs a person**, the few numbers under it, and banners for
what is broken; then lists of one row per item with a status in a word on the
left, grouped by day wherever a list runs past one. On the pipe's board and the
judge's a row opens the whole of its item in a drawer from the right, with its
own address, closed by Back or Esc. Settings — language, appearance, the
token — live in a drawer behind the gear. Running a procedure, handing a plan
over, deleting a runbook or a role, silencing every source, throwing away
unsaved routing and forgetting the tokens each ask first, in the same dialog on
every page; a one-click ruling, label or retry is the click itself, not a
question about it. Every change says it happened in the same toast. Every word
a page writes itself is there in Chinese and English, the live control's
included, the saved choice first and the browser's language otherwise.

The tabs: the pipe's board (alerts · ledger · deliveries · silences · routing ·
help); the judge's (board · verdicts · review · help, the review tab carrying
its count so a disagreement queue is visible without being on screen); the
investigator's console (work · sessions · approvals · **knowledge** skills,
agents, memory, prompt · **runtime** system, actions, audit · help). The
console opens what it holds in the tab itself instead of a drawer — a runbook,
a role, a session — because those are worked in, not glanced at: a crumb leads
back, and the sessions keep their list beside the open conversation, which on
a phone becomes one pane at a time, like a mail app. Its only drawer is the
settings. Tables on a phone let their headers and status pills wrap, and a
table with more columns than a phone holds becomes one small block per row: the
pipe's channels and dead letters, the investigator's flight recorder and its
approvals.

On a phone a board is laid out as an app (since 2026-11-12). The strip is the
tab bar, pinned to the bottom edge where a thumb already rests, frosted, and it
never slides sideways: one row of equal cells, the icon over the name, the page
on screen marked by a soft pill behind its icon, a count that needs somebody a
filled badge on the icon. The console's two groups are one cell each, opening
the group's first page; the group's own pages are a segmented control under
the large title. The bar steps aside while a field is typed in, so it never
rides the keyboard over a composer. The page opens with its name as a large
title, and once that has scrolled under the header the header says it in small,
centred, and draws its bottom line. The header keeps only what a thumb needs:
the board's name, a search button that opens the search field under it, the
gear, and the live dot (the word appears when the board is not live) — the
language, light or dark and ↻ are in the settings drawer, and the console's
budget chip sits beside them. A dialog is a sheet that rises from the bottom
edge with answers full width and tall enough for a thumb; a toast drops from
under the status bar as a capsule. An installed board refreshes when pulled
down from the top, through the same ↻ a desk presses — a browser tab has its
own pull and keeps it. A page that is shown fades and rises into place; a live
refetch redraws inside it and moves nothing. On a desk none of this changes the
layout: one strip under the header, the controls in the header.

All three boards install on a phone (the pipe's since 2026-10-05, the judge's
and the investigator's since 2026-10-06): a web manifest, icons drawn from
each favicon's own geometry on a surface lit from above
(`scripts/make_app_icons.py`; iOS gets the full-bleed one, since it rounds the
corners itself and fills what is transparent with black), and one service
worker — the same file in all three services, pinned like the blocks above —
that keeps the shell and never the data, so offline a board says its service
is out of reach rather than showing a stale one as current. The settings
drawer carries the install row: the browser's prompt where there is one, the
Share → Add to Home Screen hint where there is not, and a plain sentence over
http, where no browser installs anything. The investigator's manifest is named
for its node: a deployment runs several, and three icons all called
"hookprobe" tell nobody which is which. A browser's own bar follows the theme the
page shows, not only the system's: two `theme-color` metas split by the
system's preference stand in before any script runs, and the install block
rewrites both to the shown theme's surface colour, because a media query
cannot see a pick. An installed iOS app is the exception: it reads its status
bar once, at launch, ignores `theme-color` afterwards, and the `default` style
follows the device's appearance rather than the page's — a board switched to
the other theme kept the old bar until relaunch. So there the bar is
translucent and the header grows by the safe-area inset and draws under the
bar in its own colour; the clock follows the page's color-scheme, which the
design tokens set with the theme, so a light page gets a dark clock and a dark
page a light one. Nothing is painted for the bar itself: a dark strip was tried
first and sat under a dark clock. `viewport-fit=cover` and the safe-area
paddings are one decision, not two — and the paddings read four variables,
never `env()` directly, because an installed app can draw under the bar and
report every inset as zero; the install block measures them and, where it
must, assumes the bar's height from the screen — only ever more than `env()`
says, never less — and the settings drawer prints what the phone was given.
The drawers run edge to edge the same way: the head grows by the inset above
and draws under the bar in its own colour, the body scrolls on under the home
indicator; dialogs and toasts keep clear of both. The phone's own habits are
undone too: no grey flash over a tapped control, no text enlarged when the
phone is turned. iOS keeps the status-bar style an icon was added with, so a
change to it reaches a phone only when the icon is added again; the settings
drawer says so when it sees an installed board under an opaque bar. On a phone every field
types at 16px: iOS zooms the page
in to a smaller one when it takes focus, and does not zoom back.

Before this the same palette sat under three idioms — tabs, a help link that
unfolded a section, a row of eight buttons — and then, once the palette and the
tabs were shared, under three sets of parts: three kinds of button, two kinds
of row, a window.confirm on one page and a modal on another. The components and
the kit are pinned now for the reason the palette was.

The pages also point at each other, because an operation crosses all three. A
hop in the pipe's timeline links to the investigation it names when the
deployment says where that console is (`HOOKRELAY_UI_LINKS`, `door=url`); an
investigation links back to its chain (`HOOKPROBE_RELAY_UI_URL`). The addresses
are the operator's browser's, not the containers' — a tunnelled loopback port in
both deployments here — which is why they are settings and not discovered.

## Staying current

The boards do not keep a clock. What they show changes when a service writes,
and the service says so: each page holds one streaming connection (`/live`,
`/v1/live` on the investigator) and refetches *what it is currently looking at*
when a `changed` arrives. The header keeps ↻ for a manual refetch — an
installed board on a phone is pulled down from the top instead — and shows
whether the connection is up: a dot that breathes while it is, and stands still,
amber and named, while it is not.

The signal carries no rows, deliberately. Every board has filters, a window, a
cursor of its own, so "look again" is smaller than pushing rows the viewer may
not have asked for — and it cannot get their filters wrong. Consecutive writes
collapse into one wake-up, so an alert storm is one refetch rather than N.

This replaced an interval dropdown (manual · 15s · 60s · 5m). It was an honest
answer to "don't poll so often", but it made the operator choose between a stale
board and a busy one, and it was worst exactly where it mattered most: the
seconds after you send an investigation a message, when a 60s tick shows nothing
at all. The service knows when something happened; asking the browser to guess
was the wrong division of labour.

`assert_design.py` now forbids `setInterval` outright. A timer is no longer the
mechanism, so any timer is drift — reconnect backoff uses `setTimeout` and is
capped, so a service that is down is not hammered and a board left open
overnight still comes back on its own.
