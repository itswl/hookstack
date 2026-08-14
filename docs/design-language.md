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

Three delimited regions, identical in all three files:

| Block | Delimiters | What it holds |
| --- | --- | --- |
| Design tokens | `── hookstack design tokens …` / `── end design tokens ──` | Colours, the mono and UI font stacks, the refresh control's own styling |
| Live control markup | `<span class="rc">` … `</span>` | The ↻ button and the connection indicator |
| Live control script | `── live control …` / `── end live control ──` | One streaming connection, capped reconnect backoff, refetch on wake |

Copy a block wholesale when changing it, in all three files, in one commit.

## Tokens

```
--bg      #0b0e14   page
--surface #11151d   cards, inputs, raised rows
--border  #1f2530   every 1px line          --border-soft #171c26  inner divisions
--text    #d7dde6   body                    --muted       #8b94a3  secondary
--accent  #4c8dff   links, focus, the one primary action
--ok      #3dd68c   delivered, sent, recovered
--warn    #f5a524   queued, running, medium
--bad     #e5484d   dead letters, failures, high and critical
```

Two rules that keep a board readable: **colour is earned by state**, so a
healthy page is quiet and grey; and `--mono` carries anything a machine wrote
(ids, keys, commands, payloads) while `--ui` carries prose. `"PingFang SC"`
stays in the UI stack on purpose — alert titles arrive in whatever language
the monitoring system speaks and are rendered here verbatim.

hookprobe's console additionally aliases its older token names
(`--panel`, `--line`, `--dim`, `--green`, `--amber`, `--red`) onto the shared
set, so its existing rules keep working without a sweeping rewrite.

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
