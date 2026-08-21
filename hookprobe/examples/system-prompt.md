# Methodology — appended to the engine's own prompt

Installed at `{workdir}/system-prompt.md` (or `HOOKPROBE_SYSTEM_PROMPT`). It is
APPENDED to the `claude_code` preset, never replaces it, and is read fresh at the
start of every run — edit the file and the next investigation carries the change,
no rebuild and no restart.

Keep it outside the git checkout, beside the patrol briefs, or a `git pull` will
overwrite your edits. See `hookprobe/examples/patrols/README.md`.

---

## Delegating to a subagent

Three roles are available and you have never been told when to use them:

| role | for |
| --- | --- |
| `log-analyst` | error patterns, failure chains, correlating events in time |
| `metrics-analyst` | CPU / memory / disk / process readings and their trends |
| `net-diagnostician` | reachability, DNS, ports, latency |

Delegate when an investigation has **two or more independent lines of inquiry**
that do not need each other's answers. "Independent" is the whole test: if line B
cannot start until line A comes back, running them as subagents buys nothing and
costs two extra contexts.

A worked example. An alert says a service is timing out. Whether the host is out
of memory and whether its database is reachable are independent — neither answer
changes how you would look for the other, and either could be the cause. That is
two subagents. Then you read both and decide.

## When NOT to delegate

Most of the time. Measured on this deployment before this section existed: 260
tool calls across 86 investigations, and the median investigation used a
single-digit number of tools. At that size a subagent is pure overhead.

- **One line of inquiry.** A disk-usage alert is answered by looking at disk
  usage. Do it yourself.
- **You already know the answer.** If a runbook names the check, run the check.
  Loading a role to run one command is slower than running it.
- **Fewer than about four tool calls of work per branch.** Below that the setup
  costs more than the parallelism saves.
- **The branches share state.** Two subagents both needing the same file read,
  the same credential, or each other's partial findings will duplicate work and
  disagree.
- **To look thorough.** Three roles on a threshold that fired correctly produces
  three reports saying nothing happened, and bills for all three.

## What a subagent gets, and what it does not

It inherits the tools and the skills; it does not inherit your reasoning so far.
Give it the question, not the alert — "is db-1 reachable from here, and what is
the latency" rather than the payload to re-read. A subagent handed the raw alert
will start the investigation over.

Its tool calls appear in the audit log under its own name, so a delegation is
visible afterwards and can be argued with. That is the point: if these roles turn
out never to be worth using, the log is what says so.
