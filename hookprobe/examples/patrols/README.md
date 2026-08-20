# Patrols — proactive investigations, zero new code

A patrol is a host crontab line that POSTs an event to hookrelay's front door.
The pipe's escalation route copies it to the investigator like any alert, the
investigation runs with every skill, role and MCP server it has, and the report
comes back through `probe-notify` onto the same channels. Nothing in any
service knows what a patrol is — see *Patrol mode* in
[hookprobe's README](../../README.md) for the mechanism.

Two patrols live here. Both answer questions the family could not answer
before, and neither needed a line of service code:

| brief | schedule | question |
| --- | --- | --- |
| [`weekly-attention-review.md`](weekly-attention-review.md) | Mondays | Is the noise going up or down, and did the spend buy anything? |
| [`nightly-silence-proposal.md`](nightly-silence-proposal.md) | nightly | Is one condition waking people for nothing — and what would quieting it actually cost? |
| [`self-review.md`](self-review.md) | Fridays | What should the investigator itself remember — and what has it proposed that nobody accepted? |

[`patrol.sh`](patrol.sh) is the sender: it reads a brief, signs the request and
posts it.

## The briefs are files, and that is the point

A brief is a **prompt**, so it belongs where a person can edit it and see the
effect on the next run — no image rebuild, no restart. The investigator already
works this way for its environment memory (`CLAUDE.md`) and its methodology
(`system-prompt.md`), both read fresh from the volume at every run.
`patrol.sh` gives the task the same property by reading the brief at send time.

So keep the briefs **outside the git checkout**, beside the deployment's other
mutable state, or a `git pull` will overwrite your edits:

```bash
ROOT=/opt/docker-compose/hookstack        # same root scripts/backup_probe_data.sh uses
mkdir -p "$ROOT/patrols"
cp "$ROOT"/hookprobe/examples/patrols/*.md "$ROOT/patrols/"
chmod +x "$ROOT"/hookprobe/examples/patrols/patrol.sh
```

`scripts/backup_probe_data.sh` backs up `probe-data/` and `shadow-data/` only —
add `patrols` to its `for dir in` list if you want your edited prompts in the
tarball.

**Briefs are capped at 4000 bytes.** The event door truncates an alert body
there (`_BODY_MAX` in `hookprobe/hookprobe/events.py`) and notes in the prompt
where it cut, which for a task brief means the last instruction quietly never
arrived. `patrol.sh` refuses to send an oversized brief instead.

## The crontab

```cron
# /etc/cron.d/hookstack-patrols  (or `crontab -e`)
SHELL=/bin/bash
HOOKRELAY_URL=http://127.0.0.1:8100
HOOKRELAY_INBOUND_SECRET=the-inbound-source-secret-from-hookrelays-config

# Mondays 09:05 — is the noise going up or down?
5 9 * * 1 /opt/docker-compose/hookstack/hookprobe/examples/patrols/patrol.sh /opt/docker-compose/hookstack/patrols/weekly-attention-review.md "Patrol: weekly attention review" >> /var/log/hookstack-patrol.log 2>&1

# Every morning 07:10 — propose one silence, or report that nothing qualified.
10 7 * * * /opt/docker-compose/hookstack/hookprobe/examples/patrols/patrol.sh /opt/docker-compose/hookstack/patrols/nightly-silence-proposal.md "Patrol: nightly silence proposal" >> /var/log/hookstack-patrol.log 2>&1

# Fridays: the investigator reviews its own last twenty investigations and
# proposes at most one durable fact. A nudge, in Hermes Agent's sense — a system
# prompting itself to consolidate rather than waiting for the work to do it.
# What it proposes still needs a person; what it REPORTS is useful either way,
# which is why it is worth running on a deployment where nobody answers.
25 9 * * 5 /opt/docker-compose/hookstack/hookprobe/examples/patrols/patrol.sh /opt/docker-compose/hookstack/patrols/self-review.md "Patrol: self review" >> /var/log/hookstack-patrol.log 2>&1
```

Verify one by hand before trusting the schedule — `curl -sS -f` prints the
error and exits non-zero, so a bad secret or a stopped pipe is loud:

```bash
HOOKRELAY_INBOUND_SECRET=... /opt/docker-compose/hookstack/hookprobe/examples/patrols/patrol.sh \
  /opt/docker-compose/hookstack/patrols/weekly-attention-review.md "Patrol: weekly attention review"
```

The nightly one runs at 07:10 rather than at 03:00 on purpose: it reports on
the night that just ended, and its own card arrives when somebody is awake to
read it.

Prefer the secret out of the crontab? Put the two assignments in a file you can
`chmod 600` on its own and prefix the command with `. /etc/hookstack-patrol.env;`.
A crontab is already only readable by its owner and root; a separate file is
just easier to rotate.

## How the request is signed

The front door verifies a per-source timestamped HMAC — the family's scheme,
`hookrelay/hookrelay/security.py`:

```
X-Hook-Timestamp: <unix seconds>
X-Hook-Signature: sha256=<hex HMAC-SHA256(secret, "{timestamp}.{body}")>
```

`patrol.sh` builds the JSON body and signs *those exact bytes* in one step —
encoding the body twice (jq to send, openssl to digest) is how a signature
comes to cover a body nobody sent. The secret is passed by environment, never
in argv, because argv is world-readable on the host.

Two honest limits, both inherited rather than introduced here. The timestamp
**bounds** the replay window to the source's `max_skew_seconds` (300 by
default) rather than closing it — there is no nonce cache, so a captured patrol
is replayable inside that window. And a source configured with an empty
`secret:` accepts the request unsigned, which is what the demo stack does;
`patrol.sh` posts unsigned when `HOOKRELAY_INBOUND_SECRET` is empty.

## Two prerequisites the briefs assume

**1. The investigator must be able to read the judge.** Both briefs `curl`
`http://hookjudge:8200/status`. If `HOOKJUDGE_READ_TOKEN` is set on the judge,
the probe container needs the same value in its environment — one line in the
`hookprobe` service of your compose file:

```yaml
      HOOKJUDGE_READ_TOKEN: ${HOOKJUDGE_READ_TOKEN:-}
```

The agent's shell inherits the container's environment, so `$HOOKJUDGE_READ_TOKEN`
resolves inside the run. Check it with
`docker compose exec hookprobe printenv HOOKJUDGE_READ_TOKEN`. With no read
token configured the judge leaves `/status` open and the header is unnecessary
— which is the demo stack, and the briefs say to drop it.

**2. A patrol lands in the ledger it is measuring.** Every front-door event is
also routed to `to-brain`, so by default each patrol becomes a verdict, a card
and a row in `summary.attention` — and since its title repeats, a `repeat`.
Eight patrol rows a week is not much, but it is noise the review would be
counting as noise. One route in the pipe's config keeps them out, and because
it stops above `escalate-inbound` it does **not** cost the investigation:

```yaml
  - name: patrol-in
    source: inbound
    when: {title: {contains: "Patrol:"}}
    send_to: [to-probe]
    priority: 60
    stop: true
```

Titles in the crontab above are prefixed `Patrol:` for exactly this match; the
shipped `hookrelay/examples/stack.yaml` does not carry the route, so add it to
your own config (`PUT /config` hot-swaps it, no restart). The weekly brief
tells the agent to count its own footprint either way.

## What these patrols deliberately cannot do

Worth knowing before you read a report and believe more than it says:

- **`mattered_pct` is null until humans press buttons**, and a press only
  exists on channels that render interactive callbacks. The weekly brief
  forbids reading missing rulings as "nobody cared".
- **`noisiest[]` is the top five** conditions with more than one interruption,
  capped because it is also emitted as Prometheus labels. It is not the list.
- **A silence matches a SOURCE, not a condition** — `POST /silences` takes
  `{source, minutes}`, and the pipe's `silence` stage looks up by source name
  only. "Silence this one condition" is not a thing the endpoint can do.
- **There is no recurring silence, and no time-of-day condition anywhere.**
  `minutes` is a duration; `filter`'s `when` matches source, level, title and
  fields, with exact / list / `contains` and nothing else. A nightly silence is
  assembled from cron plus a duration, and it is source-wide. The gap is on
  file with both designs that were considered:
  [a recurring, condition-scoped silence](../../../.agents/notes/proposed/2026-08-20-a-recurring-condition-scoped-silence.md).
- **A silence proposal cannot become a one-click `remediation` step.** Every
  command needs an admin token, and the remediation executor refuses `$` (it
  runs an argv, never a shell), so the token would have to be a literal — in a
  proposal file, in the case file, on the card. The brief says propose in
  prose; a human runs it.
