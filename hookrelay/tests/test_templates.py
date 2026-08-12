"""One door, many payload shapes.

The production `inbound` door takes Grafana alerts and SNS relays through the
same public URL. With one template the shapes it does not understand fell back
to "webhook from inbound" — an event in the ledger that cannot be identified,
which for anyone trying to answer a question with it is the same as a lost
event. These tests pin the reading, the selection, and the account of WHICH
template read what.
"""

from __future__ import annotations

import pytest

from hookrelay.config import Config, ConfigError
from hookrelay.extract import extract_event
from hookrelay.pipeline import handle_hook

GRAFANA = {"title": "磁盘将满", "message": "/data 92%", "state": "alerting", "evalMatches": [{"metric": "disk"}]}
SNS = {"TopicArn": "arn:aws:sns:x", "Subject": "AWS 健康事件", "Message": "维护窗口", "Severity": "warning"}
UNKNOWN = {"something": "entirely else"}

MULTI = {
    "templates": [
        {
            "name": "grafana-in",
            "match": {"exists": ["evalMatches"]},
            "title": "{title}",
            "body": "{message}",
            "level": "{state}",
            "level_map": {"alerting": "high", "ok": "info"},
            "fields": {"metric": "{evalMatches.0.metric}"},
        },
        {
            "name": "sns-in",
            "match": {"exists": "TopicArn"},
            "title": "{Subject}",
            "body": "{Message}",
            "level": "{Severity}",
        },
        {"name": "catch-all", "title": "{title}", "body": "{message}"},
    ],
    "sources": [{"name": "inbound", "secret": "", "templates": ["grafana-in", "sns-in", "catch-all"]}],
    "channels": [{"name": "sink", "type": "generic", "url": "https://sink.example/in"}],
    "routes": [{"name": "all", "source": "*", "send_to": ["sink"]}],
}


def _door(raw: dict = MULTI):
    return Config.from_dict(raw).sources["inbound"]


def test_each_shape_is_read_by_the_template_that_claims_it():
    door = _door()

    grafana = extract_event(door, GRAFANA)
    assert grafana["_template"] == "grafana-in"
    assert grafana["title"] == "磁盘将满" and grafana["level"] == "high"
    assert grafana["fields"]["metric"] == "disk"

    sns = extract_event(door, SNS)
    assert sns["_template"] == "sns-in"
    assert sns["title"] == "AWS 健康事件" and sns["level"] == "warning"
    assert sns["fields"] == {}, "each template brings only its own fields"


def test_unclaimed_shapes_land_on_the_fallback_not_on_the_floor():
    read = extract_event(_door(), UNKNOWN)
    assert read["_template"] == "catch-all"
    assert read["title"] == "webhook from inbound", "poorly titled, never dropped"


def test_selection_is_ordered_first_match_wins():
    """A payload matching two selectors takes the earlier template."""
    both = dict(GRAFANA, TopicArn="arn:aws:sns:x", Subject="也像 SNS")
    assert extract_event(_door(), both)["_template"] == "grafana-in"

    swapped = dict(MULTI)
    swapped["sources"] = [{"name": "inbound", "secret": "", "templates": ["sns-in", "grafana-in", "catch-all"]}]
    assert extract_event(_door(swapped), both)["_template"] == "sns-in"


def test_equals_selector_matches_on_a_value():
    cfg = Config.from_dict(
        {
            "templates": [
                {"name": "firing", "match": {"equals": {"state": "alerting"}}, "title": "🔥 {title}"},
                {"name": "other", "title": "{title}"},
            ],
            "sources": [{"name": "d", "secret": "", "templates": ["firing", "other"]}],
            "channels": [{"name": "c", "type": "generic", "url": "https://x.example"}],
            "routes": [{"name": "r", "source": "*", "send_to": ["c"]}],
        }
    )
    door = cfg.sources["d"]
    assert extract_event(door, {"state": "alerting", "title": "t"})["title"] == "🔥 t"
    assert extract_event(door, {"state": "ok", "title": "t"})["_template"] == "other"


def test_the_inline_form_still_works_forever():
    """Production configs predate templates; the single-shape form must stay
    valid — it becomes a one-entry list called 'inline'."""
    door = Config.from_dict(
        {
            "sources": [{"name": "d", "secret": "", "title": "{title}", "body": "{message}", "level": "{state}"}],
            "channels": [{"name": "c", "type": "generic", "url": "https://x.example"}],
            "routes": [{"name": "r", "source": "*", "send_to": ["c"]}],
        }
    ).sources["d"]
    assert [t.name for t in door.templates] == ["inline"]
    assert extract_event(door, GRAFANA)["_template"] == "inline"
    assert extract_event(door, GRAFANA)["title"] == "磁盘将满"


def test_config_refuses_a_door_whose_last_template_can_decline():
    """Caught a real bug in this very feature: without the rule, a door can
    face a payload nothing claims and silently reuse the last template."""
    bad = dict(MULTI)
    bad["sources"] = [{"name": "inbound", "secret": "", "templates": ["grafana-in", "sns-in"]}]
    with pytest.raises(ConfigError, match="must have no match selector"):
        Config.from_dict(bad)


def test_config_refuses_unknown_template_names_and_duplicates():
    missing = dict(MULTI)
    missing["sources"] = [{"name": "inbound", "secret": "", "templates": ["nope", "catch-all"]}]
    with pytest.raises(ConfigError, match="unknown template"):
        Config.from_dict(missing)

    dupes = dict(MULTI)
    dupes["templates"] = MULTI["templates"] + [{"name": "catch-all", "title": "{x}"}]
    with pytest.raises(ConfigError, match="duplicate template name"):
        Config.from_dict(dupes)

    unknown_kind = dict(MULTI)
    unknown_kind["templates"] = [{"name": "t", "kind": "render", "title": "{x}"}]
    unknown_kind["sources"] = [{"name": "inbound", "secret": "", "templates": ["t"]}]
    with pytest.raises(ConfigError, match="unknown kind"):
        Config.from_dict(unknown_kind)


def test_a_field_may_not_shadow_a_routing_key():
    """The routing context merges fields last, so a field named `level` would
    silently override the mapped level and routing would never know."""
    shadow = dict(MULTI)
    shadow["templates"] = [{"name": "t", "title": "{title}", "fields": {"level": "{state}"}}]
    shadow["sources"] = [{"name": "inbound", "secret": "", "templates": ["t"]}]
    with pytest.raises(ConfigError, match="would shadow the routing key"):
        Config.from_dict(shadow)

    inline_shadow = {
        "sources": [{"name": "d", "secret": "", "title": "{t}", "fields": {"source": "{s}"}}],
        "channels": [{"name": "c", "type": "generic", "url": "https://x.example"}],
        "routes": [{"name": "r", "source": "*", "send_to": ["c"]}],
    }
    with pytest.raises(ConfigError, match="would shadow the routing key"):
        Config.from_dict(inline_shadow)


async def test_the_ledger_records_which_template_read_the_event(store):
    cfg = Config.from_dict(MULTI)
    result = await handle_hook(store, cfg, cfg.sources["inbound"], SNS, now=1000.0)

    assert result["outcome"] == "routed"
    extract_step = result["steps"][0]
    assert extract_step == {"gate": "extract", "template": "sns-in"}

    recorded = (await store.recent_events(1))[0]
    assert recorded["steps"][0]["template"] == "sns-in"
    assert recorded["title"] == "AWS 健康事件", "the ledger shows an identifiable event, not a fallback"


async def test_routing_can_condition_on_a_templates_own_fields(store):
    """Templates define the vocabulary routing speaks: a field only one
    template extracts can still drive a route."""
    cfg = Config.from_dict(
        {
            **MULTI,
            "channels": [
                {"name": "disk-team", "type": "generic", "url": "https://disk.example"},
                {"name": "sink", "type": "generic", "url": "https://sink.example/in"},
            ],
            "routes": [
                {"name": "disk", "source": "*", "when": {"metric": "disk"}, "send_to": ["disk-team"], "priority": 10},
                {"name": "rest", "source": "*", "send_to": ["sink"], "priority": 0},
            ],
        }
    )
    grafana = await handle_hook(store, cfg, cfg.sources["inbound"], GRAFANA, now=1000.0)
    assert grafana["channels"] == ["disk-team", "sink"]

    sns = await handle_hook(store, cfg, cfg.sources["inbound"], SNS, now=1001.0)
    assert sns["channels"] == ["sink"], "the sns template extracts no metric, so the disk route misses"


def test_inline_processor_timeout_is_capped_at_config_load():
    """An inline processor holds the sender's connection; past the cap the
    right answer is the async topology, not a bigger number."""
    base = {
        "sources": [{"name": "d", "secret": "", "title": "{t}"}],
        "channels": [{"name": "c", "type": "generic", "url": "https://x.example"}],
        "routes": [{"name": "r", "source": "*", "send_to": ["c"]}],
    }
    ok = dict(
        base, pipeline=["dedup", {"type": "http", "name": "brain", "url": "https://b", "timeout_seconds": 5}, "routes"]
    )
    assert Config.from_dict(ok).pipeline[1].options["timeout_seconds"] == 5

    slow = dict(
        base, pipeline=["dedup", {"type": "http", "name": "brain", "url": "https://b", "timeout_seconds": 47}, "routes"]
    )
    with pytest.raises(ConfigError, match="async topology"):
        Config.from_dict(slow)


# ── primitives an ecosystem spec needs ───────────────────────────────────────


def test_fallback_paths_take_the_first_that_yields():
    """Upstream shapes carry one meaning under different keys; without {a|b}
    each variant needs its own template."""
    from hookrelay.render import render

    payload = {"detail": {"eventArn": "", "service": "SES"}, "region": "ap-southeast-1"}
    assert render("{detail.eventArn|region}", payload) == "ap-southeast-1", "empty is skipped, not returned"
    assert render("{detail.service|region}", payload) == "SES"
    assert render("{nope.here|also.missing}", payload) == ""
    assert render("res={detail.eventArn|region} svc={detail.service}", payload) == "res=ap-southeast-1 svc=SES"


def test_any_of_selector_claims_either_variant():
    cfg = Config.from_dict(
        {
            "templates": [
                {
                    "name": "prom-like",
                    "match": {"any_of": ["alerts", "commonLabels"]},
                    "title": "{alerts.0.labels.alertname|commonLabels.alertname}",
                },
                {"name": "rest", "title": "{title}"},
            ],
            "sources": [{"name": "d", "secret": "", "templates": ["prom-like", "rest"]}],
            "channels": [{"name": "c", "type": "generic", "url": "https://x.example"}],
            "routes": [{"name": "r", "source": "*", "send_to": ["c"]}],
        }
    )
    door = cfg.sources["d"]
    listed = extract_event(door, {"alerts": [{"labels": {"alertname": "DiskFull"}}]})
    assert listed["_template"] == "prom-like" and listed["title"] == "DiskFull"

    common = extract_event(door, {"commonLabels": {"alertname": "CpuHot"}})
    assert common["_template"] == "prom-like" and common["title"] == "CpuHot"

    neither = extract_event(door, {"title": "something else"})
    assert neither["_template"] == "rest"


def test_a_real_ecosystem_spec_is_expressible():
    """The migration test: a platform's aws_health adapter, written as a pipe
    template. Its identity choice (eventTypeCode, NOT eventArn — the arn is
    per-occurrence and would defeat dedup) survives the move."""
    cfg = Config.from_dict(
        {
            "templates": [
                {
                    "name": "aws-health",
                    "match": {"equals": {"source": "aws.health"}, "exists": ["detail.eventTypeCode"]},
                    "title": "{detail.eventTypeCode}",
                    "body": "{detail.eventDescription.0.latestDescription}",
                    # No level on purpose: eventTypeCategory is issue /
                    # accountNotification / scheduledChange — not a severity
                    # scale, so inventing a ranking AWS never expressed would
                    # be the pipe judging, which is not its job.
                    "fields": {
                        "service": "{detail.service}",
                        "resource": "{detail.eventArn|region}",
                        "category": "{detail.eventTypeCategory}",
                    },
                },
                {"name": "rest", "title": "{title}"},
            ],
            "sources": [
                {
                    "name": "inbound",
                    "secret": "",
                    "templates": ["aws-health", "rest"],
                    "fingerprint_fields": ["title", "service"],
                }
            ],
            "channels": [{"name": "c", "type": "generic", "url": "https://x.example"}],
            "routes": [{"name": "r", "source": "*", "send_to": ["c"]}],
        }
    )
    event = {
        "source": "aws.health",
        "region": "ap-southeast-1",
        "detail": {
            "service": "SES",
            "eventTypeCode": "AWS_SES_ENFORCEMENT_PROBATION",
            "eventTypeCategory": "accountNotification",
            "eventArn": "arn:aws:health:abc",
            "eventDescription": [{"latestDescription": "SES account on probation"}],
        },
    }
    read = extract_event(cfg.sources["inbound"], event)
    assert read["_template"] == "aws-health"
    assert read["title"] == "AWS_SES_ENFORCEMENT_PROBATION", "identity is the recurring condition"
    assert read["body"] == "SES account on probation"
    assert read["level"] == "info", "no severity invented where the sender expressed none"
    assert read["fields"]["service"] == "SES" and read["fields"]["resource"] == "arn:aws:health:abc"

    # Dedup grain follows the identity choice, not the per-occurrence arn.
    from hookrelay.extract import fingerprint

    other_occurrence = json_roundtrip(event)
    other_occurrence["detail"]["eventArn"] = "arn:aws:health:xyz"
    same = extract_event(cfg.sources["inbound"], other_occurrence)
    assert fingerprint(cfg.sources["inbound"], read) == fingerprint(cfg.sources["inbound"], same)


def json_roundtrip(value):
    import json

    return json.loads(json.dumps(value))
