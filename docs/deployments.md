# Two deployments, one family

This repository runs two deployments that share every line of service code and
agree on almost nothing else. They are the clearest answer to what hookstack
actually is: the graph is config, and two useful graphs look nothing alike.

| | `deploy/shadow.yaml` | `deploy/work.yaml` |
|---|---|---|
| Carries | alerts from a monitoring platform | work signals from chat and a ticket tracker |
| Front door | one signed door the platform posts to | a timer, and the watcher's own findings |
| Brains | three judges (one live, two comparison arms) | none — the watcher already judged |
| Investigator | one, escalated by level | one, escalated by level AND kind |
| Destinations | one chat, cards with buttons | two chats, no buttons |
| Services | 4 containers + a bridge | 4 containers |

Neither needed a line of Python that the other did not.

## The alert shape

```
platform ──► /hook/ww ──► to-judge ──┐
                        to-judge-b ──┤  three verdicts, independently
                        to-judge-c ──┘
                                     │
              ┌──── /hook/judge-notify ◄──┘   (only the live judge answers back)
              │
              ├──► to-me      ── card, with buttons
              └──► to-probe   ──► investigator ──► /hook/probe-notify ──► to-me
```

Two comparison arms exist to answer a question no single judge can: they judge
the same traffic and disagree with each other in a ledger, which is labelled
data at production rates for free. They have no return door, deliberately — an
arm that answered back would be a second copy of the chain rather than a second
opinion.

The `quiet-wake-no` filter stage drops a card when the judge said nobody needs
to act now. Measured the week it was added: 440 interruptions, 95% repeats.

## The work shape

```
timer ──► scan (plain code) ──► nothing new? the round ends here. No event,
                             │  no model, no bill — which is most rounds.
                             ▼
              /hook/watch-due ──► watcher   reads findings, decides which are
                                     │      worth interrupting a person for
                                     ▼
                          /hook/watch ──┬──► chat A  (the signal)
                                        │
                        (kind=task) ────┴──► planner ──► /hook/plan-notify ──► chat B
```

**The scan is not the watcher.** Reading a window of chat and tickets, comparing
timestamps against a cursor and dropping fragments is procedure, and procedure
written into a prompt is procedure a model can decline to follow — it declined
once, posting a signal and advancing neither cursor. It is a script now, and the
watcher is handed findings rather than instructions for finding them. The saving
is not tokens: an empty round used to cost as much as a busy one, because the
agent had to complete the whole scan to learn there was nothing in it.

The timer's side of that is `PATROL_PRESCAN` in `patrol-timer.sh`, which is
generic — a prescan that exits quiet skips the round, and one that FAILS fires
anyway, carrying the failure. A prescan that broke and a prescan that found
nothing are the same silence from outside, and only one of them is a quiet day.

Two chats rather than one, which is the whole reason this is not the alert
shape: a signal ("someone assigned you this") and a plan ("here is how it would
be done") are read at different moments. One bot would interleave a five-second
notice with a five-minute investigation of the notice before it.

No judge. The watcher already decided what deserves attention, and a judge
calibrated on alerts — severity keywords, recovery semantics, flap suppression —
has no vocabulary for a colleague's question.

## Four decisions that differ, and why

**dedup: off for alerts, on for work signals.** In the alert shape the judge
does the noise accounting, and two counters mean two ledgers and neither one
true. In the work shape nothing else counts: the watcher dedups by TOPIC in its
own state, which is a different question from "is this the identical signal
again" — a crashed watcher re-running replays what it already sent.

**Return doors: unsigned on a server, signed on a laptop.** Same words, different
threat model. "An in-network hop between two containers of one deployment" means
a private server network in one case and *every process on this machine* in the
other, any of which could otherwise post a fabricated card into somebody's chat.

**Escalation gating: level alone, or level and kind.** The investigator gates on
level and uses `fields.kind` only to pick which prompt it runs. So a work
deployment whose contract is "task AND high buys a plan" has to express the
`kind` half in its ROUTE TABLE — left as one fan-out, a `note` at `high` quietly
funds a plan nobody asked for.

**Timer: host crontab, or a container.** The work deployment ships its own,
after the host version could not read its own brief: macOS keeps `~/Documents`
behind TCC and `cron` is not allowed through, so every fire logged `Operation
not permitted` while the pipe had no delivery to fail, the investigator sat
healthy and idle, and the chat stayed quiet. A watcher that was never triggered
and a quiet afternoon look identical from outside. `patrol-timer.sh` is the
general form.

## Three kinds of event, one door

`POST /hooks/event` dispatches on `fields.kind`:

| `kind` | The question | Body cap |
|---|---|---|
| absent, or anything else | what broke — root cause, impact, remediation | 4 KB |
| `task` | how would this be done — what exists, what is missing, the steps | 4 KB |
| `brief` | *the body IS the instruction*: read it and do what it says | 16 KB |

`brief` exists because the other two presume a SUBJECT to analyse and wrap the
body in that framing. A scheduled procedure has neither shape, and the framing
fights it: given a brief and the alert wrapper, one run did the brief's first
step, then the wrapper's ("open the case files first"), called no MCP tool at
all, and answered with the brief's silence token. A blend of two instructions.

Its larger cap is not generosity. Truncating an alert loses detail about one
incident; truncating a procedure deletes STEPS, and the run then does most of a
job and reports success.

## What to read next

- [`docs/containment.md`](containment.md) — every boundary an agent runs behind,
  and in its own column, what each one does NOT stop.
- [`STACK.md`](../STACK.md) — the local demo stack, and the two assertion sets:
  what the DIALECT requires versus what one implementation happens to do.
- `deploy/shadow.yaml` and `deploy/work.yaml` — both carry their reasoning in
  comments, and where a comment and this page disagree, the config is right.
