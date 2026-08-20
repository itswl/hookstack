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
ARG LARK_CLI_VERSION=1.0.77
RUN npm install -g "@larksuite/cli@${LARK_CLI_VERSION}" && npm cache clean --force

WORKDIR /app
COPY bridge.py .

# lark-cli keeps its app credentials and its event cursor here; mounted so a
# restart does not re-authenticate and re-subscribe from scratch.
ENV LARK_CLI_HOME=/config
ENV BRIDGE_PORT=9100
EXPOSE 9100

# No published port and no healthcheck endpoint of its own: the pipe's delivery
# ledger already reports whether a card landed, which is a better signal than
# this process being up.
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "bridge.py"]
