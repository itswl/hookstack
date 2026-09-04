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
#   FORCE=1 bash scripts/stack-smoke.sh    # yes, wipe the existing probe volume
#
# It begins and ends with `docker compose down -v`. On CI that is free; on a
# laptop it deletes the investigator's skills, memory and case files, so the
# opening wipe refuses when that volume already exists.
set -euo pipefail
cd "$(dirname "$0")/.."

RELAY=http://127.0.0.1:8100
JUDGE=http://127.0.0.1:8200

# The node under test, named rather than assumed. `STACK_BRAIN=mine` skips
# hookjudge's own ledger assertions and leaves the dialect ones — that pair is
# how "the reference implementation is replaceable" stops being a claim and
# becomes a command somebody can run. RETURN_DOOR and BRAIN_CHANNEL are the two
# names your node's wiring uses in examples/stack.yaml.
STACK_BRAIN="${STACK_BRAIN:-hookjudge}"
RETURN_DOOR="${RETURN_DOOR:-judge-notify}"
BRAIN_CHANNEL="${BRAIN_CHANNEL:-to-judge}"
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
#
# WHICH names those are is read back out of the compose files, not listed here.
# It was a hand-kept list until 2026-09-03 and fell behind three times — the
# shadow's secret pair, then brain C's endpoint and model, then the watch door's
# two — each time turning main red until somebody appended one more export. A
# ${...:?} is precisely the declaration that `config` will demand the name, so
# deriving the list from it is the only version that cannot drift from the thing
# it stands in for. Why any given name is required is written at its own
# ${...:?} in the compose file; this step only owes each one a value.
(
  seed_required() {
    local name
    for name in $(grep -hoE '\$\{[A-Z0-9_]+:\?' "$@" | sed 's/^\${//; s/:?$//' | sort -u); do
      [ -n "${!name:-}" ] || export "$name=placeholder"
    done
  }

  # The one name whose SHAPE compose reads, so a bare placeholder is wrong for
  # it: this is a volume source, and with no leading ./ compose takes it for a
  # named volume rather than a path. `config` does not check that the file
  # exists, so the wrong shape passes here and fails somewhere far away.
  export HOOKRELAY_CONFIG_FILE=./config.yaml

  # Seeded per file rather than once for all of them, so each parse answers for
  # its own file: a variable that moves between composes still has to be
  # declared where it is now actually used.
  for compose in hookrelay/deploy/docker-compose.yml \
                 hookjudge/deploy/docker-compose.yml \
                 hookprobe/deploy/docker-compose.yml \
                 deploy/docker-compose.yml; do
    seed_required "$compose"
    docker compose -f "$compose" config >/dev/null
  done

  # The shadow compose was checked by nothing at all while being the newest file
  # here. It needs no .env branch: its `networks.default.external: true` names a
  # network `config` never looks for, so the parse works in a fresh checkout.
  seed_required deploy/docker-compose.shadow.yml
  docker compose -f deploy/docker-compose.shadow.yml config >/dev/null
)
echo "standalone, family and shadow composes parse"

step "the three pages share one design"
python3 scripts/assert_design.py

step "agent notes keep their shape"
python3 scripts/assert_agent_notes.py
# The production composes read .env from the deployment root, which a fresh
# checkout does not have. Validate them when the file is there and say so when
# it is not — the alternative is marking it `required: false`, which would
# weaken production so that CI could pass. Wrong way round.
if [ -f .env ]; then
  # Both lines carry --env-file, and the pipe's did not. Compose interpolates
  # ${...} from the compose FILE's own directory, so without the flag this one
  # resolves against hookrelay/deploy/.env — a file that has never existed —
  # while its sibling below resolves against the deployment root's. Today the
  # pipe's prod compose interpolates nothing, so the flag changes no answer;
  # the point is that the day someone adds a ${VAR} to it, it reads the .env
  # this branch just tested for, instead of silently reading nothing. Two
  # sibling lines disagreeing about their own contract is how that lands
  # unnoticed.
  docker compose --env-file .env -f hookrelay/deploy/docker-compose.prod.yml config >/dev/null
  docker compose --env-file .env -f hookprobe/deploy/docker-compose.prod.yml config >/dev/null
  echo "production composes parse"
else
  echo "production composes skipped — no .env in this checkout"
fi

step "build and start"
# `down -v` is how this smoke gets a clean slate, and on CI it destroys nothing.
# On a laptop the same command eats the investigator's volume: distilled skills,
# the environment memory, every case file it recalls from — and takes the demo
# probe down with it, which is somebody's live dependency the moment anything is
# pointed at it. So the wipe asks first when there is state to lose. FORCE=1
# says "I know, do it".
# Matched by suffix, not by "<directory>_probe-data": the project is named in
# the compose file (`name: hookstack`), so deriving it from the checkout's
# directory name looks right, silently matches nothing, and wipes the volume it
# was written to protect. It did exactly that once.
PROBE_VOLUMES=$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -E '_probe-data$' || true)
if [ "${FORCE:-0}" != "1" ] && [ -n "$PROBE_VOLUMES" ]; then
  printf '\033[1;31mrefusing to wipe an existing investigator volume\033[0m\n\n'
  printf 'This smoke starts with `docker compose down -v`. That deletes:\n\n'
  printf '  %s\n' $PROBE_VOLUMES
  printf '    — distilled skills, the environment memory, every case file\n'
  printf '  the relay and judge ledgers alongside it\n\n'
  printf 'and takes the demo probe down with them, which is a live dependency\n'
  printf 'when something else is pointed at it.\n\n'
  printf 'Run it in a scratch checkout, or accept the loss:\n\n'
  printf '  FORCE=1 bash scripts/stack-smoke.sh\n'
  # The EXIT trap is already armed, and cleanup() is `down -v` — so a bare
  # `exit 1` here deleted the very volume this guard just refused to delete.
  # Disarm the wipe on the way out; refusing must cost nothing.
  KEEP=1
  exit 1
fi
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

step "the shadow config is one the pipe can boot"
# deploy/shadow.yaml is a hookrelay CONFIG file, not a compose file — the parse
# above says nothing about what it contains, and nothing else read it either.
# See scripts/assert_shadow_config.py for what it asserts and why that file
# deserves a gate of its own.
#
# Run inside the relay container rather than on the host: loading a config the
# way the pipe loads it means importing hookrelay, which needs its runtime
# dependencies, and that container has exactly the ones the pipe will have in
# production. The host needs nothing this smoke did not already need.
#
# Hence the placement, after health rather than up in the config step: `compose
# exec` needs a running container, and `compose run` cannot have one — the
# service sets container_name, so a one-off would collide with the stack that is
# already up. A bad shadow config is therefore found a couple of minutes in
# instead of in the first seconds. Worth it to keep the check honest.
docker compose cp deploy/shadow.yaml hookrelay:/tmp/shadow.yaml >/dev/null \
  || fail "could not reach into the relay container to check the shadow config"
docker compose cp scripts/assert_shadow_config.py hookrelay:/tmp/assert_shadow_config.py >/dev/null \
  || fail "could not reach into the relay container to check the shadow config"
docker compose exec -T hookrelay python /tmp/assert_shadow_config.py /tmp/shadow.yaml \
  || fail "deploy/shadow.yaml is not a config the shadow deployment could boot"

fire() {
  curl -sf --max-time 10 -o /dev/null -X POST "$RELAY/hook/$1" \
    -H 'content-type: application/json' -d "$2" || fail "door $1 refused the event"
}

step "drive every route"
fire inbound '{"title":"Payment gateway 5xx rate 8.1%","message":"gateway-2 5xx at 8.1% over 5 minutes","state":"alerting","env":"prod"}'
sleep 4
fire inbound '{"title":"Payment gateway 5xx rate 8.1%","message":"gateway-2 5xx at 8.4% over 5 minutes","state":"alerting","env":"prod"}'
sleep 4
fire alertmanager '{"status":"firing","commonLabels":{"alertname":"DiskWillFill","env":"prod"},"alerts":[{"status":"firing","labels":{"alertname":"DiskWillFill","env":"prod","service":"k8s"},"annotations":{"summary":"k8s node disk usage 93%","description":"node-3 /var has 7% free"}}]}'
sleep 4
fire alertmanager '{"status":"resolved","commonLabels":{"alertname":"DiskWillFill","env":"prod"},"alerts":[{"status":"resolved","labels":{"alertname":"DiskWillFill","env":"prod","service":"k8s"},"annotations":{"summary":"k8s node disk usage 93%","description":"fell back to 41%"}}]}'

step "wait for the far end"
# Wait on the LAST link, not a midpoint. A brain's ledger reaching four only
# means it judged four — the judgement still has to travel back through the
# pipe and be delivered downstream. Waiting on the brain and then reading the
# sink asserted against a chain that had not finished.
#
# Both signals are read from the PIPE and the sink, never from the brain's own
# schema. A smoke that waits on `summary.judged` cannot be pointed at a node
# that has no such field, and being pointable at one is the whole test below.
for _ in $(seq 1 45); do
  returned=$(curl -sf "$RELAY/status" | RETURN_DOOR="$RETURN_DOOR" python3 -c \
    'import sys,json,os;print(sum(1 for r in json.load(sys.stdin)["recent"] if r["source"]==os.environ["RETURN_DOOR"]))' \
    2>/dev/null || echo 0)
  cards=$(docker compose logs sink 2>/dev/null | python3 -c 'import sys;print(sum("delivery on" in l for l in sys.stdin))' 2>/dev/null || echo 0)
  # Four results, each dressed for two channels.
  if [ "${returned:-0}" -ge 4 ] && [ "${cards:-0}" -ge 8 ]; then break; fi
  sleep 2
done
echo "the node returned ${returned:-0} result(s); sink received ${cards:-0} deliveries"

step "assert the ledger"
curl -sf "$RELAY/status" > /tmp/stack-relay.json || fail "the pipe's ledger is unreadable"
docker compose logs sink > /tmp/stack-sink.log 2>&1 || true

# THE DIALECT, measured from the bus. Runs whatever node is plugged in, and
# asserts only what the async-node contract asks of one: took the handover,
# signed its way back in, returned something the pipe could read and route,
# closed the loop. Nothing here reads the node's own ledger.
python3 scripts/assert_dialect.py /tmp/stack-relay.json /tmp/stack-sink.log \
  --return-door "$RETURN_DOOR" --brain-channel "$BRAIN_CHANNEL" --events 4 \
  || fail "the node plugged in here does not speak the async-node dialect"

# THE REFERENCE IMPLEMENTATION's own ledger — judged counts, the ai/reuse/
# recovery split, priced tokens, identity, recovery inheritance. Every one is a
# property of hookjudge and none is in the dialect, which is exactly why it is
# skippable: `STACK_BRAIN=mine bash scripts/stack-smoke.sh` is how the stated
# acceptance test ("swap hookjudge for a twenty-line judge of your own and this
# is still green") is actually expressed. It used to be unexpressible, because
# these assertions ran unconditionally and a conforming twenty-line node has no
# reason to have `summary.cost`.
if [ "${STACK_BRAIN:-hookjudge}" = "hookjudge" ]; then
  curl -sf "$JUDGE/status" > /tmp/stack-judge.json || fail "the brain's ledger is unreadable"
  python3 scripts/assert_stack.py /tmp/stack-judge.json /tmp/stack-sink.log || fail "the stack ran but produced the wrong answer"
else
  echo "STACK_BRAIN=${STACK_BRAIN} — hookjudge's own ledger assertions skipped; the dialect ones above still ran"
fi

printf '\n\033[1;32mSTACK GREEN\033[0m\n'
