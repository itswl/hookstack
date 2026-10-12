"""An empty inbound secret refuses to start, instead of accepting every card.

`docs/containment.md` used to name the open bridge as the shipped default: with
BRIDGE_INBOUND_SECRET unset the port took a card from anything on the shared
network, and the only thing standing between that and the operator's chat was an
operator remembering to set it. These tests pin the fail-closed replacement — an
empty secret is a refusal to run, and BRIDGE_ALLOW_UNSIGNED=1 is the loud,
deliberate way to say a deployment has nobody untrusted on the wire.

The two `is_authentic*` halves are checked as well as `main()`, because the door
and the guard are separate code paths and only one of them used to be reachable
through `main()`.
"""

from __future__ import annotations

import bridge
import pytest


def test_protocol_door_closed_when_secret_empty_and_not_allowed(monkeypatch):
    monkeypatch.setattr(bridge, "INBOUND_SECRET", "")
    monkeypatch.setattr(bridge, "ALLOW_UNSIGNED", False)
    assert bridge.is_authentic_protocol({"x-hook-timestamp": "1", "x-hook-signature": "x"}, b"{}") is False


def test_legacy_door_closed_when_secret_empty_and_not_allowed(monkeypatch):
    monkeypatch.setattr(bridge, "INBOUND_SECRET", "")
    monkeypatch.setattr(bridge, "ALLOW_UNSIGNED", False)
    assert bridge.is_authentic({"timestamp": "1", "sign": "x"}) is False


def test_everything_is_accepted_only_when_allowed_out_loud(monkeypatch):
    monkeypatch.setattr(bridge, "INBOUND_SECRET", "")
    monkeypatch.setattr(bridge, "ALLOW_UNSIGNED", True)
    # The deliberate posture: no secret AND the opt-in. Nothing is checked.
    assert bridge.is_authentic_protocol({"x-hook-timestamp": "1", "x-hook-signature": "x"}, b"{}") is True
    assert bridge.is_authentic({"timestamp": "1", "sign": "x"}) is True


def test_main_refuses_to_start_without_a_secret(monkeypatch):
    monkeypatch.setattr(bridge, "INBOUND_SECRET", "")
    monkeypatch.setattr(bridge, "ALLOW_UNSIGNED", False)
    with pytest.raises(SystemExit, match="BRIDGE_INBOUND_SECRET is empty"):
        bridge.main()


def test_main_starts_when_the_opt_in_is_set(monkeypatch):
    monkeypatch.setattr(bridge, "INBOUND_SECRET", "")
    monkeypatch.setattr(bridge, "ALLOW_UNSIGNED", True)
    monkeypatch.setattr(bridge, "WEBHOOK_MODE", True)

    class FakeServer:
        def __init__(self, address, handler):
            self.address = address

        def serve_forever(self, **kwargs):
            return None

        def shutdown(self):
            return None

    monkeypatch.setattr(bridge, "ThreadingHTTPServer", FakeServer)
    # Returns rather than raising: the opt-in is the only way past the guard.
    assert bridge.main() is None
