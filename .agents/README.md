# Agent Notes

A decision record per file, kept in the repository rather than in a chat log or
a commit message nobody re-reads. The point is not process: it is that six weeks
later "why is it done this way" and, more valuable, "why is it *not* done the
obvious way" have written answers next to the code.

Borrowed from [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
(`.agents/notes`), trimmed to the size of three small services.

## Buckets

| Directory | Holds |
| --- | --- |
| `notes/implemented/` | Shipped decisions. The code is the truth; the note says why it is shaped that way. |
| `notes/proposed/` | Decisions taken but not built, with enough detail to build or drop them. |
| `notes/rejected/` | Ideas evaluated and declined — including ones that were built and then removed. The most useful bucket, and the one that never survives in a commit log. |
| `notes/archived/` | Shipped notes that no longer guide any future work. |

## Format

One file per decision, named `YYYY-MM-DD-slug.md`, opening with front matter:

```markdown
---
title: One line naming the decision
status: implemented
date: 2026-08-14
scope: hookprobe
---

## Decision

What was decided, in the present tense.

## Why

The reasoning, including the evidence. Numbers and observed behaviour beat
adjectives.

## Consequences

What this makes easy, what it makes hard, and what to watch for.
```

`scope` is one of `hookrelay`, `hookjudge`, `hookprobe`, `stack`. Add
`supersedes: YYYY-MM-DD-slug` when a note replaces an earlier one.

## Rules

- **Keep an implemented note's facts current in place.** Paths, defaults and
  mechanisms change; rewrite them in the same change that alters them, and do
  not append a change history — the git log already has one.
- **A reversal needs a new note.** Updating facts is maintenance; reversing the
  *decision* or its reasoning is a new decision. Write a new note, cross-link
  both, and move the old one to `archived/` if it no longer guides anything.
- **Write the rejected ones.** An idea that was tried and removed is worth more
  than an idea that shipped, because nothing else in the repository records it.
- `scripts/assert_agent_notes.py` checks the shape of every note and runs in
  `ci-stack` and `scripts/stack-smoke.sh`.
