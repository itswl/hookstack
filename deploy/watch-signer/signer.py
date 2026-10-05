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
    manufactured one. "This round's" holds by timing rather than by
    construction: the scanner rewrites the file on every tick, so a run that is
    still posting after the next tick is checked against that tick's offer.
    Measured over 326 rounds the longest run was 897 s against a 1200 s tick.
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
  * the contract checker's own producer, `patrol-timer`, is refused: the
    checker skips signals it posted itself, so a round posting under that name
    would be a round the checker cannot see. The timer signs from its own file
    and never comes through here.

WHAT THIS IS NOT. It is not a judgement about content: a round that read a real
conversation and describes it dishonestly passes here, and nothing short of a
person reading the thread would catch that. It is a boundary around WHOSE NAME
is on the signal and WHICH conversations can carry one. The producer half of an
origin is free text (the chat tool's own name, by configuration), so a round can
mislabel where a signal came from; the conversation half is what is checked.
And the `reported` cursor stays the agent's promise: this signs and forwards,
and the wrapper in front of it writes the cursor afterwards, so a signal that
landed can still leave the cursor where it was — the contract checker is what
catches that, from the pipe's ledger.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import http.client
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s watch-signer %(message)s")
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
# that scripts/assert_node_contract.py reads the conversation back out of.
ORIGIN_SEPARATOR = " / "
# The one subject admitted without an offer — see the module docstring. The
# brief names it, so it is a literal here and there rather than a variable
# the two would have to agree on.
NOTE_SUBJECT = os.environ.get("WATCH_SIGNER_NOTE_SUBJECT") or "scanner-notes"
# scripts/assert_node_contract.py's SELF_PRODUCER: the one name it does not
# check, so the one name nothing checked here may post under.
CHECKER_PRODUCER = "patrol-timer"


def constant_time_eq(expected: str, provided: str | None) -> bool:
    """Compare two header-derived strings without leaking length by timing.

    Wraps hmac.compare_digest because that function raises TypeError on a
    str holding non-ASCII, and http.server decodes header bytes as latin-1 —
    so a single 0xF6 byte in the bearer killed the handler with no 401 and no
    ledger row. Comparing the utf-8 bytes keeps the constant-time property and
    answers the way it should. The same copy the family's three doors carry.
    """
    return hmac.compare_digest(expected.encode("utf-8"), (provided or "").encode("utf-8"))


def sign_timestamped(secret: str, body: bytes, *, now: float | None = None) -> dict[str, str]:
    """Headers for an outbound family delivery; empty when unsigned."""
    if not secret:
        return {}
    stamp = str(int(time.time() if now is None else now))
    digest = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {"X-Hook-Timestamp": stamp, "X-Hook-Signature": digest}


def split_origin(origin: str) -> tuple[str, str]:
    """The two halves of `<producer> / <conversation>`, stripped.

    An origin with no separator names no conversation — the contract checker's
    rule, so a signal it would skip is one this refuses rather than one it
    admits under a name the checker never sees.
    """
    text = str(origin or "").strip()
    if ORIGIN_SEPARATOR not in text:
        return "", ""
    producer, subject = text.split(ORIGIN_SEPARATOR, 1)
    return producer.strip(), subject.strip()


def conversation_of(origin: str) -> str:
    """The conversation an origin names, or "" when it names none."""
    return split_origin(origin)[1]


def offered_now(scan_file: Path) -> tuple[set[str] | None, float]:
    """What this round was handed, and when the round began.

    `None` — not the empty set — when the scan states nothing, which is a
    deployment with no prescan rather than a round that was offered nothing.
    The caller treats those differently on purpose.
    """
    try:
        scan = json.loads(scan_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("no scan to check against (%s)", exc)
        return None, 0.0
    offered = scan.get("offered")
    if not isinstance(offered, dict):
        return None, float(scan.get("round_at") or 0)
    return set(offered), float(scan.get("round_at") or 0)


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


class Ledger:
    """Every signal, signed or refused. A refusal here is the one thing that can
    say "a round tried to post about something nobody handed it"."""

    def __init__(self, path: str) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.Lock()

    def write(self, **fields: Any) -> None:
        if self.path is None:
            return
        line = json.dumps({"ts": round(time.time(), 3), **fields}, ensure_ascii=False)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError as exc:  # noqa: BLE001 — never lose a signal because the ledger cannot be written
            logger.warning("ledger write failed: %s", exc)


class Counter:
    """How many signals this round has posted. Keyed on the round's own clock, so
    a new round starts at zero without anybody resetting anything."""

    def __init__(self) -> None:
        self._round = 0.0
        self._count = 0
        self._lock = threading.Lock()

    def take(self, round_at: float, ceiling: int) -> bool:
        with self._lock:
            if round_at != self._round:
                self._round, self._count = round_at, 0
            if self._count >= ceiling:
                return False
            self._count += 1
            return True

    def refund(self, round_at: float) -> None:
        """A slot back, for a signal the door never took: the ceiling counts
        signals that landed, or a door outage would spend a round's whole
        allowance on failures and refuse the real signals once it is back."""
        with self._lock:
            if round_at == self._round and self._count > 0:
                self._count -= 1


def check(signal: dict[str, Any], offered: set[str] | None) -> tuple[dict[str, Any] | None, str]:
    """The signal as it will be signed, or None and the reason it will not be."""
    title = str(signal.get("title") or "").strip()
    if not title:
        return None, "no title"
    level = str(signal.get("level") or "").strip().lower()
    kind = str(signal.get("kind") or "").strip().lower()
    origin = str(signal.get("origin") or "").strip()
    producer, subject = split_origin(origin)
    if producer == CHECKER_PRODUCER:
        return None, f"{CHECKER_PRODUCER!r} is the contract checker's own producer and never posts through here"
    if subject == NOTE_SUBJECT:
        # The scanner's own notes: admitted without an offer, at the level that
        # funds nothing. See the module docstring.
        level, kind = "low", "note"
    elif offered is not None and subject not in offered:
        if not subject:
            return None, f"the origin names no conversation: expected '<producer>{ORIGIN_SEPARATOR}<conversation>'"
        # The check this exists for. Stated with what WAS offered, because the
        # common cause is a name copied wrong, not an attack, and the two read
        # identically in a log that only says "refused".
        return None, f"nothing offered the conversation {subject!r} this round (offered: {sorted(offered)})"
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
    return clean, ""


_CONTROL_CHARS = getattr(BaseHTTPRequestHandler, "_control_char_table", None)


def plain(text: str) -> str:
    """A request line as the log may show it: control characters escaped, as
    the stdlib does since 3.12, so a path carrying a carriage return or an ANSI
    sequence cannot rewrite the line above it on a terminal."""
    return text.translate(_CONTROL_CHARS) if _CONTROL_CHARS else repr(text)[1:-1]


class Signer(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "watch-signer"
    # A peer that declares a body and stops sending holds a thread for exactly
    # this long. The egress proxy beside this sets the same.
    timeout = 30

    door = ""
    secret = ""
    token = ""
    scan_file = Path("/scan/scan.json")
    ledger = Ledger("")
    counter = Counter()

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s", plain(fmt % args))

    def handle(self) -> None:
        # A peer that resets the socket mid-exchange has nobody to answer; the
        # stdlib would print a traceback that reads exactly like a real failure.
        with contextlib.suppress(ConnectionResetError, BrokenPipeError):
            super().handle()

    def _answer(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _refuse(self, status: int, message: str) -> None:
        """An answer given before the body was read: the connection closes with
        it, or on keep-alive the unread body is parsed as the next request."""
        self.close_connection = True
        self._answer(status, {"error": message})

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        self._answer(200 if self.path == "/healthz" else 404, {"ok": self.path == "/healthz"})

    def do_POST(self) -> None:  # noqa: N802
        header = self.headers.get("Authorization", "")
        given = header[7:].strip() if header[:7].lower() == "bearer " else ""
        if not constant_time_eq(self.token, given):
            self.ledger.write(event="signal.unauthorized")
            self._refuse(401, "a bearer token this signer knows is required")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._refuse(400, "Content-Length is not a number")
            return
        if length < 0:
            self._refuse(400, "Content-Length is negative")
            return
        if length > BODY_MAX:
            self._refuse(413, f"a signal is at most {BODY_MAX} bytes")
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            self._answer(400, {"error": "not JSON"})
            return
        # Either shape: the door's own `{"signal": {...}}` envelope, or the bare
        # signal, because the poster in front of this has sent both over its life.
        signal = payload.get("signal") if isinstance(payload, dict) and "signal" in payload else payload
        if not isinstance(signal, dict):
            self._answer(400, {"error": "the body must be one signal object"})
            return
        offered, round_at = offered_now(self.scan_file)
        clean, why = check(signal, offered)
        if clean is None:
            self.ledger.write(
                event="signal.refused",
                why=why,
                title=str(signal.get("title") or "")[:120],
                origin=str(signal.get("origin") or "")[:120],
            )
            logger.warning("refused a signal: %s", why)
            self._answer(422, {"error": why})
            return
        key = round_key(round_at)
        if not self.counter.take(key, PER_ROUND_MAX):
            self.ledger.write(event="signal.over_ceiling", origin=str(clean.get("origin") or "")[:120])
            self._answer(429, {"error": f"this round has already posted {PER_ROUND_MAX} signals"})
            return
        self._forward(clean, key)

    def _forward(self, signal: dict[str, Any], key: float) -> None:
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
            self._answer(502, {"error": f"the door refused it: HTTP {exc.code} {detail}"})
            return
        except (OSError, http.client.HTTPException) as exc:
            # HTTPException is not an OSError: a door that answers a status line
            # and then cuts the body is this branch, not a crash with no answer.
            self.counter.refund(key)
            self.ledger.write(event="signal.door_unreachable", reason=str(exc)[:200])
            self._answer(502, {"error": "the door did not answer"})
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
        self.ledger.write(
            event="signal.signed",
            origin=str(signal.get("origin") or "")[:120],
            level=signal.get("level"),
            kind=signal.get("kind"),
            event_id=landed.get("event_id"),
        )
        self._answer(200, {"event_id": landed.get("event_id"), "channels": landed.get("channels")})


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
