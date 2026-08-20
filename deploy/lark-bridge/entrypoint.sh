#!/bin/sh
# Configure lark-cli from the environment, using its OWN init command rather
# than hand-writing its config file. The first attempt did write that file by
# hand and the CLI ignored it — the on-disk shape is the CLI's business and not
# a contract, so asking it to do the writing is the only version that stays
# correct when it changes.
#
# The secret goes in on STDIN, never as an argv: a process list is readable.
set -eu
: "${LARK_APP_ID:?LARK_APP_ID is required}"
: "${LARK_APP_SECRET:?LARK_APP_SECRET is required}"

if ! lark-cli config show >/dev/null 2>&1; then
  printf '%s' "$LARK_APP_SECRET" | lark-cli config init \
    --app-id "$LARK_APP_ID" \
    --app-secret-stdin \
    --brand "${LARK_BRAND:-lark}" >/dev/null
  echo "lark-cli configured for $LARK_APP_ID"
else
  echo "lark-cli already configured; leaving it alone"
fi

exec "$@"
