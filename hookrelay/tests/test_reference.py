"""A source may name the sending platform's own id for an event, and the pipe
carries it to brains as `reference` — beside fields, never in them.

Why beside: fields feed the judge's identity, and an id that differs per event
would split a firing from its recovery. Why at all: since the platform's own
investigation leg was switched off, the pipe starts investigations on its own
and their reports reached chat and the investigator's console but never the
platform's alert page — nothing on the report said which alert it was about.
"""

from __future__ import annotations

import json

from hookrelay.channels import build_request
from hookrelay.config import Channel, Config
from hookrelay.extract import extract_event
from hookrelay.pipeline import handle_hook
from hookrelay.store import Store

ENVELOPE = {
    "meta": {"event_id": 2630, "alert_name": "Example deposit threshold", "importance": "medium"},
    "analysis": {"summary": "one deposit over the line", "detail": "amount 856.59, threshold 500"},
}


def _config(**source_extra: object) -> Config:
    return Config.from_dict(
        {
            "sources": [
                {
                    "name": "platform",
                    "secret": "",
                    "title": "{meta.alert_name}",
                    "body": "{analysis.summary}",
                    "level": "{meta.importance}",
                    "fields": {"origin": "{meta.source}"},
                    **source_extra,
                }
            ],
            "channels": [{"name": "to-brain", "type": "generic", "url": "https://brain.example/hooks/event"}],
            "routes": [{"name": "all", "source": "*", "send_to": ["to-brain"]}],
        }
    )


def test_the_reference_is_extracted_beside_fields_and_bounded():
    source = _config(reference="{meta.event_id}").sources["platform"]
    extracted = extract_event(source, ENVELOPE)
    assert extracted["reference"] == "2630"
    assert "reference" not in extracted["fields"], "never an identity field"

    long_id = extract_event(source, {"meta": {"event_id": "x" * 500}})
    assert len(long_id["reference"]) == 200

    unnamed = _config().sources["platform"]
    assert "reference" not in extract_event(unnamed, ENVELOPE), "a source that names none sends none"
    absent = extract_event(source, {"meta": {"alert_name": "no id here"}})
    assert absent["reference"] == "", "a missing path renders empty, as every template does"


async def test_the_reference_rides_the_delivery_row_into_the_brains_payload(store: Store):
    cfg = _config(reference="{meta.event_id}")
    result = await handle_hook(store, cfg, cfg.sources["platform"], ENVELOPE, now=1000.0)
    assert result["outcome"] == "routed"

    row = (await store.due_deliveries(now=1001.0))[0]
    assert row["reference"] == "2630", "the ledger keeps it with the event"

    message = {
        "event_id": row["event_id"],
        "source": row["source"],
        "title": row["title"],
        "body": row["body"],
        "level": row["level"],
        "fields": json.loads(row["fields_json"] or "{}"),
        "received_at": row["received_at"],
        "reference": row["reference"],
        "payload": json.loads(row["payload_json"]),
        "_idempotency_key": "x",
    }
    channel = Channel(name="to-brain", type="generic", url="https://brain.example/hooks/event")
    _, body, _ = build_request(channel, message, now=1002.0)
    sent = json.loads(body)
    assert sent["reference"] == "2630" and sent["fields"] == {"origin": ""}
    assert "payload" not in sent and "_idempotency_key" not in sent


def test_touch_names_a_channel_that_must_exist():
    cfg = Config.from_dict(
        {
            "sources": [{"name": "s", "secret": "", "title": "{t}"}],
            "channels": [{"name": "to-platform-touch", "type": "generic", "url": "https://platform.example/touch"}],
            "routes": [{"name": "all", "source": "*", "send_to": ["to-platform-touch"]}],
            "touch": {"forward_to": "to-platform-touch"},
        }
    )
    assert cfg.touch_forward_to == "to-platform-touch"
    assert _config().touch_forward_to == "", "absent means no touch channel, as before"
    import pytest

    from hookrelay.config import ConfigError

    with pytest.raises(ConfigError):
        Config.from_dict(
            {
                "sources": [{"name": "s", "secret": "", "title": "{t}"}],
                "channels": [{"name": "c", "type": "generic", "url": "https://c.example/"}],
                "routes": [{"name": "all", "source": "*", "send_to": ["c"]}],
                "touch": {"forward_to": "nowhere"},
            }
        )


async def test_the_ledger_answers_an_events_reference_and_the_chain_origin_carries_it(store: Store):
    cfg = _config(reference="{meta.event_id}")
    result = await handle_hook(store, cfg, cfg.sources["platform"], ENVELOPE, now=1000.0)
    event_id = int(result["event_id"])
    assert await store.event_reference(event_id) == "2630"
    assert await store.event_reference(event_id + 1000) == "", "an unknown event has none, not an error"
    trip = await store.round_trip(event_id)
    assert trip is not None and str(trip["origin"].get("reference")) == "2630", "the origin row the thread lookup reads"
