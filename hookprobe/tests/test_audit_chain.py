"""The flight recorder, linked — so an edit or a deletion afterwards shows.

The audit is what a compliance reader is actually asking about, and until this
it was append-only JSONL: a line rewritten later was indistinguishable from one
written that way. These tests are the tamper cases, plus the two ways an honest
gap must NOT be reported as tampering.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from hookprobe import audit


def _write(audit_dir: Path, n: int = 4) -> None:
    for i in range(n):
        audit.append(audit_dir, {"ts": round(time.time(), 3), "session": "s", "tool": "Bash", "detail": f"cmd {i}"})


def _day_file(audit_dir: Path) -> Path:
    return next(p for p in audit_dir.glob("*.jsonl"))


def test_a_clean_run_verifies(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    _write(audit_dir, 5)
    report = audit.verify_chain(audit_dir)
    assert report["intact"] is True
    assert report["checked"] == 5 and report["unchained"] == 0


def test_an_edited_line_is_caught_and_named(tmp_path: Path) -> None:
    """The case the whole thing exists for: somebody tidies one record."""
    audit_dir = tmp_path / "audit"
    _write(audit_dir, 5)
    path = _day_file(audit_dir)
    lines = path.read_text(encoding="utf-8").splitlines()
    doctored = json.loads(lines[2])
    doctored["detail"] = "something much less alarming"
    lines[2] = json.dumps(doctored, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = audit.verify_chain(audit_dir)
    assert report["intact"] is False
    assert report["broken_at"].endswith(":3"), report["broken_at"]


def test_a_removed_line_is_caught(tmp_path: Path) -> None:
    """Deleting is the tidier's other move, and a chain over content alone
    would not see it — each line naming its predecessor is what does."""
    audit_dir = tmp_path / "audit"
    _write(audit_dir, 5)
    path = _day_file(audit_dir)
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[2]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = audit.verify_chain(audit_dir)
    assert report["intact"] is False


def test_reordering_is_caught(tmp_path: Path) -> None:
    audit_dir = tmp_path / "audit"
    _write(audit_dir, 5)
    path = _day_file(audit_dir)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1], lines[3] = lines[3], lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert audit.verify_chain(audit_dir)["intact"] is False


def test_formatting_is_not_content(tmp_path: Path) -> None:
    """The digest is over canonical JSON, not the bytes on disk, so a reader
    that re-serialises differently still verifies. The record is the facts."""
    audit_dir = tmp_path / "audit"
    _write(audit_dir, 3)
    path = _day_file(audit_dir)
    rewritten = [json.dumps(json.loads(raw), indent=None, sort_keys=True) for raw in path.read_text().splitlines()]
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    assert audit.verify_chain(audit_dir)["intact"] is True


def test_lines_written_before_the_chain_existed_are_a_gap_not_a_break(tmp_path: Path) -> None:
    """An honest gap reported as tampering makes the alarm useless on the first
    day it runs — every existing deployment has unchained history."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    legacy = audit_dir / (time.strftime("%Y-%m-%d") + ".jsonl")
    legacy.write_text('{"ts": 1, "tool": "Bash"}\n{"ts": 2, "tool": "Read"}\n', encoding="utf-8")
    _write(audit_dir, 2)

    report = audit.verify_chain(audit_dir)
    assert report["intact"] is True
    assert report["unchained"] == 2 and report["checked"] == 2


def test_an_unchained_line_AFTER_the_chain_started_is_a_break(tmp_path: Path) -> None:
    """Because that is what stripping the links off a record looks like."""
    audit_dir = tmp_path / "audit"
    _write(audit_dir, 3)
    path = _day_file(audit_dir)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": 9, "tool": "Bash", "detail": "slipped in"}\n')
    report = audit.verify_chain(audit_dir)
    assert report["intact"] is False
    assert "no link" in report["broken_at"]


def test_the_line_is_never_lost_when_the_chain_cannot_be_kept(tmp_path: Path, monkeypatch) -> None:
    """A missing audit line is worse than an unverifiable one. Verification
    reports the gap; a writer that dropped the record leaves nothing to report."""
    audit_dir = tmp_path / "audit"

    real_open = Path.open

    def refuse_chain(self: Path, *args, **kwargs):
        if self.name == audit._CHAIN_FILE:
            raise OSError("read-only mount")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_chain)
    audit.append(audit_dir, {"ts": 1, "tool": "Bash", "detail": "still recorded"})
    monkeypatch.undo()

    written = _day_file(audit_dir).read_text(encoding="utf-8")
    assert "still recorded" in written
    assert "hash" not in json.loads(written.splitlines()[0])


# ── the off-box half ──────────────────────────────────────────────────────────


def test_the_head_is_cheap_and_empty_before_anything_is_chained(tmp_path: Path) -> None:
    """Read on the path of every report that goes home, so it is one small file
    and never a walk."""
    audit_dir = tmp_path / "audit"
    assert audit.chain_head(audit_dir) == ""
    _write(audit_dir, 2)
    head = audit.chain_head(audit_dir)
    assert len(head) == 64
    last = json.loads(_day_file(audit_dir).read_text(encoding="utf-8").splitlines()[-1])
    assert head == last["hash"]


def test_an_anchored_head_survives_a_rebuilt_chain(tmp_path: Path) -> None:
    """The point of writing the head somewhere else. Chaining alone is evident
    only on this disk: whoever can rewrite the audit can rewrite `.chain` beside
    it and produce something self-consistent — which `verify_chain` accepts.

    What they cannot produce is a chain that still contains a hash the pipe
    recorded hours ago, on its own disk.
    """
    audit_dir = tmp_path / "audit"
    _write(audit_dir, 4)
    anchored = audit.chain_head(audit_dir)  # what a report carried home
    assert audit.chain_anchored(audit_dir, anchored) is True

    # The tidier rewrites history AND relinks it, perfectly — and rewriting
    # means REMOVING something, which is why the rebuild is three lines and not
    # four. The first version of this test rebuilt an identical four and was
    # flaky: `_write` stamps `round(time.time(), 3)`, both passes can land in
    # the same millisecond, and byte-identical lines hash to a byte-identical
    # head. That is the digest being correct. A tidier who reproduces the
    # record exactly has deleted nothing and there is nothing to catch.
    _day_file(audit_dir).unlink()
    (audit_dir / audit._CHAIN_FILE).unlink()
    _write(audit_dir, 3)

    assert audit.verify_chain(audit_dir)["intact"] is True, (
        "a rebuilt chain is locally consistent — that is the problem"
    )
    # The message carries both heads on purpose, and it earned that within the
    # hour: the assertion failed twice on 2026-09-10 and the second failure
    # printed `anchor=39ae873a… rebuilt=39ae873a…` — EQUAL, which named the
    # cause as the fixture's millisecond timestamps rather than anything in
    # `chain_anchored`. `assert True is False` had said nothing at all. Kept,
    # because the next failure here deserves the same head start.
    rebuilt = audit.chain_head(audit_dir)
    assert audit.chain_anchored(audit_dir, anchored) is False, (
        f"the off-box head is what catches it — anchor={anchored} rebuilt={rebuilt}"
    )


def test_the_anchor_check_is_not_fooled_by_a_substring(tmp_path: Path) -> None:
    """A digest that merely APPEARS in a line — in some detail field — is not
    that line's hash, and must not read as anchored."""
    audit_dir = tmp_path / "audit"
    _write(audit_dir, 2)
    head = audit.chain_head(audit_dir)
    audit.append(audit_dir, {"ts": 9, "tool": "Bash", "detail": f"echo {head}"})
    assert audit.chain_anchored(audit_dir, head) is True

    invented = "f" * 64
    audit.append(audit_dir, {"ts": 10, "tool": "Bash", "detail": f"echo {invented}"})
    assert audit.chain_anchored(audit_dir, invented) is False


def test_a_report_carries_the_head_home(tmp_path: Path, monkeypatch) -> None:
    """Through the real return payload, so the pipe's ledger keeps a copy."""
    import asyncio

    from hookprobe import notify
    from hookprobe.runs import Run, RunStore
    from tests.helpers import make_settings

    settings = make_settings(tmp_path, workdir=tmp_path, return_url="http://relay/hook/probe-notify")
    _write(tmp_path / "audit", 3)
    expected = audit.chain_head(tmp_path / "audit")

    posted: list[dict] = []
    monkeypatch.setattr(
        notify.ReturnDelivery, "_post_return", lambda self, body: (posted.append(json.loads(body)), 200)[1]
    )
    run = Run(session_key="probe:a:1", run_id="r1", origin="relay", text='{"summary": "ok"}')
    run.meta = {"title": "t", "source": "a"}
    asyncio.run(notify.ReturnDelivery(settings, RunStore(tmp_path / "results")).deliver(run, (0.0,)))

    assert posted[-1]["meta"]["audit_head"] == expected
    assert len(expected) == 64


def test_a_window_that_starts_after_genesis_does_not_break_the_chain(tmp_path: Path) -> None:
    """THE bug this suite missed for six days of production: verify_chain seeded
    its walk at `_CHAIN_SEED` and demanded the first line it saw name it — but
    once genesis rolled out of the CHAIN_DAYS window (2026-09-17, a week after
    chaining shipped), the first in-window line legitimately named *yesterday*,
    and every hourly check "broke" at line 1 while the chain was intact.

    The first chained line in a window is the entry root: its own hash must
    recompute, and where it points is outside the window's business — that is
    what the off-box anchor is for.
    """
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    # One continuous chain across a month of day files — the .chain file's job,
    # recreated here by carrying `prev` across days the way append() does.
    prev = audit._CHAIN_SEED
    for day in range(1, 32):
        date = time.strftime("%Y-%m-%d", time.localtime(time.time() - (31 - day) * 86400))
        rows = []
        for i in range(3):
            body = {"ts": day * 100 + i, "session": "s", "tool": "Bash", "detail": f"{day}-{i}", "prev": prev}
            body["hash"] = audit._line_hash(body)
            rows.append(body)
            prev = body["hash"]
        (audit_dir / f"{date}.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8"
        )

    report = audit.verify_chain(audit_dir, days=7)
    assert report["intact"] is True, report
    assert report["checked"] == 21 and report["broken_at"] is None

    # A genuine break inside the window is still caught, including at a file's
    # first line — the seeding must not become a way to restart the chain.
    newest = sorted(audit_dir.glob("*.jsonl"))[-1]
    rows = [json.loads(x) for x in newest.read_text().splitlines() if x.strip()]
    rows[1]["detail"] = "edited after the fact"
    newest.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    report = audit.verify_chain(audit_dir, days=7)
    assert report["intact"] is False and report["broken_at"] is not None

    # And a mid-window file claiming genesis is a break too, not a new root.
    rows = [json.loads(x) for x in newest.read_text().splitlines() if x.strip()]
    rows[0]["prev"] = audit._CHAIN_SEED  # rewind the link; recompute so only the LIE is wrong
    rows[0].pop("hash")
    rows[0]["hash"] = audit._line_hash(rows[0])
    newest.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")
    report = audit.verify_chain(audit_dir, days=7)
    assert report["intact"] is False, report
