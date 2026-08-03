#!/usr/bin/env bash
# Fetch the demo target repository if it is not already there.
#
# `data/target/` is gitignored — it is a checkout of someone else's project, not our
# source — so a fresh clone of FORGE arrives with nothing to index. That is fine for
# a developer who already has it and fatal for the clean-machine test, which is how
# this script came to exist (D14 / C9).
#
# sqlparse is pinned: the RAG ablation numbers, the swe_mini seeded bugs and every
# recorded fixture are all tied to this exact tree. A floating HEAD would silently
# invalidate the evaluation chapter.
set -euo pipefail

TARGET_DIR="${TARGET_REPO:-data/target}"
TARGET_URL="${TARGET_REPO_URL:-https://github.com/andialbrecht/sqlparse.git}"
TARGET_SHA="${TARGET_REPO_SHA:-0d240230939bfb3b751b504878b1c7df04a3cab3}"

cd "$(dirname "$0")/.."

if [ -d "${TARGET_DIR}/.git" ]; then
  have="$(git -C "${TARGET_DIR}" rev-parse HEAD 2>/dev/null || echo none)"
  if [ "$have" = "$TARGET_SHA" ]; then
    echo "target repo already at the pinned sha (${TARGET_SHA:0:7})"
    exit 0
  fi
  echo "target repo present but at ${have:0:7}; pinning to ${TARGET_SHA:0:7}"
  git -C "${TARGET_DIR}" fetch --quiet origin "$TARGET_SHA" 2>/dev/null || true
  git -C "${TARGET_DIR}" checkout --quiet "$TARGET_SHA"
  exit 0
fi

echo "fetching the demo target repo into ${TARGET_DIR} ..."
mkdir -p "$(dirname "${TARGET_DIR}")"
# Not --depth 1: swe_mini and the demo both need to check out an exact sha.
if ! git clone --quiet "$TARGET_URL" "$TARGET_DIR"; then
  echo "WARNING: could not clone ${TARGET_URL} (no network?)." >&2
  echo "         FORGE will still start, but there is nothing indexed to ask about." >&2
  echo "         Set TARGET_REPO to a local Python git repo instead." >&2
  exit 0
fi
git -C "${TARGET_DIR}" checkout --quiet "$TARGET_SHA"
echo "target repo ready at ${TARGET_SHA:0:7}"
