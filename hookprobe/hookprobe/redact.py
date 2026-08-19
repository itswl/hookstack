"""Credential-shaped material, masked before anything durable copies it.

A tool call's one-line detail is the most-copied string in this service. It
lands on the run's event feed and from there in `results/*.json`; the flight
recorder writes it to `audit/*.jsonl`; and hookprobe.distill embeds up to thirty
of them verbatim into the case block of a generated SKILL.md — which every later
run then loads as standing instruction and `/v1/skills` serves to a browser. So
one `curl -H "Authorization: Bearer sk-…"` in a tool call did not merely persist:
it propagated into the investigator's own learned knowledge, where nobody would
ever think to look for a secret.

Masking therefore happens where the detail is CAPTURED (hookprobe.engine's
`_tool_detail`), not at the sinks. Three sinks copy that string today and a
fourth is one feature away; a redaction applied at each of them is a redaction
the next one leaks around.

The marker keeps the shape. `Authorization: Bearer [redacted]` rather than a
deleted header, `postgres://user:[redacted]@db-1` rather than a deleted URL: an
investigator reading its own case file still has to be able to see that a header
WAS sent and which host was reached, and an operator reading the audit log still
has to be able to tell `--password=` from a missing flag.

Two boundaries, stated rather than overstated:

* On a key-name match this errs toward masking, the way guard.py errs toward
  blocking — `max_tokens=100` becomes `max_tokens=[redacted]`, which is a cheap
  loss. It deliberately does NOT do the same for names that are only sometimes a
  secret: bare `key=` is a label selector as often as a credential, and `auth`
  as a fragment rather than a whole word would take `git log --author=` with it.
* It does not guess at entropy. A 40-character hex string is as likely to be a
  git SHA as a key, and a pod is named `api-7d9f8b6c4-x2klm`; masking either
  would cost real evidence to protect nothing. Only vendor-prefixed shapes that
  cannot be anything else are matched bare — so a secret passed as a positional
  argument (`mytool a1b2c3…`) is indistinguishable from an argument and
  survives. That is the hole, and the answer to it is not a longer regex but the
  read-only credentials this runner is given in the first place.
"""

from __future__ import annotations

import re

MARKER = "[redacted]"

# A value as a command line hands one over: quoted, or a bare run of anything
# that is not shell punctuation. Backslash is excluded so a value inside an
# escaped quote — which is how a detail looks once it has been through
# json.dumps — does not swallow the escape along with the secret.
_VALUE = r"""(?:"[^"]*"|'[^']*'|[^\s"';|&\\]+)"""

# What may sit either side of the secret word inside one key name: bounded, and
# never nested inside another quantifier, so this cannot backtrack its way into
# a hang on a long command line.
_NAME = r"[A-Za-z0-9_.\[\]-]{0,48}"

# Fragments that mean "what follows is a credential", matched anywhere in a key
# name — so AWS_SECRET_ACCESS_KEY, --client-secret and ?refresh_token= each cost
# exactly one word here.
# nosec B105 — these are the NAMES a secret hides behind, not a secret. The
# scanner cannot tell a redaction pattern from a hardcoded credential, and
# this module exists precisely to remove the latter.
_SECRET_FRAGMENT = "secret|password|passwd|passphrase|pwd|token|api[_-]?key|access[_-]?key|private[_-]?key|credential"  # nosec B105

# Names that are a credential only as a WHOLE token, guarded by the boundary
# below: `--pass` is a password and `bypass=true` is not, `--auth` is a
# credential and `--author=` is not.
_WHOLE_NAME = "set-cookie|cookie|passphrase|pass|auth|sig"

# One key: optional leading dashes, then either shape. `authorization` is
# deliberately absent — the rule below owns it, and letting this one match it
# too would strip the scheme word back off on a second pass.
_KEY = rf"(?:--?)?(?:{_NAME}(?:{_SECRET_FRAGMENT}){_NAME}|(?:{_WHOLE_NAME})(?![A-Za-z0-9_.-]))"

# A name can only start where a name can start, so `bypass=` is not read as
# `pass=`.
_LEFT = r"(?<![A-Za-z0-9_.-])"

# Authorization on its own, because the scheme word is worth keeping: "a bearer
# token was sent" and "basic auth was sent" are different facts about the
# request, and both are lost if the whole value goes.
_AUTH_HEADER = re.compile(
    rf"(?i){_LEFT}((?:proxy-)?authorization)(\s*[:=]\s*)"
    rf"((?:bearer|basic|token|digest|apikey|negotiate)\s+)?{_VALUE}"
)

# scheme://user:pass@host — the password half only. The user and the host are
# what say WHICH system was reached, and an investigation needs that.
_URL_USERINFO = re.compile(r"(?<=://)([^\s:/@\"']{0,64}:)[^\s/@\"']{1,256}(?=@)")

# key=value and key: value in one rule: `?api_key=…` and `X-Api-Key: …` are the
# same leak wearing different punctuation.
_ASSIGNED = re.compile(rf"(?i){_LEFT}({_KEY}\s*[:=]\s*){_VALUE}")

# The same, spelled as a flag with a space. The dashes are required (bare
# `token foo` in prose is not an assignment) and the value must not itself look
# like a flag, or `--token --verbose` would mask the following argument.
_FLAGGED = re.compile(rf"(?i){_LEFT}(--?(?:{_NAME}(?:{_SECRET_FRAGMENT}){_NAME}|(?:{_WHOLE_NAME}))\s+)(?!-){_VALUE}")

# `mysql -pS3cret` and `redis-cli -a S3cret`. A single-letter flag is only a
# password when the client says so: `-p` is `mkdir -p` and `docker run -p` far
# more often than it is a secret, and masking those would cost evidence for
# nothing. The span between binary and flag is bounded for the reason _NAME is.
_MYSQL_PASSWORD = re.compile(r"(?i)(\b(?:mysql|mysqldump|mysqladmin|mariadb)\w*\b[^|;&\n]{0,200}?\s-p)[^\s|;&]+")
_REDIS_AUTH = re.compile(r"(?i)(\bredis-cli\b[^|;&\n]{0,200}?\s(?:-a|--pass)\s+)[^\s|;&]+")

# Vendor-prefixed keys, self-identifying and so safe to match bare. The prefix
# survives the mask: "an Anthropic key leaked here" and "a GitHub token leaked
# here" are different incidents needing different rotations.
_PREFIXED: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![A-Za-z0-9_-])(sk-ant-|sk-)[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?<![A-Za-z0-9_-])(gh[pousr]_)[A-Za-z0-9]{16,}"),
    re.compile(r"(?<![A-Za-z0-9_-])(github_pat_)[A-Za-z0-9_]{20,}"),
    re.compile(r"(?<![A-Za-z0-9_-])(xox[abprse]-)[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9_-])(AIza)[A-Za-z0-9_-]{30,}"),
    re.compile(r"(?<![A-Za-z0-9_-])(eyJ)[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),
    re.compile(r"(?<![A-Za-z0-9])(AKIA|ASIA|AROA|AIDA|AGPA|ANPA|ANVA)[0-9A-Z]{16}(?![0-9A-Z])"),
)

# Every rule keeps its leading groups and drops the last thing it matched, which
# is always the value. \g<3> is optional in the header rule, so it gets its own
# template rather than a shared one.
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_AUTH_HEADER, rf"\g<1>\g<2>\g<3>{MARKER}"),
    (_URL_USERINFO, rf"\g<1>{MARKER}"),
    (_ASSIGNED, rf"\g<1>{MARKER}"),
    (_FLAGGED, rf"\g<1>{MARKER}"),
    (_MYSQL_PASSWORD, rf"\g<1>{MARKER}"),
    (_REDIS_AUTH, rf"\g<1>{MARKER}"),
    *((pattern, rf"\g<1>{MARKER}") for pattern in _PREFIXED),
)


def redact(text: str) -> str:
    """The same text with credential-shaped material replaced by the marker.

    Idempotent, because the callers are not all one call apart: a detail is
    masked at capture and is then re-read, re-serialised and merged into a
    manifest, and none of those steps may change the text a second time.
    """
    if not text:
        return text
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text
