"""DingTalk and WeCom as plugins: two markdown dialects rendered from the pipe's card model.

These two used to be built into the pipe. They are custom-bot webhooks — a
finished message in, nothing back, no callbacks, no threads — and the pipe's
own outbound kinds are now exactly two: `generic` for machines and `bridge`
for people (docs/bridge-protocol.md). A markdown webhook is a dialect, and a
dialect is a plugin: one file, loaded from HOOKRELAY_PLUGINS, registering a
channel type the same way the built-ins do. Delete this file and the pipe
knows one platform fewer; copy it and rename two strings for the next one.

Both render the same card model a bridge receives (`card_model_for`), so a
judgement reads the same in every chat. Actions become LINKS to the pipe's
confirm page when the channel says where that is (`options.action_link_base`,
else HOOKRELAY_PUBLIC_URL): a webhook robot cannot call back, so a button here
would do nothing, which is worse than none — and without a link these channels
could never take part in the feedback the rest of the stack depends on.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Any
from urllib.parse import quote, quote_plus

from hookrelay import registry
from hookrelay.channels import card_model_for
from hookrelay.config import Channel

# Escaping lives with the renderer: payload text is other people's data, and
# an alert titled `<at id=all></at> disk nominal` once paged a whole company
# through a renderer that did not know which of its slots rendered markup.
_OPENERS = ("\\", "<", "[", "]")
_CLICKABLE = ("http://", "https://")


def escape_markup(text: str) -> str:
    for opener in _OPENERS:
        text = text.replace(opener, "\\" + opener)
    return text


def clickable_url(url: str) -> str | None:
    text = str(url or "").strip()
    if not text.lower().startswith(_CLICKABLE) or any(c.isspace() for c in text):
        return None
    return text.replace("(", "%28").replace(")", "%29")


def markdown_link(text: str, url: str) -> str:
    """`[text](url)` when the url is clickable; both as plain words when not, so
    an alert that lost its runbook does not look like one that never had one."""
    label = escape_markup(str(text or "").strip() or str(url or "").strip())
    if not label:
        return ""
    target = clickable_url(url)
    return f"[{label}]({target})" if target else f"{label} ({escape_markup(str(url or '').strip())})"


def _action_links(actions: list[dict[str, Any]], base: str) -> str:
    if not base:
        return ""
    out = []
    for action in actions:
        value = action.get("value")
        token = str(value.get("hookrelay_action") or "") if isinstance(value, dict) else ""
        if token:
            out.append(
                markdown_link(str(action.get("text") or "Action"), f"{base}/card-action?t={quote(token, safe='')}")
            )
    return " · ".join(x for x in out if x)


def render_markdown(model: dict[str, Any], *, heading: bool, link_base: str = "") -> str:
    """The model as markdown: DingTalk wants a `### heading`, WeCom renders bold."""
    headline = escape_markup(str(model.get("title") or "Alert"))
    lines = [f"### {headline}" if heading else f"**{headline}**"]
    lead = " ".join(escape_markup(str(model.get(k) or "")) for k in ("lead", "summary") if model.get(k))
    if lead:
        lines.append(lead)
    if model.get("crumb"):
        lines.append(escape_markup(str(model["crumb"])))
    if model.get("impact"):
        lines.append(f"**Impact**: {escape_markup(str(model['impact']))}")
    for line in str(model.get("details") or "").splitlines():
        name, sep, value = line.partition(": ")
        lines.append(f"**{escape_markup(name)}**: {escape_markup(value)}" if sep else escape_markup(line))
    for link in model.get("links") or []:
        if isinstance(link, dict):
            rendered = markdown_link(str(link.get("text") or ""), str(link.get("url") or ""))
            if rendered:
                lines.append(rendered)
    actions = _action_links([a for a in (model.get("actions") or []) if isinstance(a, dict)], link_base)
    if actions:
        lines.append(actions)
    if model.get("footer"):
        lines.append(f"> {escape_markup(str(model['footer']))}")
    return "\n\n".join(lines)


def _link_base(channel: Channel) -> str:
    return (
        str(channel.options.get("action_link_base") or os.environ.get("HOOKRELAY_PUBLIC_URL", "")).strip().rstrip("/")
    )


@registry.channel("dingtalk", capabilities=("links",))
def build_dingtalk(channel: Channel, message: dict[str, Any], now: float) -> tuple[str, dict[str, Any], dict[str, str]]:
    """DingTalk custom robot: markdown, signed in the query string when the robot
    has a secret — timestamp in ms, sign = base64(HMAC-SHA256(secret, "{ts}\\n{secret}"))."""
    model = card_model_for(channel, message)
    url = channel.url
    if channel.secret:
        ts = str(int(now * 1000))
        digest = hmac.new(channel.secret.encode(), f"{ts}\n{channel.secret}".encode(), hashlib.sha256).digest()
        sign = quote_plus(base64.b64encode(digest).decode())
        url = f"{channel.url}{'&' if '?' in channel.url else '?'}timestamp={ts}&sign={sign}"
    text = render_markdown(model, heading=True, link_base=_link_base(channel))
    return url, {"msgtype": "markdown", "markdown": {"title": str(model.get("title") or "Alert"), "text": text}}, {}


@registry.channel("wecom", capabilities=("links",))
def build_wecom(channel: Channel, message: dict[str, Any], now: float) -> tuple[str, dict[str, Any], dict[str, str]]:
    """WeCom group robot: markdown; these robots have no signing."""
    model = card_model_for(channel, message)
    content = render_markdown(model, heading=False, link_base=_link_base(channel))
    return channel.url, {"msgtype": "markdown", "markdown": {"content": content}}, {}
