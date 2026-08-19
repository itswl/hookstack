---
title: One async test harness across the three suites, declined — churn without a defect
status: rejected
date: 2026-08-19
scope: stack
---

## Decision

The three suites keep two async harnesses. hookrelay and hookjudge run
pytest-asyncio in `asyncio_mode = "auto"` with bare `async def` tests and
fixtures in `conftest.py`; hookprobe runs synchronous
`def test_…() -> None:` bodies wrapping `asyncio.run(scenario())`, with a
`fastapi.testclient.TestClient` and helpers imported from `tests.helpers`.
Converting hookprobe was considered and declined.

New tests follow the suite they land in. That is the whole rule.

## Why

A style audit on 2026-08-19 called this the largest single divergence between
the packages, and it is — it is the one thing that makes the three suites read
as different authors. Everything else on that audit's list was cheap enough to
just do: one ruff rule set, py312 everywhere, stdlib logging, the fifteen inert
async markers deleted.

This one is not cheap. hookprobe has 183 tests and the harness is not a detail
of their surface — it is their control flow. Every file would change, every
`asyncio.run(scenario())` unwraps into a bare async body, and the TestClient
calls become `await`ed ASGITransport calls with a hand-run lifespan. The
mechanical part is large and the review is larger, because a converted async
test that silently no longer awaits the thing it used to await still passes.

Against that: no defect. Both harnesses run their tests, both fail on failure.
The one real problem in this area was found and fixed on its own —
eleven hookjudge tests carried `@pytest.mark.anyio` and were being claimed by
the anyio plugin (visible as an `[asyncio]` parametrisation) while their
neighbours ran on pytest-asyncio, on a dependency that was not even declared.
That was two harnesses inside ONE file, which is a real inconsistency; two
harnesses in two independent packages, each self-contained by design, is a
preference.

## Consequences

- The cosmetic divergence stays, and someone reading all three suites in one
  sitting will notice it. That is the accepted cost.
- hookprobe's shape has one genuine advantage worth recording: its tests never
  import the Claude Agent SDK, and a synchronous body wrapping one
  `asyncio.run` makes the boundary between "the test" and "the async scenario"
  explicit, which suits a suite whose subject is a long-running agent session.
- If hookprobe's suite is ever restructured for another reason, convert it then
  — the cost is in the reading, and that cost is already being paid at that
  point.

## Rejected

- **Converting hookprobe to pytest-asyncio now.** Above.
- **Converting hookrelay and hookjudge to hookprobe's shape instead.** Same
  argument pointing the other way, and worse: it is 239 tests rather than 183,
  and it would trade a plugin that handles the event loop for hand-rolled
  `asyncio.run` calls.
- **Requiring one harness for NEW tests repo-wide.** A new test in hookprobe
  written in the other style would be the only one of its kind in that suite —
  the inconsistency moves inside a package instead of between them, which is
  the version of this problem that actually bit.
