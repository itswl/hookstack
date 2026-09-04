# Containment

An agent in this family is treated as an untrusted network service that costs
money, reads text an attacker may have written, and holds credentials. Every
boundary below exists because one of those three is true.

This page is the map. Each row's authority is the code named in the last column;
where the two disagree, the code is right.

The third column is the point of the page. A boundary whose limits are not
written down gets trusted for things it never claimed — the way a unit test
proving a shape check refuses a string a developer typed gets read as proof that
a real model cannot be steered into emitting a harmful one.

## The boundaries

| Boundary | Stops | Does NOT stop | Enforced in |
|---|---|---|---|
| **Per-source HMAC** — every door verifies `sha256(secret, "{ts}.{body}")` | An unsigned or forged event reaching a door | A replay inside `max_skew_seconds` (300): the window is **limited, not closed**, and there is no nonce cache | `hookrelay/security.py`, `hookprobe/wire.py` |
| **Per-node credentials** — each process-external node gets its own secrets, budget and guards | One node's compromise becoming every node's | Anything inside a node once it is compromised | the compose files; `docs/containment.md` is not a runtime |
| **Declared bypass lanes** — config names the channel set, a payload can only pick from inside it | An injected message routing itself around a gatekeeper | A node with a legitimate lane sending the wrong thing down it | `hookrelay/config.py`, `routing.py` |
| **`on_error` per stage** — `pass` (fail open) or `drop` (fail closed), chosen per stage, never defaulted globally | A stage's failure silently changing policy | Choosing wrong: this makes the choice explicit, not correct | `hookrelay/processors.py` |
| **Inline timeout cap** — 10s, refused at config load | An inline decider holding the sender's connection until it retries and duplicates the alert | A node that answers in 9s and is still the wrong place for the work | `hookrelay/processors.py` (`MAX_INLINE_TIMEOUT_SECONDS`) |
| **Verdict vocabulary** — `HOOKPROBE_VERDICTS`, closed set, empty denies all | An investigator's free text choosing which lane spends money | A wrong choice among lanes the operator already declared | `hookprobe/reports.py` (`verdict`) |
| **MCP tool allowlist** — `HOOKPROBE_MCP_TOOLS`, closed set, empty denies all | A chat message asking the agent to use a write tool on a mounted server | A tool that is read-only in name and not in effect — that judgement is the operator's and nothing here can check it | `hookprobe/engine.py` (`mcp_deny_reason`) |
| **Bash guard** — mutating verbs of known CLIs; `aws` inverted to deny-unless-read | An over-eager model running `kubectl delete` or `aws s3 rm` | An adversary: the rules need a binary and a verb in one pipeline segment, and separating them gets through. **The read-only credential is what holds** | `hookprobe/guard.py` |
| **Input guard** — writes to the files that steer the next run are refused | A run installing its own runbook as a persistence vector | Writes anywhere else in the workspace, which are allowed on purpose | `hookprobe/inputs.py`, `engine.py` |
| **Memory shape check** — a suggested fact whose SHAPE could act waits for a human | An instruction smuggled through alert text into `CLAUDE.md` | A benign-shaped line that is simply wrong | `hookprobe/suggestions.py` |
| **Budget breaker** — window spend ≥ ceiling refuses new autonomous runs | An unattended loop spending without limit | Operator-driven doors, which are never gated | `hookprobe/service.py` |
| **Card action signing** — a button carries a signed single-use token | Anyone in a group chat pressing on your behalf | Nothing, when `HOOKRELAY_ACTION_SECRET` is empty: the buttons render and do nothing | `hookrelay/actions.py`, `app.py` |
| **The watcher holds a signing key** — probe-watch signs its findings into the pipe's watch door | Nothing. It is listed here because it is the residual, not a boundary | A signal it FABRICATES. The door authenticates "a watcher", never "a truthful watcher", and the agent must be able to sign or it cannot do its job. What bounds it: a fabricated `high`+`task` buys one investigation against a budget ceiling, and the round's own case file records what it saw when it decided | `deploy/docker-compose.work.yml`, `.agents/notes/proposed/2026-09-02-the-agent-shares-the-services-secrets.md` |
| **Topology invariants** — no unreachable door, starved exit, or wildcard a return door can reach | A config change silently feeding a brain its own output | A graph that is legal and still wrong | `scripts/assert_topology.py`, `hookrelay/topology.py` |

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
