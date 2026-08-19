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

GRAFANA = {
    "title": "Disk about to fill",
    "message": "/data 92%",
    "state": "alerting",
    "evalMatches": [{"metric": "disk"}],
}
SNS = {
    "TopicArn": "arn:aws:sns:x",
    "Subject": "AWS health event",
    "Message": "maintenance window",
    "Severity": "warning",
}
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
    assert grafana["title"] == "Disk about to fill" and grafana["level"] == "high"
    assert grafana["fields"]["metric"] == "disk"

    sns = extract_event(door, SNS)
    assert sns["_template"] == "sns-in"
    assert sns["title"] == "AWS health event" and sns["level"] == "warning"
    assert sns["fields"] == {}, "each template brings only its own fields"


def test_unclaimed_shapes_land_on_the_fallback_not_on_the_floor():
    read = extract_event(_door(), UNKNOWN)
    assert read["_template"] == "catch-all"
    assert read["title"] == "webhook from inbound", "poorly titled, never dropped"


def test_selection_is_ordered_first_match_wins():
    """A payload matching two selectors takes the earlier template."""
    both = dict(GRAFANA, TopicArn="arn:aws:sns:x", Subject="looks like SNS too")
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
    assert extract_event(door, GRAFANA)["title"] == "Disk about to fill"


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


def test_a_fingerprint_field_nothing_extracts_is_refused_at_boot():
    """The quietest total alert loss this service can suffer: a misspelled name
    resolves to "" for every event, so every alert of that source shares ONE
    fingerprint and everything after the first is skipped as `duplicate`. On the
    board that reads as excellent dedup. So it fails at boot, like every other
    name in the config."""
    typo = dict(MULTI)
    typo["sources"] = [
        {
            "name": "inbound",
            "secret": "",
            "templates": ["grafana-in", "sns-in", "catch-all"],
            "fingerprint_fields": ["title", "metirc"],
        }
    ]
    with pytest.raises(ConfigError, match="fingerprint_fields names"):
        Config.from_dict(typo)

    inline_typo = {
        "sources": [
            {"name": "d", "secret": "", "title": "{t}", "fields": {"service": "{s}"}},
            {"name": "e", "secret": "", "title": "{t}", "fingerprint_fields": ["titel"]},
        ],
        "channels": [{"name": "c", "type": "generic", "url": "https://x.example"}],
        "routes": [{"name": "r", "source": "*", "send_to": ["c"]}],
    }
    with pytest.raises(ConfigError, match="fingerprint_fields names"):
        Config.from_dict(inline_typo)


def test_fields_only_one_template_extracts_can_still_carry_identity():
    """The honest check is the UNION of every template, never one of them: a
    door's templates are ordered ALTERNATIVES, so `metric` (grafana-in only) and
    `topic` (sns-in only) are both legitimate identity for this door. Events read
    by the other template simply miss the one they do not extract, which is
    documented behaviour and must not become a boot failure — the fingerprint
    names here are deliberately spread across two templates so that a check
    against any SINGLE template refuses this config."""
    from hookrelay.extract import fingerprint

    raw = dict(MULTI)
    raw["templates"] = [
        {**MULTI["templates"][1], "fields": {"topic": "{TopicArn}"}} if t["name"] == "sns-in" else t
        for t in MULTI["templates"]
    ]
    raw["sources"] = [
        {
            "name": "inbound",
            "secret": "",
            "templates": ["grafana-in", "sns-in", "catch-all"],
            "fingerprint_fields": ["title", "metric", "topic"],
        }
    ]
    door = Config.from_dict(raw).sources["inbound"]
    assert door.fingerprint_fields == ("title", "metric", "topic")

    # And the fields really reach identity — the check is not just paperwork.
    other_metric = {**GRAFANA, "evalMatches": [{"metric": "memory"}]}
    assert fingerprint(door, extract_event(door, GRAFANA)) != fingerprint(door, extract_event(door, other_metric))
    # Each shape misses the field its own template does not extract, and that is
    # the point: identity is per reading, not per door.
    assert extract_event(door, GRAFANA)["fields"] == {"metric": "disk"}
    assert extract_event(door, SNS)["fields"] == {"topic": "arn:aws:sns:x"}


def test_an_enrichment_stage_before_dedup_widens_the_vocabulary():
    """A `set` stage writes its field names down in the config, so they are
    knowable and accepted — but only ahead of the dedup stage that takes the
    fingerprint, because a field set afterwards is still "" when identity is
    decided. An `http` brain answers with names nobody here can enumerate, so
    that config is not judged at all: refusing honest config is worse than
    missing a typo."""
    base = {
        "sources": [{"name": "d", "secret": "", "title": "{t}", "fingerprint_fields": ["title", "team"]}],
        "channels": [{"name": "c", "type": "generic", "url": "https://x.example"}],
        "routes": [{"name": "r", "source": "*", "send_to": ["c"]}],
    }
    tag = {"type": "set", "name": "tag", "set": {"fields": {"team": "db"}}}

    assert Config.from_dict(dict(base, pipeline=[tag, "dedup", "routes"])).sources["d"].fingerprint_fields == (
        "title",
        "team",
    )
    with pytest.raises(ConfigError, match="ahead of the dedup stage"):
        Config.from_dict(dict(base, pipeline=["dedup", tag, "routes"]))

    brain = {"type": "http", "name": "brain", "url": "https://b.example"}
    assert Config.from_dict(dict(base, pipeline=[brain, "dedup", "routes"])).sources["d"].fingerprint_fields == (
        "title",
        "team",
    )


async def test_the_ledger_records_which_template_read_the_event(store):
    cfg = Config.from_dict(MULTI)
    result = await handle_hook(store, cfg, cfg.sources["inbound"], SNS, now=1000.0)

    assert result["outcome"] == "routed"
    extract_step = result["steps"][0]
    assert extract_step == {"gate": "extract", "template": "sns-in"}

    recorded = (await store.recent_events(1))[0]
    assert recorded["steps"][0]["template"] == "sns-in"
    assert recorded["title"] == "AWS health event", "the ledger shows an identifiable event, not a fallback"


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


def test_recovery_template_sets_a_top_level_flag_not_a_field():
    """The recovery flag must ride OUTSIDE fields: identity is built from
    fields, and a flag that flips between firing and recovery would split the
    pair into two identities — the recovery could never find its firing."""
    from hookrelay.templates import ExtractTemplate

    template = ExtractTemplate(
        name="t", title="{meta.alert_name}", body="{analysis.summary}", recovery="{meta.is_recovery}"
    )
    firing = template.extract(
        {"meta": {"alert_name": "cpu", "is_recovery": False}, "analysis": {"summary": "cpu high"}}, door="ww"
    )
    recovery = template.extract(
        {"meta": {"alert_name": "cpu", "is_recovery": True}, "analysis": {"summary": "cpu high"}}, door="ww"
    )
    assert firing["is_recovery"] is False
    assert recovery["is_recovery"] is True
    assert "is_recovery" not in firing["fields"] and "is_recovery" not in recovery["fields"]
    # Unconfigured template: no key at all — downstream falls back to sniffing.
    bare = ExtractTemplate(name="t", title="{meta.alert_name}").extract({"meta": {"alert_name": "cpu"}}, door="ww")
    assert "is_recovery" not in bare
