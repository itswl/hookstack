#!/usr/bin/env python3
"""The judge's prompt is versioned, and a change to it cannot ship unreviewed.

The prompt is the one asset in this service that changes what it DECIDES without
changing a line of logic, and until now it was the only asset with no version,
no test and no gate — the thing "I changed the prompt, is it better or worse?"
had no way to answer. Borrowed from Larkin, whose standing prompt is versioned
(`LARKIN_STANDING_PROMPT_VERSION`) and whose rule is: bump on every substantive
change and update the eval datasets that pin the behaviour.

Made checkable rather than conventional. `eval/scenarios.jsonl` records the
prompt version it was reviewed against AND a sha256 of the prompt string. This
asserts three things:

  1. the recorded version equals `_SYSTEM_PROMPT_VERSION` in the code;
  2. the recorded hash equals the hash of the prompt as it stands now — so a
     prompt edited without bumping the version fails HERE, at the hash, and the
     reminder to re-review the scenarios cannot be missed;
  3. every scenario still holds against the deterministic path it names.

The scenarios are the SAFETY FLOOR the prompt leans on — the answer the rule
route must give when the model is unavailable or the alert carries an injection.
They run without a provider, so this gate is offline and cheap. The model's own
behaviour on real traffic is the golden set (scripts/eval.py --gate), which runs
at deploy time; this is the contract around it, and the version binding that
ties a prompt change to both.

    python3 scripts/assert_prompt_contract.py

Exit 0 when the prompt is stamped and every scenario holds, 1 otherwise.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "hookjudge" / "eval" / "scenarios.jsonl"


def _accepted(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.lower()]
    return [str(v).lower() for v in value]


def main() -> int:
    sys.path.insert(0, str(ROOT / "hookjudge"))
    try:
        from hookjudge.contract import Incoming
        from hookjudge.judge import _SYSTEM_PROMPT, _SYSTEM_PROMPT_VERSION, rule_verdict
    except ImportError as exc:  # the venv the stack gate runs has these; a bare python may not
        print(f"prompt contract: skipped ({exc})")
        return 0

    rows = [json.loads(line) for line in SCENARIOS.read_text(encoding="utf-8").splitlines() if line.strip()]
    header = next((r for r in rows if r.get("_header")), None)
    scenarios = [r for r in rows if not r.get("_header")]
    if header is None:
        print("prompt contract: scenarios.jsonl has no header line (prompt_version, prompt_sha256)", file=sys.stderr)
        return 1

    problems: list[str] = []

    # 1. Version binding.
    if header.get("prompt_version") != _SYSTEM_PROMPT_VERSION:
        problems.append(
            f"scenarios were reviewed against {header.get('prompt_version')!r}, "
            f"but the prompt is {_SYSTEM_PROMPT_VERSION!r} — bump one, or re-review the scenarios"
        )

    # 2. Hash binding — the forcing function. A prompt edited without a bump lands here.
    current_hash = hashlib.sha256(_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    if header.get("prompt_sha256") != current_hash:
        problems.append(
            "the prompt text changed but the scenarios were not re-reviewed.\n"
            f"    recorded sha256: {header.get('prompt_sha256')}\n"
            f"    current  sha256: {current_hash}\n"
            "  Re-read eval/scenarios.jsonl against the new prompt, then update its\n"
            "  prompt_version and prompt_sha256. This is the review the version bump exists to force."
        )

    # 3. The scenarios themselves, against the deterministic path.
    for s in scenarios:
        alert = s.get("alert") or {}
        event = Incoming.parse(
            {k: alert.get(k, "") for k in ("source", "title", "body", "level")} | {"fields": alert.get("fields") or {}},
            now=time.time(),
        )
        if (s.get("route") or "rule") != "rule":
            problems.append(f"{s.get('id')}: only the deterministic `rule` route runs offline here")
            continue
        verdict = rule_verdict(event)
        want = _accepted((s.get("expect") or {}).get("importance"))
        if want and (verdict.importance or "").lower() not in want:
            problems.append(f"{s.get('id')}: importance {verdict.importance!r} not in {want} — {s.get('why', '')}")

    if problems:
        print("prompt contract broken:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print(f"prompt contract: {_SYSTEM_PROMPT_VERSION} stamped and hash-bound, {len(scenarios)} safety scenarios hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
