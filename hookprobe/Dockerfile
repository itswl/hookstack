# hookprobe: Python runtime + Node (the Claude Agent SDK drives the Claude
# Code CLI) + nothing else. Diagnostic CLIs are deliberately NOT bundled —
# extend this image with exactly the read-only tools your alerts need.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Example: add the read-only diagnostic CLIs your prompts rely on.
# RUN curl -fsSLo /usr/local/bin/kubectl \
#       "https://dl.k8s.io/release/v1.31.4/bin/linux/$(dpkg --print-architecture)/kubectl" \
#     && chmod +x /usr/local/bin/kubectl

WORKDIR /app
COPY pyproject.toml README.md ./
COPY hookprobe ./hookprobe
RUN pip install .

RUN useradd --create-home --uid 10001 hookprobe \
    && mkdir -p /data \
    && chown -R hookprobe:hookprobe /data
USER hookprobe
# HOME lives on the volume: the engine keeps session transcripts in
# $HOME/.claude, and follow-up turns (/sessions/{key}/continue) resume them.
ENV HOOKPROBE_WORKDIR=/data \
    HOME=/data/home

EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=3)"]
CMD ["python", "-m", "hookprobe"]
