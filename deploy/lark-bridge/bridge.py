"""The Lark adapter, kept OUT of the pipe.

Two directions, one process, and neither of them belongs in hookrelay:

  out  hookrelay's `feishu` channel already renders a Feishu card and POSTs it
       as a custom-bot webhook would. This accepts that exact shape and sends it
       through the Lark message API as the APPLICATION instead. That swap is the
       whole reason this exists: a custom bot can only send, so the buttons on
       its cards have nowhere to call back to, and every feedback feature the
       family grew this week depends on a press being receivable.

  in   the app's `card.action.trigger` events arrive over a LONG CONNECTION —
       the bridge dials out to Lark. So a button press needs no public route
       into the network, which matters here: hookrelay's public front door was
       deliberately rolled back on 2026-08-07 and this does not reopen it.

Why a sidecar and not a hookrelay plugin: the pipe is content-blind and its
README puts a ceiling on its own size (scripts/assert_weight.py enforces it).
An IM platform's auth, token refresh and websocket dialect are none of the four
pillars, and a `generic`/`feishu` channel pointed at localhost is all the
coupling needed to keep them out.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess  # nosec B404 — the Lark CLI is the transport; see _lark()
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("lark-bridge")

CHAT_ID = os.environ["LARK_CHAT_ID"]
RELAY_ACTION_URL = os.environ.get("RELAY_ACTION_URL", "http://hookrelay:8100/card-action")
LISTEN_PORT = int(os.environ.get("BRIDGE_PORT", "9100"))
# Max bytes accepted from the pipe. A card is a few KB; anything near this is a
# misconfiguration, not a notification.
MAX_BODY = 256 * 1024


def _lark(args: list[str], stdin: str | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """One place that shells out, so there is one place to read for what it runs.

    argv, never a shell string: the card JSON contains operator-authored alert
    text, and a shell would treat a quote in an alert title as syntax.
    """
    return subprocess.run(  # nosec B603 — fixed argv, no shell, input passed on stdin
        ["lark-cli", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def send_card(card: dict) -> tuple[bool, str]:
    """Post one interactive card to the private chat, as the application."""
    result = _lark(
        [
            "im",
            "+messages-send",
            "--chat-id",
            CHAT_ID,
            "--msg-type",
            "interactive",
            "--content",
            json.dumps(card, ensure_ascii=False),
            "--as",
            "bot",
            "--format",
            "json",
        ]
    )
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "lark-cli failed").strip()[:300]
    try:
        answer = json.loads(result.stdout or "{}")
    except ValueError:
        return False, "lark-cli returned no JSON"
    if not answer.get("ok", False):
        return False, json.dumps(answer.get("error") or answer, ensure_ascii=False)[:300]
    return True, str((answer.get("data") or {}).get("message_id") or "")


class Handler(BaseHTTPRequestHandler):
    """The custom-bot webhook shape, accepted and re-sent as the app."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info("http %s", fmt % args)

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's contract
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            self._reply(413, {"ok": False, "error": "body too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._reply(400, {"ok": False, "error": "body is not JSON"})
            return
        card = payload.get("card") if isinstance(payload, dict) else None
        if not isinstance(card, dict):
            # The pipe sends {msg_type: interactive, card: {...}}. Anything else
            # is a channel pointed here by mistake, and saying so beats a 200.
            self._reply(400, {"ok": False, "error": "expected an interactive card payload"})
            return
        ok, detail = send_card(card)
        if ok:
            logger.info("card delivered message_id=%s", detail)
            self._reply(200, {"ok": True, "message_id": detail})
        else:
            logger.error("card rejected by Lark: %s", detail)
            # 502, not 200: hookrelay's outbox retries a failed delivery and
            # dead-letters it in the open. Swallowing this would lose the alert
            # quietly, which is the one thing that ledger exists to prevent.
            self._reply(502, {"ok": False, "error": detail})

    def _reply(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def forward_press(event: dict) -> None:
    """One button press, handed to the pipe that minted the token."""
    raw = event.get("action_value") or "{}"
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        logger.warning("press carried no readable action_value")
        return
    token = (value or {}).get("hookrelay_action") if isinstance(value, dict) else None
    if not token:
        logger.info("press on a card with no hookrelay action — ignored")
        return
    # The token IS the authorisation: signed by the pipe, single-use, expiring.
    # The bridge never inspects it and could not forge one.
    body = json.dumps({"action": {"value": {"hookrelay_action": token}}, "actor": event.get("operator_id") or ""})
    request = urllib.request.Request(  # nosec B310 — a fixed http:// URL from env, not user input
        RELAY_ACTION_URL,
        data=body.encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310
            logger.info("press forwarded: %s %s", response.status, response.read(200).decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 — a lost press must not kill the consumer
        logger.exception("forwarding the press failed: %s", exc)


def consume_presses() -> None:
    """Stream card.action.trigger forever, restarting if the stream drops.

    NDJSON on stdout, one event per line, which is the contract lark-cli
    documents for `event consume`. The restart loop is not optional: a long
    connection is a network object and will be dropped, and a bridge that
    stopped listening after the first blip would look exactly like nobody
    pressing anything.
    """
    backoff = 2
    while True:
        logger.info("connecting the event stream")
        process = subprocess.Popen(  # nosec B603 — fixed argv, no shell
            ["lark-cli", "event", "consume", "card.action.trigger", "--as", "bot"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # A PIPE on stdin that stays OPEN, and this is load-bearing: lark-cli
            # treats stdin closing as "stop gracefully", so inheriting the
            # container's closed stdin made it connect, report ready, and exit
            # with `reason: signal` in the same millisecond — then reconnect,
            # forever. It looked exactly like nobody pressing any buttons.
            stdin=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line.startswith("{"):
                if line:
                    logger.info("lark-cli: %s", line[:200])
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "card.action.trigger":
                forward_press(event)
                backoff = 2  # a working stream resets the penalty
        process.wait()
        logger.warning("event stream ended (rc=%s); reconnecting in %ss", process.returncode, backoff)
        threading.Event().wait(backoff)
        backoff = min(backoff * 2, 60)


def main() -> None:
    logger.info("bridge up: chat=%s relay=%s port=%s", CHAT_ID, RELAY_ACTION_URL, LISTEN_PORT)
    threading.Thread(target=consume_presses, daemon=True).start()
    # 0.0.0.0 inside a container network with no published port: only the pipe
    # beside it can reach this.
    server = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)  # nosec B104
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("bridge stopping")
        server.shutdown()


if __name__ == "__main__":
    sys.exit(main())
