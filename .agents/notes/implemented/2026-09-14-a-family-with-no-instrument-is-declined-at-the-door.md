---
title: A family the investigator has no instrument for is declined at the door, not investigated and reported unreachable
status: implemented
date: 2026-09-14
scope: hookprobe
---

## Decision

`HOOKPROBE_DECLINE_PATTERNS` names a file of full-match regexes over alert
titles. An event whose title matches is answered `skipped` at the event door,
with the pattern that matched, before any run is opened; nothing is spent and no
card is produced by this node. The list is hot-read on every event, `#`
comments allowed, and it fails OPEN on a line that is not a regex: the bad line
is logged and skipped. Rule-driven escalations only — a run a person opened from
chat (`fields.sender`) is never declined, and `/hooks/agent` is untouched. The
production compose mounts `hookprobe/deploy/operator/` read-only at
`/etc/hookprobe/operator/`; the real list lives there, gitignored, and only a
`.example` with invented families is tracked.

## Why

Two facts, both from 2026-09-14, and the order they happened in is the point.

WebhookWise — the platform this deployment's alerts come through — excluded four
alert families from deep analysis on 2026-09-01 with `skip_deep_analysis`
inbound rules: mail delivery, the message broker, cache metrics, two
managed-database metrics. Its note says why in one sentence: the investigator
holds no credential for the cloud account their data lives in, so every
investigation of them ends in a report that restates the alert. That
deployment's production investigator has no AWS credentials at all.

On 2026-09-04 hookstack's own escalation leg went live: every judged critical or
high verdict funds an investigation, gated by level and budget and nothing else.
It funded twenty investigations of the mail family the following week, 70% of
the investigator's spend, at $0.65–4.82 each; five of them proposed the same
read-only `get-account` check the node cannot run. The platform had already
decided these were not worth investigating, and the pipe re-decided the opposite
by omission, because the judge's `high` was the only input its leg had.

A judge saying "high" is evidence the alert matters. It is not evidence the
investigator can look. Those are different facts about different components,
and the second one belongs in a file the operator writes for the investigator —
which is how the platform expressed it, and how this node's other gates
(the remediation allowlist, the blast radius) already work.

**Why titles, and why full-match.** The pipe is content-blind by doctrine and the
judge-notify event carries no rule-group label; the title is the identity every
other gate here keys on. Full-match rather than search so that `SES` alone
declines nothing — a substring list would decline every title that happened to
contain a word, which is how a list of exclusions becomes a silence nobody
ordered.

**Why it fails open.** The remediation allowlist fails closed: a broken pattern
means nothing runs, which is the safe direction for a gate that executes. This
list's job is to save money, and the safe direction for a typo in it is to save
nothing — declining everything because of a stray parenthesis would be a
silence disguised as thrift. So a bad line is logged at WARNING and skipped, the
good lines still apply, and the reference row says so.

**Why not the pipe.** The `verdict-to-me` route sends to `to-me` and `to-probe`
in one line, and a per-title filter on one destination would give the pipe an
opinion about content. The investigator already decides by level
(`HOOKPROBE_ESCALATE_LEVELS`); deciding by family is the same judgement, one
clause wider, in the component that knows what instruments it has.

**Why a mounted operator directory.** The remediation allowlist's compose
comment has said "mount it read-only beside the other operator config" since
2026-08-18, and there was no such place: the only mount was `/data`, which is the
agent's workdir. A gate keyed on a file the agent can edit is not a gate — the
input guard exists to say exactly that about `.claude/`. `/etc/hookprobe/operator/`
is that place now, and the allowlists can move there when they are armed.

## Consequences

- Declined families still produce the judge's card through `to-me`; what they
  stop producing is a paid investigation ending in "unreachable" and the proposal
  it parked. The door's answer carries the pattern, and the log line carries the
  title, so a decline is findable; nothing counts them yet, and a weekly-page line
  is the obvious next reader.
- The list is deployment-local knowledge and the tracked example is fiction.
  On the production deployment the real file mirrors the platform's four rules by
  title prefix; the cache family has no alert title on record yet and is not
  listed until one exists — a pattern written against an unseen title would be
  a guess dressed as a rule.
- A family declined here and later given an instrument (credentials, an MCP
  server) is one deleted line away from being investigated again, with no
  restart.
- Same title, two doors: `/hooks/agent` is not gated, because it is the door a
  person or another platform uses deliberately, and the platform's own leg
  carries its own exclusion list.
