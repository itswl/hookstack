This is a scheduled patrol, not an alert. There is no incident and no root
cause to find. Do the task below and ignore the framing that asks you to
triage something.

QUESTION: did any agent loop go red and stay red — and is the automation
volume drifting?

An "agent loop" event is this stack reporting on its own automation through
the front door: gate verdicts, deploy-eval results, memory auto-applies. The
adapter that admits them (hookrelay/examples/agent-loops.yaml) prefixes every
title with `agent-loop`, and that prefix is your query key. If this
deployment has no agent-loop source configured, say exactly that and stop —
do not improvise a different query.

1. Read the loop rows from the judge's ledger:
   curl -sS "http://hookjudge:8200/status?q=agent-loop&limit=200" -H "X-Read-Token: $HOOKJUDGE_READ_TOKEN"
   (drop the header if no read token is configured — /status stays open then).
   `recent` is NOT windowed — it is simply the newest matching rows. Window it
   yourself: keep rows whose received_at falls in the last 7 days, and say how
   many you kept out of how many came back. summary.* in the same response is
   windowed but GLOBAL — every source, not just loops — so quote nothing from
   it as a loop number.

2. Find last week's edition of this patrol BEFORE concluding anything: grep
   /data/results/ for this task's title and read the most recent hit. If there
   is none, say so plainly — this run is the baseline, and a baseline is a
   finding, not a failure.

3. Lead with what is red and unfixed. Group the kept rows by their `rule_key`
   column (the loop's name); the outcome is the title's suffix. A loop whose
   LAST row this week is red — or that fired red more than once with no green
   after — is the lead: an automation failure nobody has fixed. One red
   followed by a green is a loop working as designed; give it one line and
   move on.

4. Then the drift. Count `applied` outcomes (memory auto-applies) this week
   against last week's edition. Rising volume is not an error — it is the
   early number the graduation note
   (.agents/notes/proposed/2026-08-31-automation-graduates-on-its-record.md)
   wants watched before any propose-only path earns auto-apply. Report the
   two counts and the direction, nothing stronger.

5. Footprint and absence:
   - This patrol enters through the same front door as an alert. Say how many
     of the rows you counted are this patrol itself.
   - A loop that stopped REPORTING is not a loop that stopped failing:
     senders are crontab and CI lines, and they die silently. If a loop
     present last week sent nothing this week, that is the second-place
     finding — name the loop, and say plainly that wiring death and genuine
     quiet cannot be told apart from here.

WHAT YOU MUST NOT DO WITH MISSING DATA

- Never read the absence of red as health when the greens are absent too.
- Counts, not percentages, under ten rows — "2 of 6 red" is a sentence,
  "33% failure rate" over six rows is theatre.
- Do not sum `recent` rows into totals for any window longer than what you
  kept in step 1; the array caps at the limit you asked for.

OUTPUT

A short Markdown report, conclusion first. The opening sentence carries the
answer and the number — e.g. "One loop is red and unfixed: deploy-eval failed
twice with no green since Tuesday; auto-applies steady at 4/week." Then the
per-loop rundown, then the drift line, then footprint and absences.

No remediation block. If a red loop needs fixing, the report NAMES it and a
person decides; proposing configuration changes is not this patrol's job.
