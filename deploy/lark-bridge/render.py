"""Feishu rendering for the chat-bridge protocol's card model.

The pipe sends five blocks as plain facts (docs/bridge-protocol.md): a title,
a tone, a lead and summary, an identity crumb, an impact, links, actions and a
footer. This module is where they become a Feishu interactive card — the card
schema, its colour names and its `lark_md` dialect live HERE, in the sidecar
that talks to Feishu, and nowhere in the pipe.

Escaping happens here for the same reason. Every text field arrived unescaped,
because only the renderer knows which of its slots render markup: `lark_md`
blocks do, so payload text going into them is neutralised; the header title and
the footer note are `plain_text`, which renders no markup and would only show
operators our backslashes. An alert titled `<at id=all></at> disk nominal` once
paged a whole company through a renderer that did not know this.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

# Header colour by tone. Red is reserved for high and critical — alarm colours
# are earned, not decorative — and a recovery is green whatever it was before,
# because a "resolved" card wearing a red header contradicts its own text.
TONE_COLOR = {
    "critical": "red",
    "high": "red",
    "warning": "orange",
    "medium": "orange",
    "low": "wathet",
    "info": "blue",
    "recovery": "green",
}
# A tone the vocabulary does not name: readable, and visibly not an alarm.
FALLBACK_COLOR = "turquoise"

_OPENERS = ("\\", "<", "[", "]")
_CLICKABLE_SCHEMES = ("http://", "https://")


def escape_markup(text: str) -> str:
    """Neutralise `lark_md` openers in a string that came from a payload."""
    for opener in _OPENERS:
        text = text.replace(opener, "\\" + opener)
    return text


def clickable_url(url: str) -> str | None:
    """The url as something safe to put behind a link, or None if it is not.

    Parentheses are percent-encoded rather than refused: a `)` inside the url
    would close the markdown link early. Whitespace is refused outright — no
    legitimate href needs it, and it is the other way to end a link target.
    """
    text = str(url or "").strip()
    if not text.lower().startswith(_CLICKABLE_SCHEMES):
        return None
    if any(character.isspace() for character in text):
        return None
    return text.replace("(", "%28").replace(")", "%29")


def markdown_link(text: str, url: str) -> str:
    """`[text](url)` when the url is clickable, both as plain words when not.

    A refused link still travels as words: an alert that quietly lost its
    runbook looks exactly like an alert that never had one.
    """
    label = escape_markup(str(text or "").strip() or str(url or "").strip())
    if not label:
        return ""
    target = clickable_url(url)
    if target is None:
        return f"{label} ({escape_markup(str(url or '').strip())})"
    return f"[{label}]({target})"


def _md(content: str) -> dict[str, Any]:
    return {"tag": "div", "text": {"tag": "lark_md", "content": content}}


def _details(text: str) -> str:
    """`name: value` lines, the name bolded — the pipe's own fields block."""
    rendered = []
    for line in str(text).splitlines():
        name, sep, value = line.partition(": ")
        rendered.append(f"**{escape_markup(name)}**: {escape_markup(value)}" if sep else escape_markup(line))
    return "\n".join(rendered)


def _action_links(actions: list[dict[str, Any]], link_base: str) -> str:
    """Actions as markdown links to the pipe's confirm page — for a delivery that
    cannot call back (a custom-bot webhook). The GET only asks; the POST behind
    it acts, because chat clients fetch links to build previews. No base, no
    links: a link nobody can reach is worse than none."""
    if not link_base:
        return ""
    rendered = []
    for action in actions:
        value = action.get("value")
        token = str(value.get("hookrelay_action") or "") if isinstance(value, dict) else ""
        if token:
            rendered.append(
                markdown_link(
                    str(action.get("text") or "Action"),
                    f"{link_base}/card-action?t={quote(token, safe='')}",
                )
            )
    return " · ".join(r for r in rendered if r)


def feishu_card(model: dict[str, Any], *, actions: str = "buttons", link_base: str = "") -> dict[str, Any]:
    """One card model → one Feishu interactive message body.

    `actions="buttons"` when this bridge sends as an application, whose button
    presses come back as callbacks; `actions="links"` when it sends through a
    custom-bot webhook, which cannot call back, so each action becomes a link
    to `link_base` (the pipe's public address, `action_link_base` in the
    envelope). Anything else drops the actions rather than draw dead buttons.
    """
    elements: list[dict[str, Any]] = []
    lead = escape_markup(str(model.get("lead") or ""))
    summary = escape_markup(str(model.get("summary") or ""))
    if lead and summary:
        elements.append(_md(f"**{lead}**  {summary}"))
    elif lead or summary:
        elements.append(_md(f"**{lead}**" if lead else summary))
    crumb = str(model.get("crumb") or "")
    if crumb:
        elements.append(_md(escape_markup(crumb)))
    impact = str(model.get("impact") or "")
    if impact:
        elements.append(_md(f"**Impact**\n{escape_markup(impact)}"))
    details = str(model.get("details") or "")
    if details:
        elements.append(_md(_details(details)))
    links = [x for x in (model.get("links") or []) if isinstance(x, dict)]
    lines = "\n".join(r for r in (markdown_link(str(x.get("text") or ""), str(x.get("url") or "")) for x in links) if r)
    if lines:
        elements.append(_md(f"**Runbooks**\n{lines}"))
    declared = [a for a in (model.get("actions") or []) if isinstance(a, dict)]
    if declared and actions == "links":
        links_line = _action_links(declared, link_base)
        if links_line:
            elements.append(_md(links_line))
    elif declared and actions == "buttons":
        # Values are opaque and already signed by whoever minted them; the
        # bridge carries them into the button and back out on a press.
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {
                            "tag": "plain_text",
                            "content": str(a.get("text") or "Action"),
                        },
                        "type": str(a.get("style") or "default"),
                        "value": a.get("value") if isinstance(a.get("value"), dict) else {},
                    }
                    for a in declared
                ],
            }
        )
    footer = str(model.get("footer") or "")
    if footer:
        elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": footer}]})
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": str(model.get("title") or "Alert"),
                },
                "template": TONE_COLOR.get(str(model.get("tone") or "").lower(), FALLBACK_COLOR),
            },
            "elements": elements,
        },
    }
