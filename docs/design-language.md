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

Seven delimited regions, identical in all three files:

| Block | Delimiters | What it holds |
| --- | --- | --- |
| Design tokens | `── hookstack design tokens …` / `── end design tokens ──` | Both sets of colours, the mono and UI font stacks, the header controls' own styling |
| Live control markup | `<span class="rc">` … the first `</span>` | The light/dark button, ↻, and the connection indicator |
| Live control script | `── live control …` / `── end live control ──` | One streaming connection, capped reconnect backoff, refetch on wake |
| Token wiring | `── hookstack token wiring …` / `── end token wiring ──` | The one way a page asks for a token and keeps it |
| Tab shell | `── hookstack tab shell …` / `── end tab shell ──` | The tab strip every page is navigated by |
| Theme first paint | `── hookstack theme …` / `── end theme ──` | The snippet in `<head>` that picks light or dark before anything is drawn |
| Theme wiring | `── theme wiring …` / `── end theme wiring ──` | The button, the reader's pick, and a system that changes its mind |

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

One tab strip on every page, pinned by `assert_design.py` like the tokens: the
pipe's board (alerts · ledger · deliveries · silences · routing · help), the judge (board · review ·
help, the review tab carrying its count so a disagreement queue is visible
without being on screen), the investigator (sessions · **knowledge** skills,
agents, memory, prompt · **runtime** system, actions, audit · help). Before this
the same palette sat under three idioms — tabs, a help link that unfolded a
section, a row of eight buttons — and the pages felt like three tools that
happened to share a colour.

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
