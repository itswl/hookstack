# Containment

An agent in hookstack is treated as an untrusted network service that costs
money, reads text an attacker may have written, and holds credentials. Every
boundary below exists because one of those three is true.

This page is the map. Each row's authority is the code named in the last column;
where the two disagree, the code is right.

The third column is the point of the page. A boundary whose limits are not
written down gets trusted for things it never claimed — the way a unit test
proving a shape check refuses a string a developer typed gets read as proof that
a real model cannot be steered into emitting a harmful one.

> **Measured, not only declared (2026-09-07).** The read-only boundary is the one boundary whose truth lives outside this
> repository — in a kubeconfig and an AWS profile an operator mounted. Every runner now checks them at startup
> (`hookprobe/posture.py`): what `kubectl auth can-i` and `iam simulate-principal-policy` say the identity may do, against
> the posture it declares. Declared `readonly` with credentials that can mutate refuses to start (`HOOKPROBE_POSTURE_CHECK=enforce`);
> a `danger-only` runner records its blast radius; an identity that cannot be asked is reported *unverifiable*, never confirmed.
> The measurement is served on `GET /v1/posture` and cited in every run's audit record.

## What holds, and what only helps

Every row below is a claim about a different kind of failure, and reading them
as one list is how a regex over model output gets trusted the way a signature
is. The split is stated here rather than left to the third column, because the
question an operator actually has — *if the model is hostile, which of these is
still standing?* — is not answerable by reading twenty-seven rows.

Nothing in this process is containment against a model that is genuinely
adversarial. The read-only credential is, and the row for the bash guard has
said so in those words since the day it was written: **the read-only credential
is what holds.** What follows sorts the rest honestly.

`scripts/assert_docs.py` requires every row in the table to appear in exactly
one of these four lists. A new boundary that nobody classified fails the gate —
otherwise this section becomes the prose that rots, which is what happened to
the mutation count one paragraph down.

### Holds against a hostile model

Cryptography, an operator's file, an identity check, or arithmetic the model
cannot reach. Its limits are in the third column; its key is not model-written.

- **Per-source HMAC** — a signature the model has no secret for.
- **Per-node credentials** — the real boundary, and the one that lives outside this repository.
- **Declared bypass lanes** — a payload picks from inside a set the config declared.
- **Verdict vocabulary** — a closed set; free text cannot widen it.
- **MCP tool allowlist** — a closed set; the model cannot name a server into it.
- **Tool gate reachability** — bounds the EXISTENCE of the heuristics below, not their content: a node whose gate cannot answer, or was never asked, stops.
- **Budget breaker** — arithmetic over a ledger the model does not write.
- **Card action signing** — a signed, single-use token.
- **Chat sender allowlist** — an identity at the door. What an allowed sender then pastes is the posture's problem, not this one's.
- **Chained audit** — tamper-evidence, bounded by needing two disks and two services.
- **The agent's bearer reads only** — the method check is in the door, not in a pattern.
- **Remediation allowlist** — deny-by-default over an operator's file. The residual is a pattern written too wide, which is the operator's text, not the model's.
- **Approved commands run in an allowlisted environment** — an allowlist, so a secret added later under an unknown name is excluded by default.
- **Approval window** — a clock. Weak, and not defeatable from inside a report.
- **Freshness cursor** — fields that arrive through the pipe; a report cannot move them.
- **Bridge inbound signature** — a signature, on the exact bytes.
- **Unattended retry caps** — counters the model does not increment.

### Reduces accidents, and an adversary defeats it

These read a string the model produced, or key on a field the model filled in.
They catch the over-eager model, which is the case that happens. Defeating one
is not a defect — see the scope section below.

- **Bash guard** — regexes over a command the model wrote. Its own row says the credential is what holds.
- **Input guard** — three write shapes, all of them patterns over model text; a redirect assembled from two variables gets through.
- **Memory shape check** — a shape, not a truth: a benign-shaped line that is simply wrong passes.
- **High-risk allowlist** — **keyed on `risk`, which the MODEL writes.** A dangerous step labelled `low` never meets it; the ordinary allowlist is what bounds that case.
- **Target cooldown** — **keyed on `target` and the command text, both MODEL-written.** A step that varies its command and relabels its target is not cooled.

### Enforced at config load, and not about the model at all

Structural properties of a configuration. An operator can still choose wrong;
these only make the choice explicit and refuse the incoherent ones.

- **`on_error` per stage** — makes fail-open versus fail-closed a stated choice.
- **Inline timeout cap** — refuses a config that would hold a sender's connection.
- **Topology invariants** — refuses a graph with an unreachable door or a starved exit.
- **Delivery method allowlist** — refuses a verb at config load.

### Named here as a residual, not a boundary

- **The watcher holds a signing key** — it stops nothing, and the row says so. It is in the table so the residual is visible rather than absent.

## What is not a defect

The distinction above decides what is worth reporting as a failure of this
design and what is the design working as documented.

**Not a defect, because the row already says so:**

- Defeating a heuristic: a bash-guard regex bypass, an input-guard write shape
  assembled at runtime, a secret-shape miss.
- A model omitting or relabelling a field a heuristic keys on — an absent
  `target` escaping the cooldown, a dangerous step self-labelled `low`. A gate
  whose key its subject writes can only add a requirement; it was never able to
  catch a lie, and both rows say so.
- Anything an allowed sender does within the posture the node declares.
- An operator's allowlist pattern written too wide. The file is the decision;
  the gate only holds it.

**A defect, and this is where the effort goes:**

- A row behaving contrary to its own third column.
- A heuristic that has silently stopped running. This is the one place this
  project spends more than the split above would justify — `gate.verify`,
  `/v1/selftest` and the mutation list exist because a guard that quietly
  stopped catching anything is indistinguishable from one with nothing to
  catch, and that has happened here more than once.
- Credentials wider than the posture a node declares (`posture.py` refuses to
  start under `enforce`).
- A refusal the refused party cannot see. A gate whose denial reaches nobody
  did not gate anything; it only lost the work.

**Outside the supported posture:**

- A node declaring `readonly` with wider credentials and `HOOKPROBE_POSTURE_CHECK=off`.
- The bridge on a shared container network with `BRIDGE_INBOUND_SECRET` empty —
  the shipped default, and why both deployments set it.
- Treating anything in the second list as the thing that holds when the input
  surface is untrusted.

## The boundaries

Every row here is a claim, and a claim is worth what its test is worth. `scripts/assert_guards_are_tested.py` breaks a subset of them on purpose and requires a test to fail for each; `--list` prints which, and the gate runs it. A mutation that survives is a guarantee nobody is really testing. The list is not repeated here on purpose — this sentence said "twelve" and named nine while the script carried seventeen, which is what a count with two homes does.


| Boundary | Stops | Does NOT stop | Enforced in |
|---|---|---|---|
| **Per-source HMAC** — every door verifies `sha256(secret, "{ts}.{body}")` | An unsigned or forged event reaching a door | A replay inside `max_skew_seconds` (300): the window is **limited, not closed**, and there is no nonce cache | `hookrelay/security.py`, `hookprobe/wire.py` |
| **Per-node credentials** — each process-external node gets its own secrets, budget and guards | One node's compromise becoming every node's | Anything inside a node once it is compromised | the compose files; `docs/containment.md` is not a runtime |
| **Declared bypass lanes** — config names the channel set, a payload can only pick from inside it | An injected message routing itself around a gatekeeper | A node with a legitimate lane sending the wrong thing down it | `hookrelay/config.py`, `routing.py` |
| **`on_error` per stage** — `pass` (fail open) or `drop` (fail closed), chosen per stage, never defaulted globally | A stage's failure silently changing policy | Choosing wrong: this makes the choice explicit, not correct | `hookrelay/processors.py` |
| **Inline timeout cap** — 10s, refused at config load | An inline decider holding the sender's connection until it retries and duplicates the alert | A node that answers in 9s and is still the wrong place for the work | `hookrelay/processors.py` (`MAX_INLINE_TIMEOUT_SECONDS`) |
| **Verdict vocabulary** — `HOOKPROBE_VERDICTS`, closed set, empty denies all | An investigator's free text choosing which lane spends money | A wrong choice among lanes the operator already declared | `hookprobe/reports.py` (`verdict`) |
| **MCP tool allowlist** — `HOOKPROBE_MCP_TOOLS`, closed set, empty denies all | A chat message asking the agent to use a write tool on a mounted server | A tool that is read-only in name and not in effect — that judgement is the operator's and nothing here can check it | `hookprobe/engine.py` (`mcp_deny_reason`) |
| **Bash guard** — under `readonly`, mutating verbs of known CLIs, `aws` inverted to deny-unless-read; under `danger-only`, only what no credential scope can undo | An over-eager model running `kubectl delete` or `aws s3 rm` | An adversary: the rules need a binary and a verb in one pipeline segment, and separating them gets through. **The read-only credential is what holds** | `hookprobe/guard.py`, `gate.py` |
| **Input guard** — writes to the files that steer the next run are refused: `CLAUDE.md` and `AGENTS.md`, `.claude/`, the system prompt, and the records a run could edit to flatter itself (`audit/`, `telemetry/`, `remediation/`, `actions/`). Three shapes are checked, because a write arrives in three: an edit tool's path argument, the paths inside a codex `apply_patch` envelope, and a shell command that redirects, `tee`s, `sed -i`s, `cp`s or `mv`s into one of them | A run installing its own runbook as a persistence vector. All three shapes were measured getting through before they were closed: `apply_patch` because the guard read argument keys and codex puts its paths inside a patch string, `AGENTS.md` because the list only knew Claude's name for the steering file, and the shell redirect on **every** runtime under **both** postures | A redirect assembled at runtime out of two variables, or any of the endless other ways a shell can name a file. This catches the obvious forms — the over-eager model, which is the case that happens — and not a determined adversary. Writes anywhere else in the workspace, which are allowed on purpose | `hookprobe/inputs.py`, `gate.py` (`patch_targets`, `shell_write_target`) |
| **Tool gate reachability** — before its first turn a node requires its gate to refuse a call no posture permits, and after every turn it requires the flight recorder to show the gate was actually asked; failing either stops the node | A node running with no gate at all while reporting one. Not hypothetical: the first live run of a spawned-gate adapter had no gate, because the hook command could not import hookprobe — codex logged it and carried on, and a `kubectl delete` ran to completion on a node whose /v1/agent said `bash_guard: readonly`. The second half catches the other shape of the same failure: a gate that answers perfectly and is never consulted, which is what driving codex through its app-server does | Anything that can write into the run's own volume. Codex trusts hooks only after a person approves them and `exec` has nobody to ask, so the adapter passes `--dangerously-bypass-hook-trust` and vets the source by writing it: whoever else can put a file in that directory installs a hook that runs as the probe | `hookprobe/gate.py` (`verify`), `engine_codex.py`, `engine_pi.py` |
| **Memory shape check** — a suggested fact whose SHAPE could act waits for a human | An instruction smuggled through alert text into `CLAUDE.md` | A benign-shaped line that is simply wrong | `hookprobe/suggestions.py` |
| **Budget breaker** — window spend ≥ ceiling refuses new autonomous runs | An unattended loop spending without limit | Operator-driven doors, which are never gated | `hookprobe/service.py` |
| **Card action signing** — a button carries a signed single-use token | Anyone in a group chat pressing on your behalf | Nothing, when `HOOKRELAY_ACTION_SECRET` is empty: the buttons render and do nothing | `hookrelay/actions.py`, `app.py` |
| **The watcher holds a signing key** — probe-watch signs its findings into the pipe's watch door | Nothing. It is listed here because it is the residual, not a boundary | A signal it FABRICATES. The door authenticates "a watcher", never "a truthful watcher", and the agent must be able to sign or it cannot do its job. What bounds it: a fabricated `high`+`task` buys one investigation against a budget ceiling, and the round's own case file records what it saw when it decided | `deploy/docker-compose.work.yml`, `.agents/notes/proposed/2026-09-02-the-agent-shares-the-services-secrets.md` |
| **Topology invariants** — no unreachable door, starved exit, or wildcard a return door can reach | A config change silently feeding a brain its own output | A graph that is legal and still wrong | `scripts/assert_topology.py`, `hookrelay/topology.py` |
| **Chat sender allowlist** — a reply in a chat thread continues an investigation, and a top-level @-mention opens one, only when the sender is in `HOOKPROBE_FOLLOW_UP_SENDERS` (and, for a reply, the pipe resolved the thread to a card it sent); one turn per message id, at most 20 per run, under the budget breaker | Anyone in the group (or anyone who can post to the bridge's door) starting paid turns; a redelivered message being answered twice | An allowed sender pasting an injection into the thread: the turn runs under the same read-only posture and guards as the first, and no more. **The posture is what holds** | `hookprobe/events.py`, `hookrelay/processors.py` (`thread_lookup`) |
| **Chained audit** — every flight-recorder line carries the hash of the one before it; `verify_chain` names the first line that stops adding up, and `/v1/selftest` walks it | A record quietly tidied after the fact. The audit is what an investigation is reconstructed from and what a compliance reader asks about, and an append-only file cannot tell a line rewritten later from one written that way. Edits, deletions and reorderings are all caught, because each line names its predecessor rather than only hashing itself | Somebody who can rewrite the audit here AND reach the pipe's ledger. Rewriting `.chain` alone is not enough since 2026-09-10: every report carries `meta.audit_head` off-box, so a rebuilt chain is locally consistent and still fails `chain_anchored` against a head another service recorded hours earlier. Two disks and two services is the bound, not proof. Lines from before the chain existed are counted, never treated as a break — an alarm that fired on all existing history would be ignored by day two | `hookprobe/gate.py` (`append_audit`, `verify_chain`), `selftest.py` |
| **The agent's bearer reads only** — the console's `HOOKPROBE_TOKEN` is blanked out of the agent's subprocess and replaced with `HOOKPROBE_AGENT_TOKEN`, which this API refuses on every method but `GET` | An injected instruction reaching a Bash step and using the service's own write surface: `PUT /v1/memory` (no shape check, unlike the suggestion path), `PUT /v1/skills`, `/v1/system-prompt`, `POST /v1/remediations/{id}/approve`, and `POST /hooks/agent`, which spends money. The regret route already *claimed* this — "not reachable by any run… the agent's subprocess holds no token" — and the claim was false for its whole life, because the token was inherited | Everything a `GET` exposes, which is most of this service's own state; and anything the agent can do without this API, which is the whole shell. It removes a write surface, not the run | `hookprobe/settings.py` (`agent_token`), `app.py` (`require_token`), `gate.py` (`agent_credentials`) |
| **Remediation allowlist** — `HOOKPROBE_REMEDIATION_ALLOWLIST`, full-match regexes, one per line, hot-read; no file means nothing runs | The one path that executes anything running a command nobody wrote down. Checked twice: over the WHOLE procedure at approval, because a half-executed 1-2-3 is the worst outcome, and again immediately before each step, so tightening the file during an incident stops the remaining ones | A command that matches a pattern an operator wrote too widely. The file is the decision; this only holds it. Nor does it stop the model PROPOSING anything — proposing is free, and refusing at the gate is what makes it safe to read | `hookprobe/remediation.py` (`deny_reason`, `approve`, `execute`) |
| **Approved commands run in an allowlisted environment** — `execution_env()` passes `PATH`/`HOME`/locale, the cloud credentials an operator mounted for the purpose (`AWS_*`, `KUBE*`, `GOOGLE_*`, `AZURE_*`) and the egress proxy variables, and nothing else | A procedure reading the family's HMAC signing keys, the Lark app secret or the provider credential out of its own environment. Until 2026-09-10 `create_subprocess_exec` passed no `env` at all, so an approved command inherited the whole service environment — the agent's shell was scrubbed for exactly this argument and the one path that actually EXECUTES had never been | A command the allowlist permits doing something with the credentials it IS given — that is the allowlist's job, not this one's. An allowlist rather than a scrub here on purpose: a denylist cannot cover the secret a future deployment adds under a name nobody has written down | `hookprobe/remediation.py` (`execution_env`) |
| **High-risk allowlist** — a step the report marked `risk: high` must full-match a pattern in `HOOKPROBE_REMEDIATION_HIGH_RISK_ALLOWLIST` as well as in the ordinary one; deny-by-default in the same direction, so the strict file being absent refuses every high-risk step rather than waving it through. Both lists are re-read before each step, and one uncovered high step refuses the whole procedure at the click | Arming remediation from also arming its worst half by the same gesture: an operator who writes `kubectl rollout restart .*` to cover restarts has not thereby licensed the steps the model itself calls dangerous | **The case that matters most: a dangerous command the report labelled `low`.** The risk label is written by the MODEL, so this gate can only ADD a requirement, never catch a missing one — a mislabelled step is bounded by the ordinary allowlist exactly as it was before this row existed. Read it as "the operator may reserve a stricter list for what the model is willing to call dangerous", not as "dangerous commands need two patterns" | `hookprobe/remediation.py` (`step_deny_reason`, `approve`, `execute`) |
| **Approval window** — a proposal is unrunnable 24h after it was made, the same span the pipe gives a card's action token | Commands chosen from evidence that has since moved being run by a click on a week-old card. Refused at the gate with the age in the message, and the board stops offering it | An approval at 23 hours on evidence that went stale in one. The window is a bound on how wrong it can be; the row below is the part of that gap a clock cannot close | `hookprobe/remediation.py` (`APPROVAL_WINDOW_SECONDS`, `stale`) |
| **Freshness cursor** — a proposal records what was true about its condition when the steps were chosen (has it ENDED, and has the investigation taken another turn); if either has moved by the time somebody presses, the procedure is retired instead of run, and the refusal returns to the chat as a report | Approving a fix for a condition that already cleared, or running steps lifted from a report the investigation has since superseded. Both were reachable at any age under the window alone, because a clock assumes the world moves at a rate. Checked in two places: the button is not drawn when the cursor has already moved, and the approval is refused for the race the card cannot see | Anything that happened outside this pipe. Every field arrives through it — nothing here reaches out to look, and it must not: the production investigator holds no credentials for the systems it writes procedures about, and a freshness probe that opened one would be a second, unaudited way of touching them. Somebody fixing the target by hand without saying so in chat is invisible to this | `hookprobe/remediation.py` (`cursor`, `moved`), `actions.py`, `service.py` (`report_superseded`) |
| **Target cooldown** — after a procedure acts, a second procedure sharing its declared `target` **or** its literal command is refused for 15 minutes (`HOOKPROBE_REMEDIATION_COOLDOWN_SECONDS`, 0 disables), and one already `running` holds its target with no window at all | A fix and the rollback of that fix being approved a minute apart, and two sequences interleaving their steps against one host. The allowlist gates WHAT may run and the click gates WHO may run it; nothing gated HOW OFTEN, and a remediation loop that flaps turns one incident into an outage. Held, not retired: the row stays `proposed` and the same press works after the window | **Keys the model writes.** Both halves are free text from the report, and a step that varies its command by a flag AND labels its target differently is not cooled. Two keys rather than one because production disproved the first version: the five proposals on the deployment name one thing three ways — `AWS SES 账户状态`, `AWS SES account status`, `AWS SES 账户状态（只读）` — while running character-identical commands, so a target-only key would have cooled nothing. The command is the half that holds: `execute()` runs it verbatim from one container, so identical strings are the same action however they were labelled. Like the input guard this catches the over-eager model, which is the case that happens, not an adversary — and it counts only what THIS node ran: somebody fixing the same box by hand is invisible to it, the same blind spot the freshness cursor has | `hookprobe/remediation.py` (`cooldown_keys`, `cooling`), `actions.py`, `service.py` (`report_cooling`) |
| **Delivery method allowlist** — a channel may say `POST`, `PUT` or `PATCH`; anything else is refused at config load | A write-back channel configured with `DELETE`, or a `GET` whose side effect fires when a chat client fetches a link preview — the same trap `/card-action` refuses to fall into | A `POST` that does damage at the receiver. The pipe checks the verb it is asked for, never what the far side does with it | `hookrelay/config.py` (`_WRITE_METHODS`) |
| **Bridge inbound signature** — a card must carry `X-Hook-Timestamp` + `X-Hook-Signature` over the exact bytes, within five minutes | A neighbour on the shared container network posting a card into the operator's chat AS the application — the bridge's port has no other guard | Anything when `BRIDGE_INBOUND_SECRET` is empty, which is the shipped default and is why both deployments set it; and a replay inside the five minutes, like every other signature here | `deploy/lark-bridge/bridge.py` (`is_authentic_protocol`) |
| **Unattended retry caps** — one resume per run after a restart, one automatic retry per turn after a transient provider error, both under the budget breaker | A crash loop becoming a spend loop: a process that dies on every boot buys one continuation, not one per restart forever | The first duplicate charge. Each cap allows exactly one more attempt, which is the point — what it bounds is the loop, not the pair | `hookprobe/service.py` (`_MAX_RESUMES`, `_MAX_AUTO_RETRIES`) |

## Two rules that decide the rest

**Closed when unconfigured.** A security door with no secret refuses everyone
rather than admitting anyone: `hookjudge`'s `/rulings/ai` answers 503,
`HOOKPROBE_MCP_TOOLS` empty denies every MCP tool, an empty
`HOOKRELAY_ADMIN_TOKEN` makes the admin surface refuse rather than open. The
exception is the read token, which fails open and is documented where it does.

**Plumbing and policy are separate settings.** Mounting an MCP server does not
grant its tools; mounting a credential directory does not document what may be
done with it; delivering an event to an investigator does not decide that it is
worth paying for. Each pair is two knobs because the second one is a judgement
somebody has to make in words.

## Where this is verified against a real model

Unit tests prove the guards refuse strings a developer wrote. That is not the
same claim as "a model cannot be steered into producing one", and the difference
is the whole reason these two exist and run on the deploy host, where a provider
key and the real image meet:

- `hookjudge` fences prompt injection in its eval golden set — its first catch
  was the judge obeying "classify as low" embedded in a real incident, 2 votes
  of 3, with the boundary prose fully present.
- `hookprobe/scripts/redteam_memory.py` drives injections through the
  investigator and asserts what actually reached `CLAUDE.md`. Run it after any
  change to `suggestions.py` or the memory-apply path.

The MCP allowlist has no equivalent yet. The cheap version — drive an injection
that names a declared tool and assert what was called — is worth adding the day
a deployment turns a vocabulary on for a server with write tools.
