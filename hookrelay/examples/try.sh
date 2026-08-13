#!/usr/bin/env bash
# Play with a running local hookrelay (docker compose up -d).
# Reads secrets from .env; every command prints what the router decided.
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

base=http://127.0.0.1:8100

echo "── health"
curl -s "$base/healthz"; echo; echo

echo "── 1. unsigned test event (routed → sink)"
curl -s "$base/hook/test" -H "content-type: application/json" \
  -d '{"title":"try.sh event","message":"hello","level":"high"}' | python3 -m json.tool
echo

echo "── 2. signed grafana event"
BODY='{"title":"Disk about to fill","message":"/data 92%","state":"alerting"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$GRAFANA_HOOK_SECRET" | awk '{print $2}')
curl -s "$base/hook/grafana" -H "content-type: application/json" -H "X-Hook-Signature: $SIG" -d "$BODY" | python3 -m json.tool
echo

echo "── 3. replay the same body → duplicate (points at the original event)"
curl -s "$base/hook/grafana" -H "content-type: application/json" -H "X-Hook-Signature: $SIG" -d "$BODY" | python3 -m json.tool
echo

echo "── 4. silence grafana for 5 minutes, send again → silenced"
curl -s "$base/silences" -H "X-Admin-Token: $HOOKRELAY_ADMIN_TOKEN" \
  -H "content-type: application/json" -d '{"source":"grafana","minutes":5,"note":"try.sh demo"}'
echo
BODY2='{"title":"Another alert","message":"x","state":"alerting"}'
SIG2=$(printf '%s' "$BODY2" | openssl dgst -sha256 -hmac "$GRAFANA_HOOK_SECRET" | awk '{print $2}')
curl -s "$base/hook/grafana" -H "content-type: application/json" -H "X-Hook-Signature: $SIG2" -d "$BODY2" | python3 -m json.tool
echo

echo "── 5. the ledger"
curl -s "$base/status" | python3 -m json.tool | head -40
echo
echo "deliveries land in:  docker compose logs -f sink"
echo "lift the silence:    curl -X DELETE $base/silences/<id> -H 'X-Admin-Token: \$HOOKRELAY_ADMIN_TOKEN'"
