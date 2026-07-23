#!/usr/bin/env bash
# PreCompact hook. Preserve the transcript before compaction discards it, so a
# summarised session can still be reconstructed. Never break compaction: exit 0.
set -uo pipefail

root="${CLAUDE_PROJECT_DIR:-.}"
input="$(cat)"

src="$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)"
[[ -z "$src" || ! -f "$src" ]] && exit 0

dest_dir="$root/.claude/transcripts"
mkdir -p "$dest_dir" 2>/dev/null || exit 0
cp "$src" "$dest_dir/$(date +%Y%m%d-%H%M%S)-precompact.jsonl" 2>/dev/null || exit 0

exit 0
