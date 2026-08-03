#!/usr/bin/env bash
# Container entrypoint: make sure there is something to index, then exec the CMD.
#
# Kept as an entrypoint rather than baked into the image so the target repo lands on
# the mounted ./data volume and survives a rebuild — and so `docker compose run` gets
# the same treatment as `up`.
set -euo pipefail

if [ "${FORGE_SKIP_BOOTSTRAP:-0}" != "1" ]; then
  /app/scripts/bootstrap_target.sh || true
fi

exec "$@"
