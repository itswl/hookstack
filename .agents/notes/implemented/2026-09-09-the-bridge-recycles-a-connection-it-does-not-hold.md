---
title: The bridge recycles a connection it does not hold, on a timer, because it cannot tell a zombie from a weekend
status: implemented
date: 2026-09-09
scope: stack
---

## Decision

After `BRIDGE_IDLE_RECYCLE_SECONDS` (default 6h, `0` = off) with no event on
**either** stream, `deploy/lark-bridge` runs `lark-cli event stop --force` and
lets its own reconnect loop rebuild everything.

It is named a **recycle**, not a health check, in the code and in the README.

## Why

The reviewed proposal asked for Larkin's "Drought Maintenance": an activity
heartbeat, TCP idle patterns, Ping-Pong RTT deviation, a graceful preventive
reconnect, and cached card receipts during the swap. The disease is real. The
instruments are not available here.

**The bridge does not hold the socket.** Read from a running container:

```
    1 python3 bridge.py
   76 lark-cli event consume card.action.trigger --as bot
   96 lark-cli event _bus --profile <app> --domain <host>
  131 lark-cli event consume im.message.receive_v1 --as bot
```

PID 96 is the daemon holding the WebSocket; 76 and 131 attach to it over a unix
socket. So there is no RTT to read and no TCP state to watch — the bridge sees
lines, or no lines. And the existing reconnect loop only fires when a **process
ends**, which a zombie socket never does: consumer alive, nothing ended, no line
ever arrives. That failure is invisible and total, and it is indistinguishable
from a quiet night. There is no keepalive line to count.

`lark-cli event stop` turned out to exist, which is what makes any of this
possible; `--force` is required because it refuses (exit 2) while consumers are
attached, and ours always are. That flag is the load-bearing detail, so a test
pins the argv: a recycle that quietly does nothing is worse than none — the
silence it was called for continues, and now a log line claims it was handled.

**Idleness is measured across both streams together**, because they share one
connection. The same container: the press stream had received 0 events in 28
hours while the message stream had taken 12. Per-stream idleness would have
recycled a provably healthy bus nightly.

Measured on the work deployment before shipping, rather than reasoned about:
both consumers ended within 250ms (`reason: signal`, rc=0), the app's one
connection slot was already free (`online_instance_cnt=0`), a fresh daemon was
up 7s after the stop and the second consumer had reattached by 10s. The bus also
self-cleans — lark-cli reports it "auto-exits 30s after last consumer".

The cached-receipts half of the proposal does not apply: cards are sent over
HTTP through `lark-cli api`, a different path from the event stream. A dead
stream does not block sends.

## Consequences

* **This is prevention, not detection.** It cannot tell a zombie from a weekend,
  so most recycles are ones it did not need. That is the honest framing and it is
  in the README in those words; the alternative was inventing a liveness signal
  the platform does not give us.
* Each recycle costs ~10s of not receiving presses, plus the risk of finding the
  app's one connection slot briefly still held — then it is the existing 60s
  backoff. That risk is why the default is hours, not minutes, and why `0` is a
  supported answer.
* A refused recycle resets the clock rather than retrying every minute. One
  ineffective command must not become an hourly log flood.
* This is the first check of any kind on that connection. Before it, "the bridge
  stopped receiving" had no detector and no remedy short of a human noticing that
  buttons did nothing.
