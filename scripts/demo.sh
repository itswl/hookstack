#!/usr/bin/env bash
# The ten-minute tour, against the hermetic family stack. Either way of
# starting it works — this script only needs the ports, and reads the sink's
# log through docker when it can:
#
#   curl -fsSLO https://raw.githubusercontent.com/itswl/hookstack/main/docker-compose.quickstart.yml
#   docker compose -f docker-compose.quickstart.yml up -d   # published images
#
#   docker compose up -d --build                            # or from source
#
#   bash scripts/demo.sh   (or: bash <(curl -fsSL .../scripts/demo.sh))
#
# No keys, no .env, no bill: the stub model answers the judge's paid route, the
# investigator replays a recorded investigation through its real read-only gate
# (the `replay` rehearsal — every number in its report was written in advance,
# and its last section says so), and the sink prints what an operator would have
# received (`docker compose logs -f sink`). Boards: http://127.0.0.1:8100 (pipe),
# :8200 (judge), :8088/ui (investigator).
#
# Once per stack: a second run inside thirty minutes re-fires the same alerts,
# which the investigator folds into the earlier investigation (a re-fire is a
# follow-up turn, not a new run) and the cooldown withholds the approve button
# from a target another procedure just acted on. `docker compose down -v`
# between runs, or read the earlier run's pages instead.
#
# Four alerts, chosen so every judgement route fires at least once, and then the
# loop the fourth one starts: a report, a card, an approve press through the
# pipe's own door, two allowlisted observations executed, the condition ending,
# and the pipe's audit record of the whole chain.
#   1. a fresh alert        -> ai        (the stub is called, tokens are billed)
#   2. the same alert again -> reuse     (same identity in the window: no call)
#   3. its recovery         -> recovery  (reuses the FIRING's verdict — a
#                                         recovery is not a new problem)
#   4. a different alert    -> ai        (new identity, new judgement — and,
#                                         being high, a copy to the investigator)
set -euo pipefail

base="${HOOKRELAY_URL:-http://127.0.0.1:8100}"
judge="${HOOKJUDGE_URL:-http://127.0.0.1:8200}"
probe="${HOOKPROBE_URL:-http://127.0.0.1:8088}"
ptoken="${HOOKPROBE_TOKEN:-quickstart-token}"

say() { printf '\n\033[1;34m── %s\033[0m\n' "$1"; }
post() { curl -sf "$base/hook/inbound" -H "content-type: application/json" -d "$1" | python3 -m json.tool; }
pcurl() { curl -sf -H "Authorization: Bearer $ptoken" "$@"; }
# Both compose files name the project `hookstack`, so the sink's log is readable
# without knowing which file started the stack. With no docker on this machine
# the press below falls back to the console's own door.
sink_log() { docker compose -p hookstack logs --no-log-prefix sink 2>/dev/null || docker logs hs-sink 2>/dev/null || true; }
field() { python3 -c 'import sys, json
try: print(json.load(sys.stdin).get(sys.argv[1], "") or "")
except Exception: print("")' "$1"; }

say "health"
curl -sf "$base/healthz" >/dev/null && echo "pipe is up"
curl -sf "$probe/healthz" >/dev/null && echo "investigator is up" || echo "investigator is not answering on $probe — the loop steps below will wait for it"

say "1. fresh alert → the ai route (stub model, visible tokens, zero cost)"
post '{"title":"Payment gateway 5xx rate 8.1%","message":"gateway-2 5xx at 8.1% over the last 5 minutes","state":"alerting","env":"prod"}'

say "2. the same alert again → reuse (same identity inside the window; no model call)"
sleep 1
post '{"title":"Payment gateway 5xx rate 8.1%","message":"gateway-2 5xx at 8.1% over the last 5 minutes","state":"alerting","env":"prod"}'

say "3. its recovery → the recovery route (reuses the firing's verdict)"
sleep 1
post '{"title":"[RESOLVED] Payment gateway 5xx rate 8.1%","message":"back under 0.2%","state":"ok","env":"prod"}'

say "4. a different alert → a new identity, a new judgement — and a copy to the investigator"
# Everything the sink has printed so far is somebody else's card; the approve
# link searched for below has to be THIS alert's, so remember where the log was.
sink_before="$(sink_log | wc -l | tr -d ' ')"
disk="$(curl -sf "$base/hook/inbound" -H "content-type: application/json" \
  -d '{"title":"Disk /data at 92% on db-1","message":"3.4G left, growing 400M/h","state":"alerting","env":"prod"}')"
printf '%s' "$disk" | python3 -m json.tool
disk_id="$(printf '%s' "$disk" | field event_id)"
session="probe:inbound:${disk_id}"

say "the judge's ledger — one line per verdict, route and cost included"
sleep 3
curl -sf "$judge/status" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('routes:', {k: v['count'] for k, v in d['summary']['routes'].items()})
print('paid ratio:', d['summary'].get('paid_ratio_pct'), '%')
for row in reversed(d.get('recent', [])):
    print(f\"  #{row['id']} {row.get('route', '?'):<9} {row.get('importance', '?'):<8} {row['title'][:48]}\")
"

say "5. the investigator: a recorded investigation, replayed through the real read-only gate"
status=""
for _ in $(seq 1 90); do
  run="$(pcurl "$probe/v1/runs/$session" 2>/dev/null || true)"
  status="$(printf '%s' "$run" | field status)"
  case "$status" in completed|failed) break ;; esac
  sleep 1
done
if [ "$status" != "completed" ]; then
  echo "the investigator did not finish (status: ${status:-none}). Is it up? docker compose ps"; exit 1
fi
RUN_JSON="$run" PROBE_URL="$probe" python3 - <<'PY'
import json, os
r = json.loads(os.environ["RUN_JSON"])
print(f"status {r.get('status')} · model {r.get('model')} · cost ${float(r.get('cost_usd') or 0):.2f} · session {r.get('engine_session_id')}")
print("tool calls, each judged by the read-only gate before it was announced:")
for e in r.get("events", []):
    if e.get("type") == "tool_use":
        verdict = "REFUSED" if e.get("error") else "allowed"
        print(f"  {verdict}  {str(e.get('name')):<5} {str(e.get('detail'))[:88]}")
text = str(r.get("text") or "")
print("\nthe report opens with the one sentence the card quotes:\n  " + text.split("\n\n")[0][:400])
print(f"\nthe full report and the audit of every call: {os.environ['PROBE_URL']}/ui")
PY

say "6. the proposal it parked: two observations, waiting for a person"
proposals="$(pcurl "$probe/v1/remediations")"
pid="$(PROPOSALS="$proposals" SESSION="$session" python3 - <<'PY'
import json, os
rows = [r for r in json.loads(os.environ["PROPOSALS"])["proposals"] if r.get("session_key") == os.environ["SESSION"]]
print(rows[0]["id"] if rows else "")
PY
)"
if [ -z "$pid" ]; then echo "no proposal was parked for $session"; exit 1; fi
PROPOSALS="$proposals" PID="$pid" python3 - <<'PY'
import json, os
row = next(r for r in json.loads(os.environ["PROPOSALS"])["proposals"] if r["id"] == os.environ["PID"])
print(f"proposal {row['id']} · status {row['status']}")
for s in row["steps"]:
    print(f"  [{s.get('risk')}] {s['command']:<22} — {s['action']}")
PY

say "7. the card, and the press — through the pipe's own door, the way a person's button arrives"
link=""
for _ in $(seq 1 30); do
  found="$(sink_log | tail -n +"$((sink_before + 1))" \
    | { grep -oE '\[Approve[^]]*\]\((http[^)]*card-action\?t=[^)]+)\)' || true; } | tail -1)"
  if [ -n "$found" ]; then
    link="$(printf '%s' "$found" | sed -E 's/^.*\((http[^)]*)\)$/\1/')"
    break
  fi
  sleep 1
done
approved=0
if [ -n "$link" ]; then
  echo "the card in the sink carries the button as a link: ${link:0:60}…"
  curl -sf -X POST "$link" -H 'content-type: application/json' \
    -d '{"actor":"the demo, pressing as a person would"}' | python3 -m json.tool && approved=1
else
  echo "no approve button reached the sink for this card. Either the sink's log is not readable from here, or the"
  echo "button was withheld on purpose: a target another procedure acted on in the last 15 minutes gets no button"
  echo "(the remediation cooldown — run this demo twice in a row and you will see it)."
  echo "Pressing the console's own door instead; it makes the same checks and says why when it refuses:"
  code="$(curl -s -o /tmp/hookstack-demo-answer -w '%{http_code}' -X POST "$probe/v1/remediations/$pid/approve" \
    -H "Authorization: Bearer $ptoken" -H 'content-type: application/json' -d '{"note":"approved from the console by the demo"}')"
  python3 -m json.tool < /tmp/hookstack-demo-answer || cat /tmp/hookstack-demo-answer
  [ "$code" = "200" ] && approved=1
  [ "$approved" = "1" ] && echo "(a console press carries no person: the pipe's ledger will show nobody behind it)"
fi

say "8. the allowlist, then the execution — as argv, never through a shell, every exit code recorded"
pstatus=""
[ "$approved" = "1" ] || echo "(nothing was approved, so nothing runs; the proposal stays parked for a person)"
for _ in $(seq 1 60); do
  [ "$approved" = "1" ] || break
  proposals="$(pcurl "$probe/v1/remediations")"
  pstatus="$(PROPOSALS="$proposals" PID="$pid" python3 - <<'PY'
import json, os
print(next((r["status"] for r in json.loads(os.environ["PROPOSALS"])["proposals"] if r["id"] == os.environ["PID"]), ""))
PY
)"
  case "$pstatus" in executed|failed|rejected|superseded) break ;; esac
  sleep 1
done
PROPOSALS="$proposals" PID="$pid" python3 - <<'PY'
import json, os
row = next(r for r in json.loads(os.environ["PROPOSALS"])["proposals"] if r["id"] == os.environ["PID"])
print(f"status {row['status']} · {row.get('approved_note') or 'approved from the console (no note, no person)'}")
for res in row.get("results", []):
    first = (res.get("output") or "").strip().splitlines()[:1]
    print(f"  exit {res['exit']:>2}  {res['command']:<22} {first[0][:66] if first else ''}")
PY

say "9. the condition ends: the recovery verifies the work, and the pipe's audit record holds the chain"
post '{"title":"Disk /data at 92% on db-1","message":"back to 15% after the slot was dropped","state":"resolved","env":"prod"}' >/dev/null
sleep 3
WORK_JSON="$(pcurl "$probe/v1/work")" SESSION="$session" python3 - <<'PY'
import json, os
session = os.environ["SESSION"]
for i in json.loads(os.environ["WORK_JSON"]).get("items") or []:
    if session in (i.get("sessions") or []) or i.get("work_id") == session:
        print(f"work {i['work_id']}: state {i['state']} · verified {i['verified']} by {i.get('verified_by') or '-'} · condition ended {i.get('recovered')}")
        for a in i.get("artifacts", []):
            print(f"  {a['kind']}: {a['name']}")
PY
AUDIT_JSON="$(curl -sf "$base/audit/$disk_id")" RELAY_URL="$base" python3 - <<'PY'
import json, os
rec = json.loads(os.environ["AUDIT_JSON"])
totals = rec.get("totals") or {}
print(f"audit record for operation {rec.get('operation')}: {totals.get('hops')} hops over {totals.get('span_seconds')}s, {totals.get('human_actions')} human action(s)")
for hop in rec.get("hops") or []:
    sent = ", ".join(f"{d.get('channel')} {d.get('status')}" for d in hop.get("deliveries") or [])
    print(f"  hop {hop.get('id')} via {hop.get('source'):<13} → {sent}")
for a in rec.get("human_actions") or []:
    print(f"  press: {a.get('kind')} by {a.get('actor') or '?'} → {a.get('outcome')}")
print(f"the page: {os.environ['RELAY_URL']}/audit/{rec.get('operation')}")
print(f"the alert's story, top to bottom: {os.environ['RELAY_URL']}/#/alert/{rec.get('operation')}")
PY

printf '\nThis was a rehearsal: no model was called, and nothing ran except the two observations a person approved.\n'
printf 'The boards:  %s  (pipe)   %s  (judge)   %s/ui  (investigator)\n' "$base" "$judge" "$probe"
printf 'What the operator would have received:  docker compose logs -f sink\n'
printf 'To investigate for real:  HOOKPROBE_RUNTIME=claude HOOKPROBE_MODEL=claude-opus-5 ANTHROPIC_API_KEY=sk-ant-... \\\n'
printf '    docker compose -f docker-compose.quickstart.yml up -d\n'
