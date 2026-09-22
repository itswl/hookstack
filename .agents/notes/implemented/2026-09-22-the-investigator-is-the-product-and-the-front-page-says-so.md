---
title: The investigator is the product, and the front page says so — one positioning where four coexisted
status: implemented
date: 2026-09-22
scope: stack
---

## Decision

The front page positions hookstack as one thing: **put agents on your production
signals without handing them the keys.** A pipe signs, routes and prices every
signal; an investigator runs read-only and is measured read-only at startup; what
comes back reaches a person as an audited, priced report. **The investigator is
the product.** The pipe and the judge are the smallest alerting front end for a
team that has no alerting platform — a substrate and a story for the standalone
reader, not the differentiator.

The page also says what hookstack is **not**: a general work-management platform,
a multi-tenant SaaS, an alerting platform, or an agent framework. One deployment
is one team, by decision.

Changed in the same commit, so the four statements below stop coexisting:
`README.md`, `docs/index.md`, `docs/zh/index.md` (the first three paragraphs and a
preface to the roadmap section), the first paragraph of `OVERVIEW.md`, the site
description in `docs/_config.yml`, and the repository description on the host.
Nothing under `hookrelay/`, `hookjudge/` or `hookprobe/` changed: their READMEs
already described their component this way.

The work-operations vocabulary — work items, agents, *verified*, *closed without
anyone stepping in* — stays exactly where it is: on the boards, in `work.py`, in
the weekly page. It is the frame the ledgers are read in. It moves from the
roadmap to the first line when the evidence in Consequences arrives, not before.

The category is the one people already search for — an agent that investigates
production for an on-call team — and the differentiation inside it is
containment by proof, self-hosting, limits written down, and no write to
production without a signed press. No category is invented while nothing is
distributed: a name nobody searches for is a name nobody finds.

## Why

**Four positionings coexisted on 2026-09-22**, each with a different amount of
evidence behind it:

| where | what it said | evidence |
| --- | --- | --- |
| README first line, the PRD of 2026-09-08 | a work operations platform for agents | one personal deployment on a laptop; 0 of 244 production work items spanned a node; no team, no second user |
| the repository description | a signed, priced, replayable bus for agent handovers | true of the pipe, but one graph runs; a bus with one participant is a pipe |
| OVERVIEW and most of the docs | a pipe, a judge and an investigator for alerts | in production with full ledgers; sits behind an alerting platform that already routes, so the judge re-judges |
| hookprobe's README | one unattended agent run behind an HTTP contract that terminates | over half the source, nearly all the model spend, every hardening note, the production value |

Read off the code and the ledgers rather than the prose: hookprobe is 28.8k of
the ~50k lines (tests included); the investigator spends ~$20/week where the
judge spends ~$0.10 (2026-09-14 window); the platform this deployment sits
behind (WebhookWise) switched its own analysis leg off on 2026-09-14 in favour
of this investigator — which is the embedded shape the OpenClaw-compatible
contract was built for, happening in the operator's own estate.

**The platform frame was already producing surfaces nobody opened.** Under it,
between 2026-09-08 and 09-10, a board, an overview, a work detail page and an
artifacts page were each proposed; two were built and two declined, and the
notes that parked them all reach the same sentence — *a page nobody opens has
no value at any price* ([[the-board-is-the-overview]],
[[what-the-prd-asks-for-and-will-not-get]]). Positioning is the filter for the
next hundred commits; the wrong one fills the backlog with pages.

**The human loop the platform frame promises has no humans in it.** Nine card
presses ever, none since 2026-08-17; five remediation proposals, zero approvals;
verified 0% in the last measured week ([[hookstack-unattended]] is the design
response). A first line that leads with "approved, verified" advertises the part
of the loop that is least exercised. The investigator's claims — terminates,
read-only by proof, priced, audited — are the ones the ledgers back today.

**There is no distribution to spend a new category on.** The repository has been
private since 2026-09-14 (recreated at the old name after the identifier leak);
0 stars, 0 forks, 0 issues, only Dependabot's pull requests. Checked 2026-09-22:
the quickstart's two raw URLs answer 404 anonymously and the image registry
answers 401, so "ten minutes, no keys" is currently true for exactly one person.

## Consequences

**The backlog reorders.** Up: read-only instruments for the production
investigator (it holds no credentials, and 70% of its spend went to a family it
could not look at — [[a-family-with-no-instrument-is-declined-at-the-door]]
stopped the bleeding, not the cause); one real approve → execute →
recovery-verified cycle on the smallest possible blast radius; recovery
verification reaching investigated conditions, which is the first lever on the
north star; a second deployment of the investigator behind a platform that is not
the operator's. Down: new console pages, tenancy, a marketplace, a fourth runtime.

**A thirty-second test for the front page**, applied before any edit to it: a
stranger reading the first screen can answer *is this for me*, *what does it do*,
*what does it not do*. Before this change the first screen answered none of the
three.

**Revisit triggers, so the platform frame is not lost but earned.** Any one of
these moves it toward the first line, and all three together move it there: a
deployment that is not the operator's own; a piece of production work that spans
more than one node; a person who answers the cards. The PRD's stage three waits
on the second, and stage four (tenancy) was declined by the operator on
2026-09-08 — the PRD document outside this repository was annotated to say so on
the same day as this note, so the two stop contradicting each other.

**Public or private stays the operator's decision and is not made here.** While
the repository is private the README's quickstart section describes a path an
outsider cannot take; when the decision is made, either the quickstart is fixed
and the front page is for strangers, or the front page's reader becomes the
operator and the agents and the essays move into `docs/`. This note only makes
the first screen true under either.

**Prose about positioning is the one thing no check can pin.** `assert_docs.py`
holds the counts on these pages honest; nothing can hold "the investigator is
the product" true if the code stops making it so. If a future measurement shows
the pipe or the judge carrying the value, that is a new note superseding this
one, not an edit to the README.

## Rejected

- **Lead with the platform.** Needs users, teams and a second deployment; had
  none; produced unopened pages.
- **Lead with the bus.** A bus is a product when it has participants. One graph
  runs on it.
- **Lead with alert noise.** The judge's whole bill is ten cents a week, and the
  platform in front of it already does this job for this deployment.
- **Invent a category name.** With zero distribution, a name nobody searches for
  is a page nobody lands on. Stand inside the category that exists and differ.
- **Rewrite the whole README to one screen now.** Wanted, and the right shape,
  but it depends on the public/private decision above; the first screen was
  fixed without waiting for it.
