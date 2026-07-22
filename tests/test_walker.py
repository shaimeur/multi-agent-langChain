"""What gets indexed decides what FORGE can know — cahier §6.1."""

from __future__ import annotations

import subprocess

import pytest

from forge.rag.walker import (
    MAX_FILE_BYTES,
    changed_files,
    head_sha,
    walk_repo,
)


@pytest.fixture
def repo(tmp_path):
    """A git repo with the shapes the walker has to get right."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main():\n    return 1\n")
    (tmp_path / "README.md").write_text("# Title\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")

    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.py").write_text("bad = 1\n")
    (tmp_path / "uv.lock").write_text("locked\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00\x00binary")
    (tmp_path / "empty.py").write_text("   \n")
    (tmp_path / "bundle.min.py").write_text("a=1\n")

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


def _paths(repo, **kw):
    return {f.rel_path for f in walk_repo(repo, **kw)}


def test_indexes_source_and_docs(repo):
    assert _paths(repo) == {"src/app.py", "README.md", "pyproject.toml"}


@pytest.mark.parametrize(
    "excluded, why",
    [
        ("node_modules/pkg/index.py", "vendored code buries the signal"),
        ("uv.lock", "machine-generated, pure noise for retrieval"),
        ("logo.png", "binary"),
        ("empty.py", "whitespace only"),
        ("bundle.min.py", "minified"),
    ],
)
def test_skips_what_should_never_be_indexed(repo, excluded, why):
    assert excluded not in _paths(repo), why


def test_language_is_tagged_for_metadata_filtering(repo):
    langs = {f.rel_path: f.language for f in walk_repo(repo)}

    assert langs["src/app.py"] == "python"
    assert langs["README.md"] == "markdown"
    assert langs["pyproject.toml"] == "config"


def test_gitignored_files_are_skipped(repo):
    """Delegated to git rather than reimplementing .gitignore precedence."""
    (repo / ".gitignore").write_text("secret.py\n")
    (repo / "secret.py").write_text("KEY = 'x'\n")

    assert "secret.py" not in _paths(repo)


def test_forgeignore_excludes_beyond_gitignore(repo):
    """A path can be worth committing and not worth indexing."""
    (repo / ".forgeignore").write_text("# generated\nsrc/\n")

    assert "src/app.py" not in _paths(repo)
    assert "README.md" in _paths(repo)


def test_only_restricts_the_walk_for_incremental_reindex(repo):
    assert _paths(repo, only=["src/app.py"]) == {"src/app.py"}


def test_only_tolerates_paths_deleted_since_the_diff(repo):
    """A stale diff naming a deleted file must not crash the reindex."""
    assert _paths(repo, only=["src/app.py", "gone.py"]) == {"src/app.py"}


def test_oversized_files_are_skipped(repo):
    (repo / "huge.py").write_text("x = 1\n" * (MAX_FILE_BYTES // 3))

    assert "huge.py" not in _paths(repo)


def test_works_outside_a_git_repo(tmp_path):
    """Ingestion must not require the target to be a git checkout."""
    (tmp_path / "loose.py").write_text("x = 1\n")

    assert _paths(tmp_path) == {"loose.py"}
    assert head_sha(tmp_path) == ""


def test_head_sha_is_stamped_for_citations(repo):
    assert len(head_sha(repo)) >= 7


def test_changed_files_drives_incremental_reindex(repo):
    first = head_sha(repo)
    (repo / "src" / "app.py").write_text("def main():\n    return 2\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "edit"],
        cwd=repo,
        check=True,
    )

    assert changed_files(repo, first) == ["src/app.py"]


def test_unknown_sha_reports_failure_rather_than_no_changes(repo):
    """Returning [] would silently skip the reindex instead of falling back."""
    assert changed_files(repo, "0" * 40) is None
