---
title: The prose, the numbers, and the page — auto-distill stays inside its case, metrics asks for the token, and the weekly page knows the work shape
status: implemented
date: 2026-10-25
scope: stack
---

## Decision

The third batch from the same review; three small surfaces that each said
something the running path did not.

1. **Model prose written into a runbook cannot change the runbook's shape.**
   A case is delimited by comment markers and read as instruction by the next
   investigation. Steps and the session key are reduced to one line; backticks
   in a step cannot close its inline code span; a conclusion keeps its
   paragraphs but loses heading markers, setext rules, and the `<!--` / `-->`
   delimiters `merge_case` searches for. The frontmatter description is
   quote-escaped from a one-line headline. This does not make the prose true —
   the case still says nobody reviewed it — it keeps it inside the case.
2. **`/metrics` is behind the read token.** The README already said the bearer
   covers everything except `/healthz` and the page shell; this was the route
   that made the sentence false. It exposes spend, run counts, queue depth and
   runbook counts on a port a sibling container can reach. Prometheus carries
   the token from a file; a scraper without one gets 401.
3. **`weekly_page.sh` knows both deployment shapes.** `HOOKSTACK_DEPLOY=work`
   runs the report inside the work stack against `work-hookrelay` and the three
   named probes, with no judge section pretending to be a zero. The retired
   shadow shape stays the default, so an existing crontab does not change
   meaning on upgrade.

## Why

The review's recurring shape again: a promise in prose, a path that did not
keep it. Auto-distill was the one durable write of attacker-influenced text
that had no shape guard; `/metrics` was the one data route outside the token;
the weekly page was the one operator tool wired only to the retired shape. Each
fix has a test at the point the value acts, not at the point it is computed.

## Consequences

- A runbook case whose conclusion contained a heading, a horizontal rule, or a
  case marker now renders those characters literally (`\##`, `\---`, `&lt;!--`).
  A person editing the runbook sees the escape and can delete it; the next model
  does not see a section it can mistake for the operator's own.
- A Prometheus scrape of the investigator needs `Authorization: Bearer
  $HOOKPROBE_TOKEN` (or `X-Read-Token`). A deployment that scraped it anonymously
  will go unread until the job carries the token — which is the intended
  direction: an absent series is visible, a leaked spend figure is not.
- The work page has no judge section because the work deployment has no judge;
  `cost_report.py` reports the service it was not given rather than inventing a
  zero. The crontab line needs `HOOKSTACK_DEPLOY=work` to use it.
