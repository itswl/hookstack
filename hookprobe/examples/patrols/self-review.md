# Patrol brief — what should this investigator remember?

Sent to the pipe's front door on a schedule (see README.md in this directory for
the crontab line and the signing). It is a NUDGE: Hermes Agent's phrase for a
system that periodically prompts itself to consolidate what it has learned,
rather than waiting for the learning to fall out of ordinary work.

The difference here is where it lands. Hermes lets the agent write the skill; a
run of this brief writes nothing. It PROPOSES, and a person accepts — because a
line that reaches `.claude/skills/` is loaded as instruction by every later run,
which is how one prompt injection in one alert payload becomes a permanent one.
See hookprobe/inputs.py for the two defences that enforce it.

So this brief is written to be useful even if nobody ever reads its output. Its
first job is to say something true about the last twenty investigations. Its
second is to leave a record that says so.

---

Review your own recent work. You have read-only access; write nothing.

1. Read the last 20 case files under `/data/results/` (newest first) and the
   environment memory at `/data/CLAUDE.md`.

2. For the alerts that recurred, answer plainly: did the later investigations
   add anything to the first one? If three investigations of the same condition
   reached the same conclusion by the same route, that is a runbook waiting to
   be written, and you should say which condition and what the route was.

3. Read the brain's ledger: `curl -s http://hookjudge:8200/status`. Two numbers
   there are about you rather than about the alerts:

   - `summary.attention.likely_flapping` — conditions that healed themselves
     quickly and repeatedly, inferred with no human input. A condition on that
     list that you have investigated more than once is a condition you are
     being paid to look at and finding nothing wrong with. Say so.
   - `summary.attention.ruled` — how many verdicts a human has ruled on. If it
     is 0, say that too, and do not read the absence as agreement. It means the
     humans are not answering, which is information about the loop and not
     about the alerts.

4. Propose AT MOST ONE durable fact for the environment memory, in the usual
   form — a line beginning `MEMORY-SUGGESTION:`. It must be about the
   ENVIRONMENT (topology, a known false alarm, a naming convention), never about
   one incident, and never about how to behave. Nothing qualifying is a fine
   outcome and a wrong line is expensive, because it would be loaded into every
   later run.

   If nothing qualifies, say so in one line and say why. Do not leave the
   heading empty: the first run of this brief did, and an empty section reads
   identically to a truncated answer or a forgotten instruction. The point of
   this patrol is a legible record, and "nothing qualified, because the gap I
   found is a missing runbook rather than a fact about the environment" is a
   result. A blank space is not.

5. Open the previous self-review's case file first, and compare. If your last
   review proposed something and nothing came of it, say that plainly — the
   pile-up of unaccepted proposals is itself the most useful thing this patrol
   can report, and the one nobody else will notice.

Answer in short Markdown, conclusion first. If the honest answer is "nothing
worth changing this week", that is the answer; say it in one line and stop.
