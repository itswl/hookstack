"""The card model becomes a Feishu card here, and only here."""

from __future__ import annotations

import json

import render


def test_tone_earns_the_colour_and_recovery_is_green() -> None:
    assert render.feishu_card({"title": "t", "tone": "high"})["card"]["header"]["template"] == "red"
    assert render.feishu_card({"title": "t", "tone": "recovery"})["card"]["header"]["template"] == "green"
    assert render.feishu_card({"title": "t", "tone": "whatever"})["card"]["header"]["template"] == "turquoise"


def test_payload_text_is_escaped_only_where_markup_renders() -> None:
    hostile = "<at id=all></at> disk nominal"
    card = render.feishu_card({"title": hostile, "tone": "info", "summary": hostile, "footer": hostile})["card"]
    assert card["header"]["title"]["content"] == hostile, (
        "plain_text renders no markup; escaping would show backslashes"
    )
    assert card["elements"][-1]["elements"][0]["content"] == hostile, "the footer note is plain_text too"
    assert card["elements"][0]["text"]["content"] == "\\<at id=all>\\</at> disk nominal", (
        "lark_md is where the mention would fire"
    )


def test_links_are_clickable_only_when_they_earn_it() -> None:
    card = render.feishu_card(
        {
            "title": "t",
            "links": [
                {"text": "runbook", "url": "https://kb.example/r/42(a)"},
                {"text": "trap", "url": "javascript:alert(1)"},
                {"text": "", "url": ""},
            ],
        }
    )["card"]
    runbooks = card["elements"][0]["text"]["content"]
    assert "[runbook](https://kb.example/r/42%28a%29)" in runbooks
    assert "trap (javascript:alert(1))" in runbooks, "a refused link still travels as words"
    assert runbooks.count("\n") == 2, "an empty link renders nothing"


def test_details_lead_summary_and_actions_render() -> None:
    model = {
        "title": "disk 94%",
        "tone": "high",
        "lead": "🔴 HIGH",
        "summary": "7% free",
        "crumb": "prod · node-3",
        "impact": "one node",
        "details": "host: node-3\nno separator here",
        "actions": [
            {
                "text": "Acknowledge",
                "style": "primary",
                "value": {"hookrelay_action": "tok"},
            },
            {"text": "x", "value": "bad"},
        ],
        "footer": "hookrelay · alertmanager · #7",
    }
    card = render.feishu_card(model)["card"]
    texts = [e["text"]["content"] for e in card["elements"] if e["tag"] == "div"]
    assert texts[0] == "**🔴 HIGH**  7% free" and texts[1] == "prod · node-3" and texts[2] == "**Impact**\none node"
    assert texts[3] == "**host**: node-3\nno separator here"
    buttons = next(e for e in card["elements"] if e["tag"] == "action")["actions"]
    assert buttons[0] == {
        "tag": "button",
        "text": {"tag": "plain_text", "content": "Acknowledge"},
        "type": "primary",
        "value": {"hookrelay_action": "tok"},
    }
    assert buttons[1]["value"] == {}, "a value that is not an object is carried as nothing, not as a crash"
    assert json.dumps(card, ensure_ascii=False).count("lark_md") == 4


def test_actions_become_links_for_a_delivery_that_cannot_call_back() -> None:
    model = {
        "title": "t",
        "actions": [
            {"text": "Silence 1h", "value": {"hookrelay_action": "tok/en"}},
            {"text": "no token", "value": {}},
        ],
    }
    as_links = render.feishu_card(model, actions="links", link_base="https://relay.example")["card"]
    (line,) = [e["text"]["content"] for e in as_links["elements"] if e["tag"] == "div"]
    assert line == "[Silence 1h](https://relay.example/card-action?t=tok%2Fen)", (
        "one link per action that carries a token"
    )
    assert not [e for e in as_links["elements"] if e["tag"] == "action"], "no dead buttons beside the links"
    assert render.feishu_card(model, actions="links")["card"]["elements"] == [], (
        "no base, no links — never a link nobody can reach"
    )
    assert render.feishu_card(model, actions="none")["card"]["elements"] == []


def test_a_link_label_cannot_hijack_the_link() -> None:
    out = render.markdown_link("Runbook](https://evil.example) click", "https://kb.example/ok")
    assert out.endswith("](https://kb.example/ok)") and "Runbook\\](https://evil.example) click" in out
