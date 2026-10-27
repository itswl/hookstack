"""Fixtures every investigator test shares."""

from __future__ import annotations

import pytest

from hookprobe import posture

_REAL_METADATA_REACHABLE = posture.metadata_reachable


@pytest.fixture(autouse=True)
def _no_instance_metadata(monkeypatch):
    """No test asks the network for an instance metadata service. A CI runner on
    a cloud VM has one at the same address, and a verdict must not depend on the
    machine that ran the tests; a test that wants a service says so."""
    monkeypatch.setattr(posture, "metadata_reachable", lambda timeout=1.0: False)


@pytest.fixture
def real_metadata_reachable():
    return _REAL_METADATA_REACHABLE
