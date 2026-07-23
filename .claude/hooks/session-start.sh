#!/usr/bin/env bash
# SessionStart hook. Its stdout is injected into the session's context — that IS
# the whole continuity mechanism, so it must never fail. No `set -e`; always exit 0.
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

echo "===== PROJECT STATE ====="
if [[ -f docs/STATE.md ]]; then
  cat docs/STATE.md
else
  echo "!! docs/STATE.md IS MISSING — the progress ledger is unavailable."
  echo "!! Do not trust recollection. Recover it from git or a transcript in"
  echo "!! .claude/transcripts/, or run /checkpoint to rebuild it, before doing work."
fi

echo
echo "===== GIT ====="
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  branch="$(git branch --show-current 2>/dev/null)"
  echo "branch: ${branch:-(detached HEAD)}"
  echo "recent commits:"
  git log --oneline -5 2>/dev/null || echo "  (no commits yet)"
  echo "working tree:"
  status="$(git status --short 2>/dev/null | head -20)"
  if [[ -n "$status" ]]; then
    echo "$status"
  else
    echo "(clean)"
  fi
else
  echo "(not a git repository)"
fi

echo
echo "===== REMINDER ====="
echo "You have no memory of previous sessions. Trust STATE.md above over your own"
echo "recollection. Do not redo anything already under 'Done'. Start from the next"
echo "concrete action it names. GOALS.md holds the full plan — read it on demand."
echo "Run /checkpoint before the session ends so the next one resumes cleanly."

exit 0
