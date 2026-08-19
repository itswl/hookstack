"""Example source adapter: GitHub's signature dialect.

Copy into your plugins/ directory and configure:

    sources:
      - name: gh
        adapter: github
        secret: ${GITHUB_HOOK_SECRET}
        # title/body templates work as usual on the GitHub payload:
        title: "{repository.full_name}: {head_commit.message}"
        body: "pushed by {pusher.name}"
"""

import hashlib
import hmac

from hookrelay import registry
from hookrelay.extract import extract_event
from hookrelay.security import constant_time_eq


@registry.source_adapter("github")
class GitHubAdapter:
    """X-Hub-Signature-256: 'sha256=' + hex HMAC of the raw body."""

    def verify(self, source, body, headers):
        if not source.secret:
            return True
        provided = str(headers.get("x-hub-signature-256", ""))
        if not provided.startswith("sha256="):
            return False
        expected = hmac.new(source.secret.encode(), body, hashlib.sha256).hexdigest()
        # Via security.constant_time_eq, not hmac.compare_digest directly: that
        # one raises TypeError on the non-ASCII str Starlette hands back for a
        # header carrying a high byte, which is an unauthenticated 500.
        return constant_time_eq(expected, provided[len("sha256=") :].lower())

    def parse(self, source, payload):
        return extract_event(source, payload)
