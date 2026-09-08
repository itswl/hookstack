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

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import subprocess  # nosec B404 — the Lark CLI is the transport; see _lark()
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("lark-bridge")

# Two ways to deliver. As the APPLICATION (default): lark-cli, a chat id, button
# callbacks, in-thread replies. Or through a custom-bot WEBHOOK
# (LARK_WEBHOOK_URL): the same rendered card posted to the bot's incoming URL —
# no app, no chat id, no message id back, no threads, and actions become links
# because a custom bot cannot call back. Same protocol on the pipe's side.
WEBHOOK_URL = os.environ.get("LARK_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ.get("LARK_WEBHOOK_SECRET", "")
WEBHOOK_MODE = bool(WEBHOOK_URL)
CHAT_ID = os.environ.get("LARK_CHAT_ID", "")
if not WEBHOOK_MODE and not CHAT_ID:
    raise SystemExit("LARK_CHAT_ID is required unless LARK_WEBHOOK_URL is set")
# Every chat this bridge may post into and will forward replies from: the
# default above plus BRIDGE_CHAT_IDS (comma-separated). A pipe channel names one
# with `options: {chat_id: …}`; a request for a chat outside this set is refused
# — the bridge's blast radius is written here, not decided by its callers.
CHAT_IDS = {
    *([CHAT_ID] if CHAT_ID else []),
    *(c.strip() for c in os.environ.get("BRIDGE_CHAT_IDS", "").split(",") if c.strip()),
}
RELAY_ACTION_URL = os.environ.get("RELAY_ACTION_URL", "http://hookrelay:8100/card-action")
# A person's reply under one of the pipe's cards, forwarded to the pipe's
# `lark-thread` door — signed with that door's secret, the pipe's own scheme
# (X-Hook-Timestamp + X-Hook-Signature over "{ts}.{body}"). Unset = the bridge
# does not listen for messages at all, which is the previous behaviour.
RELAY_THREAD_URL = os.environ.get("RELAY_THREAD_URL", "")
THREAD_SECRET = os.environ.get("THREAD_SECRET", "")
LISTEN_PORT = int(os.environ.get("BRIDGE_PORT", "9100"))
# Max bytes accepted from the pipe. A card is a few KB; anything near this is a
# misconfiguration, not a notification.
MAX_BODY = 256 * 1024

# Inbound authentication. This listens on 0.0.0.0 over a network SHARED with the
# platform's containers, so "only the pipe can reach it" was never true —
# anything on the wire could POST a card into the operator's private chat as the
# app. When BRIDGE_INBOUND_SECRET is set, require the pipe's Feishu-style sign:
# hookrelay's feishu channel, given a channel secret, adds {timestamp, sign} to
# the body, sign = base64(HMAC-SHA256(key="{ts}\n{secret}", msg="")). Set the
# same value as the `to-me` channel's secret. Unset = accept everything, which
# is the previous behaviour, so turning this on is a two-line .env change with no
# code risk: an unset bridge never rejects a card.
INBOUND_SECRET = os.environ.get("BRIDGE_INBOUND_SECRET", "")
_SIGN_SKEW_SECONDS = 300


PROTOCOL = "hookstack-bridge/1"
DRY_RUN_HEADER = "x-hookstack-dry-run"


def is_authentic_protocol(headers: Any, body: bytes) -> bool:
    """The protocol's signature: the pipe's own scheme, over the exact bytes.

    X-Hook-Timestamp and X-Hook-Signature = hex HMAC-SHA256(secret, "{ts}.{body}")
    — the same scheme this bridge uses when it signs a message for the pipe's
    thread door, so both directions are one function. The body is covered, which
    the legacy in-body sign below never managed. Unset secret = always True.
    """
    if not INBOUND_SECRET:
        return True
    timestamp = str(headers.get("x-hook-timestamp") or "")
    provided = str(headers.get("x-hook-signature") or "")
    if not timestamp or not provided:
        return False
    try:
        if abs(time.time() - int(timestamp)) > _SIGN_SKEW_SECONDS:
            return False
    except ValueError:
        return False
    return hmac.compare_digest(_sign(INBOUND_SECRET, body, timestamp), provided)


def is_authentic(payload: dict) -> bool:
    """True when the body proves knowledge of the shared secret within the skew.

    The sign covers the timestamp, not the card body, so a captured card can be
    replayed inside the window — bounded, and the card is not attacker-chosen.
    Forging a NEW card still needs the secret. Unset secret = always True.
    """
    if not INBOUND_SECRET:
        return True
    timestamp = str(payload.get("timestamp") or "")
    provided = str(payload.get("sign") or "")
    if not timestamp or not provided:
        return False
    try:
        if abs(time.time() - int(timestamp)) > _SIGN_SKEW_SECONDS:
            return False
    except ValueError:
        return False
    key = f"{timestamp}\n{INBOUND_SECRET}".encode()
    expected = base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, provided)


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


def send_card(card: dict, reply_to: str = "", chat_id: str = "") -> tuple[bool, str]:
    """Post one interactive card to a chat, as the application — the default
    chat unless the pipe named another it may use — or, when the pipe says which
    message it answers, as a reply in that message's thread, so a follow-up's
    answer lands under the question."""
    if reply_to:
        args = ["im", "+messages-reply", "--message-id", reply_to, "--reply-in-thread"]
    else:
        args = ["im", "+messages-send", "--chat-id", chat_id or CHAT_ID]
    result = _lark(
        [
            *args,
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


def send_webhook(card: dict) -> tuple[bool, str]:
    """Post one rendered card to a custom bot's incoming webhook.

    The bot's own signing when LARK_WEBHOOK_SECRET is set — {timestamp, sign},
    sign = base64(HMAC-SHA256(key="{ts}\n{secret}", msg="")). Feishu answers
    200 with an in-body code, and a 200 that says "invalid sign" is a failure.
    No message id comes back: a custom bot's messages cannot be replied to.
    """
    body: dict = {"msg_type": "interactive", "card": card}
    if WEBHOOK_SECRET:
        ts = str(int(time.time()))
        key = f"{ts}\n{WEBHOOK_SECRET}".encode()
        body["timestamp"] = ts
        body["sign"] = base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()
    request = urllib.request.Request(  # nosec B310 — a fixed https:// URL from env, not user input
        WEBHOOK_URL,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310
            answer = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return False, f"http {error.code}: {error.read()[:200]!r}"
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return False, str(error)[:300]
    code = answer.get("code", answer.get("StatusCode", 0)) if isinstance(answer, dict) else 0
    if code not in (0, None):
        return False, json.dumps(answer, ensure_ascii=False)[:300]
    return True, ""


class Handler(BaseHTTPRequestHandler):
    """The pipe's cards, accepted and sent as the app — the protocol's card
    model rendered here, or a finished Feishu card passed through."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        logger.info("http %s", fmt % args)

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            self._reply(413, {"ok": False, "error": "body too large"})
            return
        raw = self.rfile.read(length) or b"{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            self._reply(400, {"ok": False, "error": "body is not JSON"})
            return
        if not isinstance(payload, dict):
            self._reply(400, {"ok": False, "error": "body is not a JSON object"})
            return
        # Two bodies are accepted. The protocol (docs/bridge-protocol.md): a
        # card MODEL, signed in headers, rendered HERE. And the legacy shape a
        # `feishu`-type channel posts to a custom bot — a finished Feishu card
        # with the in-body sign — so a channel pointed here by either type works.
        protocol = payload.get("protocol") == PROTOCOL
        authentic = is_authentic_protocol(self.headers, raw) if protocol else is_authentic(payload)
        if not authentic:
            # A card that cannot prove the shared secret is refused, not
            # delivered. hookrelay treats the 401 as a failed delivery and
            # dead-letters it in the open, which is the visible failure we want.
            logger.warning("inbound card refused: signature missing or invalid")
            self._reply(401, {"ok": False, "error": "unauthenticated"})
            return
        card = payload.get("card")
        if not isinstance(card, dict):
            self._reply(
                400,
                {"ok": False, "error": "expected a card model or an interactive card"},
            )
            return
        if protocol:
            # As the app, actions are buttons that call back; through a webhook
            # they are links to the pipe's confirm page, at the base the pipe
            # named — or nothing, never a button that does nothing.
            card = render.feishu_card(
                card,
                actions="links" if WEBHOOK_MODE else "buttons",
                link_base=str(payload.get("action_link_base") or "")[:200],
            )["card"]
        # `reply_to` is the pipe's request to answer inside a thread (see
        # hookrelay's feishu channel, `thread_replies`). Read here, never sent on.
        reply_to = str(payload.get("reply_to") or "")[:120]
        chat_id = str(payload.get("chat_id") or "")[:120]
        if chat_id and chat_id not in CHAT_IDS and not WEBHOOK_MODE:
            logger.warning("card refused: chat %s is not one this bridge serves", chat_id[:12])
            self._reply(400, {"ok": False, "error": "chat not served by this bridge"})
            return
        if WEBHOOK_MODE and (reply_to or chat_id):
            # A webhook has one destination and no threads; say so once per
            # card instead of pretending. The card still goes out.
            logger.info(
                "webhook mode: thread/chat hints ignored (reply_to=%s chat_id=%s)",
                bool(reply_to),
                bool(chat_id),
            )
        if protocol and self.headers.get(DRY_RUN_HEADER):
            # The conformance hook: everything but the send. A bridge for any
            # platform answers this the same way, with its own rendering.
            logger.info("dry run: card rendered, not sent")
            self._reply(200, {"ok": True, "dry_run": True, "message_id": "", "rendered": card})
            return
        ok, detail = send_webhook(card) if WEBHOOK_MODE else send_card(card, reply_to, chat_id)
        if ok:
            logger.info(
                "card delivered %s%s",
                "via webhook" if WEBHOOK_MODE else f"message_id={detail}",
                " (in thread)" if reply_to and not WEBHOOK_MODE else "",
            )
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


def acknowledge(event: dict, note: str) -> None:
    """Rewrite the pressed card so a person can see the press landed.

    Without this a press did exactly what an INERT button did: nothing visible.
    That is the state this deployment was in for a day, and being right about the
    plumbing while looking identical to being broken is not much of an
    improvement — the operator cannot tell, and presses again.

    Two things change. The action block is removed, which is honest rather than
    cosmetic: the token in those buttons is single-use and genuinely spent. And a
    note records what the PIPE did, not what the far end concluded — the relay
    answers as soon as it has enqueued the delivery, so it does not yet know
    whether the investigator accepted anything, and a card claiming "remembered"
    would be claiming an outcome nobody has observed.

    Never fatal. The press has already been forwarded by the time this runs, so a
    failed repaint must not be reported as a failed press.
    """
    token = str(event.get("token") or "")
    content = str(event.get("card_content") or "")
    if not token or not content:
        # Say WHICH is missing. "no token or card_content" was true and useless:
        # a press came back forwarded-but-unrepainted and the log could not tell
        # an expired update token from a card body the platform declined to hand
        # over, which are different problems with different fixes.
        missing = ", ".join(name for name, value in (("token", token), ("card_content", content)) if not value)
        logger.info(
            "press not repainted — missing %s (event carried: %s)",
            missing,
            ",".join(sorted(k for k, v in event.items() if v not in (None, "", {}))),
        )
        return
    try:
        card = json.loads(content)
        if not isinstance(card, dict):
            raise TypeError("card_content is not an object")
        elements = [el for el in (card.get("elements") or []) if el.get("tag") != "action"]
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": note}]})
        card["elements"] = elements
        # Card 1.0 needs open_ids or the API answers 300090 "openid empty". Ours
        # are 1.0 — hookrelay builds {header, elements} with no schema key.
        if str(card.get("schema") or "") != "2.0":
            card["open_ids"] = [str(event.get("operator_id") or "")]
        result = _lark(
            [
                "api",
                "POST",
                "/open-apis/interactive/v1/card/update",
                "--as",
                "bot",
                "--data",
                json.dumps({"token": token, "card": card}, ensure_ascii=False),
            ]
        )
        if result.returncode != 0:
            logger.warning(
                "card repaint failed rc=%s %s",
                result.returncode,
                (result.stderr or result.stdout)[:200],
            )
        else:
            logger.info("card repainted after the press")
    except Exception as exc:  # noqa: BLE001 — cosmetic; the press already landed
        logger.warning("could not repaint the pressed card: %s", exc)


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
    # The protocol's action shape: the token at the top level, and who pressed.
    # The platform's own envelope (action.value…) stays on this side of the seam.
    body = json.dumps({"hookrelay_action": token, "actor": event.get("operator_id") or ""})
    request = urllib.request.Request(  # nosec B310 — a fixed http:// URL from env, not user input
        RELAY_ACTION_URL,
        data=body.encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    stamp = time.strftime("%H:%M")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310
            answer = response.read(400).decode("utf-8", "replace")
            logger.info("press forwarded: %s %s", response.status, answer[:200])
        # The relay's own `outcome` reads "forwarded remember to to-probe-action".
        # Accurate, and it names an internal channel — plumbing language arriving
        # in somebody's chat window, where `to-probe-action` means nothing. It
        # stays in the log above, where a maintainer wants exactly that noun.
        #
        # `kind` is the one word here that IS the operator's: it is what the
        # button said it would do. The rest says what is true at this moment and
        # no more — the pipe took it and passed it on, which is all any component
        # has observed yet.
        kind = outcome = ""
        try:
            parsed = json.loads(answer) or {}
            kind = str(parsed.get("kind") or "")
            outcome = str(parsed.get("outcome") or "")
        except ValueError:
            pass
        did = f"{kind} — " if kind else ""
        # `already_done` is a 200, and every 200 used to be reported as an
        # acceptance. So a second press on a spent token repainted the card
        # saying "accepted and passed on" — which is a duplicate being described
        # as a fresh acceptance, on the one surface whose whole job is to tell a
        # person what happened. The token is single-use by design; saying so is
        # the honest answer, and it also explains why nothing appeared to change
        # the first time.
        if outcome == "already_done":
            note = f"↺ pressed {stamp} · {did}already recorded. The first press took it; a button is single-use."
        else:
            note = f"✓ pressed {stamp} · {did}accepted and passed on. These buttons are spent."
        acknowledge(event, note)
    except Exception:  # a lost press must not kill the consumer
        # logger.exception already carries the traceback; naming the exception
        # again put the same message on the line twice.
        logger.exception("forwarding the press failed")
        # Say so on the card. A press that failed and looks unpressed is the
        # worst of the three outcomes: the operator will press it again, and the
        # token is single-use, so the retry cannot work either.
        acknowledge(
            event,
            f"✗ pressed {stamp} — the pipe did not accept it. Nothing was recorded.",
        )


def _sign(secret: str, body: bytes, ts: str) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def forward_message(event: dict) -> None:
    """A message in the chat, handed to the pipe only when it is a reply under
    something — the pipe decides whether that something was one of its cards.

    The bridge keeps no map of message ids to alerts on purpose: the ledger has
    that, and a stateless bridge is one that can be restarted without losing a
    thread. What it does refuse here is cheap and structural: messages from
    other chats, from bots (the pipe's own replies included, or a loop starts),
    and top-level messages that reply to nothing.
    """
    if not RELAY_THREAD_URL:
        return
    chat_id = str(event.get("chat_id") or "")
    if chat_id not in CHAT_IDS:
        return
    if str(event.get("sender_type") or "user") != "user":
        return
    message_id = str(event.get("message_id") or event.get("id") or "")
    mentions = event.get("mentions") or []
    root = str(event.get("root_id") or event.get("reply_to") or "")
    # A reply under something continues that something. A top-level message
    # that mentions the bot OPENS a topic: the message itself becomes the root,
    # and the pipe decides what a new topic starts (`on_new_topic` on its
    # thread_lookup stage). A top-level message mentioning nobody is chatter.
    if root:
        topic = "reply"
    elif mentions and message_id:
        topic, root = "new", message_id
    else:
        return
    text = str(event.get("content") or "").strip()
    # Lark renders mentions as @_user_N placeholders in `content`; the bot being
    # addressed is not part of the question.
    for mention in mentions:
        key = str((mention or {}).get("key") or "")
        if key:
            text = text.replace(key, "").strip()
    if not text:
        return
    body = json.dumps(
        {
            "root_message_id": root,
            "topic": topic,
            "message_id": message_id,
            "sender": str(event.get("sender_id") or ""),
            "chat_id": chat_id,
            "text": text[:4000],
        },
        ensure_ascii=False,
    ).encode()
    ts = str(int(time.time()))
    headers = {"content-type": "application/json", "X-Hook-Timestamp": ts}
    if THREAD_SECRET:
        headers["X-Hook-Signature"] = _sign(THREAD_SECRET, body, ts)
    request = urllib.request.Request(  # nosec B310 — a fixed http:// URL from env, not user input
        RELAY_THREAD_URL, data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # nosec B310
            answer = response.read(300).decode("utf-8", "replace")
            logger.info("thread reply forwarded: %s %s", response.status, answer[:160])
    except Exception:  # a lost message must not kill the consumer
        logger.exception("forwarding the thread reply failed")


def bus_running() -> bool:
    """Is this container's lark-cli event bus daemon up (holding the app's ONE
    long connection)? `event status` prints `Bus: running` per app when it is."""
    try:
        result = _lark(["event", "status"], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    # The CLI pads the label to a column ("Bus:              running (PID …)"),
    # and says "Bus: not running" with one space — match the words, not the gap.
    return re.search(r"Bus:\s+running", result.stdout or "") is not None


def consume_messages() -> None:
    """Stream im.message.receive_v1 forever; every reply goes to forward_message.

    Lark allows ONE event connection per app, and lark-cli refuses to open a
    second ("another event bus is already connected"). Two consumers that both
    try to open it block each other forever: each attempt's short-lived
    connection is the "remote connection" the other one finds. So this consumer
    never opens the bus itself — it waits until the presses consumer has it
    running, then attaches to that daemon, which is what `event consume` does
    when a bus is already up.
    """
    consume("im.message.receive_v1", forward_message, attach_only=True)


def consume_presses() -> None:
    """Stream card.action.trigger forever, restarting if the stream drops.

    NDJSON on stdout, one event per line, which is the contract lark-cli
    documents for `event consume`. The restart loop is not optional: a long
    connection is a network object and will be dropped, and a bridge that
    stopped listening after the first blip would look exactly like nobody
    pressing anything.
    """
    consume("card.action.trigger", forward_press)


def consume(event_key: str, handler, attach_only: bool = False) -> None:
    backoff = 2
    while True:
        if attach_only:
            while not bus_running():
                threading.Event().wait(5)
        logger.info("connecting the event stream for %s", event_key)
        process = subprocess.Popen(  # nosec B603 — fixed argv, no shell
            ["lark-cli", "event", "consume", event_key, "--as", "bot"],
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
            if event.get("type") in (event_key, None) or event_key.startswith("im.message"):
                handler(event)
                backoff = 2  # a working stream resets the penalty
        process.wait()
        logger.warning(
            "event stream %s ended (rc=%s); reconnecting in %ss",
            event_key,
            process.returncode,
            backoff,
        )
        threading.Event().wait(backoff)
        backoff = min(backoff * 2, 60)


def main() -> None:
    if WEBHOOK_MODE:
        # Nothing to consume: a custom bot has no events. Cards in, webhook out.
        logger.info("bridge up (webhook mode): port=%s", LISTEN_PORT)
    else:
        logger.info(
            "bridge up: chat=%s relay=%s port=%s",
            CHAT_ID,
            RELAY_ACTION_URL,
            LISTEN_PORT,
        )
        threading.Thread(target=consume_presses, daemon=True).start()
        if RELAY_THREAD_URL:
            threading.Thread(target=consume_messages, daemon=True).start()
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
