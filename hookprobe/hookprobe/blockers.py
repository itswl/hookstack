"""A gap nobody can act on is not a finding, it is noise repeated.

Measured on the planning node on 2026-09-11, over every `task` report it has
ever written: 11 reports, 10 of them carrying an `unknowns` section, and ZERO
of them carrying a single runnable command or a name an operator could act on.
Three consecutive reports opened with the same line — "GITHUB_TOKEN 为空" —
and the operator answered it the same way each time: by leaving the chat,
opening a terminal that DOES have access, and re-establishing by hand the
context the investigation already had.

The reason that line kept coming back is the part worth building for. From
inside the container, an environment variable nobody set and an environment
variable no compose PASSES are indistinguishable — both read as empty. So the
report said "the token is empty", which is unactionable, rather than "nothing
passes GITHUB_TOKEN to me", which is one compose line. Measured the same day:
no compose passed it, AND `api.github.com` was not on the egress allowlist, so
setting the token would not have helped either. The advice was unactionable
twice over and cost three investigations their answer.

So a gap declares its KIND and the key that opens it, in a fenced block the
service lifts out of the report — the same mechanism as ```remediation, for
the same reason: prose at the bottom of a report is read by a person once and
by nothing ever again.

    credential  an env var this process would need      → name it
    egress      a host the proxy refused                → name it
    question    only the requester can answer it        → ask it
    probe       a system this node has no route to      → hand over the command

What this module does NOT do is judge whether the gap is real. The agent
writes these, so an agent that omits a blocker simply has none recorded — the
same honest floor as the remediation cooldown's model-written key. What it
buys is arithmetic: the same blocker across N investigations is a number
(`tally`), and a number gets fixed once instead of being read N times.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

# `blocked:` with a colon is what a runner wrote on 2026-09-22 (trial 4) after
# being told "a fenced block labelled blocked", and the strict fence matched
# nothing — a gap declared in the agreed shape, dropped on a punctuation mark.
# Be liberal in what a report may write; the JSON inside is still strict.
_BLOCK = re.compile(r"```blocked:?\s*\n(.*?)```", re.DOTALL)
_MAX = 6
_KINDS = ("credential", "egress", "question", "probe")


def extract(text: str) -> list[dict[str, Any]]:
    """The gaps a report declared, validated — or nothing.

    Shaped exactly like `remediation.extract`, including leaving the block in
    the report: a person reading the case file wants to see what stopped it,
    and this only lifts a structured copy for the arithmetic.
    """
    match = _BLOCK.search(text or "")
    if not match:
        return []
    try:
        raw = json.loads(match.group(1))
    except ValueError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw[:_MAX]:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "").strip().lower()
        answers = str(entry.get("answers") or "").strip()
        # `name` for the three kinds that have one; a probe carries a command
        # instead, because "the name of the thing I cannot reach" is not what
        # the operator needs — the command is.
        name = str(entry.get("name") or "").strip()
        command = str(entry.get("command") or "").strip()
        if kind not in _KINDS or not answers:
            continue
        if kind == "probe" and not command:
            continue
        if kind != "probe" and not name:
            continue
        out.append(
            {
                "kind": kind,
                "name": name[:120],
                "command": command[:500],
                "answers": answers[:300],
            }
        )
    return out


def annotate(entries: list[dict[str, Any]], env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Fill in the half of a credential blocker the AGENT cannot see.

    `os.environ.get(NAME)` returns None for "nothing passes this to me" and ""
    for "something passes it, empty" — and those are different repairs: a
    compose line versus an `.env` line. The distinction is invisible from
    inside a report and decisive to whoever fixes it, which is exactly why
    three reports in a row said the useless half.

    Deliberately reads only the NAME's presence, never its value: a blocker
    that quoted a token would put it in the case file, the audit and the chat.
    """
    source = os.environ if env is None else env
    out = []
    for entry in entries:
        row = dict(entry)
        if row["kind"] == "credential":
            value = source.get(row["name"])
            row["reach"] = "absent" if value is None else ("empty" if not value.strip() else "set")
            row["repair"] = {
                "absent": f"nothing passes {row['name']} to this container — add it to the compose",
                "empty": f"{row['name']} arrives empty — set it in .env",
                "set": "",
            }[row["reach"]]
        out.append(row)
    return out


def key(entry: dict[str, Any]) -> str:
    """What makes two blockers the same blocker, for counting."""
    return f"{entry.get('kind')}:{(entry.get('name') or entry.get('command') or '').strip().lower()}"


def tally(runs: list[Any], limit: int = 10) -> list[dict[str, Any]]:
    """The same gap across investigations, counted — the whole point.

    One report saying "no GitHub token" is a sentence somebody skims. Three
    reports saying it is a number, and a number is what gets a compose line
    written. Derived on every request from the run records rather than stored,
    the same rule the work board follows: nothing here is a second copy.
    """
    seen: dict[str, dict[str, Any]] = {}
    for run in runs:
        for entry in (getattr(run, "meta", None) or {}).get("blocked_on") or []:
            if not isinstance(entry, dict):
                continue
            row = seen.setdefault(
                key(entry),
                {
                    "kind": entry.get("kind"),
                    "name": entry.get("name") or entry.get("command"),
                    "runs": 0,
                    "answers": entry.get("answers"),
                },
            )
            row["runs"] += 1
    ranked = sorted(seen.values(), key=lambda r: (-r["runs"], str(r["name"])))
    return ranked[:limit]
