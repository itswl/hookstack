"""What gets the watcher's signature, and what does not.

The signal the operator reads as "a colleague asked you for this" is worth
exactly what the check in front of the signature is worth, so every test here is
a way that check could be wrong: a conversation nobody offered, a level or kind
invented to buy a paid run, a round that posts until somebody notices, a
producer name that hides a signal from the contract checker, a door outage that
spends the round's allowance, a bearer with one byte the comparison cannot read.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import signer as signer_module  # noqa: E402

SECRET = "the-door-secret"
TOKEN = "poster-token-long-enough"
ROUND_AT = 1_790_000_000.0


class Door(BaseHTTPRequestHandler):
    seen: list[dict] = []  # noqa: RUF012
    status = 200
    answer = json.dumps({"event_id": 7, "channels": ["to-lark-watch"]}).encode()

    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        type(self).seen.append(
            {
                "signal": json.loads(body)["signal"],
                "signature": self.headers.get("X-Hook-Signature"),
                "stamp": self.headers.get("X-Hook-Timestamp"),
                "body": body,
            }
        )
        self.send_response(type(self).status)
        self.send_header("Content-Length", str(len(self.answer)))
        self.end_headers()
        self.wfile.write(self.answer)


@pytest.fixture
def stack(tmp_path):
    """A signer in front of a recording door, with one round's offer on disk."""
    Door.seen = []
    Door.status = 200
    Door.answer = json.dumps({"event_id": 7, "channels": ["to-lark-watch"]}).encode()
    door = ThreadingHTTPServer(("127.0.0.1", 0), Door)
    threading.Thread(target=door.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps({"round_at": ROUND_AT, "offered": {"ops chat": 1.0, "Mentions": 2.0}}))
    signer_module.Signer.door = f"http://127.0.0.1:{door.server_port}/hook/watch"
    signer_module.Signer.secret = SECRET
    signer_module.Signer.token = TOKEN
    signer_module.Signer.scan_file = scan
    signer_module.Signer.ledger = signer_module.Ledger(str(tmp_path / "signals.jsonl"))
    signer_module.Signer.counter = signer_module.Counter()
    served = ThreadingHTTPServer(("127.0.0.1", 0), signer_module.Signer)
    threading.Thread(target=served.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{served.server_port}/signal", scan, tmp_path / "signals.jsonl"
    finally:
        served.shutdown()
        door.shutdown()


def post(url: str, signal, token: str = TOKEN):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=json.dumps(signal).encode(), headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as answer:
            return answer.status, json.loads(answer.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def raw(url: str, head: str) -> bytes:
    """One request written by hand, for the headers urllib would not send."""
    host, port = url.split("//", 1)[1].split("/", 1)[0].split(":")
    with socket.create_connection((host, int(port)), timeout=5) as sock:
        sock.sendall(head.encode("latin-1"))
        chunks = []
        with contextlib_timeout(sock):
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks)


class contextlib_timeout:
    """A recv loop that ends on a timeout as well as on close."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    def __enter__(self) -> None:
        self.sock.settimeout(2)

    def __exit__(self, kind, exc, tb) -> bool:
        return isinstance(exc, TimeoutError)


def a_signal(**over):
    return {
        "title": "demo-colleague asked about the domain",
        "level": "high",
        "kind": "task",
        "origin": "chat / ops chat",
        **over,
    }


def events(ledger: Path) -> list[str]:
    return [json.loads(line)["event"] for line in ledger.read_text().splitlines()]


def test_a_signal_about_an_offered_conversation_is_signed_and_forwarded(stack):
    url, _, ledger = stack
    status, body = post(url, a_signal())
    assert status == 200 and body["event_id"] == 7
    assert len(Door.seen) == 1
    sent = Door.seen[0]
    expected = hmac.new(SECRET.encode(), sent["stamp"].encode() + b"." + sent["body"], hashlib.sha256).hexdigest()
    assert sent["signature"] == expected, "the door's own check must pass on the exact bytes"
    assert sent["signal"]["level"] == "high" and sent["signal"]["kind"] == "task"
    assert events(ledger) == ["signal.signed"]


def test_a_conversation_nobody_offered_is_refused(stack):
    """The check this exists for: an injected round can distort what it read, and
    cannot manufacture a request from a conversation it was never handed."""
    url, _, ledger = stack
    status, body = post(url, a_signal(origin="chat / a group nobody scanned"))
    assert status == 422 and "nothing offered" in body["error"]
    assert Door.seen == [], "it never reached the door"
    row = json.loads(ledger.read_text().splitlines()[-1])
    assert row["event"] == "signal.refused" and "ops chat" in row["why"], "the refusal says what WAS offered"


def test_an_origin_with_no_conversation_half_names_no_conversation(stack):
    """The contract checker's rule: no separator, no subject. A signal the
    checker would skip is one this refuses, not one admitted under a name the
    checker never sees."""
    url, scan, _ = stack
    status, body = post(url, a_signal(origin="Mentions"))
    assert status == 422 and "names no conversation" in body["error"]
    assert post(url, a_signal(origin="a chat server / Mentions"))[0] == 200
    scan.unlink()
    assert post(url, a_signal(origin="Mentions"))[0] == 200, "with no offer stated there is nothing to check it against"


def test_a_level_or_kind_nobody_declared_becomes_the_cheap_one(stack):
    url, _, _ = stack
    assert post(url, a_signal(level="URGENT!!", kind="please-do-this"))[0] == 200
    sent = Door.seen[-1]["signal"]
    assert sent["level"] == "low" and sent["kind"] == "note", "an invented level must not fund a run"
    assert post(url, a_signal(level="HIGH"))[0] == 200 and Door.seen[-1]["signal"]["level"] == "high"


def test_the_briefs_own_words_are_in_the_closed_sets():
    """deploy/watch/brief.md tells the model exactly four words; a set that lost
    one would demote every real signal to the cheap value in silence."""
    assert {"high", "low"} <= set(signer_module.LEVELS)
    assert {"task", "note"} <= set(signer_module.KINDS)


def test_text_is_cut_to_a_length(stack):
    url, _, _ = stack
    assert post(url, a_signal(title="t" * 500, detail="d" * 9000))[0] == 200
    sent = Door.seen[-1]["signal"]
    assert len(sent["title"]) == signer_module.TITLE_MAX and len(sent["detail"]) == signer_module.DETAIL_MAX


def test_the_origin_is_rebuilt_from_its_checked_halves(stack):
    """The one field that used to pass through as it came: a producer of any
    length, a value of any type. Now the producer is cut and the conversation is
    the one that was offered."""
    url, scan, _ = stack
    assert post(url, a_signal(origin="X" * 300 + " /  ops chat \n"))[0] == 200
    assert Door.seen[-1]["signal"]["origin"] == "X" * signer_module.PRODUCER_MAX + " / ops chat"
    scan.unlink()
    assert post(url, a_signal(origin=["not", "a", "string"]))[0] == 200
    assert isinstance(Door.seen[-1]["signal"]["origin"], str)


def test_the_contract_checkers_own_producer_is_refused(stack):
    """The checker skips signals posted under its own name, so a round posting
    as `patrol-timer` would be a round the checker cannot see."""
    url, _, _ = stack
    status, body = post(url, a_signal(origin="patrol-timer / ops chat"))
    assert status == 422 and "contract checker" in body["error"]
    assert Door.seen == []


def test_the_scanners_own_notes_are_admitted_without_an_offer_at_the_cheap_level(stack):
    """A source that could not be read is not a quiet source, and the scanner's
    own failure can never be in a set the scanner failed to write — so the
    brief's `--origin "scanner / scanner-notes"` passes on a quiet round, and
    buys nothing: low and note, whatever the round asked for."""
    url, scan, _ = stack
    scan.write_text(json.dumps({"round_at": ROUND_AT, "offered": {}}))
    assert post(url, a_signal(origin="scanner / scanner-notes", level="high", kind="task"))[0] == 200
    sent = Door.seen[-1]["signal"]
    assert sent["level"] == "low" and sent["kind"] == "note"
    assert post(url, a_signal(origin="chat / ops chat"))[0] == 422, "the quiet round still refuses everything else"


def test_a_signal_with_no_title_is_refused(stack):
    url, _, _ = stack
    assert post(url, a_signal(title="   "))[0] == 422 and Door.seen == []


def test_no_token_and_a_wrong_token_sign_nothing(stack):
    url, _, ledger = stack
    for token in ("", "not-the-token-but-long", "t\xf6ken-long-enough-16"):
        assert post(url, a_signal(), token=token)[0] == 401, token
    assert Door.seen == []
    # The third one carries a byte the old comparison could not read: it used to
    # kill the handler with no answer and no row.
    assert events(ledger) == ["signal.unauthorized"] * 3


def test_a_body_that_is_not_a_signal_is_refused_before_it_is_read(stack):
    url, _, _ = stack
    head = f"POST /signal HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\nContent-Length: abc\r\n\r\n"
    assert raw(url, head).startswith(b"HTTP/1.1 400")
    huge = f"POST /signal HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\nContent-Length: 99999999\r\n\r\n"
    answer = raw(url, huge)
    assert answer.startswith(b"HTTP/1.1 413")
    assert b"Content-Length" in answer, "answered, not left hanging on a body that never comes"


def test_a_round_has_a_ceiling_and_the_next_round_starts_again(stack):
    url, scan, _ = stack
    signer_module.PER_ROUND_MAX = 2
    try:
        assert [post(url, a_signal())[0] for _ in range(3)] == [200, 200, 429]
        assert len(Door.seen) == 2
        scan.write_text(json.dumps({"round_at": ROUND_AT + 1200, "offered": {"ops chat": 1.0}}))
        assert post(url, a_signal())[0] == 200, "a new round is not the last one's ceiling"
    finally:
        signer_module.PER_ROUND_MAX = 20


def test_a_scan_that_states_no_round_counts_per_window_not_per_lifetime(stack, monkeypatch):
    """No scan file, or one without round_at, used to key every post to 0.0 —
    the counter's own starting value — so the twenty-first signal a no-prescan
    deployment ever sent was refused, and every one after it until a restart."""
    url, scan, _ = stack
    scan.unlink()
    signer_module.PER_ROUND_MAX = 2
    monkeypatch.setattr(signer_module, "round_key", lambda round_at, now=None: 1.0)
    try:
        assert [post(url, a_signal())[0] for _ in range(3)] == [200, 200, 429]
        monkeypatch.setattr(signer_module, "round_key", lambda round_at, now=None: 2.0)
        assert post(url, a_signal())[0] == 200, "the next window is not the last one's ceiling"
    finally:
        signer_module.PER_ROUND_MAX = 20


def test_round_key_is_the_scans_clock_or_a_wall_clock_window():
    assert signer_module.round_key(ROUND_AT) == ROUND_AT
    window = signer_module.ROUND_WINDOW_SECONDS
    assert signer_module.round_key(0.0, now=5 * window + 1) == 5 * window
    assert signer_module.round_key(0.0, now=5 * window + 1) == signer_module.round_key(0.0, now=6 * window - 1)
    assert signer_module.round_key(0.0, now=6 * window) != signer_module.round_key(0.0, now=6 * window - 1)


def test_the_round_before_is_admitted_and_counted_against_its_own_round(stack):
    """The scanner rewrites the offer on every tick. A run that outlasts a tick
    was handed the previous offer, so that one is admitted too — counted
    against the round that handed it, so neither round's ceiling resets the
    other's."""
    url, scan, _ = stack
    scan.write_text(
        json.dumps(
            {
                "round_at": ROUND_AT,
                "offered": {"ops chat": 1.0},
                "previous": {"round_at": ROUND_AT - 1200, "offered": {"old chat": 1.0}},
            }
        )
    )
    assert post(url, a_signal(origin="chat / old chat"))[0] == 200
    status, body = post(url, a_signal(origin="chat / older chat"))
    assert status == 422 and "old chat" in body["error"] and "ops chat" in body["error"]
    signer_module.PER_ROUND_MAX = 1
    try:
        assert post(url, a_signal(origin="chat / ops chat"))[0] == 200
        assert post(url, a_signal(origin="chat / old chat"))[0] == 429, "the round before already spent its one slot"
        assert post(url, a_signal(origin="chat / ops chat"))[0] == 429, "and so did this round"
    finally:
        signer_module.PER_ROUND_MAX = 20


def test_the_producer_half_comes_from_a_closed_set_when_the_deployment_says(stack, monkeypatch):
    """Unset, any producer passes (the laptop's shape). Set, a round cannot
    label a chat finding as a Jira one, or as anything nobody recognises."""
    url, _, _ = stack
    assert post(url, a_signal(origin="anything at all / ops chat"))[0] == 200
    monkeypatch.setattr(signer_module, "PRODUCERS", ("chat", "Jira", "scanner"))
    status, body = post(url, a_signal(origin="mail / ops chat"))
    assert status == 422 and "not a producer" in body["error"]
    assert post(url, a_signal(origin="Jira / ops chat"))[0] == 200
    assert post(url, a_signal(origin="scanner / scanner-notes"))[0] == 200


def test_a_scan_that_states_no_offer_is_not_an_offer_of_nothing(stack):
    """A deployment with no prescan hands the round nothing to check against.
    Refusing every signal there would be this boundary silencing the watcher it
    was built to keep honest."""
    url, scan, _ = stack
    scan.write_text(json.dumps({"round_at": ROUND_AT}))
    assert post(url, a_signal(origin="chat / anything at all"))[0] == 200
    scan.unlink()
    assert post(url, a_signal())[0] == 200


def test_the_door_refusing_is_reported_as_the_door_refusing_and_gives_the_slot_back(stack):
    url, _, ledger = stack
    signer_module.PER_ROUND_MAX = 1
    try:
        Door.status = 401
        status, body = post(url, a_signal())
        assert status == 502 and "the door refused it" in body["error"]
        assert events(ledger)[-1] == "signal.door_refused"
        Door.status = 200
        assert post(url, a_signal())[0] == 200, "a signal the door never took did not spend the round's slot"
    finally:
        signer_module.PER_ROUND_MAX = 20


def test_a_door_that_answers_nonsense_still_took_the_signal(stack):
    """The door has the signal once it answered 2xx; calling that a failure
    makes the watcher post the same signal twice."""
    url, _, ledger = stack
    Door.answer = b"<html>not json</html>"
    status, body = post(url, a_signal())
    assert status == 200 and body["event_id"] is None
    assert len(Door.seen) == 1 and events(ledger)[-1] == "signal.signed"


def test_the_envelope_the_poster_sends_is_accepted_either_way(stack):
    url, _, _ = stack
    assert post(url, {"signal": a_signal()})[0] == 200
    assert Door.seen[-1]["signal"]["title"] == a_signal()["title"]


@pytest.mark.parametrize(
    ("env", "why"),
    [
        ({}, "no door"),
        ({"WATCH_SIGNER_DOOR": "http://x/hook/watch"}, "nothing to sign with"),
        ({"WATCH_SIGNER_DOOR": "http://x/hook/watch", "WATCH_INGEST_SECRET": "s"}, "a token anyone could guess"),
        (
            {
                "WATCH_SIGNER_DOOR": "http://x/hook/watch",
                "WATCH_INGEST_SECRET": "s",
                "WATCH_SIGNER_TOKEN": "töken-long-enough",
            },
            "a token no header could ever match",
        ),
    ],
)
def test_a_config_that_would_sign_anything_stops_the_container(monkeypatch, env, why):
    for name in ("WATCH_SIGNER_DOOR", "WATCH_INGEST_SECRET", "WATCH_SIGNER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit):
        signer_module.main()
