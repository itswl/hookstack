"""Escaping for the chat dialects this pipe renders into.

Inbound payloads are other people's data, and nearly all of that data ends up
inside markup: a Feishu `lark_md` block, DingTalk's `### markdown`, WeCom's
markdown. Nothing stood between the two, so an alert whose title was

    <at id=all></at> disk usage nominal

paged an entire company from a door that, on an unsigned source, nobody had to
authenticate to reach — and one whose body was `[open the runbook](http://…)`
put a stranger's link in an operator's notification wearing the pipe's own
credibility, which is the only credibility that link needed.

So payload text is escaped where it enters markup and left alone where it does
not, and that boundary is per DIALECT rather than per field: Feishu's card
header is `plain_text`, which renders no markup and would only show operators
our backslashes, while the SAME title inside DingTalk's markdown body must be
escaped. Callers pick the right one at the point of rendering, which is the only
place that knows which of the two it is writing.

A leaf module, imported by both renderers (channels.py and processed.py) and
importing nothing of ours, so neither has to reach through the other.
"""

from __future__ import annotations

# The characters that OPEN markup in all three chat dialects, and only those.
# `<` covers Feishu's `<at id=all>` all-mention along with the `<a href>` and
# `<font color>` tags WeCom renders; `[` covers the markdown link all three
# accept; `\` is escaped FIRST so an escape can never assemble a new opener out
# of a backslash the payload supplied.
#
# Emphasis (`*`, `_`, `~`, backtick) is deliberately left alone. It can make an
# alert ugly; it cannot make it reach somebody or send them anywhere, and an
# escape that fires on every `*` in every stack trace would be its own kind of
# damage to the thing an operator is trying to read.
_OPENERS = ("\\", "<", "[")

# Two schemes get to be links, because a card is a thing people click without
# reading a status bar first. `javascript:` and `data:` reaching a Feishu card
# are how a notification becomes a payload, and `file:` is how it becomes a
# probe of the operator's own machine.
_CLICKABLE_SCHEMES = ("http://", "https://")


def escape_markup(text: str) -> str:
    """Neutralise markup openers in a string that came from a payload."""
    for opener in _OPENERS:
        text = text.replace(opener, "\\" + opener)
    return text


def clickable_url(url: str) -> str | None:
    """The url as something safe to put behind a link, or None if it is not.

    Parentheses are percent-encoded rather than refused: a `)` inside the url
    would close the markdown link early and hand everything after it to the
    renderer as markup, and real runbook links (Wikipedia-style paths) do carry
    them. Whitespace is refused outright — no legitimate href needs it, and it
    is the other way to end a link target early.
    """
    text = str(url or "").strip()
    if not text.lower().startswith(_CLICKABLE_SCHEMES):
        return None
    if any(character.isspace() for character in text):
        return None
    return text.replace("(", "%28").replace(")", "%29")


def markdown_link(text: str, url: str) -> str:
    """`[text](url)` when the url is clickable, both as plain words when not.

    Dropping a refused link would take with it the one piece of information an
    operator needs in order to judge it — an alert that quietly lost its runbook
    looks exactly like an alert that never had one. So the label and the target
    still travel; they just stop being something the card invites a click on.
    """
    label = escape_markup(str(text or "").strip() or str(url or "").strip())
    if not label:
        return ""
    target = clickable_url(url)
    if target is None:
        return f"{label} ({escape_markup(str(url or '').strip())})"
    return f"[{label}]({target})"
