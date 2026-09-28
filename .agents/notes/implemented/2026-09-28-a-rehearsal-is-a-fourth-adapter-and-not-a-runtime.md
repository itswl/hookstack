---
title: A rehearsal is a fourth adapter and not a runtime — the first ten minutes show the loop with no key
status: implemented
date: 2026-09-28
scope: stack
---

## Decision

`HOOKPROBE_RUNTIME=replay` plays a recorded investigation instead of running
one. The scripts ship inside the package (`hookprobe/replays/*.json`): the tool
calls an investigation made, what they returned, the report it wrote, the answer
it gives a follow-up. Every host, name and number in them is invented. The
adapter is in `runtimes.ADAPTERS` beside the three runtimes, so the conformance
suite judges it; the contract docstring names it; and it is not called a
runtime anywhere, because it is not one.

The quickstart and the source compose bring the investigator up by default on
it. The demo pipe config (`hookrelay/examples/stack.yaml`) offers `approve`,
`followup` and `remember` on report cards and forwards them to a new
`to-probe-action` channel; the investigator is armed with an allowlist that
ships beside the scripts and admits exactly the two observations the disk
script proposes (`df -h /data`, `du -sh /data/results`); its console URL is set
so cards carry a link. `scripts/demo.sh` ends on the loop: the report, the
card, a press through the pipe's own door with an actor on it, the two steps'
exit codes, the alert resolving, the pipe's audit page with the press in it.
`scripts/stack-smoke.sh` — CI's stack job — asserts the same sequence.

One board fix rode along: a work item whose procedure exited clean and whose
condition then ended is `done`, not `verifying` forever. The recovery door had
recorded the ending on the run since 2026-09-08; the state machine never looked.

## Why

The quickstart showed the pipe and the judge and left the investigator behind a
profile that needed a key ("ten minutes, no keys" was true of the two components
that are not the product). The direction memo of 2026-09-28 asked for an
"offline stub investigator"; the reading of pilot zero beside it
([[pilot-zero-read-back-and-the-order-of-the-next-ninety-days]]) says why the
stub had to be a REPLAY through the real gate and not a fake model server:

- the investigator is a CLI agent runtime behind an HTTP door, not an HTTP call
  to a model, so the judge's stub (which speaks the chat-completions shape)
  cannot stand in for it;
- the loop's claim is containment by proof, and a demo that described the
  boundary would be one more description. Each script carries exactly one call
  the read-only guard refuses — `kubectl exec` into the database pod, `kubectl
  rollout undo` on the deployment — and the refusal is the gate's verdict,
  written to the real audit with `denied: true`, counted as a guard trip on the
  result. Under `danger-only` the same scripts record the same calls and refuse
  none of them, which is the honest reading of that posture and why no
  rehearsal node is deployed under it;
- pilot zero's approve door had three locks, and two of them are configuration
  this demo now ships open on purpose: the buttons are offered, the allowlist
  has two lines. The third — nobody could read the evidence where the card was
  read — is why the console URL is set, and why the rehearsal's report is the
  card's full text and not a summary of it.

Obligation by obligation, what a thing that runs no tool does with the
contract: the gate before every recorded call, by the one gate; the audit
written by the adapter, one line per replayed call, in the spawned gate's shape
plus `replayed: true` so no reader mistakes it for a live line; a session id
derived from the session key, so a resume after a restart lands on the same id;
`session` first, then `tool_use`/`tool_done` pairs, then `text`, paced at
0.35 s so the board's live feed shows a run; `cost_usd = 0.0` beside a usage of
zero tokens — the one case the contract lets a zero mean free.

What the smoke and the demo now prove, from the bus and the sink rather than
from the node: the disk alert's report returns through `probe-notify`; the
approve link the DingTalk dialect renders is pressed through `/card-action`
with an actor; the pipe forwards it to `/hooks/action`; the investigator checks
the allowlist and runs both steps as argv with exit 0; the item is `done` once
the resolve arrives.

## Consequences

The quickstart runs published images, so this needs a release: the compose
pins `0.4.0` and cannot point at an older tag, because the pipe config that
offers the buttons and the investigator that carries the rehearsal both live in
the images. Until the tag is published the source compose is the path that
works.

`ci-stack` now builds the investigator's image and drives a rehearsal, which
costs the job a few minutes it did not spend before and buys the first
end-to-end check of the approve path that has ever run outside a unit test —
production never offered the button ([[pilot-zero-read-back-and-the-order-of-the-next-ninety-days]]).

A rehearsal node reads nothing and must never hold a credential worth
investigating with; `describe_inputs` says `rehearsal: true`, `/v1/agent` says
`replay`, and every report's last section says what it is. The runs are not
marked synthetic — the board is meant to show them — so a rehearsal deployment
leaves auto-distill at its default of off; a deployment that turned it on would
distil a runbook from a fiction.

`--profile probe` is gone from the composes; the Prometheus sidecar in the source
compose moved to `--profile prometheus`, because the rehearsal has no use for it.
The judge's stub is unchanged, and stays a stub: it never claimed to be a model.

## Rejected

- **A fake Anthropic-dialect model server behind the real Claude adapter.** It
  would need the CLI, a credential shape and a tool-use protocol to fake, and
  the first thing it would fake is the gate. The replay reaches the real one.
- **Replaying a real recorded run from the production archive.** The most honest
  fixture and the least publishable: its evidence is the operator's estate.
  The scripts are composites with everything invented, and say so.
- **Marking rehearsal runs synthetic.** Keeps them off the board, which is the
  one place the demo has to show them.
- **Executing the recorded observations for real during the replay.** Then the
  report's numbers would disagree with the tool output beside them, and the
  adapter would be running commands after all. Nothing runs until a person
  presses approve, which is the sentence the whole loop is built on.
