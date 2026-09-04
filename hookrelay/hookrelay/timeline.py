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
    for row in rows:
        # An event that quoted a correlation id belongs to whatever it quoted;
        # everything else is the head of its own chain.
        key = str(row.get("correlation_id") or row.get("id"))
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
        "totals": {
            "chains": len(out),
            "hops": sum(len(c["hops"]) for c in out),
            "cost_usd": round(sum(c["cost_usd"] for c in out), 6),
            "unpriced_hops": sum(len(c["hops"]) - c["priced_hops"] for c in out),
        },
    }
