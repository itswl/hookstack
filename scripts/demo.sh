#!/usr/bin/env bash
# The ten-minute tour, against the hermetic family stack:
#
#   docker compose up -d --build     # pipe + judge + stub model + sink
#   bash scripts/demo.sh
#
# No keys, no .env, no bill: the stub model answers the paid route, the sink
# prints what an operator would have received (`docker compose logs -f sink`),
# and the boards are at http://127.0.0.1:8100 (pipe) and :8200 (judge).
#
# Four alerts, chosen so every judgement route fires at least once:
#   1. a fresh alert        -> ai        (the stub is called, tokens are billed)
#   2. the same alert again -> reuse     (same identity in the window: no call)
#   3. its recovery         -> recovery  (reuses the FIRING's verdict — a
#                                         recovery is not a new problem)
#   4. a different alert    -> ai        (new identity, new judgement)
set -euo pipefail

base="${HOOKRELAY_URL:-http://127.0.0.1:8100}"

say() { printf '\n\033[1;34m── %s\033[0m\n' "$1"; }
post() { curl -sf "$base/hook/inbound" -H "content-type: application/json" -d "$1" | python3 -m json.tool; }

say "health"
curl -sf "$base/healthz" >/dev/null && echo "pipe is up"

say "1. fresh alert → the ai route (stub model, visible tokens, zero cost)"
post '{"title":"Payment gateway 5xx rate 8.1%","message":"gateway-2 5xx at 8.1% over the last 5 minutes","state":"alerting","env":"prod"}'

say "2. the same alert again → reuse (same identity inside the window; no model call)"
sleep 1
post '{"title":"Payment gateway 5xx rate 8.1%","message":"gateway-2 5xx at 8.1% over the last 5 minutes","state":"alerting","env":"prod"}'

say "3. its recovery → the recovery route (reuses the firing's verdict)"
sleep 1
post '{"title":"[RESOLVED] Payment gateway 5xx rate 8.1%","message":"back under 0.2%","state":"ok","env":"prod"}'

say "4. a different alert → a new identity, a new judgement"
post '{"title":"Disk /data at 92% on db-1","message":"3.4G left, growing 400M/h","state":"alerting","env":"prod"}'

say "the judge's ledger — one line per verdict, route and cost included"
sleep 3
curl -sf http://127.0.0.1:8200/status | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('routes:', {k: v['count'] for k, v in d['summary']['routes'].items()})
print('paid ratio:', d['summary'].get('paid_ratio_pct'), '%')
for row in reversed(d.get('recent', [])):
    print(f\"  #{row['id']} {row.get('route', '?'):<9} {row.get('importance', '?'):<8} {row['title'][:48]}\")
"

printf '\nWhat the operator would have received:  docker compose logs -f sink\n'
printf 'The boards:  http://127.0.0.1:8100  (pipe)   http://127.0.0.1:8200  (judge)\n'
printf 'The investigator (needs a model key):  docker compose --profile probe up -d --build\n'
