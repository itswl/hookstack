"""Example source adapter: AWS SNS, whose real payload hides inside a string.

SNS wraps every notification in an envelope whose `Message` field is a JSON
*string* — extraction templates cannot reach into a string, so this adapter
unwraps it first and hands the INNER object to the usual templates. It also
answers the SubscriptionConfirmation handshake (a GET to SubscribeURL, pinned
to SNS hosts) and verifies SNS's certificate signatures when `cryptography`
is installed.

Copy into your plugins/ directory and configure:

    sources:
      - name: cloudwatch
        adapter: aws-sns
        secret: ""                       # SNS signs with PKI, not a shared key
        # Templates address the UNWRAPPED CloudWatch alarm:
        title: "{AlarmName}"
        body: "{NewStateReason}"
        level: "{NewStateValue}"
        level_map: {ALARM: high, OK: info, INSUFFICIENT_DATA: info}

Signature posture, honestly: with `cryptography` importable the adapter
verifies SigningCertURL (https, SNS host only) and the SignatureVersion 1/2
string-to-sign, and refuses anything that fails. Without it, it logs one
warning and trusts the transport — acceptable behind private ingress, not on
the open internet. Install `cryptography` for production exposure.
"""

import base64
import json
import logging
import re
import urllib.request

from fastapi import HTTPException

from hookrelay import registry
from hookrelay.extract import extract_event

logger = logging.getLogger("hookrelay.plugins.aws_sns")

# sns.<region>.amazonaws.com (+ .cn partitions). Everything else is refused —
# SubscribeURL and SigningCertURL are attacker-suppliable, so following them
# anywhere else is an SSRF invitation.
_SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com(\.cn)?$")

# String-to-sign field order is fixed by the SNS spec, per message type.
_SIGNED_FIELDS = {
    "Notification": ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"),
    "SubscriptionConfirmation": (
        "Message",
        "MessageId",
        "SubscribeURL",
        "Timestamp",
        "Token",
        "TopicArn",
        "Type",
    ),
    "UnsubscribeConfirmation": (
        "Message",
        "MessageId",
        "SubscribeURL",
        "Timestamp",
        "Token",
        "TopicArn",
        "Type",
    ),
}

_CERT_CACHE: dict[str, bytes] = {}
_WARNED_UNVERIFIED = False


def _url_is_sns(url: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(str(url or ""))
    return parsed.scheme == "https" and bool(_SNS_HOST.match(parsed.hostname or ""))


def _fetch(url: str) -> bytes:
    if url not in _CERT_CACHE:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 — host pinned above  # nosec B310
            _CERT_CACHE[url] = response.read()
    return _CERT_CACHE[url]


def _verify_pki(payload: dict) -> bool:
    """SignatureVersion 1 (SHA1) / 2 (SHA256) over the spec's field list."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.x509 import load_pem_x509_certificate
    except ImportError:
        global _WARNED_UNVERIFIED
        if not _WARNED_UNVERIFIED:
            _WARNED_UNVERIFIED = True
            logger.warning(
                "aws-sns: cryptography is not installed — accepting UNVERIFIED SNS messages. "
                "Fine behind private ingress; install cryptography before exposing this door."
            )
        return True

    cert_url = str(payload.get("SigningCertURL") or "")
    if not _url_is_sns(cert_url):
        return False
    fields = _SIGNED_FIELDS.get(str(payload.get("Type") or ""))
    if not fields:
        return False
    string_to_sign = "".join(f"{name}\n{payload[name]}\n" for name in fields if payload.get(name) is not None)
    digest = hashes.SHA256() if str(payload.get("SignatureVersion")) == "2" else hashes.SHA1()  # noqa: S303  # nosec B303
    try:
        certificate = load_pem_x509_certificate(_fetch(cert_url))
        certificate.public_key().verify(
            base64.b64decode(payload.get("Signature") or ""),
            string_to_sign.encode(),
            padding.PKCS1v15(),
            digest,
        )
        return True
    except (InvalidSignature, ValueError, OSError):
        return False


@registry.source_adapter("aws-sns")
class SnsAdapter:
    """The envelope dialect: verify PKI, answer handshakes, unwrap Message."""

    def verify(self, source, body, headers):
        # SNS has no shared-secret dialect; authenticity is the PKI signature
        # on the payload itself, checked in parse() where the JSON exists.
        return True

    def parse(self, source, payload):
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="sns payload is not an object")
        if not _verify_pki(payload):
            raise HTTPException(status_code=401, detail="sns signature verification failed")

        kind = str(payload.get("Type") or "")
        if kind == "SubscriptionConfirmation":
            subscribe_url = str(payload.get("SubscribeURL") or "")
            if not _url_is_sns(subscribe_url):
                raise HTTPException(status_code=400, detail="SubscribeURL is not an SNS endpoint")
            _fetch(subscribe_url)
            logger.info("aws-sns: confirmed subscription for %s", payload.get("TopicArn"))
            # 2xx stops SNS retrying; no event enters the pipeline.
            raise HTTPException(status_code=202, detail="sns subscription confirmed")
        if kind == "UnsubscribeConfirmation":
            # Deliberately NOT re-subscribing: an unsubscribe someone chose
            # should not be silently undone by the pipe.
            raise HTTPException(status_code=202, detail="sns unsubscribe acknowledged")

        message = payload.get("Message")
        if isinstance(message, str):
            try:
                inner = json.loads(message)
            except ValueError:
                inner = {"message": message}
        else:
            inner = message
        if not isinstance(inner, dict):
            inner = {"message": inner}
        # The envelope's own facts stay reachable for templates and fields.
        inner.setdefault("_sns", {})
        inner["_sns"] = {
            "topic": payload.get("TopicArn"),
            "subject": payload.get("Subject"),
            "timestamp": payload.get("Timestamp"),
        }
        return extract_event(source, inner)
