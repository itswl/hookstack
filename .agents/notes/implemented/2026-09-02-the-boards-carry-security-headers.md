---
title: The three boards carry CSP and framing/sniff/referrer headers
status: implemented
date: 2026-09-02
scope: stack
---

## Decision

The relay/judge/probe board site blocks (live host Caddyfile, mirrored in
`deploy/caddy/hookstack.caddy`) now set on every response:

- `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline';
  style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';
  frame-ancestors 'none'; base-uri 'none'; form-action 'self'`
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`

## Why

Each board keeps a bearer token in `localStorage`, so an XSS is a token-theft
risk. The pages are single-file with inline `<script>`/`<style>` and NO external
assets (the design language guarantees this), so `'self' + 'unsafe-inline'` does
not break them while the policy still buys real protection:

- `connect-src 'self'` blocks exfiltration of a stolen token to an external host
  (the practical payoff — an injected script cannot phone home).
- `frame-ancestors 'none'` / `X-Frame-Options: DENY` block clickjacking.
- `default-src 'self'` blocks loading external script/resources at all.

`'unsafe-inline'` is a stated limit: it does NOT stop an inline event handler,
which is why the console ALSO escapes link hrefs at the source
([[the-agent-subprocess-does-not-inherit-the-services-secrets]] shipped that in
the same review). CSP here is defence in depth, not the primary control.

Verified after reload: all three hosts return the headers through Cloudflare and
still serve their full HTML.

## Consequences

- If a board ever needs an external asset or a cross-origin fetch, the policy
  must be widened deliberately (that is the point — it fails closed).
- A stricter `script-src` with per-file hashes was rejected: the inline script
  changes on every UI edit, so a hash pin would break the board on each change
  and rot. `'unsafe-inline'` + the source-level escaping is the maintainable
  posture for self-contained pages.
- The live Caddyfile is not version-controlled (it serves unrelated sites too);
  `deploy/caddy/hookstack.caddy` is the record and now matches.
