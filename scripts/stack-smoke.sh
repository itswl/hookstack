#!/usr/bin/env bash
# Brings the whole family up and drives one alert through every route.
#
# This exists because neither service's gate can see the stack. Each one is
# green while the pipe cannot reach the brain, the brain answers into the void,
# a compose path points at a directory that moved, or a stand-in container
# starts but nothing is wired to it — every one of which has happened here.
#
#   bash scripts/stack-smoke.sh            # build, test, tear down
#   KEEP=1 bash scripts/stack-smoke.sh     # leave it running to poke at
set -euo pipefail
cd "$(dirname "$0")/.."

RELAY=http://127.0.0.1:8100
JUDGE=http://127.0.0.1:8200
step() { printf '\n\033[1;34m── %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31mSTACK RED\033[0m — %s\n' "$1"; docker compose logs --tail 40; exit 1; }

cleanup() { [ "${KEEP:-0}" = "1" ] || docker compose down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

step "config is valid"
docker compose config >/dev/null
echo "stack compose parses"
# The standalone files and the family file guard their required variables
# with ${...:?}. Feed placeholders in a subshell so `config` exercises each
# file itself, not whatever this checkout's .env happens to contain.
(
  # Must LOOK like a path (leading ./) or compose reads it as a named
  # volume; `config` does not check that the file exists.
  export HOOKRELAY_CONFIG_FILE=./config.yaml
  export HOOKJUDGE_RETURN_URL=http://hookrelay:8100/hook/judge-notify
  export HOOKPROBE_TOKEN=placeholder
  docker compose -f hookrelay/deploy/docker-compose.yml config >/dev/null
  docker compose -f hookjudge/deploy/docker-compose.yml config >/dev/null
  docker compose -f hookprobe/deploy/docker-compose.yml config >/dev/null
  docker compose -f deploy/docker-compose.yml config >/dev/null
)
echo "standalone and family composes parse"
# The production composes read .env from the deployment root, which a fresh
# checkout does not have. Validate them when the file is there and say so when
# it is not — the alternative is marking it `required: false`, which would
# weaken production so that CI could pass. Wrong way round.
if [ -f .env ]; then
  docker compose -f hookrelay/deploy/docker-compose.prod.yml config >/dev/null
  docker compose --env-file .env -f hookprobe/deploy/docker-compose.prod.yml config >/dev/null
  echo "production composes parse"
else
  echo "production composes skipped — no .env in this checkout"
fi

step "build and start"
docker compose down -v >/dev/null 2>&1 || true
docker compose up -d --build >/dev/null

step "wait for health"
for _ in $(seq 1 60); do
  if curl -sf "$RELAY/healthz" >/dev/null 2>&1 && curl -sf "$JUDGE/healthz" >/dev/null 2>&1; then
    echo "pipe and brain are up"; break
  fi
  sleep 2
done
curl -sf "$RELAY/healthz" >/dev/null || fail "the pipe never became healthy"
curl -sf "$JUDGE/healthz" >/dev/null || fail "the brain never became healthy"

fire() {
  curl -sf --max-time 10 -o /dev/null -X POST "$RELAY/hook/$1" \
    -H 'content-type: application/json' -d "$2" || fail "door $1 refused the event"
}

step "drive every route"
fire inbound '{"title":"支付网关 5xx 比例 8.1%","message":"gateway-2 近 5 分钟 5xx 8.1%","state":"alerting","env":"prod"}'
sleep 4
fire inbound '{"title":"支付网关 5xx 比例 8.1%","message":"gateway-2 近 5 分钟 5xx 8.4%","state":"alerting","env":"prod"}'
sleep 4
fire alertmanager '{"status":"firing","commonLabels":{"alertname":"DiskWillFill","env":"prod"},"alerts":[{"status":"firing","labels":{"alertname":"DiskWillFill","env":"prod","service":"k8s"},"annotations":{"summary":"k8s 节点磁盘使用率 93%","description":"node-3 /var 剩余 7%"}}]}'
sleep 4
fire alertmanager '{"status":"resolved","commonLabels":{"alertname":"DiskWillFill","env":"prod"},"alerts":[{"status":"resolved","labels":{"alertname":"DiskWillFill","env":"prod","service":"k8s"},"annotations":{"summary":"k8s 节点磁盘使用率 93%","description":"已回落至 41%"}}]}'

step "wait for the far end"
# Wait on the LAST link, not a midpoint. The brain's ledger reaching four only
# means it judged four — the judgement still has to travel back through the
# pipe and be delivered downstream. Waiting on the brain and then reading the
# sink asserted against a chain that had not finished.
for _ in $(seq 1 45); do
  judged=$(curl -sf "$JUDGE/status" | python3 -c 'import sys,json;print(json.load(sys.stdin)["summary"]["judged"])' 2>/dev/null || echo 0)
  cards=$(docker compose logs sink 2>/dev/null | python3 -c 'import sys;print(sum("delivery on" in l for l in sys.stdin))' 2>/dev/null || echo 0)
  # Four judgements, each dressed for two channels.
  if [ "$judged" -ge 4 ] && [ "${cards:-0}" -ge 8 ]; then break; fi
  sleep 2
done
echo "brain judged $judged; sink received ${cards:-0} deliveries"

step "assert the ledger"
curl -sf "$JUDGE/status" > /tmp/stack-judge.json || fail "the brain's ledger is unreadable"
docker compose logs sink > /tmp/stack-sink.log 2>&1 || true
python3 scripts/assert_stack.py /tmp/stack-judge.json /tmp/stack-sink.log || fail "the stack ran but produced the wrong answer"

printf '\n\033[1;32mSTACK GREEN\033[0m\n'
