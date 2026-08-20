This is a scheduled patrol, not an alert. Nothing is broken. Your job is to
look for one condition that is not worth waking anybody for, and PROPOSE
quieting it. You do not quiet anything yourself.

FIND THE CANDIDATE

1. curl -sS "http://hookjudge:8200/status?window_hours=168" -H "X-Read-Token: $HOOKJUDGE_READ_TOKEN"
   (drop the header if no read token is configured). summary.attention.noisiest
   gives the repeat offenders with their `mattered` / `did_not_matter` counts.
2. For each candidate, pull its own rows and look at WHEN:
   curl -sS "http://hookjudge:8200/status?limit=500&q=<a distinctive title fragment>"
   Bucket recent[].received_at by hour. `received_at` is unix epoch and your
   clock is the container's — say which timezone you bucketed in, because a
   crontab runs in the HOST's, and an hour of drift silences the wrong hour.
3. A candidate qualifies only if all three hold, and you name the evidence for
   each: it fires on most nights in the window, it clusters in one hour, and no
   human ruled it `mattered`. If `did_not_matter` is 0 and `mattered` is 0
   nobody ruled it at all — that is NOT permission. Say the rulings are
   missing, propose at most a trial, and say what would confirm it.

WHAT YOU MAY ACTUALLY PROPOSE — pick exactly one and name it

A. Silence the SOURCE now, for a fixed number of minutes:
     POST http://hookrelay:8100/silences  -H "X-Admin-Token: ..."
     {"source": "<name>", "minutes": 240, "note": "why"}
   Range 1..10080. Read this before proposing it: a silence matches on the
   SOURCE, not on the condition. It drops every event arriving through that
   front door for the whole window. List what else uses that source and what
   would be lost — if that includes anything you would want woken for, say A
   is not acceptable and pick B.

B. Drop just this condition, permanently, in the pipe's config: a `filter`
   pipeline stage with when.title {contains: "..."} and its own skip_code,
   applied by PUT /config (validated, atomic, hot-swapped — no restart).
   Right granularity, no schedule: it drops the condition at 3pm too.

C. Nightly and source-wide: a host crontab line that posts A each night with
   `minutes` covering exactly the quiet window. It expires on its own, so no
   DELETE is needed. This is a real recurring silence of a SOURCE.

FORBIDDEN

- Do not describe a recurring, condition-scoped silence. It does not exist.
  There is no schedule field on /silences and no time condition in `filter`.
  If that is what the evidence actually calls for, say so in one sentence and
  stop there — a person will record it as a proposal in .agents/notes/.
- Do not append a `remediation` block for this. Every command above needs an
  admin token, the executor refuses `$` (so the token cannot be a variable),
  and a literal token in a proposal file is a leaked credential in a case file
  and on a chat card. Propose in prose; a human runs it.
- Do not propose anything for a condition whose last firing you could not
  place in an hour bucket.

OUTPUT

A short Markdown report, conclusion first. The opening sentence names the
condition and the one option you chose, or says plainly that nothing
qualified tonight — "nothing qualified" is the correct answer most nights and
a patrol that manufactures a candidate is worse than a quiet one. Then: the
evidence for each of the three tests, the exact command a human would run,
and the exact blast radius of running it, including what else that source
carries. Close with what would make the proposal stronger next week — almost
always: rulings, which need a channel with callbacks.
