"""Four properties the ledger claimed and did not have.

Each one was declared somewhere — in the schema, in a helper's name, in a
docstring — and none of them was enforced or tested. That combination is the
one this repository keeps finding the hard way: a promise with no mechanism
behind it reads exactly like a kept one.
"""

from __future__ import annotations

import aiosqlite
import pytest


def _event(title: str = "t", body: str = "b") -> dict:
    return {"title": title, "body": body, "level": "info", "fields": {}}


async def test_a_delivery_cannot_name_an_event_that_does_not_exist(store) -> None:
    """`REFERENCES events (id)` has been in the schema from the first commit and
    enforced by nothing: SQLite ships foreign keys OFF and the connection never
    said otherwise. The purge deleted children by hand and so never noticed, but
    it was the only writer that did — any other path removing an event left
    orphans that nothing in the system could see."""
    with pytest.raises(aiosqlite.IntegrityError):
        await store.db.execute(
            "INSERT INTO deliveries (event_id, channel, next_attempt_at) VALUES (?, ?, ?)",
            (999999, "nowhere", 0.0),
        )
        await store.db.commit()


async def test_a_write_that_returns_a_value_still_announces(store) -> None:
    """The mechanism behind the missing refreshes, and the reason it hit exactly
    the methods it did.

    A writer that returns something puts `return cursor.rowcount` inside the
    lock, and a `return` leaves the block before any line after it runs. So
    `add_silence` announced and `delete_silence` did not — remove a silence and
    the boards went on showing one that was gone. An `__aexit__` cannot be
    returned past, which is why the commit and the announcement now live in one.
    """
    woke: list[int] = []
    store.on_change = lambda: woke.append(1)

    silence_id = await store.add_silence("*", 9999.0, "", 0.0)
    assert woke, "adding one always did announce"
    woke.clear()

    assert await store.delete_silence(silence_id) is True
    assert woke, "and removing one is the same event to a board"


async def test_a_failed_write_neither_commits_nor_announces(store) -> None:
    """The other half: an announcement is a claim that the ledger MOVED."""
    woke: list[int] = []
    store.on_change = lambda: woke.append(1)
    with pytest.raises(aiosqlite.IntegrityError):
        async with store._write():
            await store.db.execute(
                "INSERT INTO deliveries (event_id, channel, next_attempt_at) VALUES (?, ?, ?)",
                (999999, "nowhere", 0.0),
            )
    assert woke == [], "nothing landed, so nothing to look at"


async def test_a_search_term_is_a_substring_and_not_a_pattern(store) -> None:
    """`LIKE '%' + text + '%'` makes the operator's own text a pattern. Searching
    for a percentage matched every row and scanned the table to prove it — the
    one query that looks most like a filter returning everything."""
    await store.insert_event("s", "fp-1", _event(title="disk at 50% and climbing"), "{}", 100.0)
    # Deliberately shares the digits and not the percent: unescaped, `50%`
    # becomes `%50%%` and takes this row too, which is what made the bug look
    # like a search that merely returned a bit much.
    await store.insert_event("s", "fp-2", _event(title="queue depth 500"), "{}", 200.0)

    hits = await store.recent_events(50, query="50%")
    assert [r["title"] for r in hits] == ["disk at 50% and climbing"]
    assert await store.recent_events(50, query="_") == [], "`_` is the quiet half of the same bug"


async def test_the_purge_never_deletes_an_event_with_a_promise_still_queued(store) -> None:
    """The invariant the batching had to preserve when it started releasing the
    lock between batches: an operator retrying a dead delivery in the gap would
    re-queue a promise against an event a list taken up front had already
    condemned."""
    keep = await store.insert_event("s", "fp-keep", _event(), "{}", 100.0)
    drop = await store.insert_event("s", "fp-drop", _event(), "{}", 100.0)
    await store.enqueue_delivery(keep, "somewhere", 100.0)

    purged = await store.purge_older_than(cutoff=1000.0, now=1000.0)
    assert purged["events"] == 1
    assert [r["id"] for r in await store.recent_events(50)] == [keep]
    assert drop not in [r["id"] for r in await store.recent_events(50)]
