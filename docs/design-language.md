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

Ten delimited regions, identical in all three files:

| Block | Delimiters | What it holds |
| --- | --- | --- |
| Design tokens | `── hookstack design tokens …` / `── end design tokens ──` | Both sets of colours, the mono and UI font stacks, the header controls' own styling |
| Live control markup | `<span class="rc">` … the first `</span>` | The light/dark button, ↻, and the connection indicator |
| Live control script | `── live control …` / `── end live control ──` | One streaming connection, capped reconnect backoff, refetch on wake |
| Token wiring | `── hookstack token wiring …` / `── end token wiring ──` | The one way a page asks for a token and keeps it |
| Tab shell | `── hookstack tab shell …` / `── end tab shell ──` | The tab strip every page is navigated by |
| Theme first paint | `── hookstack theme …` / `── end theme ──` | The snippet in `<head>` that picks light or dark before anything is drawn |
| Theme wiring | `── theme wiring …` / `── end theme wiring ──` | The button, the reader's pick, and a system that changes its mind |
| Components | `── hookstack components …` / `── end components ──` | Everything a board is built from: the top bar, buttons and fields, the status pill, the headline and its numbers, the list row, the drawer, the dialog, the toast |
| Dialogs markup | `── hookstack dialogs …` / `── end dialogs ──` | The one dialog and the toast stack every page asks with |
| Kit script | `── hookstack kit …` / `── end kit ──` | The helpers the components are drawn by: the words in two languages, dates and durations, icons, the dialog, the toast, the clipboard |

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
the monitoring system speaks and are rendered here verbatim.

hookprobe's console additionally aliases its older token names
(`--panel`, `--line`, `--dim`, `--green`, `--amber`, `--red`) onto the shared
set, so its existing rules keep working without a sweeping rewrite.

## Two modes

Every page comes in dark and light, and follows the system's setting until the
reader picks the other one. The tokens carry both sets of values under one set
of names, and `data-theme` on `<html>` says which applies. A snippet in
`<head>` sets it before anything is drawn — the reader's pick if there is one,
otherwise what `prefers-color-scheme` asks for — so a phone in light mode never
flashes a dark page. The button beside ↻ switches; switching back to what the
system uses forgets the pick, so the same button is the way back to following
the system. The pipe's settings drawer offers the three choices by name. The
pick is kept in the browser under one key, per board, like the tokens.

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
settings.

All three boards install on a phone (the pipe's since 2026-10-05, the judge's
and the investigator's since 2026-10-06): a web manifest, icons drawn from
each favicon's own geometry (`scripts/make_app_icons.py`), and one service
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
translucent and the header paints the strip under the clock itself, growing
by the safe-area inset; `viewport-fit=cover` and the `env(safe-area-inset-*)`
paddings are one decision, not two. iOS draws the clock white over a
translucent bar whatever the page shows, so the light theme's strip is the
dark surface. And on a phone every field types at 16px: iOS zooms the page
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
when a `changed` arrives. The header keeps ↻ for a manual refetch and shows
whether the connection is up.

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
