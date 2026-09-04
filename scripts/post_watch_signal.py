#!/usr/bin/env python3
"""Post one watcher signal to the pipe's `watch` door, signed.

The counterpart to the `watch` source in deploy/shadow.yaml. It exists so the
signing secret never has to leave this host: the watcher runs on a laptop, and
reaching the door means one ssh per signal rather than a standing tunnel and a
copy of the secret on a machine that travels.

    echo '{"title": "...", "detail": "...", "level": "high", "origin": "...",
           "kind": "task"}' | python3 scripts/post_watch_signal.py

`level` gates what happens next and is the only field with teeth: the
investigator funds a paid run for critical/high and declines everything else by
itself, so a signal worth telling somebody about but not worth investigating is
simply `low`. `kind: task` picks the investigator's how-would-this-be-done
prompt over its root-cause one.

Exit codes: 0 delivered, 1 refused by the door, 2 bad input or no secret. The
caller is expected to report a non-zero to its own operator — a signal that
fails to reach the pipe must not look like a quiet day.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Derived from where this file sits, not typed: the script lives in
# <deployment>/scripts/, so the env file is its parent's .env. That works in
# any checkout, and keeps a deployment path out of a public repository.
ENV_FILE = Path(os.environ.get("HOOKSTACK_ENV_FILE") or Path(__file__).resolve().parent.parent / ".env")
# Overridable for the same reason ENV_FILE is, and found the same way: the demo
# stack (`scripts/stack-smoke.sh`) also binds 127.0.0.1:8100, so a machine
# running a watch deployment cannot run the smoke without one of them moving.
# The default is unchanged, so a caller that has never heard of this is unaffected.
DOOR = os.environ.get("HOOKSTACK_WATCH_DOOR") or "http://127.0.0.1:8100/hook/watch"
_REQUIRED = ("title", "level")


def _secret() -> str:
    match = re.search(r"^WATCH_INGEST_SECRET=(.+)$", ENV_FILE.read_text(encoding="utf-8"), re.M)
    if not match:
        raise SystemExit("no WATCH_INGEST_SECRET in the deployment .env")
    return match.group(1).strip().strip('"')


def main() -> int:
    try:
        signal = json.loads(sys.stdin.read())
    except ValueError as exc:
        print(f"stdin is not JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(signal, dict):
        print("stdin must be a JSON object describing ONE signal", file=sys.stderr)
        return 2
    missing = [key for key in _REQUIRED if not str(signal.get(key) or "").strip()]
    if missing:
        print(f"signal is missing: {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        secret = _secret()
    except (OSError, SystemExit) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    body = json.dumps({"signal": signal}, ensure_ascii=False).encode("utf-8")
    stamp = str(int(time.time()))
    signature = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        DOOR,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Hook-Signature": signature,
            "X-Hook-Timestamp": stamp,
        },
    )
    try:
        answer = json.loads(urllib.request.urlopen(request, timeout=30).read())
    except urllib.error.HTTPError as exc:
        print(f"door refused: HTTP {exc.code} {exc.read()[:200].decode(errors='replace')}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - the caller only needs "it did not land"
        print(f"door unreachable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"event_id": answer.get("event_id"), "channels": answer.get("channels")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
