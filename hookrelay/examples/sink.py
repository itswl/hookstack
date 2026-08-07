"""A local delivery sink for trying hookrelay without real channel tokens.

Prints every POST it receives to stdout (visible via `docker compose logs
sink`) and answers {"code": 0} so the delivery ledger records `sent`.

HTTP/1.1 with an accurate Content-Length on purpose: httpx keep-alive
reuses connections, and a toy server that closes without answering (or
answers without a length) shows up in the ledger as transport errors —
we learned both the hard way.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def render(data):
    """Feishu cards get laid out; anything else is printed as JSON.

    A raw card is 40 lines of nested tags, which buries the four things worth
    seeing — colour, headline, severity, footer. Since this is the only view of
    the last link in the chain, it may as well be readable.
    """
    if data.get("msg_type") != "interactive" or not isinstance(data.get("card"), dict):
        return json.dumps(data, ensure_ascii=False, indent=2)

    card = data["card"]
    header = card.get("header") or {}
    lines = [f"飞书卡片 [{header.get('template')}]  {(header.get('title') or {}).get('content', '')}"]
    for element in card.get("elements") or []:
        if element.get("tag") == "note":
            text = (element.get("elements") or [{}])[0].get("content", "")
            lines.append(f"    · {text}")
        else:
            text = (element.get("text") or {}).get("content", "")
            lines.append("    " + text.replace("\n", "\n    "))
    return "\n".join(lines)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802 — http.server API
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        try:
            line = render(json.loads(raw))
        except ValueError:
            line = raw.decode(errors="replace")
        signature = self.headers.get("X-Hook-Signature") or self.headers.get("X-Webhook-Signature") or "(unsigned)"
        print(f"── delivery on {self.path} · signature: {signature[:24]}…\n{line}", flush=True)
        body = b'{"code": 0}'
        try:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The caller hung up before reading the reply. The delivery arrived
            # and is already printed; a stack trace here would bury the very
            # output this service exists to produce.
            pass

    def log_message(self, *args):  # quiet the access log; the payload IS the log
        pass


if __name__ == "__main__":
    print("sink listening on :9000", flush=True)
    HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
