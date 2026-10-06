#!/usr/bin/env python3
"""The three doors between the two networks, in one container.

The egress proxy (proxy.py), the MCP gate (gate.py) and the console ingress
(forward.py) are the three things that sit on BOTH networks: the probes'
internal one and the host-facing one. Each is a stateless stdlib server; each
was its own container until 2026-10-06, and three containers for three
listeners was the shape the operator asked about. This runs all three in one
process — three ports, one mount, one restart policy — and changes nothing
about what each of them refuses: the proxy still holds the allowlist, the
gate still holds the tool lists, the ingress still forwards only where it was
told. The container keeps the three old service names as network aliases, so
the probes' proxy variables, their MCP config and every document that names
`egress-proxy`, `mcp-gate` or `probe-ingress` still resolve.

What is NOT merged: the watch signer. It holds the door's secret and sits on
the probes' network only, with no route to the host — a process that also
carried the proxy's reach would carry that reach for the secret too.

One process failing takes the three doors down together; they are stateless
and `restart: unless-stopped` brings them back as one. The gate's ledger is
the only thing written, into its own volume.
"""

from __future__ import annotations

import sys
import threading

import forward
import gate
import proxy


def servers() -> list:
    """All three bound, none serving yet; a port that cannot be had raises here
    and stops the container, before anything answers."""
    return [proxy.build(), gate.build(), *forward.build()]


def main() -> int:
    bound = servers()
    for server in bound[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.current_thread().name = "doors"
    bound[0].serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
