FROM python:3.14-slim

WORKDIR /app

# Runtime dependencies only — pytest and ruff belong in the gate, not in the
# image that runs in production.
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -c requirements.lock \
      "$(grep -E '^fastapi' requirements.txt)" \
      "$(grep -E '^uvicorn' requirements.txt)" \
      "$(grep -E '^aiosqlite' requirements.txt)" \
      "$(grep -E '^httpx' requirements.txt)"

COPY hookjudge/ hookjudge/
# The canned model, so the quickstart can exercise the paid route with no key.
# See the note in hookrelay/Dockerfile: an image that cannot run its own demo
# forces every trial to begin with a clone and a build.
COPY examples/ examples/

ENV HOOKJUDGE_HOST=0.0.0.0 \
    HOOKJUDGE_PORT=8200 \
    HOOKJUDGE_DB=/data/hookjudge.db

# Non-root: the brain is an HTTP server that writes its ledger to /data and
# needs no privilege or capability. A named volume inherits this ownership; a
# bind mount (the prod shape) must be chowned to uid 10001 on the host. uid
# 10001 matches hookprobe's and hookrelay's — one chown convention for the
# family.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data \
    && chown -R app:app /data
USER app

# The ledger lives here; without a mount it is a fresh memory on every restart.
VOLUME ["/data"]
EXPOSE 8200

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8200/healthz').read()"]

CMD ["python", "-m", "hookjudge"]
