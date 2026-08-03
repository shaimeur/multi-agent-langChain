#!/usr/bin/env bash
# C9 — "docker compose up on a clean machine" (cahier §16 C9, GOALS D14).
#
# The point is not that compose works *here*, where the caches are warm, .env is
# populated and the index is already built. It is that it works somewhere that has
# never seen the project. So this clones HEAD into an empty directory outside the
# worktree and brings it up there with nothing but `.env.example`.
#
# What it deliberately does NOT inherit from the dev checkout:
#   · .env            — a real key would hide a missing default
#   · data/qdrant     — the index must be built by the container, not borrowed
#   · data/workspaces — session worktrees
#   · node_modules, .venv, __pycache__ — `git clone` gives us that for free
#
# It DOES inherit data/fixtures, because those are committed and are the whole
# reason the stack runs with no API key (CACHE_MODE=replay).
#
#   scripts/clean_machine_test.sh            # clone HEAD, up, probe, tear down
#   KEEP=1 scripts/clean_machine_test.sh     # leave it running to poke at
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLONE="${CLONE_DIR:-$(mktemp -d /tmp/forge-clean-XXXXXX)}"
PROJECT="forgeclean$$"
KEEP="${KEEP:-0}"
API_PORT="${API_PORT:-18000}"

step() { printf '\n\033[1;36m== %s\033[0m\n' "$1"; }
fail() { printf '\033[1;31mFAIL: %s\033[0m\n' "$1" >&2; exit 1; }

cleanup() {
  local code=$?
  if [ "$KEEP" = "1" ] && [ $code -eq 0 ]; then
    echo "KEEP=1 — left running at http://localhost:${API_PORT} (clone: ${CLONE})"
    echo "tear down with: docker compose -p ${PROJECT} down -v && rm -rf ${CLONE}"
    return
  fi
  step "tearing down"
  (cd "$CLONE" && docker compose -p "$PROJECT" down -v --remove-orphans >/dev/null 2>&1) || true
  rm -rf "$CLONE"
}
trap cleanup EXIT

command -v docker >/dev/null || fail "docker is not installed"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 is not available"

step "cloning HEAD into a directory that has never seen this project"
git clone --quiet --depth 1 "file://${REPO_ROOT}" "$CLONE"
cd "$CLONE"
echo "clone: $CLONE  ($(git rev-parse --short HEAD))"

# A clean machine has no .env. This is the documented first-run step, and if the
# example is missing or incomplete the whole test is supposed to fail here.
[ -f .env.example ] || fail ".env.example is missing — a clean machine has nothing to copy"
cp .env.example .env

# The index has to be built by the stack, not inherited. Prove it is absent.
[ ! -d data/qdrant ] || fail "the clone carried a prebuilt index — data/qdrant should be gitignored"
[ -d data/fixtures/llm ] || fail "fixtures are missing from the clone — replay cannot work"
echo "fixtures present: $(find data/fixtures/llm -name '*.json' | wc -l) recorded completions"

step "docker compose up --build (this is the command being tested)"
API_PORT="$API_PORT" docker compose -p "$PROJECT" up --build -d

step "waiting for the api healthcheck"
for i in $(seq 1 180); do
  status=$(docker compose -p "$PROJECT" ps --format json api 2>/dev/null \
           | python3 -c "import sys,json
try:
    d=json.loads(sys.stdin.read() or '{}')
    d=d[0] if isinstance(d,list) else d
    print(d.get('Health') or d.get('State',''))
except Exception:
    print('')" 2>/dev/null || echo "")
  [ "$status" = "healthy" ] && break
  if [ "$status" = "exited" ]; then
    docker compose -p "$PROJECT" logs --tail 40 api
    fail "the api container exited"
  fi
  sleep 2
done
[ "$status" = "healthy" ] || { docker compose -p "$PROJECT" logs --tail 40 api; fail "api never became healthy"; }
echo "api is healthy after ~$((i * 2))s"

step "probing the running stack"
base="http://localhost:${API_PORT}"

health=$(curl -fsS "${base}/v1/health") || fail "GET /v1/health failed"
echo "health: $health"
echo "$health" | grep -q '"status":"ok"' || fail "health is not ok"

curl -fsS "${base}/openapi.json" >/dev/null || fail "OpenAPI document is not served (C8)"
echo "openapi: served"

# C9 means a person can *open* it. An API with no UI does not satisfy §15.6.
ui=$(curl -fsS "${base}/" ) || fail "GET / did not serve the SPA"
echo "$ui" | grep -qi "<div id=\"root\"" || fail "/ served something that is not the FORGE SPA"
echo "web ui: served from the same origin"

step "indexing the target repo inside the container"
curl -fsS -X POST "${base}/v1/index" -H 'Content-Type: application/json' \
     -d '{"incremental":false}' >/dev/null || fail "POST /v1/index failed"
for i in $(seq 1 120); do
  hits=$(curl -fsS -X POST "${base}/v1/search" -H 'Content-Type: application/json' \
         -d '{"query":"tokenize sql statements","k":3}' 2>/dev/null \
         | python3 -c "import sys,json;print(len(json.load(sys.stdin).get('hits',[])))" 2>/dev/null || echo 0)
  [ "${hits:-0}" -gt 0 ] && break
  sleep 2
done
[ "${hits:-0}" -gt 0 ] || fail "retrieval returned nothing after indexing"
echo "search: $hits hits — the index was built inside the container"

step "C4 — the checkpoint survives a container restart"
sid="clean-$$"
curl -fsS -X POST "${base}/v1/sessions" -H 'Content-Type: application/json' \
     -d "{\"session_id\":\"${sid}\"}" >/dev/null || fail "could not create a session"
docker compose -p "$PROJECT" restart api >/dev/null
for i in $(seq 1 90); do
  curl -fsS "${base}/v1/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "${base}/v1/sessions/${sid}/history" >/dev/null \
  || fail "the session did not survive the restart — C4 is not satisfied"
echo "session ${sid} resumed after restart"

printf '\n\033[1;32mC9 PASS — one command brought the whole system up on a clean clone.\033[0m\n'
