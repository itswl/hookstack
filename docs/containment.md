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
still standing?* — is not answerable by reading the table top to bottom.

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

- **Signatures at every door** — HMACs the model has no secret for: the pipe's doors, a card's buttons, the bridge's inbound.
- **Per-node credentials, and the agent's bearer reads only** — the real boundary, which lives outside this repository; inside a node the agent's own token can only `GET`.
- **Closed sets at the doors** — a payload, a verdict or a tool call picks from inside a set the config declared; free text cannot widen it. The MCP list binds the agent's TOOL calls and not its sockets, which is why the row below exists.
- **The chat's tools are on a list in another container** — a network with no route, and a list the agent's process cannot edit. The tools that write are not refused, they are unreachable; a hostile model has nowhere to send the request.
- **The watcher's signature is held where no agent runs** — the key is in another container and the check in front of it is against a file the agent does not write. What it bounds is which conversations can carry a signal, not whether one is honest.
- **Tool gate reachability** — bounds the EXISTENCE of the heuristics below, not their content: a node whose gate cannot answer, or was never asked, stops.
- **Budget breaker and retry caps** — arithmetic over a ledger and counters the model does not write.
- **Chat sender allowlist** — an identity at the door. What an allowed sender then pastes is the posture's problem, not this one's.
- **Chained audit** — tamper-evidence, bounded by needing two disks and two services.
- **What an approved procedure may run, and with what** — deny-by-default over an operator's file, an allowlisted environment, and a declared blast radius measured against the cluster and IAM. The residual is a pattern or a grant written too wide, which is the operator's text, not the model's.
- **Approval window, freshness cursor and the version pressed** — a clock, fields that arrive through the pipe, and a digest carried in the pipe's signed token or drawn by the console; a report cannot move any of them.

### Reduces accidents, and an adversary defeats it

These read a string the model produced, or key on a field the model filled in.
They catch the over-eager model, which is the case that happens. Defeating one
is not a defect — see the scope section below.

- **Bash guard and input guard** — regexes over a command the model wrote, and three write shapes over the files that steer the next run. The row says the credential is what holds.
- **Memory shape check** — a shape, not a truth: a benign-shaped line that is simply wrong passes.
- **High-risk allowlist and target cooldown** — **keyed on `risk`, `target` and the command text, which the MODEL writes.** A dangerous step labelled `low` never meets the strict list; a step that varies its command and relabels its target is not cooled.

### Enforced at config load, and not about the model at all

Structural properties of a configuration. An operator can still choose wrong;
these only make the choice explicit and refuse the incoherent ones.

- **Refused at config load** — fail-open versus fail-closed as a stated choice per stage, an inline decider's timeout capped, a graph with an unreachable door or a starved exit refused, a write-back verb that is not a write refused.

### Named here as a residual, not a boundary

Nothing, since 2026-09-30. The watcher's signing key was the one residual this page named, and it left the watcher's reach for `watch-signer` that day; the row that named it went with it.

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
| **Signatures at every door** — every pipe door verifies `sha256(secret, "{ts}.{body}")`; a card's button carries a signed single-use token; the bridge accepts a card only with `X-Hook-Timestamp` + `X-Hook-Signature` over the exact bytes, within five minutes | An unsigned or forged event reaching a door; anyone in a group chat pressing on your behalf; a neighbour on the shared container network posting a card into the operator's chat AS the application — the bridge's port has no other guard | A replay inside the window (`max_skew_seconds`, 300; five minutes at the bridge): **limited, not closed**, and there is no nonce cache. Nothing when a secret is empty: with `HOOKRELAY_ACTION_SECRET` unset the buttons render and do nothing, and with `BRIDGE_INBOUND_SECRET` unset — the shipped default — the bridge's port is open, which is why both deployments set it | `hookrelay/security.py`, `hookprobe/wire.py`, `hookrelay/actions.py`, `app.py`, `deploy/lark-bridge/bridge.py` (`is_authentic_protocol`) |
| **Per-node credentials, and the agent's bearer reads only** — each process-external node gets its own secrets, budget and guards; inside a node the console's `HOOKPROBE_TOKEN` is blanked out of the agent's subprocess and replaced with `HOOKPROBE_AGENT_TOKEN`, which the API refuses on every method but `GET` | One node's compromise becoming every node's; an injected instruction reaching a Bash step and using the service's own write surface: `PUT /v1/memory` (no shape check, unlike the suggestion path), `PUT /v1/skills`, `/v1/system-prompt`, approving a remediation, and `POST /hooks/agent`, which spends money. The regret route *claimed* this for its whole life while the token was inherited | Anything inside a node once it is compromised; everything a `GET` exposes, which is most of a node's own state; and anything the agent can do without this API, which is the whole shell. The real boundary is the credential each node is given, and that lives outside this repository | the compose files; `hookprobe/settings.py` (`agent_token`), `app.py` (`require_token`), `gate.py` (`agent_credentials`) |
| **Closed sets at the doors** — config names the channel set a payload may pick from (the bypass lanes); `HOOKPROBE_VERDICTS` and `HOOKPROBE_MCP_TOOLS` are closed sets, and empty denies all | An injected message routing itself around a gatekeeper; an investigator's free text choosing which lane spends money; a chat message asking the agent to use a write tool on a mounted server | A wrong choice among lanes the operator already declared. **A socket.** The MCP allowlist is enforced where the agent asks for a TOOL and says nothing about a request the agent makes itself: measured on 2026-09-30, one plain HTTP POST from inside the watcher reached the chat client's own MCP port, with no credential, and called a tool this list does not name. The row below is the answer; this one alone was advice | `hookrelay/config.py`, `routing.py`; `hookprobe/reports.py` (`verdict`), `engine.py` (`mcp_deny_reason`) |
| **The chat's tools are on a list in another container** — the probes sit on a docker network with `internal: true`, so they have no route to the host or the internet at all; `mcp-gate` holds the only reach to the chat client and forwards a `tools/call` only when the tool's name is on the calling node's list, recording both halves | An agent that goes around its own MCP allowlist with an HTTP request, which is what the row above could not stop. The writes are not refused, they are unreachable: the watcher sees 8 read tools where the server offers 34, 20 of them writes | Anything a PERMITTED read does: an argument is not checked, and what a read returns still goes to the model provider. The server-to-client stream is declined rather than relayed (405), so a chat server that pushed notifications would not reach the agent through this. A client token lives in the agent's own MCP config by construction — it admits exactly the tools that agent already had, which is the point of the list being here rather than there | `deploy/sidecars/gate.py`, `deploy/docker-compose.work.yml` (`probe_net`) |
| **The watcher's signature is held where no agent runs** — `watch-signer` holds `WATCH_INGEST_SECRET`; the watcher posts its signal unsigned to it, and it signs only a signal naming a conversation the scanner offered THIS round, with a level and kind from a closed set, text cut to a length, and a ceiling per round | A round steered by a colleague's message manufacturing a request from a conversation nobody scanned — at `high`, as a `task`, which buys a paid planner run and a card the operator reads as real. Until 2026-09-30 the secret sat in a file the agent could read, so the door's only check answered yes to whatever the round chose to say | A round that read a real conversation and describes it dishonestly: the offer bounds WHICH conversations can carry a signal, not whether the signal is true. Nothing here is a judgement about content, and a deployment with no prescan states no offer, which is passed through rather than read as an offer of nothing. The producer half of an origin is a closed set only where `WATCH_SIGNER_PRODUCERS` names one (the work compose does: the chat tool, Jira, the scanner); the scanner's own `scanner-notes` subject is admitted without an offer at `low`/`note`, so a round can always post one cheap note; and the offer is this round's or the round before it, so a run that outlasts a tick is checked against what it was handed, one round wide. Its ledger is also the record of what was reported (conversation and offered cursor); `scripts/assert_watch_signed.py` reads it against the pipe's ledger once per tick, and a signal in the pipe with no signer row is the one thing it pages about | `deploy/sidecars/signer.py`, `scripts/post_watch_signal.py` |
| **Tool gate reachability** — before its first turn a node requires its gate to refuse a call no posture permits, and after every turn it requires the flight recorder to show the gate was actually asked; failing either stops the node | A node running with no gate at all while reporting one. Not hypothetical: the first live run of a spawned-gate adapter had no gate, because the hook command could not import hookprobe — codex logged it and carried on, and a `kubectl delete` ran to completion on a node whose /v1/agent said `bash_guard: readonly`. The second half catches the other shape of the same failure: a gate that answers perfectly and is never consulted, which is what driving codex through its app-server does | Anything that can write into the run's own volume. Codex trusts hooks only after a person approves them and `exec` has nobody to ask, so the adapter passes `--dangerously-bypass-hook-trust` and vets the source by writing it: whoever else can put a file in that directory installs a hook that runs as the probe | `hookprobe/gate.py` (`verify`), `engine_codex.py`, `engine_pi.py` |
| **Budget breaker and retry caps** — window spend ≥ ceiling refuses new autonomous runs; one resume per run after a restart and one automatic retry per turn after a transient provider error, both under the breaker | An unattended loop spending without limit; a crash loop becoming a spend loop — a process that dies on every boot buys one continuation, not one per restart forever | Operator-driven doors, which are never gated; and the first duplicate charge: each cap allows exactly one more attempt, which is the point — what it bounds is the loop, not the pair | `hookprobe/service.py` (`_MAX_RESUMES`, `_MAX_AUTO_RETRIES`) |
| **Chat sender allowlist** — a reply in a chat thread continues an investigation, and a top-level @-mention opens one, only when the sender is in `HOOKPROBE_FOLLOW_UP_SENDERS` (and, for a reply, the pipe resolved the thread to a card it sent); one turn per message id, at most 20 per run, under the budget breaker | Anyone in the group (or anyone who can post to the bridge's door) starting paid turns; a redelivered message being answered twice | An allowed sender pasting an injection into the thread: the turn runs under the same read-only posture and guards as the first, and no more. **The posture is what holds** | `hookprobe/events.py`, `hookrelay/processors.py` (`thread_lookup`) |
| **Chained audit** — every flight-recorder line carries the hash of the one before it; `verify_chain` names the first line that stops adding up, and `/v1/selftest` walks it | A record quietly tidied after the fact. The audit is what an investigation is reconstructed from and what a compliance reader asks about, and an append-only file cannot tell a line rewritten later from one written that way. Edits, deletions and reorderings are all caught, because each line names its predecessor rather than only hashing itself | Somebody who can rewrite the audit here AND reach the pipe's ledger. Rewriting `.chain` alone is not enough since 2026-09-10: every report carries `meta.audit_head` off-box, so a rebuilt chain is locally consistent and still fails `chain_anchored` against a head another service recorded hours earlier. Two disks and two services is the bound, not proof. Lines from before the chain existed are counted, never treated as a break — an alarm that fired on all existing history would be ignored by day two | `hookprobe/gate.py` (`append_audit`, `verify_chain`), `selftest.py` |
| **What an approved procedure may run, and with what** — `HOOKPROBE_REMEDIATION_ALLOWLIST` (full-match regexes, one per line, hot-read; no file means nothing runs), checked over the WHOLE procedure at approval and again before each step; `execution_env()` passes `PATH`/`HOME`/locale, the cloud credentials an operator mounted for the purpose (`AWS_*`, `KUBE*`, `GOOGLE_*`, `AZURE_*`) and the egress proxy variables, and nothing else; a WRITING node names what its credential may do (`HOOKPROBE_BLAST_RADIUS`, one measured line per entry as `/v1/posture` reports it) and under `enforce` a credential that can do more refuses to start, naming the excess | The one path that executes anything running a command nobody wrote down, or a half-executed 1-2-3 when the file is tightened mid-incident; a procedure reading the family's HMAC keys, the Lark app secret or the provider credential out of its own environment — until 2026-09-10 `create_subprocess_exec` passed no `env` at all; a writing node quietly growing, which under `danger-only` had nothing to be compared against for its whole life | A pattern an operator wrote too widely — the file is the decision, this only holds it, and the model proposing anything stays free. A permitted command doing something with the credentials it IS given. Anything inside the declared radius, a grant that was unwise (`iam:*` declared is `iam:*`), and a credential widened while the node runs, which is caught at the next boot and by the hourly selftest, not at the moment it changes | `hookprobe/remediation.py` (`deny_reason`, `approve`, `execute`, `execution_env`), `posture.py` (`declared_radius`, `beyond`, `verdict`) |
| **Approval window, freshness cursor and the version pressed** — a proposal is unrunnable 24h after it was made (the span the pipe gives a card's token), and it records what was true about its condition when the steps were chosen — has it ENDED, has the investigation taken another turn; if either has moved by the time somebody presses, the procedure is retired instead of run, the button is not drawn, and the refusal returns to the chat as a report. And a press names WHICH proposal it approves: the digest of its steps, run, clock and cursor as they were shown — inside the card's signed token, or as the console listed them — and a press naming none or another version is refused with the row untouched; the approved digest goes on the row | Commands chosen from evidence that has since moved being run by a click on a week-old card; approving a fix for a condition that already cleared, or running steps lifted from a report the investigation has since superseded — both reachable at any age under a clock alone, because a clock assumes the world moves at a rate; and commands nobody saw running under an approval of others — a proposal rewritten under the same id between the reading and the press | An approval at 23 hours on evidence that went stale in one is what the cursor is for; what neither sees is anything that happened outside this pipe. Nothing here reaches out to look, and it must not — the production investigator holds no credentials for the systems it writes procedures about — so somebody fixing the target by hand without saying so in chat is invisible | `hookprobe/remediation.py` (`APPROVAL_WINDOW_SECONDS`, `stale`, `cursor`, `moved`, `content_hash`, `Changed`), `actions.py`, `events.py`, `service.py` (`report_superseded`) |
| **Bash guard and input guard** — under `readonly`, mutating verbs of known CLIs with `aws` inverted to deny-unless-read, under `danger-only` only what no credential scope can undo; and writes to the files that steer the next run (`CLAUDE.md` and `AGENTS.md`, `.claude/`, the system prompt, `audit/`, `telemetry/`, `remediation/`, `actions/`) refused in the three shapes a write arrives in — an edit tool's path argument, the paths inside a codex `apply_patch` envelope, and a shell command that redirects, `tee`s, `sed -i`s, `cp`s or `mv`s into one of them | An over-eager model running `kubectl delete` or `aws s3 rm`; a run installing its own runbook as a persistence vector. All three write shapes were measured getting through before they were closed: `apply_patch` because the guard read argument keys and codex puts its paths inside a patch string, `AGENTS.md` because the list only knew Claude's name for the steering file, and the shell redirect on **every** runtime under **both** postures | An adversary: the rules need a binary and a verb in one pipeline segment, and separating them gets through; a redirect assembled at runtime out of two variables, or any of the endless other ways a shell can name a file. These catch the obvious forms — the over-eager model, which is the case that happens. Writes anywhere else in the workspace are allowed on purpose. **The read-only credential is what holds** | `hookprobe/guard.py`, `inputs.py`, `gate.py` (`patch_targets`, `shell_write_target`) |
| **Memory shape check** — a suggested fact whose SHAPE could act waits for a human | An instruction smuggled through alert text into `CLAUDE.md` | A benign-shaped line that is simply wrong | `hookprobe/suggestions.py` |
| **High-risk allowlist and target cooldown** — a step the report marked `risk: high` must also full-match a pattern in `HOOKPROBE_REMEDIATION_HIGH_RISK_ALLOWLIST`, deny-by-default in the same direction, both lists re-read before each step and one uncovered high step refusing the whole procedure at the click; after a procedure acts, a second one sharing its declared `target` **or** its literal command is refused for 15 minutes (`HOOKPROBE_REMEDIATION_COOLDOWN_SECONDS`, 0 disables), and one already `running` holds its target with no window at all | Arming remediation from also arming its worst half by the same gesture — `kubectl rollout restart .*` written to cover restarts has not licensed the steps the model itself calls dangerous; a fix and the rollback of that fix approved a minute apart, and two sequences interleaving their steps against one host. Held, not retired: the row stays `proposed` and the same press works after the window | **Keys the model writes.** A dangerous command the report labelled `low` never meets the strict list — the risk label is the MODEL's, so this can only ADD a requirement, and a mislabelled step is bounded by the ordinary allowlist exactly as before. A step that varies its command by a flag AND labels its target differently is not cooled; two keys because production named one target three ways while running character-identical commands, so the command half is the one that holds. Both count only what THIS node ran — somebody fixing the same box by hand is invisible, the same blind spot the freshness cursor has | `hookprobe/remediation.py` (`step_deny_reason`, `cooldown_keys`, `cooling`), `actions.py`, `service.py` (`report_cooling`) |
| **Refused at config load** — `on_error` per stage (`pass` or `drop`, chosen per stage, never defaulted globally); an inline decider's timeout capped at 10s; no unreachable door, starved exit, or wildcard a return door can reach; a write-back channel may say `POST`, `PUT` or `PATCH` and nothing else | A stage's failure silently changing policy; an inline decider holding the sender's connection until it retries and duplicates the alert; a config change silently feeding a brain its own output; a channel configured with `DELETE`, or a `GET` whose side effect fires when a chat client fetches a link preview — the trap `/card-action` refuses to fall into | Choosing wrong inside what is legal: an explicit fail-open, a node that answers in 9s and is still the wrong place for the work, a graph that is legal and still wrong, a `POST` that does damage at the receiver — the pipe checks the verb it is asked for, never what the far side does with it | `hookrelay/processors.py` (`MAX_INLINE_TIMEOUT_SECONDS`), `config.py` (`_WRITE_METHODS`), `topology.py`, `scripts/assert_topology.py` |

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
is the whole reason these two exist and run where a provider key and the real
image meet — the deploy host while there was one, a live deployment since:

- `hookjudge` fences prompt injection in its eval golden set — its first catch
  was the judge obeying "classify as low" embedded in a real incident, 2 votes
  of 3, with the boundary prose fully present.
- `hookprobe/scripts/redteam_memory.py` drives injections through the
  investigator and asserts what actually reached `CLAUDE.md`. Run it after any
  change to `suggestions.py` or the memory-apply path.

The MCP allowlist has no equivalent yet. The cheap version — drive an injection
that names a declared tool and assert what was called — is worth adding the day
a deployment turns a vocabulary on for a server with write tools.
