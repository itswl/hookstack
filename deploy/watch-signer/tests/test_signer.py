"""What gets the watcher's signature, and what does not.

The signal the operator reads as "a colleague asked you for this" is worth
exactly what the check in front of the signature is worth, so every test here is
a way that check could be wrong: a conversation nobody offered, a level or kind
invented to buy a paid run, a round that posts until somebody notices.
"""

from __future__ import annotations

import hashlib
import hmac
import json
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
        answer = json.dumps({"event_id": 7, "channels": ["to-lark-watch"]}).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Length", str(len(answer)))
        self.end_headers()
        self.wfile.write(answer)


@pytest.fixture
def stack(tmp_path):
    """A signer in front of a recording door, with one round's offer on disk."""
    Door.seen = []
    Door.status = 200
    door = ThreadingHTTPServer(("127.0.0.1", 0), Door)
    threading.Thread(target=door.serve_forever, daemon=True).start()
    scan = tmp_path / "scan.json"
    scan.write_text(json.dumps({"round_at": ROUND_AT, "offered": {"ops chat": 1.0, "Mentions": 2.0}}))
    signer_module.Signer.door = f"http://127.0.0.1:{door.server_port}/hook/watch"
    signer_module.Signer.secret = SECRET
    signer_module.Signer.token = TOKEN
    signer_module.Signer.scan_file = scan
    signer_module.Signer.ledger = signer_module.Ledger(str(tmp_path / "signals.jsonl"))
    signer_module.Signer.counter = signer_module.Counter()
    served = ThreadingHTTPServer(("127.0.0.1", 0), signer_module.Signer)
    threading.Thread(target=served.serve_forever, daemon=True).start()
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


def a_signal(**over):
    return {
        "title": "Calvin asked about the domain",
        "level": "high",
        "kind": "task",
        "origin": "chat / ops chat",
        **over,
    }


def test_a_signal_about_an_offered_conversation_is_signed_and_forwarded(stack):
    url, _, ledger = stack
    status, body = post(url, a_signal())
    assert status == 200 and body["event_id"] == 7
    assert len(Door.seen) == 1
    sent = Door.seen[0]
    expected = hmac.new(SECRET.encode(), sent["stamp"].encode() + b"." + sent["body"], hashlib.sha256).hexdigest()
    assert sent["signature"] == expected, "the door's own check must pass on the exact bytes"
    assert sent["signal"]["level"] == "high" and sent["signal"]["kind"] == "task"
    assert [json.loads(line)["event"] for line in ledger.read_text().splitlines()] == ["signal.signed"]


def test_a_conversation_nobody_offered_is_refused(stack):
    """The check this exists for: an injected round can distort what it read, and
    cannot manufacture a request from a conversation it was never handed."""
    url, _, ledger = stack
    status, body = post(url, a_signal(origin="chat / a group nobody scanned"))
    assert status == 422 and "nothing offered" in body["error"]
    assert Door.seen == [], "it never reached the door"
    row = json.loads(ledger.read_text().splitlines()[-1])
    assert row["event"] == "signal.refused" and "ops chat" in row["why"], "the refusal says what WAS offered"


def test_the_bare_conversation_name_is_read_out_of_the_origin(stack):
    url, _, _ = stack
    assert post(url, a_signal(origin="Mentions"))[0] == 200, "an origin with no prefix is the conversation"
    assert post(url, a_signal(origin="a chat server / Mentions"))[0] == 200


def test_a_level_or_kind_nobody_declared_becomes_the_cheap_one(stack):
    url, _, _ = stack
    assert post(url, a_signal(level="URGENT!!", kind="please-do-this"))[0] == 200
    sent = Door.seen[-1]["signal"]
    assert sent["level"] == "low" and sent["kind"] == "note", "an invented level must not fund a run"
    assert post(url, a_signal(level="HIGH"))[0] == 200 and Door.seen[-1]["signal"]["level"] == "high"


def test_text_is_cut_to_a_length(stack):
    url, _, _ = stack
    assert post(url, a_signal(title="t" * 500, detail="d" * 9000))[0] == 200
    sent = Door.seen[-1]["signal"]
    assert len(sent["title"]) == signer_module.TITLE_MAX and len(sent["detail"]) == signer_module.DETAIL_MAX


def test_a_signal_with_no_title_is_refused(stack):
    url, _, _ = stack
    assert post(url, a_signal(title="   "))[0] == 422 and Door.seen == []


def test_no_token_and_a_wrong_token_sign_nothing(stack):
    url, _, _ = stack
    for token in ("", "not-the-token-but-long"):
        assert post(url, a_signal(), token=token)[0] == 401, token
    assert Door.seen == []


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


def test_a_scan_that_states_no_offer_is_not_an_offer_of_nothing(stack):
    """A deployment with no prescan hands the round nothing to check against.
    Refusing every signal there would be this boundary silencing the watcher it
    was built to keep honest."""
    url, scan, _ = stack
    scan.write_text(json.dumps({"round_at": ROUND_AT}))
    assert post(url, a_signal(origin="chat / anything at all"))[0] == 200
    scan.unlink()
    assert post(url, a_signal())[0] == 200


def test_the_door_refusing_is_reported_as_the_door_refusing(stack):
    url, _, ledger = stack
    Door.status = 401
    status, body = post(url, a_signal())
    assert status == 502 and "the door refused it" in body["error"]
    assert json.loads(ledger.read_text().splitlines()[-1])["event"] == "signal.door_refused"


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
    ],
)
def test_a_config_that_would_sign_anything_stops_the_container(monkeypatch, env, why):
    for name in ("WATCH_SIGNER_DOOR", "WATCH_INGEST_SECRET", "WATCH_SIGNER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit):
        signer_module.main()
