"""The gate walk: order, outcomes, and the trace every event must leave."""

from __future__ import annotations

from hookrelay.config import Config
from hookrelay.pipeline import handle_hook

PAYLOAD = {"title": "db down", "message": "primary unreachable", "state": "alerting"}


async def test_routed_event_enqueues_deliveries_and_records_why(store, cfg):
    result = await handle_hook(store, cfg, cfg.sources["grafana"], PAYLOAD, now=1000.0)

    assert result["outcome"] == "routed"
    # priority 100 route matched (level high) AND the catch-all mirror:
    assert result["channels"] == ["feishu-main", "ding-main", "mirror"]
    gates = [step.get("gate") for step in result["steps"]]
    # extract leads: which template read the payload is part of the account.
    assert gates == ["extract", "dedup", "silence", "routes"]

    rows = await store.due_deliveries(now=1001.0)
    assert sorted(row["channel"] for row in rows) == ["ding-main", "feishu-main", "mirror"]

    recent = await store.recent_events(5)
    assert recent[0]["outcome"] == "routed"


async def test_duplicate_within_window_is_skipped_against_the_original(store, cfg):
    first = await handle_hook(store, cfg, cfg.sources["grafana"], PAYLOAD, now=1000.0)
    second = await handle_hook(store, cfg, cfg.sources["grafana"], PAYLOAD, now=1030.0)

    assert second["outcome"] == "skipped" and second["skip_code"] == "duplicate"
    dedup_step = second["steps"][1]  # [0] is the extract step
    assert dedup_step["first_event_id"] == first["event_id"]
    assert dedup_step["seconds_ago"] == 30
    # No deliveries were enqueued for the repeat.
    assert len(await store.due_deliveries(now=2000.0)) == 3


async def test_duplicate_outside_window_passes(store, cfg):
    await handle_hook(store, cfg, cfg.sources["grafana"], PAYLOAD, now=1000.0)
    later = await handle_hook(store, cfg, cfg.sources["grafana"], PAYLOAD, now=1000.0 + 121)
    assert later["outcome"] == "routed"


async def test_silence_stops_routing_but_still_records(store, cfg):
    await store.add_silence("grafana", until_ts=2000.0, note="maintenance", now=900.0)
    result = await handle_hook(store, cfg, cfg.sources["grafana"], PAYLOAD, now=1000.0)

    assert result["skip_code"] == "silenced"
    assert await store.due_deliveries(now=2000.0) == []
    recent = await store.recent_events(5)
    assert recent[0]["skip_code"] == "silenced"


async def test_global_silence_covers_every_source(store, cfg):
    await store.add_silence("*", until_ts=2000.0, note="", now=900.0)
    result = await handle_hook(store, cfg, cfg.sources["ci"], {"job": "build", "detail": "x"}, now=1000.0)
    assert result["skip_code"] == "silenced"


async def test_no_route_is_a_named_outcome_not_an_error(store, cfg):
    # ci events are info-level; only the mirror catch-all claims them — so
    # drop that route to manufacture a no_route.
    slim = cfg.__class__(
        sources=cfg.sources, channels=cfg.channels, routes=tuple(r for r in cfg.routes if r.name == "high")
    )
    result = await handle_hook(store, slim, cfg.sources["ci"], {"job": "build", "detail": "x"}, now=1000.0)
    assert result["outcome"] == "skipped" and result["skip_code"] == "no_route"
    # The trace shows which routes were considered and why they missed.
    route_step = result["steps"][-1]
    assert route_step["gate"] == "routes" and route_step["matched_channels"] == []


async def test_payload_is_stored_whole_for_raw_fidelity(store, cfg):
    """Since raw-passthrough channels deliver the stored payload, truncating it
    would corrupt deliveries. Size is bounded at the DOOR (413 over
    max_body_bytes), so storage keeps every byte that was admitted."""
    big = dict(PAYLOAD, blob="x" * 40_000)
    result = await handle_hook(store, cfg, cfg.sources["grafana"], big, now=1000.0)
    assert result["outcome"] == "routed"
    cursor = await store.db.execute("SELECT payload_json FROM events WHERE id = ?", (result["event_id"],))
    row = await cursor.fetchone()
    import json as _json

    assert _json.loads(row["payload_json"])["blob"] == "x" * 40_000


# ── the doctrine, with a voice ───────────────────────────────────────────────


def test_a_judgment_stage_in_front_of_a_brain_says_so() -> None:
    """README's doctrine says `filter`, `set` and dedup-as-noise-control belong
    to standalone posture and "should all yield" in a paired deployment. That
    sentence had no way to make itself heard: a config could run dedup in front
    of a brain forever and nothing would mention it.

    A warning, not a refusal — a deployment mid-migration legitimately runs both
    for a while, and refusing to boot over a posture preference would be the
    pipe overruling its operator.
    """
    from hookrelay.config import _warn_posture_mix

    base = {
        "sources": [{"name": "grafana", "secret": "", "title": "{title}", "body": "{message}"}],
        "channels": [{"name": "ops", "type": "feishu", "url": "https://feishu.example/hook"}],
        "routes": [{"name": "all", "source": "*", "send_to": ["ops"]}],
    }

    # Paired via the pipeline: an http stage hands the event to a brain.
    paired_http = Config.from_dict(
        {
            **base,
            "pipeline": [
                "dedup",
                "silence",
                {"type": "http", "name": "triage", "url": "https://brain.example/score"},
                "routes",
            ],
        }
    )
    warning = _warn_posture_mix(paired_http.pipeline, paired_http.channels)
    assert "posture mix" in warning and "dedup" in warning
    assert "http stage" in warning

    # Paired via the channel: this deployment renders a brain's RESULT.
    paired_return = Config.from_dict(
        {
            **base,
            "channels": [
                {
                    "name": "ops",
                    "type": "feishu",
                    "url": "https://feishu.example/hook",
                    "options": {"payload": "processed"},
                }
            ],
            "pipeline": [{"type": "filter", "name": "mute-low", "when": {"level": ["low"]}}, "routes"],
        }
    )
    assert "payload: processed" in _warn_posture_mix(paired_return.pipeline, paired_return.channels)

    # But a filter that only matches the brain's own RETURN — pinned to one
    # source and keyed on the verdict's wake answer — is the brain deciding,
    # not the pipe second-guessing it. Warning on that every boot would teach
    # operators that this warning cries wolf.
    enforcing_return = Config.from_dict(
        {
            **base,
            "channels": [
                {
                    "name": "ops",
                    "type": "feishu",
                    "url": "https://feishu.example/hook",
                    "options": {"payload": "processed"},
                }
            ],
            "pipeline": [
                {"type": "filter", "name": "quiet", "when": {"source": "grafana", "wake": "no"}},
                "routes",
            ],
        }
    )
    assert _warn_posture_mix(enforcing_return.pipeline, enforcing_return.channels) == ""

    # Standalone: the judgment stages are exactly what this posture is for.
    standalone = Config.from_dict({**base, "pipeline": ["dedup", "silence", "routes"]})
    assert _warn_posture_mix(standalone.pipeline, standalone.channels) == ""

    # Paired with no judgment stage — the shape the doctrine actually asks for.
    clean_paired = Config.from_dict(
        {
            **base,
            "channels": [
                {
                    "name": "ops",
                    "type": "feishu",
                    "url": "https://feishu.example/hook",
                    "options": {"payload": "processed"},
                }
            ],
            "pipeline": ["silence", "routes"],
        }
    )
    assert _warn_posture_mix(clean_paired.pipeline, clean_paired.channels) == ""


async def test_wake_no_quiets_and_everything_else_fails_open(store):
    """The shadow deployment's quiet stage, exercised with its real shape: a
    filter on an EXTRACTED field carrying the judge's wake answer.

    Three payloads, three fates. An explicit "no" is dropped with a named code
    on its own trace; an explicit "yes" delivers; and '' — the unanswered rows,
    every pre-wake verdict and every parse failure — delivers too. That last
    one is the contract: a quiet that triggers on absence would silently extend
    itself to every row a future bug fails to annotate.
    """
    cfg = Config.from_dict(
        {
            "sources": [
                {
                    "name": "judge-notify",
                    "secret": "",
                    "title": "{meta.alert_name}",
                    "body": "{analysis.summary}",
                    "fields": {"wake": "{meta.wake_someone}"},
                }
            ],
            "channels": [{"name": "to-me", "type": "feishu", "url": "http://bridge:9000/send"}],
            "routes": [{"name": "verdict-to-me", "source": "judge-notify", "send_to": ["to-me"]}],
            "pipeline": [
                {
                    "type": "filter",
                    "name": "quiet-wake-no",
                    "when": {"source": "judge-notify", "wake": "no"},
                    "skip_code": "wake_no",
                },
                "routes",
            ],
        }
    )
    source = cfg.sources["judge-notify"]

    def verdict(wake: str, name: str) -> dict:
        return {"meta": {"alert_name": name, "wake_someone": wake}, "analysis": {"summary": "s"}}

    quiet = await handle_hook(store, cfg, source, verdict("no", "top-up over 500"), now=1000.0)
    assert quiet["outcome"] == "skipped" and quiet["skip_code"] == "wake_no"

    loud = await handle_hook(store, cfg, source, verdict("yes", "SES bounce rate"), now=1001.0)
    assert loud["outcome"] == "routed" and loud["channels"] == ["to-me"]

    unanswered = await handle_hook(store, cfg, source, verdict("", "legacy verdict"), now=1002.0)
    assert unanswered["outcome"] == "routed", "'' must fail open into a card, never into silence"
