"""One stream of what happened, projected from the ledger that already holds it.

Written after an afternoon in which answering "how is this deployment doing"
took five endpoints across two machines and a person joining them by eye. The
pipe had every fact already; nothing could read them as one thing.
"""

from __future__ import annotations

from hookrelay.timeline import render

# A real shape: a trigger fans out to a node, the node's result comes back
# quoting the original, and a second unrelated signal stands alone.
ROWS = [
    {
        "id": 3,
        "received_at": 300.0,
        "source": "watch",
        "title": "unrelated signal",
        "level": "low",
        "outcome": "routed",
        "channels": ["to-lark"],
        "fields": {},
        "correlation_id": None,
    },
    {
        "id": 2,
        "received_at": 200.0,
        "source": "plan-notify",
        "title": "the plan",
        "level": "high",
        "outcome": "routed",
        "channels": ["to-lark-plan"],
        "fields": {"cost_usd": "0.42"},
        "correlation_id": "1",
    },
    {
        "id": 1,
        "received_at": 100.0,
        "source": "watch",
        "title": "the signal",
        "level": "high",
        "outcome": "routed",
        "channels": ["to-plan"],
        "fields": {},
        "correlation_id": None,
    },
]


def test_a_chain_gathers_what_quoted_it() -> None:
    out = render(ROWS)
    chains = {c["chain"]: c for c in out["chains"]}
    assert set(chains) == {"1", "3"}, "the return quoted event 1, so it belongs to it"
    assert [h["id"] for h in chains["1"]["hops"]] == [1, 2], "hops read oldest first inside a chain"
    assert chains["1"]["doors"] == ["plan-notify", "watch"]


def test_an_uncorrelated_event_stands_alone() -> None:
    """Honest rather than tidy: a watcher's signals really are unrelated to each
    other, and grouping them would invent a story."""
    assert len(render(ROWS)["chains"]) == 2


def test_cost_is_summed_per_chain_from_the_ledger() -> None:
    """The point of the whole exercise — what a chain spent, answered without
    asking any node."""
    chains = {c["chain"]: c for c in render(ROWS)["chains"]}
    assert chains["1"]["cost_usd"] == 0.42
    assert chains["3"]["cost_usd"] == 0.0


def test_unpriced_is_not_free() -> None:
    """A hop nobody priced and a hop that cost nothing are different facts, and
    only the config knows which — so the projection counts them rather than
    flattening both to zero."""
    out = render(ROWS)
    assert out["totals"]["unpriced_hops"] == 2, "only the plan-notify hop carries a cost field"
    assert out["totals"]["cost_usd"] == 0.42
    chains = {c["chain"]: c for c in out["chains"]}
    assert chains["1"]["hops"][0]["cost_usd"] is None, "None, never 0.0, when nobody priced it"


def test_newest_chain_first_and_limit_applies() -> None:
    out = render(ROWS, limit=1)
    assert len(out["chains"]) == 1
    assert out["chains"][0]["chain"] == "3", "chain 3 started at 300, chain 1 at 100"
    assert out["totals"]["chains"] == 1, "totals describe what was returned, not what was read"


def test_a_malformed_cost_does_not_break_the_stream() -> None:
    """`fields` values are strings from a template, so the cost can arrive as
    anything a node put in `meta.cost_usd` — including nothing."""
    rows = [dict(ROWS[1], fields={"cost_usd": "not-a-number"})]
    assert render(rows)["totals"]["cost_usd"] == 0.0


def test_a_return_that_quotes_the_egress_stamp_joins_its_origin() -> None:
    """The form the pipe actually mints.

    delivery.py stamps `hr-<event_id>` on egress and hookjudge echoes exactly
    that back, so a verdict arrives quoting `hr-1887` while the alert it judged
    is keyed `1887`. Grouped on the raw string those are two chains — which on
    production read as "50 chains across 50 hops", every event alone, the same
    output as a pipe nothing had ever answered.

    hookprobe returns the bare id instead, deliberately: it never sees the
    pipe's correlation id. Both forms have to land in one chain, so this pins
    both rather than whichever one was looked at first.
    """
    rows = [
        {
            "id": 1887,
            "received_at": 100.0,
            "source": "ww",
            "title": "alert",
            "level": "high",
            "outcome": "routed",
            "channels": [],
            "fields": {},
            "correlation_id": None,
        },
        {
            "id": 1888,
            "received_at": 200.0,
            "source": "judge-notify",
            "title": "verdict",
            "level": "low",
            "outcome": "routed",
            "channels": [],
            "fields": {},
            "correlation_id": "hr-1887",
        },
        {
            "id": 1889,
            "received_at": 300.0,
            "source": "probe-notify",
            "title": "report",
            "level": "high",
            "outcome": "routed",
            "channels": [],
            "fields": {"cost_usd": "1.44"},
            "correlation_id": "1887",
        },
    ]
    out = render(rows)
    assert [c["chain"] for c in out["chains"]] == ["1887"], "one alert, one chain, both forms in it"
    chain = out["chains"][0]
    assert [h["id"] for h in chain["hops"]] == [1887, 1888, 1889]
    assert chain["cost_usd"] == 1.44

    # Not everything that looks like the prefix is one: a correlation a node
    # chose for itself must stay its own chain rather than being parsed apart.
    odd = render([dict(rows[1], correlation_id="hr-session-42")])
    assert odd["chains"][0]["chain"] == "hr-session-42"


async def test_the_ledger_query_returns_what_the_projections_read(store) -> None:
    """The gap every test above stepped over.

    All of them hand `render()` rows with `fields` and `correlation_id` already
    in them, so the projection was covered and the query that feeds it was not.
    It did not select either column. On a live deployment that produced 39
    chains across 39 hops — every event its own chain — at $0.00 with all 39
    hops unpriced, which is indistinguishable from a quiet week, so nobody
    asked. scripts/assert_node_contract.py went blind the same way at the same
    time, reading every round as "posted 0 signals" and passing.

    So this asserts about the SEAM rather than about either side of it.
    """
    await store.insert_event(
        "plan-notify",
        "fp-1",
        {"title": "the plan", "body": "", "level": "high", "fields": {"cost_usd": "0.42", "origin": "chat / X"}},
        "{}",
        100.0,
        correlation_id="7",
    )
    row = (await store.recent_events(10))[0]
    assert row["fields"] == {"cost_usd": "0.42", "origin": "chat / X"}
    assert row["correlation_id"] == "7"


async def test_the_endpoint_is_read_gated(client) -> None:
    assert (await client.get("/timeline")).status_code == 401  # read gate answers 401; the admin gate answers 403
    answer = await client.get("/timeline", headers={"X-Read-Token": "read-t"})
    assert answer.status_code == 200
    assert {"chains", "totals"} <= answer.json().keys()
