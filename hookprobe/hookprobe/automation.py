"""How far automation may go, and the record that lets it earn more ground.

Two things live here, one for policy and one for evidence, because a write gate
should move only when both agree:

  THE TIER is a declared ceiling per class of automation. A class may do at most
  its tier, and the ladder is ordered — verdict_only < investigate < propose <
  auto_apply. It generalises the one tier knob this service already had
  (escalate_levels: which alerts earn a paid run) into something an operator can
  read in one place instead of inferring from four scattered switches.

  THE RECORD is what a class actually did: every proposal, and every decision a
  human made about it — approved, dismissed, and later regretted. Graduation to
  a higher tier is justified by the record, never by an argument, and never by
  an agreement rate. That last exclusion is the whole point: two models agreeing
  measures the instrument, not the truth, so a trust score built on agreement is
  386 samples of a model nodding at itself. Every number here comes from a
  human's press or a sampling review — a label the family already collects.

WHAT THIS DOES NOT DO, stated because the restraint is the design:

  It never moves a tier on its own. `supports()` answers "would the record
  justify this ceiling", and an operator writes the ceiling. A gate that raised
  itself the moment its counters looked good would be the record grading its own
  homework — the exact failure it exists to prevent. Both the tier config and
  the record are files an operator diffs.

  It does not replace the shape check in front of memory, or the allowlist in
  front of remediation. A track record answers "has this been going well"; it
  does not answer "can this input carry an injection". Different axes, both
  required, and memory's auto-apply stays behind its red-team smoke however
  clean its record looks.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# Low to high. The index IS the comparison: a class configured `propose` may not
# `auto_apply`, and asking is `TIERS.index(want) <= TIERS.index(ceiling)`.
TIERS: tuple[str, ...] = ("verdict_only", "investigate", "propose", "auto_apply")

# The decisions a record is built from. `proposed` is the denominator; the rest
# are what a human (or an after-the-fact sampling review) said about one.
EVENTS: tuple[str, ...] = ("proposed", "auto_applied", "approved", "dismissed", "regretted")

_LOG = "automation-log.jsonl"
_LOG_CAP = 5000  # oldest lines drop; this is a rolling record, not an archive

# Defaults preserve today's behaviour EXACTLY, so turning this module on changes
# nothing until an operator edits a ceiling. memory ships at auto_apply because
# its shape-safe lines already self-apply (gated by shape, not by this); the
# rest sit where their current knobs leave them.
_DEFAULT_TIERS: dict[str, str] = {
    "memory": "auto_apply",
    "remediation": "propose",
    "silence": "propose",
    "distill": "auto_apply",
}


def parse_tiers(spec: str) -> dict[str, str]:
    """`memory=propose,remediation=propose` over the defaults.

    An unknown tier name is dropped with the default kept rather than raising:
    this is read at construction and a typo must not take the service down, but
    it must also not silently grant a higher ceiling than the operator spelled.
    So an unparseable entry fails toward the LOWER of default and nothing, never
    higher.
    """
    tiers = dict(_DEFAULT_TIERS)
    for part in (spec or "").split(","):
        name, _, tier = part.partition("=")
        name, tier = name.strip(), tier.strip()
        if name and tier in TIERS:
            tiers[name] = tier
    return tiers


def permits(tiers: dict[str, str], cls: str, want: str) -> bool:
    """May `cls` do `want`? Its ceiling is its configured tier, default propose.

    An unknown class defaults to `propose`, not `auto_apply`: a behaviour nobody
    declared a ceiling for may propose and no more, which is the safe direction
    for a class added in code before its config line exists.
    """
    ceiling = tiers.get(cls, "propose")
    if want not in TIERS or ceiling not in TIERS:
        return False
    return TIERS.index(want) <= TIERS.index(ceiling)


def record(workdir: Path, cls: str, item_id: str, event: str, **detail: Any) -> None:
    """Append one line to the record. Never raises into the caller.

    A record that broke a memory write or a remediation approval would be a
    bookkeeping tail wagging the dog — the decision already happened, and losing
    the note of it is worse repaired by a missing line than by a failed action.
    """
    if event not in EVENTS:
        return
    path = workdir / _LOG
    row = {"at": round(time.time(), 3), "class": cls, "id": item_id, "event": event, **detail}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        _trim(path)
    except OSError:
        return


def _trim(path: Path) -> None:
    """Keep the tail. A rolling record, so the oldest proposals age out once the
    class has a long enough run that the recent ones are what a graduation reads."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= _LOG_CAP:
        return
    try:
        path.write_text("\n".join(lines[-_LOG_CAP:]) + "\n", encoding="utf-8")
    except OSError:
        return


def ledger(workdir: Path, cls: str | None = None) -> list[dict[str, Any]]:
    """The record, oldest first, optionally for one class."""
    try:
        raw = (workdir / _LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in raw:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a torn last line on a crash is not the whole record
        if cls is None or row.get("class") == cls:
            rows.append(row)
    return rows


def stats(workdir: Path, cls: str, *, window: int = 50) -> dict[str, Any]:
    """What the last `window` proposals of a class came to.

    Counted over PROPOSALS, not over log lines: a proposal and its later
    decision are two lines about one thing, and "how many of the last 50 did a
    human bless" is the question graduation asks. Decisions are matched back to
    their proposal by id, so a decision whose proposal has aged out of the
    window does not distort the rate.
    """
    rows = ledger(workdir, cls)
    proposed = [r for r in rows if r.get("event") in ("proposed", "auto_applied")]
    recent = proposed[-window:]
    ids = {r.get("id") for r in recent}
    decided: dict[str, str] = {}
    for r in rows:
        if r.get("id") in ids and r.get("event") in ("approved", "dismissed", "regretted"):
            # Last decision wins: a dismiss later regretted, or an approval later
            # reversed by a sampling review, is what the record should reflect.
            decided[str(r.get("id"))] = str(r.get("event"))
    counts = {"proposed": len(recent)}
    for outcome in ("auto_applied", "approved", "dismissed", "regretted"):
        counts[outcome] = 0
    for r in recent:
        if r.get("event") == "auto_applied":
            counts["auto_applied"] += 1
    for outcome in decided.values():
        counts[outcome] = counts.get(outcome, 0) + 1
    return {"class": cls, "window": window, **counts, "pending": len(recent) - len(decided)}


# Graduation thresholds. Deliberately conservative and deliberately few — a
# record that has to clear a subtle formula is one nobody trusts. The rule for
# every step up is the same: enough decided proposals, a clean approval rate,
# and not one regret. A single regret resets the argument, because the cost of
# an auto-applied mistake is the whole reason a human was in the loop.
_MIN_DECIDED = 20
_MAX_DISMISS_RATE = 0.1


def supports(counters: dict[str, Any], target: str) -> tuple[bool, str]:
    """Would this record justify moving a class to `target`? With the reason.

    The reason is the product, not the boolean: an operator reads "3 regrets in
    the window" and knows what to look at, where "no" alone would send them
    digging. verdict_only and investigate need no record — they spend a model
    call, they do not act — so anything up to `propose` is always supported.
    """
    if target not in TIERS:
        return False, f"{target!r} is not a tier"
    if TIERS.index(target) <= TIERS.index("propose"):
        return True, "proposing needs no track record; it changes nothing on its own"

    decided = counters.get("approved", 0) + counters.get("dismissed", 0) + counters.get("regretted", 0)
    regrets = counters.get("regretted", 0)
    dismissed = counters.get("dismissed", 0)

    if regrets:
        return False, f"{regrets} regret(s) in the window — one is enough to keep a human in the loop"
    if decided < _MIN_DECIDED:
        return False, f"only {decided} decided proposal(s); {_MIN_DECIDED} is the floor for a rate to mean anything"
    if decided and dismissed / decided > _MAX_DISMISS_RATE:
        pct = round(100 * dismissed / decided)
        return (
            False,
            f"{pct}% dismissed; above {round(100 * _MAX_DISMISS_RATE)}% the operator is still doing the judging",
        )
    return True, f"{decided} decided, {dismissed} dismissed, 0 regretted — the record carries the ceiling"


def review(workdir: Path, cls: str | None = None, *, tiers: dict[str, str] | None = None) -> dict[str, Any]:
    """The whole picture for the automation page and the sampling patrol: each
    class, its configured ceiling, its record, and whether the two agree."""
    tiers = tiers or dict(_DEFAULT_TIERS)
    names = [cls] if cls else sorted({*_DEFAULT_TIERS, *(r.get("class", "") for r in ledger(workdir))} - {""})
    out = []
    for name in names:
        ceiling = tiers.get(name, "propose")
        counters = stats(workdir, name)
        ok, reason = supports(counters, ceiling)
        # The forward-looking half: not "does the record carry today's ceiling"
        # but "what would the record support" — the number an operator reads
        # when deciding whether a class has earned a step up.
        earned = "propose"
        for tier in TIERS:
            if supports(counters, tier)[0]:
                earned = tier
        out.append(
            {
                "class": name,
                "ceiling": ceiling,
                "record": counters,
                "ceiling_supported": ok,
                "ceiling_reason": reason,
                "record_would_support": earned,
            }
        )
    return {"classes": out, "tiers": TIERS}
