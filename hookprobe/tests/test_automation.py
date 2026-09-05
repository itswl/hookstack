"""How far automation may go, and the record that lets it earn more.

The record is the point, so these are mostly about the record being HONEST: a
number that traces to a human press, a regret that resets an argument, a ceiling
that never raises itself. The graduation math is deliberately blunt, and the
tests pin the bluntness — a formula nobody can predict is a gate nobody trusts.
"""

from __future__ import annotations

from hookprobe import automation


def test_the_ladder_is_ordered_and_a_ceiling_caps(tmp_path) -> None:
    """A class may do at most its tier, and 'at most' is an index comparison."""
    tiers = {"remediation": "propose", "memory": "auto_apply"}
    assert automation.permits(tiers, "memory", "auto_apply") is True
    assert automation.permits(tiers, "remediation", "auto_apply") is False
    assert automation.permits(tiers, "remediation", "propose") is True


def test_an_unknown_class_may_propose_and_no_more(tmp_path) -> None:
    """A behaviour added in code before its config line exists must fail toward
    propose, never toward acting on its own."""
    assert automation.permits({}, "something-new", "propose") is True
    assert automation.permits({}, "something-new", "auto_apply") is False


def test_a_typo_in_the_tier_spec_keeps_the_default_never_raises_it(tmp_path) -> None:
    """A misspelled tier is dropped with the default kept — it must not silently
    grant a higher ceiling than the operator spelled."""
    tiers = automation.parse_tiers("memory=looks-safe,remediation=auto_apply")
    assert tiers["memory"] == "auto_apply", "the default, because 'looks-safe' is not a tier"
    assert tiers["remediation"] == "auto_apply", "this one parsed"


def test_the_record_counts_over_proposals_not_log_lines(tmp_path) -> None:
    """A proposal and its decision are two lines about one thing. The rate is
    per proposal, so a decision matched back to its proposal counts once."""
    for i in range(3):
        automation.record(tmp_path, "memory", f"m{i}", "proposed")
    automation.record(tmp_path, "memory", "m0", "approved")
    automation.record(tmp_path, "memory", "m1", "dismissed")
    st = automation.stats(tmp_path, "memory")
    assert st["proposed"] == 3
    assert st["approved"] == 1 and st["dismissed"] == 1
    assert st["pending"] == 1, "m2 was proposed and never decided"


def test_proposing_needs_no_record(tmp_path) -> None:
    """verdict_only, investigate and propose change nothing on their own, so a
    fresh class with no history supports them."""
    st = automation.stats(tmp_path, "remediation")
    for tier in ("verdict_only", "investigate", "propose"):
        ok, _ = automation.supports(st, tier)
        assert ok is True, tier


def test_auto_apply_needs_a_clean_record_of_enough_size(tmp_path) -> None:
    """Enough decided proposals, a clean approval rate, and not one regret."""
    # 25 approvals, no dismissals, no regrets: the record carries it.
    for i in range(25):
        automation.record(tmp_path, "memory", f"a{i}", "proposed")
        automation.record(tmp_path, "memory", f"a{i}", "approved")
    ok, reason = automation.supports(automation.stats(tmp_path, "memory", window=50), "auto_apply")
    assert ok is True, reason


def test_one_regret_keeps_a_human_in_the_loop(tmp_path) -> None:
    """The cost of an auto-applied mistake is the whole reason a human was there.
    A single regret in the window resets the argument, however clean the rest."""
    for i in range(30):
        automation.record(tmp_path, "memory", f"a{i}", "proposed")
        automation.record(tmp_path, "memory", f"a{i}", "approved")
    automation.record(tmp_path, "memory", "a0", "regretted")
    ok, reason = automation.supports(automation.stats(tmp_path, "memory", window=50), "auto_apply")
    assert ok is False
    assert "regret" in reason


def test_too_few_decisions_is_not_a_record(tmp_path) -> None:
    """Five approvals is a coincidence, not a track record."""
    for i in range(5):
        automation.record(tmp_path, "memory", f"a{i}", "proposed")
        automation.record(tmp_path, "memory", f"a{i}", "approved")
    ok, reason = automation.supports(automation.stats(tmp_path, "memory"), "auto_apply")
    assert ok is False
    assert "floor" in reason


def test_a_high_dismiss_rate_means_the_operator_is_still_judging(tmp_path) -> None:
    """If a fifth of proposals get dismissed, the human is doing the work the
    automation wants to take over — it has not earned it."""
    for i in range(20):
        automation.record(tmp_path, "memory", f"a{i}", "proposed")
        automation.record(tmp_path, "memory", f"a{i}", "approved")
    for i in range(5):
        automation.record(tmp_path, "memory", f"d{i}", "proposed")
        automation.record(tmp_path, "memory", f"d{i}", "dismissed")
    ok, reason = automation.supports(automation.stats(tmp_path, "memory", window=50), "auto_apply")
    assert ok is False
    assert "dismissed" in reason


def test_review_says_what_the_record_would_support(tmp_path) -> None:
    """The forward-looking number an operator reads: not just 'is today's ceiling
    justified' but 'what has this class earned'."""
    for i in range(25):
        automation.record(tmp_path, "remediation", f"a{i}", "proposed")
        automation.record(tmp_path, "remediation", f"a{i}", "approved")
    view = automation.review(tmp_path, "remediation", tiers={"remediation": "propose"})
    row = view["classes"][0]
    assert row["ceiling"] == "propose"
    assert row["ceiling_supported"] is True, "propose needs no record"
    assert row["record_would_support"] == "auto_apply", "the record has earned a step up nobody has taken"


def test_a_torn_last_line_does_not_lose_the_record(tmp_path) -> None:
    """A crash mid-append leaves a half-written line; the rest of the record is
    still the record."""
    automation.record(tmp_path, "memory", "m0", "proposed")
    with (tmp_path / "automation-log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"at": 1, "class": "memory"')  # no newline, no close
    assert len(automation.ledger(tmp_path, "memory")) == 1


def test_recording_never_raises_into_the_caller(tmp_path) -> None:
    """The decision already happened; a bookkeeping failure must not undo it.
    An unknown event is dropped rather than raised."""
    automation.record(tmp_path, "memory", "m0", "not-an-event")
    assert automation.ledger(tmp_path) == []


def _client(tmp_path, **overrides):
    from fastapi.testclient import TestClient

    from hookprobe.app import create_app
    from hookprobe.runs import RunStore
    from hookprobe.service import RunService
    from tests.helpers import FakeEngine, make_settings

    settings = make_settings(tmp_path, **overrides)
    return TestClient(
        create_app(settings, RunService(settings, FakeEngine(), RunStore(tmp_path / "results")))
    ), settings


def test_memory_approve_and_dismiss_land_in_the_record(tmp_path) -> None:
    """The record is built from the labels the family already collects — a press
    of accept or dismiss on the memory page. This pins that the press writes the
    record, end to end, not that a test wrote it directly."""
    from hookprobe import suggestions

    suggestions.append(tmp_path, "probe:1", ["the api gateway is behind cloudflare"], apply_safe=False)
    row = suggestions.load(tmp_path)[0]
    suggestions.resolve(tmp_path, row["id"], accept=True)

    st = automation.stats(tmp_path, "memory")
    assert st["proposed"] == 1 and st["approved"] == 1


def test_the_regret_endpoint_is_the_after_the_fact_review(tmp_path) -> None:
    """An unattended deployment's human-in-the-loop, made asynchronous: a
    sampling review posts a regret, and it is the one event that resets a class's
    argument for a higher tier."""
    client, settings = _client(tmp_path)
    for i in range(25):
        automation.record(tmp_path, "memory", f"a{i}", "auto_applied")
        automation.record(tmp_path, "memory", f"a{i}", "approved")
    assert automation.supports(automation.stats(tmp_path, "memory", window=50), "auto_apply")[0] is True

    r = client.post(
        "/v1/automation/memory/a0/regret",
        headers={"Authorization": f"Bearer {settings.token}"},
        json={"note": "that host was not actually behind cloudflare"},
    )
    assert r.status_code == 200
    assert automation.supports(automation.stats(tmp_path, "memory", window=50), "auto_apply")[0] is False


def test_a_run_cannot_write_a_regret(tmp_path) -> None:
    """A label the automation could write about itself is not a label. The route
    needs the operator token; the agent's subprocess holds none."""
    client, _ = _client(tmp_path)
    assert client.post("/v1/automation/memory/x/regret").status_code == 401


def test_the_ceiling_caps_the_auto_apply_knob(tmp_path) -> None:
    """memory_auto_apply may be on, but a tier of propose halts it — one config
    line, without hunting the knob down. The demotion is visible in the record:
    nothing is auto_applied, everything is proposed."""
    from hookprobe import suggestions

    tiers = {"memory": "propose"}
    apply_safe = True and automation.permits(tiers, "memory", "auto_apply")
    assert apply_safe is False
    out = suggestions.append(tmp_path, "probe:1", ["a shape-safe fact with no imperative"], apply_safe=apply_safe)
    assert out["applied"] == 0 and out["queued"] == 1, "capped to propose, so it queued instead of applying"
