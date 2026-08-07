FROM python:3.14-slim

WORKDIR /app

# Runtime dependencies only — pytest and ruff belong in the gate, not in the
# image that runs in production.
COPY requirements.txt .
RUN pip install --no-cache-dir \
      "$(grep -E '^fastapi' requirements.txt)" \
      "$(grep -E '^uvicorn' requirements.txt)" \
      "$(grep -E '^aiosqlite' requirements.txt)" \
      "$(grep -E '^httpx' requirements.txt)"

COPY hookjudge/ hookjudge/

ENV HOOKJUDGE_HOST=0.0.0.0 \
    HOOKJUDGE_PORT=8200 \
    HOOKJUDGE_DB=/data/hookjudge.db

# The ledger lives here; without a mount it is a fresh memory on every restart.
VOLUME ["/data"]
EXPOSE 8200

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8200/healthz').read()"]

CMD ["python", "-m", "hookjudge"]
