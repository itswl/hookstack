"""The chat client against a fake chat server: the session id it was given
rides on every later call, and a refusal reaches the caller with the server's
own words rather than a status code alone."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chat_mcp  # noqa: E402


class FakeChat(BaseHTTPRequestHandler):
    seen: list[dict] = []  # noqa: RUF012

    def log_message(self, *args) -> None:  # noqa: D102
        pass

    def _send(self, status: int, body: bytes, content_type: str = "application/json", extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        name = (body.get("params") or {}).get("name")
        type(self).seen.append({"method": body["method"], "session": self.headers.get("Mcp-Session-Id"), "name": name})
        if body["method"] == "initialize":
            answer = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {}}).encode()
            self._send(200, answer, extra={"Mcp-Session-Id": "s-1"})
        elif name == "chat.refused":
            self._send(403, json.dumps({"error": "tool refused: chat.refused is not on this token's list"}).encode())
        else:
            result = {"content": [{"type": "text", "text": "Found 1 message(s)."}], "structuredContent": {"fine": True}}
            frame = (
                "event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}) + "\n\n"
            )
            self._send(200, frame.encode(), content_type="text/event-stream")


@pytest.fixture
def chat():
    FakeChat.seen.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeChat)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address[:2]
    yield chat_mcp.Chat(url=f"http://{host}:{port}/mcp/", prefix="chat", timeout=5)
    srv.shutdown()
    srv.server_close()


def test_the_session_id_from_initialize_rides_on_every_later_call(chat):
    assert chat.call("fine") == {"fine": True}
    assert [(s["method"], s["session"]) for s in FakeChat.seen] == [("initialize", None), ("tools/call", "s-1")]
    assert FakeChat.seen[1]["name"] == "chat.fine"


def test_a_refusal_carries_the_servers_reason(chat):
    with pytest.raises(chat_mcp.McpError) as err:
        chat.call("refused")
    assert "HTTP 403" in str(err.value) and "not on this token's list" in str(err.value)


def test_a_server_that_is_not_there_is_unreachable_not_quiet():
    with pytest.raises(chat_mcp.McpError):
        chat_mcp.Chat(url="http://127.0.0.1:9/mcp/", prefix="chat", timeout=1).call("fine")
