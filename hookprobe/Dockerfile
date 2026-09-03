# hookprobe: Python runtime + Node (the Claude Agent SDK drives the Claude
# Code CLI) + a lean read-only diagnostic core. Domain CLIs (kubectl, db
# clients) stay opt-in below — extend with exactly what your alerts need.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

# The CLI is the agent runtime itself. Unpinned, every image rebuild
# silently adopts whatever shipped upstream that day — the one dependency
# where drift changes behaviour most, pinned last. Bump deliberately:
#   docker compose build --build-arg CLAUDE_CODE_VERSION=x.y.z
# 2.1.259: 2.1.232 rejected third-party model names its catalog did not know
# (`[claude-code:unrecognized_model]` on e.g. gpt-5.6-luna behind a custom
# ANTHROPIC_BASE_URL), which broke the investigator against gateways that serve
# only their own model ids. 2.1.259 accepts them.
ARG CLAUDE_CODE_VERSION=2.1.259

# The diagnostic core: what any investigation reaches for first, and what
# marketplace runbooks assume exists (a patrol skill mis-read this container
# for lack of `ps`). All read-only by nature: process/socket/DNS/JSON.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
        procps jq iproute2 dnsutils netcat-openbsd lsof \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Domain CLIs — build args rather than edits, because a deployment that tracks
# this repository with `git pull --ff-only` cannot carry a modified Dockerfile.
# Install exactly what your alerts need:
#
#   docker compose build --build-arg APT_EXTRAS="postgresql-client redis-tools" \
#                        --build-arg KUBECTL_VERSION=v1.31.4
#
# A CLI is worth adding only once the investigator has something to point it at:
# the boundary is the credential you mount (a read-only kubeconfig, a viewer
# token), and the bash guard blocks mutating verbs on top of that. A container
# with no credentials mounted gains nothing from a database client but the
# bytes.
ARG APT_EXTRAS=""
RUN if [ -n "$APT_EXTRAS" ]; then \
      apt-get update \
      && apt-get install -y --no-install-recommends $APT_EXTRAS \
      && rm -rf /var/lib/apt/lists/*; \
    fi

ARG KUBECTL_VERSION=""
RUN if [ -n "$KUBECTL_VERSION" ]; then \
      curl -fsSLo /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/$(dpkg --print-architecture)/kubectl" \
      && chmod +x /usr/local/bin/kubectl; \
    fi

# Optional: browser evidence via the Playwright MCP server (wire it with
# deploy/mcp.example.json -> HOOKPROBE_MCP_CONFIG). Baking chromium and its
# system deps into the image (~400 MB) means first navigation downloads
# nothing at runtime:
# RUN npx -y playwright@1.57.0 install --with-deps chromium \
#     && npm cache clean --force

WORKDIR /app
COPY pyproject.toml README.md requirements.lock ./
COPY hookprobe ./hookprobe
# The lock is a ceiling, not a wish list: pyproject declares intent with
# floors, the lock pins the resolution the gate actually ran against.
RUN pip install -c requirements.lock .

# The layout is seeded in the image so a FRESH named volume inherits it with the
# right ownership. This is not cosmetic: mounting a host skills library at
# /data/home/.claude/skills makes Docker create the missing parents itself, as
# root — and then the non-root app cannot write $HOME/.claude/projects, so the
# engine keeps no transcripts and every follow-up turn dies with "No
# conversation found". Seeding the directories first keeps that mount harmless.
RUN useradd --create-home --uid 10001 hookprobe \
    && mkdir -p /data/home/.claude/projects /data/.claude/skills /data/.claude/agents /data/results \
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
