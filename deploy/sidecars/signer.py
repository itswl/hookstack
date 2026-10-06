"""The watcher's signature, held by something the watcher cannot read.

`post_watch_signal.py` signs a signal with `WATCH_INGEST_SECRET` and posts it to
the pipe's watch door. On the operator's laptop that is exactly right: the
secret never leaves the host it belongs to. Inside `probe-watch` it is a
different arrangement wearing the same clothes — the process that decides WHAT
to post is an agent whose input is colleagues' chat messages, and the secret sat
in a file that agent can read. So the door's only check, "this was signed by
something that holds the secret", answered yes to anything an injected round
chose to say: a fabricated request from a colleague, at `high`, as a `task`,
which buys a paid planner run and a card the operator reads as real.

The secret moves in here, where no agent runs. What arrives is unsigned and is
checked before it is signed:

  * it names a conversation THIS ROUND was handed. The scanner writes
    `offered` into `scan.json` before the round starts, and this reads that file
    at post time, so the set is the one the round actually saw. An injected
    round can still lie about a conversation it read; it cannot invent one it
    was never given, which is the difference between a distorted signal and a
    manufactured one. The scanner keeps the round before alongside (`previous`
    in scan.json), and a subject offered only there is admitted and counted
    against THAT round: a run that outlasts a 20-minute tick — queued behind
    the node's two-run semaphore, or slow — was handed the previous offer, and
    the file it reads at post time has moved on. Two rounds and no more: a run
    is capped at 30 minutes and cannot cross two ticks.
  * its level and kind come from a closed set, and its text is cut to a length.
    The origin is rebuilt from its two checked halves, so the producer half is
    cut too and a value that is not a string never reaches the door.
  * the round has a ceiling on how many signals it may post at all, counted
    against the scan's own clock — or against a wall-clock window of the
    timer's cadence when the scan states no round, so a deployment with no
    prescan is not capped once for the life of the process.
  * one subject is admitted without an offer: the scanner's own ⚠️ notes, which
    the brief tells the model to relay with `--origin "scanner / scanner-notes"`
    (a source that could not be read is not a quiet source, and the scanner's
    own failure can never be in a set the scanner failed to write). That
    subject is forced to `low` and `note`, the level that tells somebody and
    funds nothing, so an injected round gains a card and nothing else by it.
  * the timer's own producer, `patrol-timer`, is refused: the signed-check
    (scripts/assert_watch_signed.py) skips signals the timer posted itself, so
    a round posting under that name would be a round that check cannot see.
    The timer signs from its own file and never comes through here.

WHAT THIS IS NOT. It is not a judgement about content: a round that read a real
conversation and describes it dishonestly passes here, and nothing short of a
person reading the thread would catch that. It is a boundary around WHOSE NAME
is on the signal and WHICH conversations can carry one. The producer half of an
origin comes from a closed set when WATCH_SIGNER_PRODUCERS names one (the chat
tool's own name, Jira, the scanner — what the work compose sets); unset, it is
free text and a round can mislabel where a signal came from.
Since 2026-10-05 the ledger here is also the record of WHAT WAS REPORTED: every
`signal.signed` row carries the conversation and the cursor the scan offered it
at, written by a process the agent cannot reach. The watch wrapper used to write
a `reported` cursor into the node's own state after posting, which was a
self-report — a node that forgot to write it was exactly the node whose word
could not be taken for it. scripts/assert_watch_signed.py reads this ledger
against the pipe's, once per tick, and the wrapper writes nothing.
"""

from __future__ import annotations

import dataclasses
import http.client
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from common import HttpHandler, Ledger, constant_time_eq, sign_timestamped

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("watch-signer")

# What the door does something different with. Anything else becomes `low`,
# which is the level that tells somebody and funds nothing.
LEVELS = ("critical", "high", "medium", "low", "info")
# `task` buys a plan, `note` does not. An unknown kind is a note for the same
# reason an unknown level is low: the cheap direction is the safe one.
KINDS = ("task", "note", "brief", "follow_up")
TITLE_MAX, DETAIL_MAX, PRODUCER_MAX = 200, 4000, 80
# A signal is a few KB after the cuts above. A body past this is not a signal,
# and it is refused before it is read: this container has 128 MB.
BODY_MAX = 64 * 1024
# Per scan round. The watcher's busiest real round posted four; a round asking
# for twenty has stopped being a watcher.
PER_ROUND_MAX = int(os.environ.get("WATCH_SIGNER_ROUND_MAX", "20"))
# The round's length when the scan states no clock: the timer's own cadence.
ROUND_WINDOW_SECONDS = int(os.environ.get("WATCH_SIGNER_ROUND_SECONDS", "1200"))
# The separator the origin is built with (`deploy/watch/watch_report.py`) and
# that scripts/assert_watch_signed.py reads the conversation back out of.
ORIGIN_SEPARATOR = " / "
# The one subject admitted without an offer — see the module docstring. The
# brief names it, so it is a literal here and there rather than a variable
# the two would have to agree on.
NOTE_SUBJECT = os.environ.get("WATCH_SIGNER_NOTE_SUBJECT") or "scanner-notes"
# scripts/assert_watch_signed.py's SELF_PRODUCER: the one name it does not
# check, so the one name nothing checked here may post under.
CHECKER_PRODUCER = "patrol-timer"
# The producers a signal may name, when the deployment says. Empty admits any.
PRODUCERS = tuple(p.strip() for p in os.environ.get("WATCH_SIGNER_PRODUCERS", "").split(",") if p.strip())


def split_origin(origin: str) -> tuple[str, str]:
    """The two halves of `<producer> / <conversation>`, stripped.

    An origin with no separator names no conversation — the signed-check's
    rule too, so a signal it would skip is one this refuses rather than one it
    admits under a name that check never sees.
    """
    text = str(origin or "").strip()
    if ORIGIN_SEPARATOR not in text:
        return "", ""
    producer, subject = text.split(ORIGIN_SEPARATOR, 1)
    return producer.strip(), subject.strip()


def conversation_of(origin: str) -> str:
    """The conversation an origin names, or "" when it names none."""
    return split_origin(origin)[1]


@dataclasses.dataclass(frozen=True)
class Offer:
    """What the scan states: this round's offer (conversation -> the cursor it
    was handed at) and clock, and the round before.

    `offered` is `None` — not the empty dict — when the scan states nothing,
    which is a deployment with no prescan rather than a round that was offered
    nothing. The caller treats those differently on purpose.
    """

    offered: dict[str, float] | None
    round_at: float
    previous: dict[str, float] | None
    previous_at: float


def _cursors(table: Any) -> dict[str, float] | None:
    if not isinstance(table, dict):
        return None
    out: dict[str, float] = {}
    for name, value in table.items():
        try:
            out[str(name)] = float(value or 0)
        except (TypeError, ValueError):
            out[str(name)] = 0.0
    return out


def read_offer(scan_file: Path) -> Offer:
    try:
        scan = json.loads(scan_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("no scan to check against (%s)", exc)
        return Offer(None, 0.0, None, 0.0)
    previous = scan.get("previous") if isinstance(scan.get("previous"), dict) else {}
    return Offer(
        _cursors(scan.get("offered")),
        float(scan.get("round_at") or 0),
        _cursors(previous.get("offered")),
        float(previous.get("round_at") or 0),
    )


@dataclasses.dataclass(frozen=True)
class Verdict:
    """What `check` decided: the signal as it will be signed (or None and why),
    the round it counts against, and the conversation and cursor the ledger
    records for it."""

    clean: dict[str, Any] | None
    why: str = ""
    key: float = 0.0
    subject: str = ""
    cursor: float | None = None


def round_key(round_at: float, now: float | None = None) -> float:
    """The round a post counts against.

    The scan's own clock when it states one. When it does not — no prescan, no
    file, a file without `round_at` — a wall-clock window the length of the
    timer's cadence, because the alternative was every such post landing in
    one round that never ended: the counter started at 0.0 and a stated clock
    of 0.0 never differed from it, so the twenty-first signal a no-prescan
    deployment ever sent was refused, and every one after it.
    """
    if round_at:
        return round_at
    now = time.time() if now is None else now
    return float(int(now // ROUND_WINDOW_SECONDS) * ROUND_WINDOW_SECONDS)


class Counter:
    """How many signals each round has posted. Keyed on the round's own clock,
    so a new round starts at zero without anybody resetting anything — and the
    two newest rounds are counted side by side, because a late post for the
    round before must not reset the current round's count, nor the other way."""

    def __init__(self) -> None:
        self._counts: dict[float, int] = {}
        self._lock = threading.Lock()

    def take(self, round_at: float, ceiling: int) -> bool:
        with self._lock:
            if round_at not in self._counts:
                self._counts[round_at] = 0
                for stale in sorted(self._counts)[:-2]:
                    del self._counts[stale]
            if self._counts[round_at] >= ceiling:
                return False
            self._counts[round_at] += 1
            return True

    def refund(self, round_at: float) -> None:
        """A slot back, for a signal the door never took: the ceiling counts
        signals that landed, or a door outage would spend a round's whole
        allowance on failures and refuse the real signals once it is back."""
        with self._lock:
            if self._counts.get(round_at, 0) > 0:
                self._counts[round_at] -= 1


def check(signal: dict[str, Any], offer: Offer) -> Verdict:
    """The signal as it will be signed, or None and the reason it will not be."""
    title = str(signal.get("title") or "").strip()
    if not title:
        return Verdict(None, "no title")
    level = str(signal.get("level") or "").strip().lower()
    kind = str(signal.get("kind") or "").strip().lower()
    origin = str(signal.get("origin") or "").strip()
    producer, subject = split_origin(origin)
    if producer == CHECKER_PRODUCER:
        return Verdict(None, f"{CHECKER_PRODUCER!r} is the timer's own producer and never posts through here")
    if PRODUCERS and producer not in PRODUCERS:
        return Verdict(None, f"{producer!r} is not a producer this signer forwards (allowed: {', '.join(PRODUCERS)})")
    key = round_key(offer.round_at)
    cursor: float | None = None
    if subject == NOTE_SUBJECT:
        # The scanner's own notes: admitted without an offer, at the level that
        # funds nothing. See the module docstring.
        level, kind = "low", "note"
    elif offer.offered is not None and subject in offer.offered:
        cursor = offer.offered[subject]
    elif offer.offered is not None:
        if offer.previous is not None and subject in offer.previous:
            # Handed to the run before the tick that rewrote the file; it
            # counts against the round that handed it. See the module docstring.
            key, cursor = round_key(offer.previous_at), offer.previous[subject]
        elif not subject:
            return Verdict(
                None, f"the origin names no conversation: expected '<producer>{ORIGIN_SEPARATOR}<conversation>'"
            )
        else:
            # The check this exists for. Stated with what WAS offered, because
            # the common cause is a name copied wrong, not an attack, and the two
            # read identically in a log that only says "refused".
            before = f", the round before: {sorted(offer.previous)}" if offer.previous is not None else ""
            msg = f"nothing offered the conversation {subject!r} this round (offered: {sorted(offer.offered)}{before})"
            return Verdict(None, msg)
    clean = dict(signal)
    clean["title"] = title[:TITLE_MAX]
    clean["level"] = level if level in LEVELS else "low"
    if kind:
        clean["kind"] = kind if kind in KINDS else "note"
    if signal.get("detail"):
        clean["detail"] = str(signal["detail"])[:DETAIL_MAX]
    # Rebuilt from the halves that were checked, never forwarded as it came:
    # the producer is cut like the title, the conversation is the one that was
    # offered, and a bare origin (admitted only when no offer is stated) is a
    # string of bounded length.
    clean["origin"] = f"{producer[:PRODUCER_MAX]}{ORIGIN_SEPARATOR}{subject}" if subject else origin[:PRODUCER_MAX]
    return Verdict(clean, "", key, subject, cursor)


class Signer(HttpHandler):
    server_version = "watch-signer"

    door = ""
    secret = ""
    token = ""
    scan_file = Path("/scan/scan.json")
    ledger = Ledger("")
    counter = Counter()

    def _say(self, status: int, payload: dict[str, Any]) -> None:
        self._answer(status, json.dumps(payload, ensure_ascii=False).encode())

    def _no(self, status: int, message: str) -> None:
        self._refuse(status, json.dumps({"error": message}, ensure_ascii=False).encode())

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        self._say(200 if self.path == "/healthz" else 404, {"ok": self.path == "/healthz"})

    def do_POST(self) -> None:  # noqa: N802
        header = self.headers.get("Authorization", "")
        given = header[7:].strip() if header[:7].lower() == "bearer " else ""
        if not constant_time_eq(self.token, given):
            self.ledger.write(event="signal.unauthorized")
            self._no(401, "a bearer token this signer knows is required")
            return
        length = self.body_length(BODY_MAX, json.dumps({"error": f"a signal is at most {BODY_MAX} bytes"}).encode())
        if length is None:
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            self._say(400, {"error": "not JSON"})
            return
        # Either shape: the door's own `{"signal": {...}}` envelope, or the bare
        # signal, because the poster in front of this has sent both over its life.
        signal = payload.get("signal") if isinstance(payload, dict) and "signal" in payload else payload
        if not isinstance(signal, dict):
            self._say(400, {"error": "the body must be one signal object"})
            return
        verdict = check(signal, read_offer(self.scan_file))
        if verdict.clean is None:
            self.ledger.write(
                event="signal.refused",
                why=verdict.why,
                title=str(signal.get("title") or "")[:120],
                origin=str(signal.get("origin") or "")[:120],
            )
            logger.warning("refused a signal: %s", verdict.why)
            self._say(422, {"error": verdict.why})
            return
        if not self.counter.take(verdict.key, PER_ROUND_MAX):
            self.ledger.write(event="signal.over_ceiling", origin=str(verdict.clean.get("origin") or "")[:120])
            self._say(429, {"error": f"this round has already posted {PER_ROUND_MAX} signals"})
            return
        self._forward(verdict)

    def _forward(self, verdict: Verdict) -> None:
        signal, key = verdict.clean or {}, verdict.key
        body = json.dumps({"signal": signal}, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json", **sign_timestamped(self.secret, body)}
        request = urllib.request.Request(self.door, data=body, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            answer = opener.open(request, timeout=30)
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200].decode(errors="replace")
            self.counter.refund(key)
            self.ledger.write(event="signal.door_refused", status=exc.code, detail=detail)
            self._say(502, {"error": f"the door refused it: HTTP {exc.code} {detail}"})
            return
        except (OSError, http.client.HTTPException) as exc:
            # HTTPException is not an OSError: a door that answers a status line
            # and then cuts the body is this branch, not a crash with no answer.
            self.counter.refund(key)
            self.ledger.write(event="signal.door_unreachable", reason=str(exc)[:200])
            self._say(502, {"error": "the door did not answer"})
            return
        # From here the door HAS the signal. Whatever its answer looks like, the
        # signal landed, and reporting otherwise makes the watcher post it twice.
        with answer:
            try:
                raw = answer.read()
            except (OSError, http.client.HTTPException):
                raw = b""
        try:
            landed = json.loads(raw or b"{}")
        except ValueError:
            landed = {}
        if not isinstance(landed, dict):
            landed = {}
        # The record of what was reported — see the module docstring. `subject`
        # and `cursor` are what the contract checker reads back.
        self.ledger.write(
            event="signal.signed",
            origin=str(signal.get("origin") or "")[:120],
            subject=verdict.subject,
            cursor=verdict.cursor,
            round_at=key,
            level=signal.get("level"),
            kind=signal.get("kind"),
            event_id=landed.get("event_id"),
        )
        self._say(200, {"event_id": landed.get("event_id"), "channels": landed.get("channels")})


def main() -> int:
    Signer.door = os.environ.get("WATCH_SIGNER_DOOR", "").strip()
    Signer.secret = os.environ.get("WATCH_INGEST_SECRET", "").strip()
    Signer.token = os.environ.get("WATCH_SIGNER_TOKEN", "").strip()
    if not Signer.door.startswith(("http://", "https://")):
        raise SystemExit("watch-signer: set WATCH_SIGNER_DOOR to the pipe's watch door")
    if not Signer.secret:
        # An unsigned forward would be accepted by a door with no secret and
        # silently rejected by one with it. Neither is a thing to discover later.
        raise SystemExit("watch-signer: set WATCH_INGEST_SECRET; there is nothing to sign with")
    if len(Signer.token) < 16:
        raise SystemExit("watch-signer: set WATCH_SIGNER_TOKEN to something at least 16 characters long")
    if not Signer.token.isascii():
        # A header arrives as latin-1 and is compared as bytes: a token with a
        # non-ASCII character could never match, and every post would be a 401.
        raise SystemExit("watch-signer: WATCH_SIGNER_TOKEN must be ASCII")
    Signer.scan_file = Path(os.environ.get("WATCH_SIGNER_SCAN", "/scan/scan.json"))
    Signer.ledger = Ledger(os.environ.get("WATCH_SIGNER_LEDGER", "/data/signals.jsonl"))
    Signer.counter = Counter()
    port = int(os.environ.get("WATCH_SIGNER_PORT", "8099"))
    logger.info("up on :%d, signing for %s, ceiling %d per round", port, Signer.door, PER_ROUND_MAX)
    ThreadingHTTPServer(("0.0.0.0", port), Signer).serve_forever()  # noqa: S104 — the container's own network only
    return 0


if __name__ == "__main__":
    sys.exit(main())
