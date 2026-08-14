"""Turn a pile of captured alerts into a dataset worth labelling.

Reads JSON (array or one object per line) on stdin and writes dataset rows on
stdout. Two things happen on the way, and both exist to make the labelling
affordable:

  * **Collapsing to one row per alert rule.** 800 alerts from one Grafana are
    not 800 distinct judgements. Measured on a real stream: 795 alerts collapse
    to 601 identities but only **29 rules**, three of which produce 80% of the
    volume. Identity is the wrong key here by design — it keeps the instance
    labels, so every host becomes its own row — while the rule is what a
    judgement is actually about. `seen` records the volume behind each row, so
    labelling in that order buys the most coverage per minute: 29 rows cover
    everything that system has ever sent.
  * **Suggestions, marked as such.** When the input carries a verdict, it is
    copied into `expect` and the row stays `reviewed: false`. Correcting a draft
    is much faster than typing from a blank line, but an unreviewed suggestion
    is the model's own answer, so scripts/eval.py refuses to score it until a
    human sets reviewed: true.

    cat alerts.json | .venv/bin/python scripts/eval_import.py > eval/dataset.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hookjudge.contract import Incoming

_FIRING_PREFIX = re.compile(r"^\[(?:FIRING|RESOLVED)(?::\d+)?\]\s*")


def read_input(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip()
    if not raw:
        return []
    if raw.startswith("["):
        return list(json.loads(raw))
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    """Accept the shapes the family actually stores alerts in."""
    fields = row.get("fields") or row.get("fields_json") or {}
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except ValueError:
            fields = {}
    return {
        "source": str(row.get("source") or row.get("platform") or "unknown"),
        "title": str(row.get("title") or row.get("summary") or ""),
        "body": str(row.get("body") or row.get("message") or row.get("description") or ""),
        "level": str(row.get("level") or row.get("severity") or ""),
        "fields": {str(k): str(v) for k, v in dict(fields).items()},
    }


def collapse_key(alert: dict[str, Any], mode: str) -> str:
    """What counts as "the same alert" for labelling purposes."""
    if mode == "identity":
        return Incoming.parse(alert, now=time.time()).identity
    title = alert["title"].strip()
    if mode == "title":
        return title
    # Grafana and Alertmanager both name the rule in a label, and that label is
    # the only reliable key: the title carries the instance detail, and its
    # "[FIRING:2]" prefix means splitting on the colon just sorts the stream
    # into firing and resolved.
    fields = alert.get("fields") or {}
    for label in ("alertname", "rulename", "RuleName"):
        named = str(fields.get(label) or "").strip()
        if named:
            return named
    return _FIRING_PREFIX.sub("", title).strip() or title


def slug(key: str) -> str:
    cleaned = "".join(char if char.isalnum() else "-" for char in key.lower())
    return "-".join(part for part in cleaned.split("-") if part)[:48] or "unnamed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-body", type=int, default=2000, help="truncate long bodies")
    parser.add_argument("--min-seen", type=int, default=1, help="drop rules rarer than this")
    parser.add_argument(
        "--collapse-by",
        choices=["rule", "title", "identity"],
        default="rule",
        help="rule (title before the first colon) groups an alert family; identity keeps every instance apart",
    )
    args = parser.parse_args()

    collapsed: dict[str, dict[str, Any]] = {}
    for raw_row in read_input(sys.stdin.read()):
        alert = normalize(raw_row)
        alert["body"] = alert["body"][: args.max_body]
        key = collapse_key(alert, args.collapse_by)
        existing = collapsed.get(key)
        if existing:
            existing["seen"] += 1
            continue
        expect = {}
        for label in ("importance", "event_type"):
            value = raw_row.get(label)
            if value:
                expect[label] = str(value).lower()
        collapsed[key] = {
            "id": slug(key),
            "seen": 1,
            "alert": alert,
            "expect": expect,
            # Set to true once a human has confirmed `expect`; until then this
            # row is counted but never scored.
            "reviewed": False,
            "note": "",
        }

    kept = sorted(
        (row for row in collapsed.values() if row["seen"] >= args.min_seen),
        key=lambda row: -row["seen"],
    )
    for row in kept:
        print(json.dumps(row, ensure_ascii=False))
    print(
        f"{len(kept)} conditions from {sum(r['seen'] for r in kept)} alerts",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
