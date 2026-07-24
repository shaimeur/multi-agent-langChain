#!/usr/bin/env bash
# C8's proof, half two: the §11 routes exist and the SSE channel actually streams.
#
#   ./scripts/sse_smoke.sh              # starts its own server on :8099
#   BASE=http://localhost:8000 ./scripts/sse_smoke.sh   # use a running one
#
# Deliberately curl and jq rather than a Python client: C8 is worded as
# `curl -s localhost:8000/openapi.json | jq '.paths | keys'`, and a proof that only
# works from inside the application's own process proves less than one that does not.
#
# The graph itself needs a model (blocker B2), so the streamed run is expected to end
# in an `error` frame on a key-less machine. That is still a real SSE stream with real
# framing — which is what this script checks. It asserts the *channel*, not the model.
set -uo pipefail

BASE="${BASE:-}"
PORT="${PORT:-8099}"
OWN_SERVER=0
FAILURES=0

pass() { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; FAILURES=$((FAILURES + 1)); }

cleanup() {
  if [ "$OWN_SERVER" = "1" ] && [ -n "${SERVER_PID:-}" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "${TMPDIR_SMOKE:-/nonexistent}"/*.sqlite* 2>/dev/null || true
  [ -n "${TMPDIR_SMOKE:-}" ] && rmdir "$TMPDIR_SMOKE" 2>/dev/null || true
}
trap cleanup EXIT

for tool in curl jq; do
  command -v "$tool" >/dev/null || { echo "sse_smoke needs $tool"; exit 2; }
done

if [ -z "$BASE" ]; then
  TMPDIR_SMOKE="$(mktemp -d)"
  BASE="http://localhost:${PORT}"
  echo "Starting a server on ${BASE} ..."
  CACHE_MODE=replay CHECKPOINT_DB="${TMPDIR_SMOKE}/smoke.sqlite" \
    uv run uvicorn forge.api.main:app --port "$PORT" --log-level warning \
    >"${TMPDIR_SMOKE}/server.log" 2>&1 &
  SERVER_PID=$!
  OWN_SERVER=1
  for _ in $(seq 1 60); do
    curl -sf "${BASE}/v1/health" >/dev/null 2>&1 && break
    sleep 1
  done
fi

curl -sf "${BASE}/v1/health" >/dev/null || { echo "no server at ${BASE}"; exit 2; }

echo
echo "1. The §11 route table (C8)"
PATHS="$(curl -s "${BASE}/openapi.json" | jq -r '.paths | keys[]')"
for route in \
  /v1/sessions \
  '/v1/sessions/{session_id}/messages' \
  '/v1/sessions/{session_id}/history' \
  '/v1/sessions/{session_id}/approve' \
  /v1/index \
  /v1/guardrails/events \
  /v1/health \
  /v1/metrics
do
  if grep -qxF "$route" <<<"$PATHS"; then pass "$route"; else fail "$route is missing"; fi
done

echo
echo "2. Session lifecycle"
SESSION="$(curl -s -X POST "${BASE}/v1/sessions" -H 'content-type: application/json' \
  -d '{}' | jq -r '.session_id // empty')"
if [ -n "$SESSION" ]; then pass "created session ${SESSION}"; else fail "could not create a session"; fi

echo
echo "3. SSE stream framing"
if [ -n "$SESSION" ]; then
  FRAMES="$(curl -sN --max-time 90 -X POST "${BASE}/v1/sessions/${SESSION}/messages" \
    -H 'content-type: application/json' -d '{"message":"make add() return a + b"}')"
  EVENTS="$(grep -c '^event:' <<<"$FRAMES" || true)"
  if [ "${EVENTS:-0}" -gt 0 ]; then pass "streamed ${EVENTS} SSE frame(s)"; else fail "no SSE frames"; fi

  # SSE frames are CRLF-terminated; without stripping the CR every comparison below
  # silently fails against "done\r".
  TERMINAL="$(grep '^event:' <<<"$FRAMES" | tail -1 | tr -d '\r' | awk '{print $2}')"
  case "$TERMINAL" in
    done|error|interrupt) pass "stream ended with a terminal frame: ${TERMINAL}" ;;
    *) fail "stream ended on '${TERMINAL:-nothing}' — a stream must say why it stopped" ;;
  esac
fi

echo
echo "4. Guardrails logged the turn (C5)"
COUNT="$(curl -s "${BASE}/v1/guardrails/events?session_id=${SESSION}" | jq 'length')"
if [ "${COUNT:-0}" -gt 0 ]; then pass "${COUNT} guardrail event(s)"; else fail "no guardrail events"; fi

echo
echo "5. Metrics"
TURNS="$(curl -s "${BASE}/v1/metrics" | jq '.totals.turns')"
if [ "${TURNS:-0}" -ge 1 ]; then pass "metrics recorded ${TURNS} turn(s)"; else fail "no turns recorded"; fi

curl -s -X DELETE "${BASE}/v1/sessions/${SESSION}" >/dev/null 2>&1 || true

echo
if [ "$FAILURES" -eq 0 ]; then
  printf '\033[32mSSE smoke: all checks passed.\033[0m\n'
else
  printf '\033[31mSSE smoke: %d check(s) failed.\033[0m\n' "$FAILURES"
fi
exit "$FAILURES"
