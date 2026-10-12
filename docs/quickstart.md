# Start here

Three doors, depending on how much time you have. None of them needs an account
or a key, and the first two do not need a checkout.

## Thirty seconds — what it is

A signal arrives — an alert, a chat message, a ticket, a timer. Three small
services handle it:

```
upstreams ──► hookrelay ──► hookjudge ──► hookrelay ──► chat bridge / webhook
                  │
                  └──► hookprobe ──► hookrelay ──► the same channels
                       (critical/high; a rehearsal until it has a model)
```

- **hookrelay** is the pipe. It signs, routes and prices every signal and keeps
  one ledger of every hop.
- **hookjudge** decides cheaply whether a person must act now. Most events never
  pay for a model.
- **hookprobe** is the investigator. One read-only agent run per event, and it
  proves it is read-only at startup. The report reaches a person as a card.

Nothing touches production without a signed press. Read
[the overview](../OVERVIEW.md) for the tour, or
[what you get](what-you-get.md) for the feature list.

## Ten minutes — the whole loop, no keys, no bill

This runs the pipe, the judge, a canned model, the investigator on a recorded
rehearsal, and a sink you can read. Docker is the only prerequisite.

```bash
curl -fsSLO https://raw.githubusercontent.com/itswl/hookstack/main/docker-compose.quickstart.yml
docker compose -f docker-compose.quickstart.yml up -d
bash <(curl -fsSL https://raw.githubusercontent.com/itswl/hookstack/main/scripts/demo.sh)
```

What you should see: an alert is judged, a recorded investigation replays through
the **real** read-only gate and the real audit log, the report arrives as a card,
an approve press runs two allowlisted commands, and the alert resolves. The
demo's own last section says every number in a rehearsal was written in advance.

To investigate with a real model instead of the rehearsal, hand the node a model
and restart:

```bash
printf 'HOOKPROBE_RUNTIME=claude\nHOOKPROBE_MODEL=claude-opus-5\nANTHROPIC_API_KEY=sk-ant-...\n' > .env
docker compose --env-file .env -f docker-compose.quickstart.yml up -d
```

Then open the three boards: the pipe at <http://127.0.0.1:8100>, the judge at
<http://127.0.0.1:8200>, the investigator's console at <http://127.0.0.1:8088/ui>.

## Half a day — run it for real

The quickstart is a demo on published images. A real deployment is the same code
with your own credentials, a chat channel and an allowlist of fixes a person may
approve. That path, step by step:

1. [STACK.md](../STACK.md) — run all three from source, in order, and watch the
   gate green as you go.
2. [Two deployments, one codebase](deployments.md) — the two deployment shapes
   (alerts, and an operator's own work signals), the compose files under
   [`deploy/`](../deploy), and what each one needs in `.env`.
3. [docs/containment.md](containment.md) — **read this before you point it at a
   real cluster.** It is the map of which boundaries hold against a hostile
   model, which only reduce accidents, and what each one does not stop. The
   read-only claim is measured against your kubeconfig and AWS profile at
   startup.
4. The service in depth, when you are ready to change it:
   [hookrelay](../hookrelay/README.md) ·
   [hookjudge](../hookjudge/README.md) ·
   [hookprobe](../hookprobe/README.md).

## If something looks wrong

- The gate: `bash scripts/gate.sh` runs every service's gate plus the stack
  checks. Run it as its own step and read the verdict — see
  [CONTRIBUTING.md](../CONTRIBUTING.md).
- The boards need a token in the browser and keep it there; offline they show
  the page and say their service is out of reach.
- Containment question, or a boundary that does not hold: [SECURITY.md](../SECURITY.md).
