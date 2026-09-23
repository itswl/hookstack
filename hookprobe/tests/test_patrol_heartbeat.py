"""A quiet round's heartbeat, as patrol.sh actually sends it.

The work pipe's `watch-due` door alarms after 25 minutes of silence, and the
timer used to skip a quiet round by posting nothing — so every two quiet rounds
raised a false "watch-due has said nothing" in the operator's chat (38 in 7.7
days, 2026-09-23). The fix is a heartbeat: the same door, the same signature,
one extra field the door's config drops before any route. This runs the shipped
script against a local server and reads back what arrived.
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import os
import subprocess
import threading
from pathlib import Path

PATROL = Path(__file__).resolve().parents[1] / "examples" / "patrols" / "patrol.sh"
SECRET = "test-secret"


def _post_with(tmp_path: Path, extra_env: dict[str, str]) -> tuple[dict, dict]:
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 — http.server's name
            length = int(self.headers.get("content-length") or 0)
            got["body"] = self.rfile.read(length)
            got["headers"] = {k.lower(): v for k, v in self.headers.items()}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"outcome":"skipped","skip_code":"quiet_round"}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    brief = tmp_path / "beat.md"
    brief.write_text("Quiet round: the prescan found nothing to judge.\n", encoding="utf-8")
    env = {
        **os.environ,
        "HOOKRELAY_URL": f"http://127.0.0.1:{server.server_port}",
        "HOOKRELAY_SOURCE": "watch-due",
        "HOOKRELAY_INBOUND_SECRET": SECRET,
        **extra_env,
    }
    done = subprocess.run(
        ["bash", str(PATROL), str(brief), "watch: quiet round"], env=env, capture_output=True, text=True, timeout=30
    )  # noqa: S603
    thread.join(timeout=5)
    server.server_close()
    assert done.returncode == 0, done.stderr
    return json.loads(got["body"]), got["headers"] | {"_raw": got["body"]}


def test_a_heartbeat_carries_beat_and_is_signed_like_a_round(tmp_path) -> None:
    payload, headers = _post_with(tmp_path, {"PATROL_BEAT": "yes", "PATROL_STATE": "ok"})
    assert payload["beat"] == "yes" and payload["state"] == "ok"
    assert payload["title"] == "watch: quiet round"
    stamp = headers["x-hook-timestamp"]
    expected = hmac.new(SECRET.encode(), stamp.encode() + b"." + headers["_raw"], hashlib.sha256).hexdigest()
    assert headers["x-hook-signature"] == f"sha256={expected}", (
        "a heartbeat the door cannot verify is a 401, not a beat"
    )


def test_a_round_carries_no_beat(tmp_path) -> None:
    """The field's absence is what lets the door route a real round."""
    payload, _ = _post_with(tmp_path, {})
    assert "beat" not in payload and payload["state"] == "alerting"
