"""Per-session git worktree — the confined surface FORGE is allowed to edit (§5.1).

Every write the EDITOR and SANDBOX make lands in a worktree of the target repo under
``workspace_root``, created on session start and removed on close. A worktree (not a
copy) shares the object store with the target repo but has its own checked-out tree,
index and branch — so a session's edits never touch the pinned clone and are trivially
discarded, and two sessions never collide. The path-policy engine (D10) enforces that
writes stay inside here; this module only creates and tears the tree down.
"""

from __future__ import annotations

import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from forge.config import Settings, get_settings


@dataclass(frozen=True)
class Workspace:
    """A live session worktree: where it lives and what it branched from."""

    session_id: str
    path: Path
    repo: Path

    @property
    def branch(self) -> str:
        return f"forge/{self.session_id}"

    def resolve(self, rel_path: str) -> Path:
        """A repo-relative path resolved *inside* the worktree, escape-guarded.

        ``realpath`` first so a symlink or ``..`` cannot climb out — the invariant
        the policy engine formalises at D10, applied here so the editor/patch tools
        can never address a file outside the session's tree.
        """
        root = self.path.resolve()
        full = (root / rel_path).resolve()
        if full != root and not str(full).startswith(str(root) + "/"):
            raise ValueError(f"path escapes the worktree: {rel_path!r}")
        return full

    def read(self, rel_path: str) -> str:
        return self.resolve(rel_path).read_text(encoding="utf-8")

    def exists(self, rel_path: str) -> bool:
        return self.resolve(rel_path).is_file()


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def create_workspace(
    session_id: str, *, settings: Settings | None = None, repo: str | Path | None = None
) -> Workspace:
    """Add a fresh worktree for ``session_id`` at ``workspace_root/<session_id>``."""
    settings = settings or get_settings()
    repo = Path(repo or settings.target_repo).resolve()
    # Resolve to absolute like ``repo`` above. ``git worktree add`` runs with cwd=repo,
    # so a *relative* workspace_root (WORKSPACE_ROOT=data/workspaces in .env) gets created
    # under the target repo, while Workspace.path resolves it against the process cwd —
    # two different places, leaving the sandbox to mount an empty dir with no source.
    root = (Path(settings.workspace_root) / session_id).resolve()
    root.parent.mkdir(parents=True, exist_ok=True)

    workspace = Workspace(session_id=session_id, path=root, repo=repo)
    if root.exists():
        remove_workspace(workspace)  # a stale tree from a crashed session

    # A per-session branch (-B resets it to HEAD) so two worktrees never fight over
    # one checked-out branch. -f in case a prior prune left the branch dangling.
    _git(repo, "worktree", "add", "-f", "-B", workspace.branch, str(root), "HEAD")
    return workspace


def remove_workspace(workspace: Workspace) -> None:
    """Remove the worktree, delete its branch, and clean the directory. Best-effort:
    a session that already tore down, or a partial one, must not raise on close."""
    _git(workspace.repo, "worktree", "remove", "--force", str(workspace.path), check=False)
    _git(workspace.repo, "worktree", "prune", check=False)
    _git(workspace.repo, "branch", "-D", workspace.branch, check=False)
    if workspace.path.exists():
        shutil.rmtree(workspace.path, ignore_errors=True)


@contextmanager
def session_workspace(
    session_id: str, *, settings: Settings | None = None, repo: str | Path | None = None
):
    """A worktree for the duration of a ``with`` block, torn down on exit."""
    workspace = create_workspace(session_id, settings=settings, repo=repo)
    try:
        yield workspace
    finally:
        remove_workspace(workspace)
