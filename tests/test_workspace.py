"""Per-session git worktree — created, confined, and torn down."""

from __future__ import annotations

import subprocess

import pytest

from forge.config import CacheMode, Settings
from forge.core.workspace import create_workspace, remove_workspace, session_workspace


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=repo,
        check=True,
    )
    return repo


@pytest.fixture
def settings(tmp_path, repo):
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        target_repo=repo,
        workspace_root=tmp_path / "ws",
    )


def test_worktree_is_created_with_the_repo_contents(settings, repo):
    ws = create_workspace("s1", settings=settings, repo=repo)
    try:
        assert ws.path.is_dir()
        assert ws.exists("src/calc.py")
        assert "def add" in ws.read("src/calc.py")
        # it is a real worktree of the repo, on its own branch
        worktrees = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True
        ).stdout
        assert str(ws.path) in worktrees
    finally:
        remove_workspace(ws)


def test_resolve_blocks_paths_that_escape_the_worktree(settings, repo):
    ws = create_workspace("s2", settings=settings, repo=repo)
    try:
        assert ws.resolve("src/calc.py").is_file()
        for escape in ("../../etc/passwd", "/etc/passwd", "src/../../outside.py"):
            with pytest.raises(ValueError):
                ws.resolve(escape)
    finally:
        remove_workspace(ws)


def test_remove_tears_down_the_worktree_and_branch(settings, repo):
    ws = create_workspace("s3", settings=settings, repo=repo)
    remove_workspace(ws)
    assert not ws.path.exists()
    branches = subprocess.run(
        ["git", "-C", str(repo), "branch", "--list", ws.branch], capture_output=True, text=True
    ).stdout
    assert ws.branch not in branches


def test_session_workspace_context_manager_cleans_up(settings, repo):
    with session_workspace("s4", settings=settings, repo=repo) as ws:
        path = ws.path
        assert path.is_dir()
    assert not path.exists()


def test_edits_do_not_touch_the_source_repo(settings, repo):
    with session_workspace("s5", settings=settings, repo=repo) as ws:
        ws.resolve("src/calc.py").write_text("def add(a, b):\n    return a + b\n")
        assert "a + b" in ws.read("src/calc.py")
    # the pinned clone is untouched
    assert "return a - b" in (repo / "src" / "calc.py").read_text()
