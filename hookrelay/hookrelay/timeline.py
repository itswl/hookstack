"""One stream of what happened, projected from the ledger that already holds it.

The pipe records every hop — the event, the decision, each delivery with the
bytes that left the socket. It has always been the only place where a whole
chain is visible, because every handover goes through it by construction. What
it lacked was a way to READ it as one thing: `/status` answers "recently", and
`/trace/{id}` answers "this one", and neither answers "what happened".

That gap had a cost measurable in a single afternoon: answering "how is the
deployment doing" meant five endpoints across two machines and a human joining
them by eye.

Deliberately NOT a shared event store. Apache Maka keeps one runtime event log
as the single source of truth with every UI a projection of it, which is right
for one process — and wrong here, because a node in this family may be written
by somebody else and run somewhere else, and a store it must write to is a
coupling that would take the replaceable node with it. The pipe's ledger is
already the single truth for HANDOVERS, which is the only layer every node has
in common. This projects that, and asks nothing new of any node.

A chain is what one original event became: the hops that quoted its correlation
id, plus the original. Events with no correlation stand alone, which is honest —
a watcher's signals really are unrelated to each other.
"""

from __future__ import annotations

from typing import Any


def _chain_key(row: dict[str, Any]) -> str:
    """Which chain this hop belongs to: what it quoted, else its own id.

    delivery.py stamps `hr-<event_id>` on egress and hookjudge echoes it back,
    so `hr-1887` and the event whose id is 1887 are one chain — keyed apart, a
    verdict sits in a chain of its own beside the alert it judged. store.py
    resolves the same prefix for /trace/{id}; the pipe mints it in one place and
    this is the third reader of it.
    """
    quoted = str(row.get("correlation_id") or "")
    if quoted.startswith("hr-") and quoted[3:].isdigit():
        return quoted[3:]
    return quoted or str(row.get("id"))


def _cost(row: dict[str, Any]) -> float:
    """What this hop cost, when the node that produced it said so.

    Comes from `fields.cost_usd`, which a return door extracts from the
    processed-event's `meta.cost_usd` — so it is present exactly when the config
    asked for it, and absent rather than zero when it did not. Absent and zero
    are different facts here: one is an unpriced hop, the other is a free one.
    """
    raw = (row.get("fields") or {}).get("cost_usd")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        # Still caught rather than assumed away: `fields` values arrive from a
        # template, so a node can put anything in meta.cost_usd — including a
        # string that is not a number.
        return 0.0


def render(rows: list[dict[str, Any]], limit: int = 50) -> dict[str, Any]:
    """Chronological chains, newest first, with what each one spent."""
    chains: dict[str, list[dict[str, Any]]] = {}
    # Which burst a chain belongs to, when a hop carried one. The judge groups N
    # alerts from one origin into a burst and now sends `fields.burst_id` back;
    # the pipe reads it, never computes it — that grouping is a judgement about
    # content, and the pipe is content-blind. A chain inherits the burst_id of
    # any hop that has one (the return hop from the judge does).
    chain_burst: dict[str, str] = {}
    for row in rows:
        key = _chain_key(row)
        chains.setdefault(key, []).append(
            {
                "id": row.get("id"),
                "at": row.get("received_at"),
                "door": row.get("source"),
                "title": (row.get("title") or "")[:120],
                "level": row.get("level"),
                "outcome": row.get("outcome"),
                "skip_code": row.get("skip_code"),
                "to": row.get("channels") or [],
                "cost_usd": _cost(row) or None,
            }
        )
        burst = str((row.get("fields") or {}).get("burst_id") or "")
        if burst:
            chain_burst[key] = burst

    out = []
    for key, hops in chains.items():
        hops.sort(key=lambda h: float(h.get("at") or 0))
        spent = sum(h["cost_usd"] or 0.0 for h in hops)
        out.append(
            {
                "chain": key,
                "hops": hops,
                "started_at": hops[0]["at"],
                "doors": sorted({h["door"] for h in hops}),
                # Stated even when zero, because "this chain was free" and "nobody
                # priced this chain" are different and only the config knows which.
                "cost_usd": round(spent, 6),
                "priced_hops": sum(1 for h in hops if h["cost_usd"] is not None),
            }
        )
    out.sort(key=lambda c: float(c["started_at"] or 0), reverse=True)
    out = out[:limit]

    return {
        "chains": out,
        "incidents": _incidents(out, chain_burst),
        "totals": {
            "chains": len(out),
            "hops": sum(len(c["hops"]) for c in out),
            "cost_usd": round(sum(c["cost_usd"] for c in out), 6),
            "unpriced_hops": sum(len(c["hops"]) - c["priced_hops"] for c in out),
        },
    }


def _incidents(chains: list[dict[str, Any]], chain_burst: dict[str, str]) -> list[dict[str, Any]]:
    """Chains that share a burst are one incident — the operator's unit.

    A chain is one alert and its round trip. An incident is what a person works
    on: five SES alerts crossing five thresholds are one reputation problem, and
    the number that matters is not how many chains there were but how many times
    ONE incident interrupted somebody. That is `interruptions` here, and it is
    the honest version of a noise metric — five cards for one root cause is the
    noise, and until burst_id travelled back nothing could see it was one cause.

    A chain with no burst stands alone, an incident of one, which is honest: most
    alerts really are unrelated, and grouping them would invent a story. Grouped
    only, never named — the pipe reports the shape (these chains share a burst)
    and leaves the meaning to whoever reads it.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for chain in chains:
        # Its own chain key when no burst — an incident of one keeps a stable id.
        key = chain_burst.get(chain["chain"], f"solo:{chain['chain']}")
        groups.setdefault(key, []).append(chain)
    incidents = []
    for key, members in groups.items():
        titles = [h["title"] for c in members for h in c["hops"] if h.get("title")]
        incidents.append(
            {
                "incident": key,
                "chains": [c["chain"] for c in members],
                # What the operator wants driven down: cards for one root cause.
                "interruptions": len(members),
                "started_at": min(float(c["started_at"] or 0) for c in members),
                "cost_usd": round(sum(c["cost_usd"] for c in members), 6),
                # One example title, so a burst id is legible without opening it.
                "example": titles[0] if titles else "",
            }
        )
    incidents.sort(key=lambda i: i["started_at"], reverse=True)
    return incidents
