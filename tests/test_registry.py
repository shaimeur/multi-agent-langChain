"""C6 — ten externally-callable tools, and they actually run (cahier §9, §16 C6).

Counting tools proves nothing on its own; a registry of ten names that raise on call
would pass a naive count test. So every tool here is *invoked* against a real little
repo, and the path-taking ones are pointed outside their root to check they refuse.
"""

from __future__ import annotations

import subprocess

import pytest

from forge.config import CacheMode, Settings
from forge.tools.registry import build_toolset, describe_toolset


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text(
        "def tokenize(sql):\n"
        '    """Split sql into tokens."""\n'
        "    return sql.split()\n"
        "\n"
        "\n"
        "def parse(sql):\n"
        "    return tokenize(sql)\n"
    )
    (root / "README.md").write_text("# demo\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=root,
        check=True,
    )
    return root


@pytest.fixture
def settings(tmp_path, repo):
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        embedding_model="hashing",
        qdrant_url="",
        qdrant_path=tmp_path / "qdrant",
        target_repo=repo,
        checkpoint_db=tmp_path / "cp.sqlite",
    )


def _by_name(tools):
    return {t.name: t for t in tools}


def test_the_registry_offers_ten_tools_with_a_workspace(settings, repo, tmp_path):
    """C6 asks for 10. Seven are knowledge tools; three need a worktree to bind to."""
    from forge.core.workspace import create_workspace, remove_workspace

    settings = settings.model_copy(update={"workspace_root": tmp_path / "ws"})
    workspace = create_workspace("c6", settings=settings, repo=repo)
    try:
        tools = build_toolset(settings=settings, workspace=workspace)
        assert len(tools) == 10, [t.name for t in tools]
        assert len({t.name for t in tools}) == 10, "names must be distinct"
        assert all(t.description for t in tools), "a tool with no description is unusable"
    finally:
        remove_workspace(workspace)


def test_every_knowledge_tool_runs_and_returns_text(settings):
    """'Opérationnels' means callable, not merely registered."""
    tools = _by_name(build_toolset(settings=settings, include_sandbox=False))
    assert len(tools) == 7

    assert "tokenize" in tools["ripgrep_search"].invoke({"pattern": "tokenize"})
    assert "tokenize" in tools["find_definitions"].invoke({"symbol": "tokenize"})
    assert "tokenize" in tools["find_references"].invoke({"symbol": "tokenize"})
    assert "def tokenize" in tools["read_file"].invoke({"path": "pkg/core.py"})
    assert "pkg/core.py" in tools["list_files"].invoke({"pattern": "*.py"})
    # No events logged yet in this fresh log, but the call must still succeed.
    assert isinstance(tools["guardrail_events"].invoke({}), str)


def test_read_file_refuses_to_leave_the_repository_root(settings):
    """A tool that takes a filename from a model is the shortest path to /etc/passwd."""
    tools = _by_name(build_toolset(settings=settings, include_sandbox=False))

    escaped = tools["read_file"].invoke({"path": "../../../../etc/passwd"})

    assert escaped.startswith("refused:"), escaped
    assert "root" not in escaped.lower() or "outside" in escaped.lower()


def test_list_files_refuses_to_leave_the_repository_root(settings):
    tools = _by_name(build_toolset(settings=settings, include_sandbox=False))

    assert tools["list_files"].invoke({"subdir": "../.."}).startswith("refused:")


def test_read_file_returns_numbered_lines_for_a_range(settings):
    """Citations are line-anchored, so a read that loses line numbers is useless."""
    tools = _by_name(build_toolset(settings=settings, include_sandbox=False))

    body = tools["read_file"].invoke({"path": "pkg/core.py", "start_line": 6, "end_line": 7})

    assert body.startswith("6\t"), body
    assert "def parse" in body
    assert "def tokenize" not in body, "the range was not honoured"


def test_describe_toolset_is_renderable(settings):
    """`forge tools` prints this; empty cells would make the C6 evidence unreadable."""
    rows = describe_toolset(build_toolset(settings=settings, include_sandbox=False))

    assert len(rows) == 7
    assert all(r["name"] and r["description"] for r in rows)
    assert any("query" in r["args"] for r in rows), "argument names should be exposed"
