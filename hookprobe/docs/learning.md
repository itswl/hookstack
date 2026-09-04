# hookprobe — what a finished run leaves behind

A run that answers a question and vanishes has paid once for something the next
run will pay for again. These are the mechanisms that make the second
investigation of the same condition cheaper than the first, and the boundaries
that keep an agent from writing its own next instructions.

## The learning loop

The investigator is told to read prior case files, and the skills directory is
described as what earlier runs distilled — so the loop is only worth anything
if something writes one. `HOOKPROBE_AUTO_DISTILL_MAX` closes it: above 0, each
completed run assembles a runbook from its own record (the question, the tool
sequence in order, the conclusion) and installs it.

The write happens **in the service**, never through the agent's tools, and that
distinction is the whole design. Layer 4 above blocks the agent from writing
`.claude/` because a run that edits its own future instructions turns one
injected line into a permanent one. Automatic distillation is a different act
with a different failure mode.

**Runbooks update themselves.** The second investigation of the same condition
adds a case rather than replacing what was there — replacing would be
regression dressed as learning, since a run only knows its own steps and would
flatten a runbook that had already seen five incidents. Cases go newest first,
inside a marked region:

```markdown

## Investigations
<!-- hookprobe:cases -->
<!-- case:start … -->   ← a new investigation inserts here
```

Neither side is restricted. A run may update a runbook a person edited; a
person may edit one a run wrote. The invariant is not *who may write* but that
**no write destroys what was there**:

- automatic writes only insert into the case region, so anything outside it —
  your corrections, your own sections, the title, the caveat — is carried
  through untouched;
- **every** write, by run or by operator, snapshots the previous manifest into
  the runbook's `history/` first, so a bad one from either side is one file
  copy away from being undone;
- a runbook with no marker (hand-written, or older than this) is appended to,
  never reshaped to fit.

The rest of the terms:

- **Never from a run that failed, produced nothing, or changed its own
  inputs.** A run that already misbehaved does not get to leave instructions.
- **New runbooks are capped**, because each is prefix cost on every later run.
  The cap never stops an existing runbook from going on learning — that costs a
  case, not a new prefix entry. At the cap the loop stops creating; it never
  evicts, because something there may have been reviewed and this is not the
  code that gets to price that. The case list itself keeps the most recent five.
- **Stamped.** `origin.json` records every revision — who wrote it, when, from
  which session, on which model — and whether anyone has read it. An operator
  saving a runbook *is* the review, and flips it to `reviewed: true`; a later
  machine write flips it back, because the text changed and nobody looked.
  The skills page badges the unreviewed ones.

Every run records what the loop did — `{"installed": name}`,
`{"updated": name}` or `{"skipped": reason}` — so "it quietly did nothing
again" is not a state this can be in.

## Skills — the runner gets smarter

The deep-analysis prompt asks the agent to distill verified diagnostic paths
into reusable SKILL.md runbooks. Those land in `/data/.claude/skills` on the
persistent volume and are loaded into every later run. Back up the volume if
you care about the accumulated experience. The skills directory is plain
files — review it, prune bad runbooks, or `git init` it for history; anything
written there instructs future runs, so treat it as part of your trust
boundary.

Skills load in two layers. The **project layer** (`{workdir}/.claude/skills`,
on the volume) is always on — it is where the agent distills. The **user
layer** is an optional host library: point `HOOKPROBE_USER_SKILLS` at it in
`.env` (the prod compose mounts it read-only at `/data/home/.claude/skills`;
only a skills subdir — never a whole `~/.claude`, credentials live there) and
set `HOOKPROBE_SETTING_SOURCES=user,project`. A library that lives in the
repo of the service it drives (e.g. WebhookWise's `.claude/skills`) versions
with that service for free. A host library tends to be big, so
`HOOKPROBE_SKILLS` pins the session's skill list to named skills (or `all`);
it is a context filter, not a sandbox. The `/v1/skills` browser shows
exactly the layers the engine would load, tagged `project`/`user`. Two
honest caveats. A skill is instructions, not a runtime — host skills that
shell out to binaries the image does not carry will load and then fail at
the tool, so pin `HOOKPROBE_SKILLS` to the ones whose tools exist. And mount
the RESOLVED directory: skill libraries are often symlink farms
(`~/.claude/skills/x -> ../../.agents/skills/x`), and a bind mount carries
the links but not their targets — `readlink` one entry first and mount what
it points at.

The format is not ours and that is the point: a skill is a directory with a
`SKILL.md` (YAML frontmatter: `name`, `description`), the shape the whole
OpenClaw-lineage ecosystem shares. Marketplace packages install unchanged —
verified live with two from the OpenOcta market
(`https://openocta.com/api/v1/skills`, ~750 skills, strong ops section):
unzip into `/data/.claude/skills/<name>/` (strip `__MACOSX`), the next run
loads them, and the engine invoked `server-patrol` by name and followed its
runbook. The trust boundary above applies double to third-party skills:
read them before installing — they will be instructing an agent that holds
your read-only credentials.

## The family loop

Inside hookstack the investigator is wired into the alert flow itself: the
pipe's escalation routes copy every front-door event to `/hooks/event`, the
probe decides by level whether an investigation is worth paying for, and the
finished report POSTs back to the pipe's `probe-notify` door — dressed by the
pipe and delivered to the same channels as the verdict. Escalated
investigations also open the case files first: the task brief tells the agent
to grep `/data/results/` for earlier investigations of the same alert and
report how the last verdict held up — a recurrence gets "first seen 101
minutes ago, verdict matched, the P1 was not acted on", not a fresh start.

The event door is also where the budget breaker lives, because it is the one
path that spends money without a human asking. Set `HOOKPROBE_BUDGET_USD`
(with `HOOKPROBE_BUDGET_WINDOW_HOURS`, default 24): once the window's
recorded spend reaches the budget, new escalations are refused — but a
refusal is not a silent drop. It settles as a report-shaped run and returns
through the same loop, so the channels say *why* there is no investigation
("Budget breaker open…"). Idempotency still holds (a redelivered, already-funded event
is never refused), operator paths — `/hooks/agent`, follow-ups, the UI — are
never gated, and `GET /v1/budget` shows the window's arithmetic. The figure
counts recorded turns only, so in-flight runs can overshoot by at most
`max_concurrent` investigations: it is a brake, not an invoice.

It is also a **floor**, and says so. The engine reports dollars only on the
SDK's final result message, and a wall-clock timeout cancels the query before
one arrives — so a turn cut off by the clock records `None`, meaning *nobody
counted*, never `0.0`, meaning *free*. The money was really spent. Both
`/v1/budget` and the `hookprobe_unpriced_turns` metric carry that count beside
the spend, so the gap is visible rather than silent.

Recovering those dollars exactly would mean driving the SDK through
`ClaudeSDKClient` and `interrupt()`, which does yield a final result — a rewrite
at the one boundary no test touches (the suite injects fake engines and never
imports the SDK). Whether that is worth it depends entirely on what fraction of
turns land there, which is why the count is a metric series and not just a number
on a page: let the graph decide it after real traffic, not an argument before. The pipe stays
content-blind; the judge is untouched; failure still completes the loop (a
stopped, crashed, budget-refused — or restart-orphaned — investigation
reports itself: runs are checkpointed at spawn, and the next boot sweeps
whatever a dead process left mid-flight into failure reports). The plain demo compose
points the escalation at the sink's `/probe-standin` so the shape is visible
without a model key; `--profile probe` (plus `HOOKPROBE_EVENT_URL` in `.env`)
swaps in the real investigator.

### The card is not a dead end

A report reaches a person as a chat card, and for a while that was where the
loop stopped: asking a follow-up or approving the procedure the report proposed
meant leaving the chat, finding the console URL and presenting a bearer token —
at 3am, on a phone. So the returned payload now **declares** the actions its
report deserves, as `actions: [{kind, text, …}]` alongside `meta`/`analysis`:
`followup` when the run left a session that can be resumed, one `approve` per
proposal still waiting (with the **command** in the label — a button that does
not say what will run is a trap), and the ruling pair on every report including
the failures. Declaring is a request, not a guarantee: the pipe drops kinds it
is not configured to accept, and only channels that have callbacks render any of
them.

The division of labour is the family's usual one. This side judges *which*
actions a report earns; hookrelay mints the signed card token and owns the IM
callback, because a card token is channel edge — nothing here names a channel or
signs a button. A press comes back to `POST /hooks/action`, signed exactly like
the event door, and lands on the paths that already existed: the same
`continue` a console follow-up takes, the same `approve` behind its two gates.

Two properties carry the weight. **A press stands in for the operator's console
click and for nothing else** — the allowlist is a file an operator edits on the
host, no button, IM user or pipe can reach it, so a press has the blast radius
of a click and a denial comes back as a denial (what the press adds is a *who*,
which the console click never had, so the actor lands on the row as its
approving note). And **each press is claimed by `(correlation_id, kind, at)`
before anything happens** — with an `O_EXCL` create, so there is no window
between looking and acting — because `followup` starts a paid turn and `approve`
runs commands at a live target, while an IM platform retries any callback it did
not hear an answer to. A redelivery reads back the first press's answer.

First live run of the loop: a "host CPU high"
alert came in, the judge ruled it medium within a second, and 3.7 minutes
later the investigator's report landed on the same channels calling it a
false alarm — with the one actionable finding named.
