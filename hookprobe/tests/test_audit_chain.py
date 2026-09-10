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

from hookprobe import gate


def _write(audit: Path, n: int = 4) -> None:
    for i in range(n):
        gate.append_audit(audit, {"ts": round(time.time(), 3), "session": "s", "tool": "Bash", "detail": f"cmd {i}"})


def _day_file(audit: Path) -> Path:
    return next(p for p in audit.glob("*.jsonl"))


def test_a_clean_run_verifies(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    _write(audit, 5)
    report = gate.verify_chain(audit)
    assert report["intact"] is True
    assert report["checked"] == 5 and report["unchained"] == 0


def test_an_edited_line_is_caught_and_named(tmp_path: Path) -> None:
    """The case the whole thing exists for: somebody tidies one record."""
    audit = tmp_path / "audit"
    _write(audit, 5)
    path = _day_file(audit)
    lines = path.read_text(encoding="utf-8").splitlines()
    doctored = json.loads(lines[2])
    doctored["detail"] = "something much less alarming"
    lines[2] = json.dumps(doctored, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = gate.verify_chain(audit)
    assert report["intact"] is False
    assert report["broken_at"].endswith(":3"), report["broken_at"]


def test_a_removed_line_is_caught(tmp_path: Path) -> None:
    """Deleting is the tidier's other move, and a chain over content alone
    would not see it — each line naming its predecessor is what does."""
    audit = tmp_path / "audit"
    _write(audit, 5)
    path = _day_file(audit)
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[2]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = gate.verify_chain(audit)
    assert report["intact"] is False


def test_reordering_is_caught(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    _write(audit, 5)
    path = _day_file(audit)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1], lines[3] = lines[3], lines[1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert gate.verify_chain(audit)["intact"] is False


def test_formatting_is_not_content(tmp_path: Path) -> None:
    """The digest is over canonical JSON, not the bytes on disk, so a reader
    that re-serialises differently still verifies. The record is the facts."""
    audit = tmp_path / "audit"
    _write(audit, 3)
    path = _day_file(audit)
    rewritten = [json.dumps(json.loads(raw), indent=None, sort_keys=True) for raw in path.read_text().splitlines()]
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    assert gate.verify_chain(audit)["intact"] is True


def test_lines_written_before_the_chain_existed_are_a_gap_not_a_break(tmp_path: Path) -> None:
    """An honest gap reported as tampering makes the alarm useless on the first
    day it runs — every existing deployment has unchained history."""
    audit = tmp_path / "audit"
    audit.mkdir()
    legacy = audit / (time.strftime("%Y-%m-%d") + ".jsonl")
    legacy.write_text('{"ts": 1, "tool": "Bash"}\n{"ts": 2, "tool": "Read"}\n', encoding="utf-8")
    _write(audit, 2)

    report = gate.verify_chain(audit)
    assert report["intact"] is True
    assert report["unchained"] == 2 and report["checked"] == 2


def test_an_unchained_line_AFTER_the_chain_started_is_a_break(tmp_path: Path) -> None:
    """Because that is what stripping the links off a record looks like."""
    audit = tmp_path / "audit"
    _write(audit, 3)
    path = _day_file(audit)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": 9, "tool": "Bash", "detail": "slipped in"}\n')
    report = gate.verify_chain(audit)
    assert report["intact"] is False
    assert "no link" in report["broken_at"]


def test_the_line_is_never_lost_when_the_chain_cannot_be_kept(tmp_path: Path, monkeypatch) -> None:
    """A missing audit line is worse than an unverifiable one. Verification
    reports the gap; a writer that dropped the record leaves nothing to report."""
    audit = tmp_path / "audit"

    real_open = Path.open

    def refuse_chain(self: Path, *args, **kwargs):
        if self.name == gate._CHAIN_FILE:
            raise OSError("read-only mount")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_chain)
    gate.append_audit(audit, {"ts": 1, "tool": "Bash", "detail": "still recorded"})
    monkeypatch.undo()

    written = _day_file(audit).read_text(encoding="utf-8")
    assert "still recorded" in written
    assert "hash" not in json.loads(written.splitlines()[0])


# ── the off-box half ──────────────────────────────────────────────────────────


def test_the_head_is_cheap_and_empty_before_anything_is_chained(tmp_path: Path) -> None:
    """Read on the path of every report that goes home, so it is one small file
    and never a walk."""
    audit = tmp_path / "audit"
    assert gate.chain_head(audit) == ""
    _write(audit, 2)
    head = gate.chain_head(audit)
    assert len(head) == 64
    last = json.loads(_day_file(audit).read_text(encoding="utf-8").splitlines()[-1])
    assert head == last["hash"]


def test_an_anchored_head_survives_a_rebuilt_chain(tmp_path: Path) -> None:
    """The point of writing the head somewhere else. Chaining alone is evident
    only on this disk: whoever can rewrite the audit can rewrite `.chain` beside
    it and produce something self-consistent — which `verify_chain` accepts.

    What they cannot produce is a chain that still contains a hash the pipe
    recorded hours ago, on its own disk.
    """
    audit = tmp_path / "audit"
    _write(audit, 4)
    anchored = gate.chain_head(audit)  # what a report carried home
    assert gate.chain_anchored(audit, anchored) is True

    # The tidier rewrites history AND relinks it, perfectly.
    _day_file(audit).unlink()
    (audit / gate._CHAIN_FILE).unlink()
    _write(audit, 4)

    assert gate.verify_chain(audit)["intact"] is True, "a rebuilt chain is locally consistent — that is the problem"
    assert gate.chain_anchored(audit, anchored) is False, "and the off-box head is what catches it"


def test_the_anchor_check_is_not_fooled_by_a_substring(tmp_path: Path) -> None:
    """A digest that merely APPEARS in a line — in some detail field — is not
    that line's hash, and must not read as anchored."""
    audit = tmp_path / "audit"
    _write(audit, 2)
    head = gate.chain_head(audit)
    gate.append_audit(audit, {"ts": 9, "tool": "Bash", "detail": f"echo {head}"})
    assert gate.chain_anchored(audit, head) is True

    invented = "f" * 64
    gate.append_audit(audit, {"ts": 10, "tool": "Bash", "detail": f"echo {invented}"})
    assert gate.chain_anchored(audit, invented) is False


def test_a_report_carries_the_head_home(tmp_path: Path, monkeypatch) -> None:
    """Through the real return payload, so the pipe's ledger keeps a copy."""
    import asyncio

    from hookprobe import notify
    from hookprobe.runs import Run, RunStore
    from tests.helpers import make_settings

    settings = make_settings(tmp_path, workdir=tmp_path, return_url="http://relay/hook/probe-notify")
    _write(tmp_path / "audit", 3)
    expected = gate.chain_head(tmp_path / "audit")

    posted: list[dict] = []
    monkeypatch.setattr(
        notify.ReturnDelivery, "_post_return", lambda self, body: (posted.append(json.loads(body)), 200)[1]
    )
    run = Run(session_key="probe:a:1", run_id="r1", origin="relay", text='{"summary": "ok"}')
    run.meta = {"title": "t", "source": "a"}
    asyncio.run(notify.ReturnDelivery(settings, RunStore(tmp_path / "results")).deliver(run, (0.0,)))

    assert posted[-1]["meta"]["audit_head"] == expected
    assert len(expected) == 64
