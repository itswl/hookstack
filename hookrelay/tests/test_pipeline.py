"""The gate walk: order, outcomes, and the trace every event must leave."""

from __future__ import annotations

from hookrelay.config import Config
from hookrelay.pipeline import handle_hook, settle_folds

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
        "channels": [{"name": "ops", "type": "bridge", "url": "https://feishu.example/hook"}],
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
                    "type": "bridge",
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
                    "type": "bridge",
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
                    "type": "bridge",
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
            "channels": [{"name": "to-me", "type": "bridge", "url": "http://bridge:9000/send"}],
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


def _sampling_cfg(pct: int, banner: str = "") -> Config:
    """The quiet-wake-no stage with a silent-audit sample turned on."""
    stage = {
        "type": "filter",
        "name": "quiet-wake-no",
        "when": {"source": "judge-notify", "wake": "no"},
        "skip_code": "wake_no",
        "sample_pct": pct,
    }
    if banner:
        stage["sample_banner"] = banner
    return Config.from_dict(
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
            "channels": [{"name": "to-me", "type": "bridge", "url": "http://bridge:9000/send"}],
            "routes": [{"name": "verdict-to-me", "source": "judge-notify", "send_to": ["to-me"]}],
            "pipeline": [stage, "routes"],
        }
    )


def _verdict(name: str) -> dict:
    return {"meta": {"alert_name": name, "wake_someone": "no"}, "analysis": {"summary": "the summary"}}


async def test_a_sampled_wake_no_card_is_delivered_not_dropped(store):
    """The whole point: the thing the filter swallows can occasionally be looked
    at. At 100% every matched card passes instead of dropping — and it delivers,
    so a person can finally press it."""
    cfg = _sampling_cfg(100)
    source = cfg.sources["judge-notify"]
    out = await handle_hook(store, cfg, source, _verdict("top-up over 500"), now=1000.0)
    assert out["outcome"] == "routed" and out["channels"] == ["to-me"]


async def test_sampling_off_by_default_still_drops(store):
    """0 percent is off, and off is exactly today's behaviour — a quiet that
    turned itself partly on by default would be the opposite of opt-in."""
    cfg = _sampling_cfg(0)
    source = cfg.sources["judge-notify"]
    out = await handle_hook(store, cfg, source, _verdict("top-up over 500"), now=1000.0)
    assert out["outcome"] == "skipped" and out["skip_code"] == "wake_no"


async def test_the_same_condition_is_sampled_consistently_not_per_fire(store):
    """Deterministic by identity: a condition is either always sampled or never,
    so a storm's Nth restatement is not delivered while its first was dropped —
    which would read as the dedup breaking. Two fires of one condition agree."""
    cfg = _sampling_cfg(100)  # deterministic regardless of pct; 100 makes the fate readable
    source = cfg.sources["judge-notify"]
    a = await handle_hook(store, cfg, source, _verdict("top-up over 500"), now=1000.0)
    b = await handle_hook(store, cfg, source, _verdict("top-up over 500"), now=2000.0)
    assert a["outcome"] == b["outcome"], "same condition, same fate"


async def test_a_sampled_card_is_marked_so_the_ledger_and_the_reader_can_tell(store):
    """It carries a banner (so a person knows they were NOT going to be paged)
    and a field (so the ledger separates it from a real wake=yes). Without the
    banner a silent-audit sample is indistinguishable from a real page, which is
    the one thing this must never be."""
    cfg = _sampling_cfg(100, banner="AUDIT — you were not going to be paged")
    source = cfg.sources["judge-notify"]
    out = await handle_hook(store, cfg, source, _verdict("top-up over 500"), now=1000.0)
    row = (await store.recent_events(1))[0]
    assert row["fields"].get("audit_sample") == "wake_no", "the ledger can find sampled cards"
    # The banner is in the event BODY, which /trace serves and recent_events does
    # not — read it straight so the assertion is about the bytes a card is built
    # from, not about the summary row.
    cursor = await store.read.execute("SELECT body FROM events WHERE id = ?", (row["id"],))
    body = (await cursor.fetchone())["body"]
    assert body.startswith("AUDIT"), "the banner leads the card so a reader sees it first"
    assert "the summary" in body, "and the real summary still follows it"
    assert out["outcome"] == "routed"


# ── fold: one card per condition per window, on the return door ──────────────


def _fold_cfg(window: int = 3600, key: str = "title", ceiling: int = 0, silence: bool = False) -> Config:
    return Config.from_dict(
        {
            "sources": [
                {
                    "name": "judge-notify",
                    "secret": "",
                    "title": "{meta.alert_name}",
                    "body": "{analysis.summary}",
                    "recovery": "{meta.is_recovery}",
                    "fields": {"wake": "{meta.wake_someone}", "rule": "{meta.rule_name}"},
                },
                {"name": "grafana", "secret": "", "title": "{title}", "body": "{message}", "level": "high"},
            ],
            "channels": [{"name": "to-me", "type": "bridge", "url": "http://bridge:9000/send"}],
            "routes": [
                {"name": "verdict-to-me", "source": "judge-notify", "send_to": ["to-me"]},
                {"name": "raw-to-me", "source": "grafana", "send_to": ["to-me"]},
            ],
            "pipeline": [
                {
                    "type": "fold",
                    "name": "fold-repeats",
                    "when": {"source": "judge-notify", "wake": "yes"},
                    "window_seconds": window,
                    "max_window_seconds": ceiling or window,
                    "key": key,
                    "skip_code": "folded",
                },
                *(["silence"] if silence else []),
                "routes",
            ],
        }
    )


def _loud(name: str, rule: str = "", recovery: bool = False, body: str = "the summary") -> dict:
    meta = {"alert_name": name, "wake_someone": "yes", "rule_name": rule or name}
    if recovery:
        meta["is_recovery"] = True
    return {"meta": meta, "analysis": {"summary": body}}


async def test_a_repeat_inside_the_window_is_folded_into_the_card_that_went(store):
    """The first verdict for a condition reaches the person; the same condition
    judged again inside the window does not, and the ledger says which card it
    folded into and how long after — recorded, never silently dropped."""
    cfg = _fold_cfg(window=3600)
    source = cfg.sources["judge-notify"]
    first = await handle_hook(store, cfg, source, _loud("Log error spike", body="1,204 lines"), now=1000.0)
    assert first["outcome"] == "routed" and first["channels"] == ["to-me"]
    again = await handle_hook(store, cfg, source, _loud("Log error spike", body="1,311 lines"), now=1000.0 + 900)
    assert again["outcome"] == "skipped" and again["skip_code"] == "folded"
    step = next(s for s in again["steps"] if s.get("gate") == "fold-repeats")
    assert step["result"] == "folded" and step["into_event_id"] == first["event_id"] and step["seconds_ago"] == 900
    recent = await store.recent_events(1)
    assert recent[0]["skip_code"] == "folded", "the ledger can find every folded repeat by name"
    assert len(await store.due_deliveries(now=5000.0)) == 1, "one card, not two"


async def test_the_window_is_anchored_on_what_was_delivered_not_on_what_arrived(store):
    """A folded repeat must not extend the window: a condition firing every
    fifteen minutes surfaces once an hour, not never."""
    cfg = _fold_cfg(window=3600)
    source = cfg.sources["judge-notify"]
    await handle_hook(store, cfg, source, _loud("Log error spike"), now=1000.0)
    for minutes in (15, 30, 45):
        out = await handle_hook(store, cfg, source, _loud("Log error spike"), now=1000.0 + minutes * 60)
        assert out["skip_code"] == "folded"
    surfaced = await handle_hook(store, cfg, source, _loud("Log error spike"), now=1000.0 + 3601)
    assert surfaced["outcome"] == "routed", "the hour is measured from the card that went, not from the last repeat"
    assert len(await store.due_deliveries(now=9000.0)) == 2


async def test_a_recovery_and_a_different_condition_are_never_folded(store):
    cfg = _fold_cfg(window=3600)
    source = cfg.sources["judge-notify"]
    await handle_hook(store, cfg, source, _loud("Log error spike"), now=1000.0)
    other = await handle_hook(store, cfg, source, _loud("Disk /data at 92%"), now=1001.0)
    assert other["outcome"] == "routed", "another condition is another card"
    resolved = await handle_hook(store, cfg, source, _loud("Log error spike", recovery=True), now=1002.0)
    assert resolved["outcome"] == "routed", "a resolved card nobody received is a firing nobody can stop worrying about"
    step = next(s for s in resolved["steps"] if s.get("gate") == "fold-repeats")
    assert "never folded" in step["why"]


async def test_the_fold_keys_on_the_field_the_door_extracts_when_told_to(store):
    """Two titles, one rule: folded together when `key: rule`, apart by default."""
    by_rule = _fold_cfg(window=3600, key="rule")
    source = by_rule.sources["judge-notify"]
    await handle_hook(store, by_rule, source, _loud("Log error spike on api-1", rule="log-error-spike"), now=1000.0)
    out = await handle_hook(
        store, by_rule, source, _loud("Log error spike on api-2", rule="log-error-spike"), now=1100.0
    )
    assert out["skip_code"] == "folded"
    by_title = _fold_cfg(window=3600)
    out = await handle_hook(
        store, by_title, source, _loud("Log error spike on api-3", rule="log-error-spike"), now=1200.0
    )
    assert out["outcome"] == "routed", "by title these are three conditions"


async def test_the_fold_leaves_every_other_door_and_every_quiet_verdict_alone(store):
    cfg = _fold_cfg(window=3600)
    twice = {"meta": {"alert_name": "Log error spike", "wake_someone": "no"}, "analysis": {"summary": "s"}}
    for now in (1000.0, 1001.0):
        out = await handle_hook(store, cfg, cfg.sources["judge-notify"], twice, now=now)
        assert out["outcome"] == "routed", "wake=no is the wake filter's business, not this stage's"
        assert next(s for s in out["steps"] if s.get("gate") == "fold-repeats")["result"] == "not_applied"
    raw = {"title": "db down", "message": "x"}
    for now in (2000.0, 2001.0):
        assert (await handle_hook(store, cfg, cfg.sources["grafana"], raw, now=now))["outcome"] == "routed"


async def test_the_card_that_goes_says_how_many_it_stands_for(store):
    """The repeats held back are not lost to the person: the next card for the
    condition carries the count and how long the run lasted, and so does its
    recovery — a digest with no clock of its own."""
    cfg = _fold_cfg(window=3600)
    source = cfg.sources["judge-notify"]
    await handle_hook(store, cfg, source, _loud("Log error spike"), now=0.0)
    for minutes in (15, 30, 45):
        await handle_hook(store, cfg, source, _loud("Log error spike"), now=minutes * 60.0)
    went = await handle_hook(store, cfg, source, _loud("Log error spike"), now=61 * 60.0)
    assert went["outcome"] == "routed"
    step = next(s for s in went["steps"] if s.get("gate") == "fold-repeats")
    assert step["folded_since_last"] == 3
    (latest,) = await store.recent_events(1)
    assert latest["fields"]["folded"] == "3 more since the last card, over 46 min"

    await handle_hook(store, cfg, source, _loud("Log error spike"), now=70 * 60.0)
    resolved = await handle_hook(store, cfg, source, _loud("Log error spike", recovery=True), now=80 * 60.0)
    assert resolved["outcome"] == "routed"
    step = next(s for s in resolved["steps"] if s.get("gate") == "fold-repeats")
    assert step["folded_since_last"] == 1 and "never folded" in step["why"], "the recovery carries the count too"


async def test_a_condition_that_keeps_coming_back_gets_its_cards_further_apart(store):
    """Every fifteen minutes for twelve hours. A fixed hour sends a card every
    seventy-five minutes; with a ceiling the window doubles with each card the
    condition already cost — one hour, one, two, four — and after a recovery a
    new firing starts from the hour again."""

    async def cards(cfg: Config, title: str) -> list[int]:
        source = cfg.sources["judge-notify"]
        went = []
        for minutes in range(0, 721, 15):
            out = await handle_hook(store, cfg, source, _loud(title), now=minutes * 60.0)
            if out["outcome"] == "routed":
                went.append(minutes)
        return went

    assert await cards(_fold_cfg(window=3600), "Fixed") == [0, 75, 150, 225, 300, 375, 450, 525, 600, 675]
    widening = _fold_cfg(window=3600, ceiling=4 * 3600)
    assert await cards(widening, "Widening") == [0, 75, 210, 465, 720]

    # The recovery the person is waiting for goes at once; a firing right after
    # that recovery card folds — and, if it stays, the worker loop sends it a
    # base window later, once.
    source = widening.sources["judge-notify"]
    resolved = await handle_hook(store, widening, source, _loud("Widening", recovery=True), now=725 * 60.0)
    assert resolved["outcome"] == "routed"
    back = await handle_hook(store, widening, source, _loud("Widening"), now=800 * 60.0)
    assert back["skip_code"] == "folded"
    assert await settle_folds(store, widening, now=800 * 60.0 + 3599) == 0, "not before a base window of quiet"
    assert await settle_folds(store, widening, now=800 * 60.0 + 3600) == 1
    assert await settle_folds(store, widening, now=800 * 60.0 + 7200) == 0, "once"
    (settled,) = [e for e in await store.recent_events(20) if e["id"] == back["event_id"]]
    assert settled["outcome"] == "routed" and settled["channels"] == ["to-me"]
    assert any(st.get("result") == "settled" for st in settled["steps"])


def test_a_bridge_card_shows_the_fold_count_under_the_brains_details() -> None:
    """A brain's card is the brain's; the pipe adds one line, and only this one."""
    from hookrelay.channels import card_model_for
    from hookrelay.config import Channel

    channel = Channel(name="to-me", type="bridge", url="http://bridge:9000/send", options={"payload": "processed"})
    payload = {"meta": {"alert_name": "Log error spike"}, "analysis": {"summary": "1,311 lines"}}
    message = {"payload": payload, "fields": {"folded": "3 more since the last card, over 46 min"}, "event_id": 9}
    card = card_model_for(channel, message)
    assert card["details"].endswith("3 more since the last card, over 46 min")
    plain = card_model_for(channel, {**message, "fields": {}})
    assert "more since the last card" not in str(plain.get("details") or "")


async def test_a_condition_flapping_between_firing_and_resolved_folds_both_halves(store):
    """Firing, resolved, firing, every quarter of an hour. The first pair goes;
    after the recovery card the rest folds, firings and recoveries alike, until
    the window ends — then a firing goes with the count of what it stands for,
    and its recovery, awaited, goes at once."""
    cfg = _fold_cfg(window=3600)
    source = cfg.sources["judge-notify"]
    went = []
    for i, minutes in enumerate(range(0, 106, 15)):
        out = await handle_hook(store, cfg, source, _loud("Flapping", recovery=bool(i % 2)), now=minutes * 60.0)
        went.append(out["outcome"] == "routed")
    assert went == [True, True, False, False, False, False, True, True]
    (back,) = [e for e in await store.recent_events(10) if e["received_at"] == 90 * 60.0]
    assert back["fields"]["folded"] == "4 more since the last card, over 1 h"
    assert await settle_folds(store, cfg, now=(105 + 120) * 60.0) == 0, "every held firing was followed by a recovery"


async def test_a_condition_that_comes_back_and_keeps_firing_is_told_once(store):
    """After a recovery card the condition comes back and re-fires. Every
    re-fire folds; the first settles a base window after it came back — the
    later ones say it is still there, they do not restart the clock — and the
    rest stay held, because the person now knows it is firing."""
    cfg = _fold_cfg(window=3600)
    source = cfg.sources["judge-notify"]
    await handle_hook(store, cfg, source, _loud("Back"), now=0.0)
    await handle_hook(store, cfg, source, _loud("Back", recovery=True), now=600.0)
    held = [await handle_hook(store, cfg, source, _loud("Back"), now=t) for t in (1200.0, 2400.0, 3000.0, 4000.0)]
    assert all(out["skip_code"] == "folded" for out in held)
    assert await settle_folds(store, cfg, now=1200.0 + 3600) == 1
    assert await settle_folds(store, cfg, now=1200.0 + 3660) == 0
    assert await settle_folds(store, cfg, now=4000.0 + 3600) == 0, "once, not once per held firing"
    (went,) = [e for e in await store.recent_events(20) if e["id"] == held[0]["event_id"]]
    assert went["outcome"] == "routed" and went["fields"]["folded"] == "3 more since the last card, over 40 min"


async def test_a_held_firing_that_ended_again_is_not_settled(store):
    """Back after the recovery card, then over again before the window was out:
    both halves fold, and the last card — "it ended" — is true again."""
    cfg = _fold_cfg(window=3600)
    source = cfg.sources["judge-notify"]
    await handle_hook(store, cfg, source, _loud("Brief"), now=0.0)
    await handle_hook(store, cfg, source, _loud("Brief", recovery=True), now=600.0)
    back = await handle_hook(store, cfg, source, _loud("Brief"), now=1200.0)
    over = await handle_hook(store, cfg, source, _loud("Brief", recovery=True), now=1800.0)
    assert back["skip_code"] == over["skip_code"] == "folded"
    assert await settle_folds(store, cfg, now=1200.0 + 3600) == 0
    assert await settle_folds(store, cfg, now=1800.0 + 3600) == 0, "nor the recovery: the last card already said so"


async def test_a_held_firing_stays_held_once_a_card_has_gone_since(store):
    """The window since the recovery card ran out and a later firing went on
    its own: the person was told, so the one held before it is not sent too."""
    cfg = _fold_cfg(window=3600)
    source = cfg.sources["judge-notify"]
    await handle_hook(store, cfg, source, _loud("Told"), now=0.0)
    await handle_hook(store, cfg, source, _loud("Told", recovery=True), now=600.0)
    held = await handle_hook(store, cfg, source, _loud("Told"), now=3000.0)
    assert held["skip_code"] == "folded"
    assert (await handle_hook(store, cfg, source, _loud("Told"), now=4300.0))["outcome"] == "routed"
    assert await settle_folds(store, cfg, now=3000.0 + 3600) == 0


async def test_a_repeat_after_a_firing_card_needs_no_settling(store):
    """Folded into a firing card, the person already knows it is firing."""
    cfg = _fold_cfg(window=3600)
    source = cfg.sources["judge-notify"]
    await handle_hook(store, cfg, source, _loud("Steady"), now=0.0)
    await handle_hook(store, cfg, source, _loud("Steady"), now=900.0)
    assert await settle_folds(store, cfg, now=900.0 + 4 * 3600) == 0


async def test_a_settle_respects_a_silence_in_force(store):
    """The held firing walks the stages after the fold; a silence by then holds
    it under the silence's own code, and nothing is sent."""
    silenced = _fold_cfg(window=3600, silence=True)
    source = silenced.sources["judge-notify"]
    await handle_hook(store, silenced, source, _loud("Quieted"), now=0.0)
    await handle_hook(store, silenced, source, _loud("Quieted", recovery=True), now=600.0)
    back = await handle_hook(store, silenced, source, _loud("Quieted"), now=1200.0)
    assert back["skip_code"] == "folded"
    await store.add_silence("judge-notify", 1200.0 + 2 * 3600, "maintenance", 1200.0)
    assert await settle_folds(store, silenced, now=1200.0 + 3600) == 0
    (held,) = [e for e in await store.recent_events(10) if e["id"] == back["event_id"]]
    assert held["outcome"] == "skipped" and held["skip_code"] == "silenced", "held by the silence, under its own code"
    assert all(d["event_id"] != back["event_id"] for d in await store.due_deliveries(now=1200.0 + 3600)), "nothing sent"
