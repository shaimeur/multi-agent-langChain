"""Turn structured edits into a diff and dry-run ``git apply --check`` (§5.1, A3).

The EDITOR emits search/replace edits (``Patch``); this module applies them to the
file content *in memory*, produces a unified diff with ``difflib``, and asks git
whether that diff would apply cleanly — **without touching disk**. A patch that fails
the check never reaches a human.

Building the diff from the worktree's actual content, rather than trusting a model to
emit valid diff syntax, is what makes this robust on a weak local coder model: the
model only has to name the text to replace, and git verifies the rest.
"""

from __future__ import annotations

import difflib
import subprocess
from collections import OrderedDict
from dataclasses import dataclass

from forge.core.workspace import Workspace
from forge.models import Patch, PatchSet


class PatchError(ValueError):
    """A structured edit that cannot be located, or is ambiguous — caught before diffing."""


@dataclass(frozen=True)
class PatchResult:
    """The outcome of a dry run: the built diff and whether git would accept it."""

    ok: bool
    diff: str
    message: str = ""


def _apply_edit(content: str, patch: Patch) -> str:
    """Apply one search/replace. ``old_string`` must occur exactly once."""
    occurrences = content.count(patch.old_string)
    if occurrences == 0:
        raise PatchError(f"{patch.path}: old_string not found in the file")
    if occurrences > 1:
        raise PatchError(f"{patch.path}: old_string occurs {occurrences}× — not unique")
    return content.replace(patch.old_string, patch.new_string, 1)


def build_diff(workspace: Workspace, patchset: PatchSet) -> str:
    """A unified diff for every edit, grouped by file, against the worktree content.

    Edits to the same file are applied in order to the file's current text, so a
    later edit sees the earlier one. A file whose edits net to no change contributes
    nothing.
    """
    by_path: OrderedDict[str, list[Patch]] = OrderedDict()
    for patch in patchset.patches:
        by_path.setdefault(patch.path, []).append(patch)

    parts: list[str] = []
    for path, patches in by_path.items():
        exists = workspace.exists(path)
        original = workspace.read(path) if exists else ""
        updated = original
        for patch in patches:
            updated = _apply_edit(updated, patch)
        if updated == original:
            continue
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=("a/" + path if exists else "/dev/null"),
            tofile="b/" + path,
        )
        parts.append("".join(diff))
    return "".join(parts)


def apply_patch_dryrun(workspace: Workspace, patchset: PatchSet) -> PatchResult:
    """Build the diff and run ``git apply --check`` in the worktree. No disk writes."""
    try:
        diff = build_diff(workspace, patchset)
    except PatchError as error:
        return PatchResult(ok=False, diff="", message=str(error))
    if not diff.strip():
        return PatchResult(ok=False, diff="", message="empty patch — no change produced")

    result = subprocess.run(
        ["git", "-C", str(workspace.path), "apply", "--check", "-"],
        input=diff,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return PatchResult(ok=True, diff=diff)
    return PatchResult(ok=False, diff=diff, message=result.stderr.strip())
