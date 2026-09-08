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

# lark-cli keeps its app credentials and its event cursor here; mounted so a
# restart does not re-authenticate and re-subscribe from scratch.
# No LARK_CLI_HOME: lark-cli 1.0.88 ignores it and writes $HOME/.lark-cli.
# Nothing it keeps there is worth carrying between containers — see the README.
ENV BRIDGE_PORT=9100
EXPOSE 9100

# No published port and no healthcheck endpoint of its own: the pipe's delivery
# ledger already reports whether a card landed, which is a better signal than
# this process being up.
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "bridge.py"]
