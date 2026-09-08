# The Lark adapter. Node for the CLI (it is the websocket transport and the API
# client), Python for the ~200 lines of glue — no third-party Python at all, so
# there is nothing here to keep locked.
FROM node:22-alpine

# curl is not optional: @larksuite/cli's postinstall shells out to it to fetch
# the platform binary, and fails with a bare ENOENT that reads like a network
# problem when it is missing.
RUN apk add --no-cache python3 ca-certificates curl

# Pinned deliberately. This is the piece that speaks a live protocol to a
# platform, so "whatever shipped today" is the wrong default — bump it on
# purpose: docker compose build --build-arg LARK_CLI_VERSION=x.y.z
ARG LARK_CLI_VERSION=1.0.88
RUN npm install -g "@larksuite/cli@${LARK_CLI_VERSION}" && npm cache clean --force

WORKDIR /app
COPY bridge.py render.py .

# No LARK_CLI_HOME here, and no volume in either compose. lark-cli 1.0.88
# ignores that variable and writes $HOME/.lark-cli; what it keeps there is a
# credential the entrypoint re-derives from the environment, a version-check
# cache, and the bus pid/socket/locks of the process running right now. The
# last of those is why the volume is gone rather than repointed — see the
# README. Every container starts from its environment and keeps nothing.
ENV BRIDGE_PORT=9100
EXPOSE 9100

# No published port and no healthcheck endpoint of its own: the pipe's delivery
# ledger already reports whether a card landed, which is a better signal than
# this process being up.
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "bridge.py"]
