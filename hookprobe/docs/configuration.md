# hookprobe — configuration

Every variable, its default, and **why it exists**. The terse generated version
— names and defaults only, straight from `hookprobe/settings.py` — is
[reference.md](reference.md); that one cannot drift from the code, and this one
carries the reasoning that a generator has nowhere to put.

| env | default | meaning |
|---|---|---|
| `HOOKPROBE_TOKEN` | *(empty = unauthenticated)* | Bearer token callers must present |
| `HOOKPROBE_MODEL` | `claude-opus-5` | Model for the agent session |
| `HOOKPROBE_MAX_TURNS` | `32` | Hard agent-loop budget per run |
| `HOOKPROBE_MAX_CONCURRENT` | `2` | Parallel runs; the rest queue |
| `HOOKPROBE_DEFAULT_TIMEOUT_SECONDS` | `900` | When the trigger omits `timeoutSeconds` |
| `HOOKPROBE_MAX_TIMEOUT_SECONDS` | `1800` | Upper clamp on requested timeouts |
| `HOOKPROBE_WORKDIR` | `/data` | Persistent workspace (skills, results) |
| `HOOKPROBE_MCP_CONFIG` | *(unset)* | Path to an `.mcp.json`-shaped file of MCP servers |
| `HOOKPROBE_MCP_TOOLS` | *(empty = none may run)* | The closed set of MCP tools this instance may call: `mcp__chat__chat_search_messages`, or `mcp__webhookwise__*` for a whole server. **Mounting a server does not grant its tools.** There is no such thing as a read-only MCP server — a chat server ships `send_message` beside `search_chat_records` — and this component reads attacker-influenced text, so "let it read the thread" and "let a message in that thread post as you" must not be the same decision. Enforced by a PreToolUse hook, like the bash guard, because `permission_mode` is `bypassPermissions` and an allowlist there is a preference rather than a gate. A refusal names what the instance *may* call, so the wrong list is a readable error and not a mystery |
| `HOOKPROBE_EVENT_SECRET` | *(empty = unsigned)* | Verifies the pipe's deliveries to `/hooks/event`. This is the one mutating route `HOOKPROBE_TOKEN` does not cover — the pipe delivers there, so the signature is its credential — and it is the only door that starts paid investigations. Setting the token and leaving this empty locks every door a person uses and leaves that one open; boot says so in the log |
| `HOOKPROBE_RETURN_URL` | *(unset = no return)* | Where event-door investigations report back — the pipe's `probe-notify` front door |
| `HOOKPROBE_RETURN_SECRET` | *(empty = unsigned)* | Signs the return delivery (timestamped HMAC) |
| `HOOKPROBE_RULING_URL` · `HOOKPROBE_RULING_SECRET` | *(both unset = off)* | Where a retrospective condition ruling goes (the judge's `/rulings/ai`) and the credential for that one door. A verdict is `worth_it` or `not_worth_it` and must carry a reason; anything else is dropped before it is sent. The agent PROPOSES with an `AI-RULING:` line and the service signs — the same division as `MEMORY-SUGGESTION`, because the agent is the component that reads attacker-influenced text and does not get a reusable key for a sibling's ledger |
| `HOOKPROBE_ESCALATE_LEVELS` | `critical,high` | The only content judgement the investigator makes: which levels are worth a paid run |
| `HOOKPROBE_VERDICTS` | *(empty = off)* | A comma-separated closed vocabulary this instance may CONCLUDE with. Declared, the prompt asks the report to end with `VERDICT: <one of them>` and the return delivery carries it as `meta.verdict`, which a pipe extracts with `fields: {verdict: "{meta.verdict}"}` and routes on. It is the one field on the return trip the investigator *decides* — `meta.importance` is the level of the event that came in — so it is what lets a report be the MIDDLE of a chain instead of a leaf. Closed rather than free text because a routing key decides where money is spent, in the component that reads attacker-influenced prose: an undeclared label yields `""`, never a guess. See [the decision note](../../.agents/notes/implemented/2026-09-04-an-investigator-verdict-may-steer-a-route-from-a-closed-set.md) |
| `HOOKPROBE_BUDGET_USD` | `0` *(off)* | Window spend ceiling for the event door; refusals report themselves. `GET /v1/budget` shows the arithmetic |
| `HOOKPROBE_BUDGET_WINDOW_HOURS` | `24` | The sliding window the budget is measured over |
| `HOOKPROBE_RETENTION_DAYS` | `0` *(keep all)* | Case files and transcripts older than this are pruned daily; skills and memory are never touched |
| `HOOKPROBE_AUTO_DISTILL_MAX` | `0` *(manual)* | How many runbooks finished runs may leave behind. Above 0, each completed run writes its own `.claude/skills/<name>/SKILL.md` — from the service, create-only, marked unreviewed. See *The learning loop* |
| `HOOKPROBE_MEMORY_AUTO_APPLY` | `1` *(on)* | The prompt invites at most one `MEMORY-SUGGESTION:` line per report and the service lifts it out. A line whose SHAPE cannot act — no imperative, no second person, no URL, no shell metacharacter, one line, under 400 characters — is appended to CLAUDE.md under its own `unverified` heading. Anything else is queued for a person, which is what the queue is now for. Set `0` to queue everything. The agent's own tools reach neither the queue nor the memory either way |
| `HOOKPROBE_REMEDIATION_ALLOWLIST` | *(unset = collect-only)* | Path to a file of full-match regexes, one per line, hot-read at execution time. A report may append a fenced `remediation` block; the service parks it as a proposal on the **actions** page, and approving runs the steps sequentially, stop-on-failure, each audited. Deny-by-default: no allowlist, no execution. The read-only investigator never runs these — the service does, after an operator's click |
| `HOOKPROBE_CONSOLIDATE_AT` | `5` *(0 = off)* | At this many accumulated cases, a runbook triggers ONE agent run that distills the pile into a curated procedure. The draft is APPLIED, snapshotting what it displaced — `auto_write` already installs these manifests with no human, so a gate on consolidating them guarded a class of text already arriving unguarded, and the draft simply stalled. Reversibility replaces permission: `POST /v1/skills/{name}/history/{stamp}/restore` puts the old version back in one call, and `reviewed` stays false because a machine writing a file does not make it read |
| `HOOKPROBE_RULING_TTL_DAYS` | `14` *(0 = off)* | How long a standing `not_worth_it` ruling (filed by the weekly patrol, kept in `rulings.jsonl` beside the case files) may answer that condition's re-fires from its runbook instead of starting a paid run. The reply is report-shaped JSON marked `answered_from_runbook`, the run costs $0, and `{"force": true}` on the trigger bypasses it |
| `HOOKPROBE_RULING_REVERIFY_DAYS` | `7` | A ruled-useless condition still gets a REAL investigation this often. Gated answers do not count as verification — only a run that actually looked does, or the gate would feed itself forever |
| `HOOKPROBE_COALESCE_WINDOW_SECONDS` | `1800` | A re-fire of the same alert (same source+title, new event id) inside this window becomes a follow-up turn in the existing session instead of a new cold-start investigation. Redelivery of the same event id stays idempotent. `0` disables |
| `HOOKPROBE_REPEAT_REMINDER_AT` | `3` *(0 = off)* | After N identical tool calls, remind the agent to change approach |
| `HOOKPROBE_HANDOFF_URL` · `HOOKPROBE_HANDOFF_SECRET` | *(both unset = off)* | Where the sessions page's **Hand off** button posts a finished report, and the credential for that one door. A PIPE door, never another node's: the pipe is what makes a handover accountable, and going through the front door means the chain the ledger already joins simply grows a hop. Dedup is left to it — two clicks produce one fingerprint, so the second is recorded as a duplicate rather than buying a second run. The button is only offered when the URL is set; an offer that answers 501 is worse than no offer |
| `HOOKPROBE_BASH_GUARD` | `readonly` | Which posture the bash guard takes. `readonly` refuses the mutating verbs of the CLIs an SRE agent reaches for — the investigator's posture, and the only one that should face an event door. `danger-only` allows them and refuses a much shorter list instead: the handful whose damage is to the container, the host or a whole estate rather than to the object an operator scoped a credential to. It is for a runner an operator gave scoped WRITE credentials to, and it is **not** what bounds such a runner — the credentials are. An allowlist would be the wrong shape here and guard.py says why. An unknown value falls back to `readonly`: a typo in the variable that decides whether a runner may write must fail closed |
| `HOOKPROBE_BASH_TIMEOUT_MS` | `120000` *(0 = CLI default)* | Deadline for a single command (`BASH_DEFAULT_TIMEOUT_MS`) |
| `HOOKPROBE_BASH_MAX_TIMEOUT_MS` | `600000` *(0 = CLI default)* | Ceiling the agent may request per command (`BASH_MAX_TIMEOUT_MS`) |
| `CLAUDE_CODE_ENABLE_TELEMETRY` + `OTEL_*` | *(unset = off)* | Passed to the CLI, which emits one OpenTelemetry event per model call. No default endpoint — see [cost.md](cost.md) |
| `HOOKPROBE_HOST` / `HOOKPROBE_PORT` | `0.0.0.0` / `8088` | Bind address |
| `ANTHROPIC_API_KEY` | — | Or `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` for a relay |

## Managing MCP servers by hand

MCP is managed as one JSON file — no API writes, on purpose: server specs
carry credentials in their `env`, and secrets belong in a file you mount,
not in a web form. The loop is:

1. Write the file. Three dialects are accepted: the bare
   `{name: {command, args, env}}` mapping, the `.mcp.json` wrapper
   (`{"mcpServers": {...}}`), and the marketplace `config.json` shape —
   entries with `"enabled": false` are skipped and the flag is stripped, so
   downloaded MCP packages work unedited.
2. Mount it read-only and point `HOOKPROBE_MCP_CONFIG` at it (see the
   commented lines in every compose). A path on the volume (e.g.
   `/data/mcp.json`) also works and is editable without remounting.
3. Verify with `GET /v1/mcp`: it reads the file fresh and shows each
   server's command/args/type/url plus its env **key names only** — env
   values never leave the file.
4. Edit any time: the config is read fresh at every run (and every
   `/v1/mcp` call), so changes apply to the next investigation without a
   restart.

## Browser evidence (optional)

Give the agent an interactive browser for dashboards that have no API: copy
`deploy/mcp.example.json` (a headless Playwright MCP server), point
`HOOKPROBE_MCP_CONFIG` at it, and uncomment the chromium block in the
Dockerfile so the image ships the browser. One caution: a browser can click
and submit on any page it can reach — the bash guard does not see browser
actions, so point it at read-only dashboards and viewer accounts; `--isolated`
keeps it from retaining any profile state between runs.
