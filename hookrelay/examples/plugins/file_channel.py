"""Example channel type: append events to a local JSONL file via a tiny
loopback trick — builders are pure, so a file channel is really a generic
POST to yourself. Shown here as the SIMPLEST possible custom channel: a
builder that reshapes the message.

    channels:
      - name: audit-log
        type: oneline
        url: http://127.0.0.1:9999/append   # any collector you run
"""

import json

from hookrelay import registry


@registry.channel("oneline")
def build_oneline(channel, message, now):
    line = f"[{message['level']}] {message['source']}: {message['title']}"
    body = json.dumps({"line": line, "event_id": message["event_id"]}, ensure_ascii=False).encode()
    return channel.url, body, {"content-type": "application/json"}
