#!/usr/bin/env bash
# PreToolUse hook (Edit|Write|MultiEdit). Blocks writes to the frozen spec files.
# The human edits these by hand; an automated session must not rewrite scope.
# Exit 2 blocks the tool call and shows stderr to the model. Exit 0 allows it.
set -uo pipefail

root="${CLAUDE_PROJECT_DIR:-.}"
input="$(cat)"

# jq missing or no path in the payload -> fail open (allow). A protect hook that
# hard-errors would block every edit in the repo.
path="$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)"
[[ -z "$path" ]] && exit 0

case "$path" in
  /*) abs="$path" ;;
  *)  abs="$root/$path" ;;
esac

base="$(basename "$path")"
for guarded in \
  "$root/docs/cahier-des-charges.md" \
  "$root/docs/descope-v1.md"
do
  if [[ "$abs" -ef "$guarded" ]] || [[ "$abs" == "$guarded" ]] || [[ "$base" == "$(basename "$guarded")" ]]; then
    {
      echo "BLOCKED: $base is a frozen specification file."
      echo
      echo "The cahier des charges and the descope register are the graded spec and the"
      echo "argued scope decisions. They are edited by hand by the human, never by an"
      echo "automated session. If this genuinely needs to change, record the reason under"
      echo "'Blocked / open decisions' in docs/STATE.md and raise it — do not edit the spec."
    } >&2
    exit 2
  fi
done

exit 0
