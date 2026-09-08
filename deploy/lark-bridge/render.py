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
        rendered.append(
            f"**{escape_markup(name)}**: {escape_markup(value)}"
            if sep
            else escape_markup(line)
        )
    return "\n".join(rendered)


def feishu_card(model: dict[str, Any]) -> dict[str, Any]:
    """One card model → one Feishu interactive message body."""
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
    lines = "\n".join(
        r
        for r in (
            markdown_link(str(x.get("text") or ""), str(x.get("url") or ""))
            for x in links
        )
        if r
    )
    if lines:
        elements.append(_md(f"**Runbooks**\n{lines}"))
    actions = [a for a in (model.get("actions") or []) if isinstance(a, dict)]
    if actions:
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
                        "value": a.get("value")
                        if isinstance(a.get("value"), dict)
                        else {},
                    }
                    for a in actions
                ],
            }
        )
    footer = str(model.get("footer") or "")
    if footer:
        elements.append(
            {"tag": "note", "elements": [{"tag": "plain_text", "content": footer}]}
        )
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": str(model.get("title") or "Alert"),
                },
                "template": TONE_COLOR.get(
                    str(model.get("tone") or "").lower(), FALLBACK_COLOR
                ),
            },
            "elements": elements,
        },
    }
