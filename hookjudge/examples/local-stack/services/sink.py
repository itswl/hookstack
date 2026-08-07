"""A stand-in downstream. Prints whatever the pipe delivers, readably.

Point the pipe's channels here instead of a real Feishu/DingTalk webhook, and
`docker compose logs -f sink` becomes the view of what an operator would have
received — the last link of the chain, which is otherwise invisible locally.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

BAR = "─" * 68


def show(path: str, body: bytes) -> None:
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError:
        print(f"\n{BAR}\n{path}  (not JSON)\n{body[:400]!r}\n", flush=True)
        return

    card = data.get("card") if data.get("msg_type") == "interactive" else None
    if card:
        header = card.get("header") or {}
        print(f"\n{BAR}")
        print(f"飞书卡片  [{header.get('template')}]  {(header.get('title') or {}).get('content')}")
        for element in card.get("elements") or []:
            if element.get("tag") == "note":
                text = (element.get("elements") or [{}])[0].get("content", "")
                print(f"    · {text}")
            else:
                text = (element.get("text") or {}).get("content", "")
                print("    " + text.replace("\n", "\n    "))
        print(BAR, flush=True)
        return

    print(f"\n{BAR}\n{path}\n{json.dumps(data, ensure_ascii=False, indent=2)[:1200]}\n{BAR}", flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        show(self.path, self.rfile.read(length))
        payload = b'{"code":0}'
        try:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            # The pipe hung up before reading the reply. The delivery still
            # arrived and is already printed; a stack trace here would bury
            # the cards this container exists to show.
            pass

    def log_message(self, *args: object) -> None:
        pass


print("sink listening on :9000", flush=True)
HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
