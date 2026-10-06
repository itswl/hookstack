"""The one promise left about the watcher's bookkeeping: every watch signal in
the pipe came through the signer. Lives under hookrelay/tests because that is
the venv the stack gate runs and the checker reads the pipe's ledger; it
asserts nothing about hookrelay itself.

The pipe ledger here is the real 2026-09-04 16:40 round, kept from the retired
contract checker's fixtures: one watch signal for one conversation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CHECKER = ROOT / "scripts" / "assert_watch_signed.py"
FIX = ROOT / "scripts" / "fixtures" / "watch-signed"
SINCE = "1788510500"


def run(ledger: Path, signer: Path, since: str = SINCE) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--ledger", str(ledger), "--signer-ledger", str(signer), "--since", since],
        capture_output=True,
        text=True,
    )


def rows(tmp_path: Path, *rows_: dict) -> Path:
    path = tmp_path / "signals.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows_))
    return path


def test_a_signal_the_signer_signed_passes(tmp_path):
    signer = rows(tmp_path, {"event": "signal.signed", "ts": 1788511200.5, "subject": "BCP-SRE", "cursor": 1.0})
    done = run(FIX / "unsigned-ledger.json", signer)
    assert done.returncode == 0, done.stdout


def test_a_signal_the_signer_never_signed_went_around_the_boundary():
    done = run(FIX / "unsigned-ledger.json", FIX / "empty-signals.jsonl")
    assert done.returncode == 1 and "FAIL" in done.stdout and "BCP-SRE" in done.stdout


def test_a_signer_row_from_before_the_subject_field_is_read_by_its_origin(tmp_path):
    signer = rows(tmp_path, {"event": "signal.signed", "ts": 1788511200.5, "origin": "chat / BCP-SRE"})
    assert run(FIX / "unsigned-ledger.json", signer).returncode == 0


def test_what_is_not_a_conversation_is_not_expected_in_the_signers_ledger(tmp_path):
    """The timer's own findings, the scanner's notes and a laptop poster's bare
    origin never pass the signer, and must not be reported as going around it."""
    ledger = json.loads((FIX / "unsigned-ledger.json").read_text())
    template = next(r for r in ledger["recent"] if r.get("source") == "watch")
    ledger["recent"] = []
    for origin in ("patrol-timer / signed", "scanner / scanner-notes", "weekly-page"):
        row = json.loads(json.dumps(template))
        row["fields"]["origin"] = origin
        ledger["recent"].append(row)
    pipe = tmp_path / "status.json"
    pipe.write_text(json.dumps(ledger))
    done = run(pipe, FIX / "empty-signals.jsonl")
    assert done.returncode == 0, done.stdout


def test_only_signals_since_the_stamp_are_checked():
    done = run(FIX / "unsigned-ledger.json", FIX / "empty-signals.jsonl", since="1788600000")
    assert done.returncode == 0
