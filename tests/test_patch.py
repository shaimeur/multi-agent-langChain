"""Structured edits → diff → ``git apply --check``, with no disk writes."""

from __future__ import annotations

import subprocess

import pytest

from forge.config import CacheMode, Settings
from forge.core.workspace import create_workspace, remove_workspace
from forge.models import Patch, PatchSet
from forge.tools.patch import apply_patch_dryrun, build_diff


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "calc.py").write_text("def add(a, b):\n    return a - b  # bug\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=repo,
        check=True,
    )
    settings = Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        target_repo=repo,
        workspace_root=tmp_path / "ws",
    )
    ws = create_workspace("patch", settings=settings, repo=repo)
    yield ws
    remove_workspace(ws)


def _set(*patches):
    return PatchSet(patches=list(patches))


def test_a_valid_edit_produces_a_diff_git_accepts(workspace):
    result = apply_patch_dryrun(
        workspace, _set(Patch(path="src/calc.py", old_string="a - b  # bug", new_string="a + b"))
    )
    assert result.ok, result.message
    assert "-    return a - b  # bug" in result.diff
    assert "+    return a + b" in result.diff
    # a dry run writes nothing
    assert "a - b" in workspace.read("src/calc.py")


def test_absent_old_string_is_rejected_not_crashed(workspace):
    result = apply_patch_dryrun(
        workspace, _set(Patch(path="src/calc.py", old_string="not in the file", new_string="x"))
    )
    assert result.ok is False
    assert "not found" in result.message


def test_ambiguous_old_string_is_rejected(workspace):
    workspace.resolve("src/dup.py").write_text("x = 1\nx = 1\n")
    result = apply_patch_dryrun(
        workspace, _set(Patch(path="src/dup.py", old_string="x = 1", new_string="x = 2"))
    )
    assert result.ok is False
    assert "not unique" in result.message


def test_empty_patch_is_rejected(workspace):
    assert apply_patch_dryrun(workspace, _set()).ok is False


def test_multiple_edits_to_one_file_compose_in_order(workspace):
    workspace.resolve("src/m.py").write_text("a = 1\nb = 2\n")
    diff = build_diff(
        workspace,
        _set(
            Patch(path="src/m.py", old_string="a = 1", new_string="a = 10"),
            Patch(path="src/m.py", old_string="b = 2", new_string="b = 20"),
        ),
    )
    assert "+a = 10" in diff and "+b = 20" in diff
    # and it still applies cleanly
    result = subprocess.run(
        ["git", "-C", str(workspace.path), "apply", "--check", "-"],
        input=diff,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
