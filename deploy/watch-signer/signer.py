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
    manufactured one.
  * its level and kind come from a closed set, and its text is cut to a length.
  * the round has a ceiling on how many signals it may post at all.

WHAT THIS IS NOT. It is not a judgement about content: a round that read a real
conversation and describes it dishonestly passes here, and nothing short of a
person reading the thread would catch that. It is a boundary around WHOSE NAME
is on the signal and WHICH conversations can carry one.
"""

from __future__ import annotations

import hashlib
import hmac
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
KINDS = ("task", "note", "report", "brief", "follow_up")
TITLE_MAX, DETAIL_MAX = 200, 4000
# Per scan round. The watcher's busiest real round posted four; a round asking
# for twenty has stopped being a watcher.
PER_ROUND_MAX = int(os.environ.get("WATCH_SIGNER_ROUND_MAX", "20"))
# The separator the origin is built with (`deploy/watch/watch_report.py`) and
# that scripts/assert_node_contract.py reads the conversation back out of.
ORIGIN_SEPARATOR = " / "


def conversation_of(origin: str) -> str:
    """The conversation an origin names: what follows the separator, or all of it."""
    text = str(origin or "").strip()
    return text.split(ORIGIN_SEPARATOR, 1)[-1].strip() if ORIGIN_SEPARATOR in text else text


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


def check(signal: dict[str, Any], offered: set[str] | None) -> tuple[dict[str, Any] | None, str]:
    """The signal as it will be signed, or None and the reason it will not be."""
    title = str(signal.get("title") or "").strip()
    if not title:
        return None, "no title"
    level = str(signal.get("level") or "").strip().lower()
    kind = str(signal.get("kind") or "").strip().lower()
    origin = str(signal.get("origin") or "").strip()
    subject = conversation_of(origin)
    if offered is not None and subject not in offered:
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
    return clean, ""


class Signer(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "watch-signer"

    door = ""
    secret = ""
    token = ""
    scan_file = Path("/scan/scan.json")
    ledger = Ledger("")
    counter = Counter()

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s", fmt % args)

    def _answer(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's spelling
        self._answer(200 if self.path == "/healthz" else 404, {"ok": self.path == "/healthz"})

    def do_POST(self) -> None:  # noqa: N802
        header = self.headers.get("Authorization", "")
        given = header[7:].strip() if header[:7].lower() == "bearer " else ""
        if not hmac.compare_digest(given, self.token):
            self.ledger.write(event="signal.unauthorized")
            self._answer(401, {"error": "a bearer token this signer knows is required"})
            return
        length = int(self.headers.get("Content-Length") or 0)
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
        if not self.counter.take(round_at, PER_ROUND_MAX):
            self.ledger.write(event="signal.over_ceiling", origin=str(clean.get("origin") or "")[:120])
            self._answer(429, {"error": f"this round has already posted {PER_ROUND_MAX} signals"})
            return
        self._forward(clean)

    def _forward(self, signal: dict[str, Any]) -> None:
        body = json.dumps({"signal": signal}, ensure_ascii=False).encode()
        stamp = str(int(time.time()))
        signature = hmac.new(self.secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            self.door,
            data=body,
            headers={"Content-Type": "application/json", "X-Hook-Signature": signature, "X-Hook-Timestamp": stamp},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=30) as answer:
                landed = json.loads(answer.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200].decode(errors="replace")
            self.ledger.write(event="signal.door_refused", status=exc.code, detail=detail)
            self._answer(502, {"error": f"the door refused it: HTTP {exc.code} {detail}"})
            return
        except OSError as exc:
            self.ledger.write(event="signal.door_unreachable", reason=str(exc)[:200])
            self._answer(502, {"error": "the door did not answer"})
            return
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
    Signer.scan_file = Path(os.environ.get("WATCH_SIGNER_SCAN", "/scan/scan.json"))
    Signer.ledger = Ledger(os.environ.get("WATCH_SIGNER_LEDGER", "/data/signals.jsonl"))
    Signer.counter = Counter()
    port = int(os.environ.get("WATCH_SIGNER_PORT", "8099"))
    logger.info("up on :%d, signing for %s, ceiling %d per round", port, Signer.door, PER_ROUND_MAX)
    ThreadingHTTPServer(("0.0.0.0", port), Signer).serve_forever()  # noqa: S104 — the container's own network only
    return 0


if __name__ == "__main__":
    sys.exit(main())
