---
title: The front pages say what it is and link the rest
status: implemented
date: 2026-09-30
scope: stack
---

## Decision

The four front pages carry the same short content:

- `README.md`
- `README.zh-CN.md`
- `docs/index.md`
- `docs/zh/index.md`

Each has five parts:

1. The pitch.
2. The three services in one table.
3. The quickstart.
4. What you get, one line per point.
5. Where to read more.

`OVERVIEW.md` is the tour: one short paragraph per picture and the facts that
tie it to that picture.

Mechanisms, history and the arguments behind them live in the service READMEs,
`docs/*.md` and these notes. They do not live on a front page.

## Why

The operator read the Chinese README and said 「太多废话了」. The English
README had grown to 3,300 words and OVERVIEW to 5,400. Four copies of long
prose were rotting in the ways the docs checks cannot see:

- a line ceiling two raises behind;
- an investigator still called opt-in;
- a compose profile that no longer existed;
- published images described as current after the boards had moved on.

A front page is read first and maintained last. Every sentence on it that
restates a mechanism is a second copy of a docstring, and it will drift.

## Consequences

- A claim added to a front page goes into all four copies, in both languages,
  in the same commit.
- Detail that wants to be on a front page goes into the service README it
  belongs to, and the front page links there.
- The pages dropped from about 3,300 words to about 720, and OVERVIEW from
  5,400 to 2,400. All thirteen pictures stayed.
