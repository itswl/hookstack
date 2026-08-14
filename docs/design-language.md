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
| Refresh control markup | `<span class="rc">` … `</span>` | The ↻ button and the interval select |
| Refresh control script | `── refresh control …` / `── end refresh control ──` | One timer, localStorage persistence, hidden-tab skip |

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

## Refreshing

One timer per page, and the operator owns it. The header carries ↻ (refresh
now) and an interval: **manual · 15s · 60s · 5m**, defaulting to 60s and
persisted in `localStorage` under `hookstack-refresh-ms`. The timer skips
hidden tabs, and `manual` means exactly that — nothing polls until the button
is pressed.

Nothing else may keep a clock. The console previously ran four timers at once
(session list every 5s, budget every 15s, audit every 8s, the open session
every 2.5s); now one tick refreshes whatever is on screen.

A clock is the wrong tool for one job, and the investigator's console shows it:
the seconds right after you send a message are when feedback matters most, and a
minute-long tick makes them the emptiest. So that feed is *pushed* — the page
opens a streaming response for the run it is watching and closes it when the run
settles. Not a second timer, and therefore not a second thing to keep in sync:
`manual` still means nothing polls, and an open investigation is still live.
`assert_design.py` fails on any `setInterval` the control does not own, which is
what keeps that distinction honest.
