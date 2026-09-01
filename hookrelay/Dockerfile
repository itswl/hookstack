FROM python:3.14-slim

WORKDIR /app
# Runtime dependencies only, pinned by the lock — pytest, ruff and mypy
# belong in the gate, not in the image that runs in production.
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -c requirements.lock \
      "$(grep -E '^fastapi' requirements.txt)" \
      "$(grep -E '^uvicorn' requirements.txt)" \
      "$(grep -E '^aiosqlite' requirements.txt)" \
      "$(grep -E '^httpx' requirements.txt)" \
      "$(grep -E '^pyyaml' requirements.txt)"

COPY hookrelay/ hookrelay/
COPY config.example.yaml .
# The demo family travels IN the image. Without this, the published image can
# serve production but cannot run its own quickstart: the compose had to mount
# ./hookrelay/examples from a checkout, so "try it" started with a git clone
# and a build of the very thing already published. examples/stack.yaml is the
# wiring and examples/sink.py is the readable downstream.
COPY examples/ examples/

ENV HOOKRELAY_HOST=0.0.0.0 \
    HOOKRELAY_PORT=8100 \
    HOOKRELAY_DB=/data/hookrelay.db \
    HOOKRELAY_CONFIG=/data/config.yaml

EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=3s CMD ["python", "-c", "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"HOOKRELAY_PORT\",\"8100\")}/healthz')"]
CMD ["python", "-m", "hookrelay"]
