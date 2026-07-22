"""Repository walk — decides what is worth indexing at all.

Cahier §6.1. Getting this wrong is expensive in both directions: indexing
`node_modules` buries the signal, and skipping a source directory makes the
assistant confidently wrong about a codebase it never read.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# Extension -> language tag used for metadata filtering at query time.
LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".md": "markdown",
    ".rst": "markdown",
    ".toml": "config",
    ".cfg": "config",
    ".ini": "config",
    ".yaml": "config",
    ".yml": "config",
    ".json": "config",
}

# Directories that are never source, whatever the ignore files say.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "dist",
        "build",
        "target",
        ".tox",
        ".nox",
        "site-packages",
        ".idea",
        ".vscode",
        "htmlcov",
        ".next",
    }
)

# Machine-generated or machine-only files. Lockfiles are the interesting case:
# they are text, they are tracked, and they are pure noise for retrieval.
SKIP_NAMES = frozenset(
    {
        "uv.lock",
        "poetry.lock",
        "package-lock.json",
        "yarn.lock",
        "Pipfile.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "composer.lock",
        ".DS_Store",
    }
)

MAX_FILE_BYTES = 512_000
"""Past this a source file is generated, vendored, or a data blob."""

FORGEIGNORE = ".forgeignore"


@dataclass(frozen=True)
class SourceFile:
    path: Path
    """Absolute."""
    rel_path: str
    """Repo-relative, POSIX separators — this is what citations use."""
    language: str
    text: str


def _git(repo: Path, *args: str) -> str | None:
    """Run a git command, or return None when this is not a usable repo."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def head_sha(repo: Path) -> str:
    """Short HEAD sha, or "" outside a repo. Stamped onto every chunk."""
    out = _git(repo, "rev-parse", "--short", "HEAD")
    return out.strip() if out else ""


def changed_files(repo: Path, since_sha: str) -> list[str] | None:
    """Repo-relative paths touched since ``since_sha``, for incremental reindex.

    None means the diff could not be computed — an unknown sha, a shallow clone,
    no git at all — and the caller must fall back to a full walk. Returning an
    empty list instead would silently skip reindexing entirely.
    """
    out = _git(repo, "diff", "--name-only", f"{since_sha}..HEAD")
    if out is None:
        return None
    return [line for line in out.splitlines() if line]


def _tracked_files(repo: Path) -> list[str] | None:
    """Tracked plus untracked-not-ignored, straight from git.

    Delegating to git is what makes `.gitignore` support correct rather than a
    reimplementation of its precedence rules that is wrong in the corner cases.
    """
    out = _git(repo, "ls-files", "--cached", "--others", "--exclude-standard")
    if out is None:
        return None
    return [line for line in out.splitlines() if line]


def _load_forgeignore(repo: Path) -> list[str]:
    """Extra prefixes/suffixes to skip, one per line. Comments with #."""
    path = repo / FORGEIGNORE
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _excluded_by_forgeignore(rel_path: str, patterns: list[str]) -> bool:
    return any(
        rel_path == pat or rel_path.startswith(pat.rstrip("/") + "/") or rel_path.endswith(pat)
        for pat in patterns
    )


def _is_indexable(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    if any(part in SKIP_DIRS for part in parts):
        return False
    name = Path(rel_path).name
    if name in SKIP_NAMES:
        return False
    if ".min." in name:
        return False
    return Path(rel_path).suffix.lower() in LANGUAGES


def _read_text(path: Path) -> str | None:
    """Decoded contents, or None when the file is binary or unreadable.

    A NUL byte is the pragmatic binary test: it cannot appear in valid UTF-8
    text and catches the compiled artefacts an extension allowlist misses.
    """
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def walk_repo(repo: Path, *, only: list[str] | None = None) -> Iterator[SourceFile]:
    """Yield every indexable source file under ``repo``.

    ``only`` restricts the walk to specific repo-relative paths, which is how an
    incremental reindex avoids rewalking a tree it already knows.
    """
    repo = repo.resolve()
    patterns = _load_forgeignore(repo)

    if only is not None:
        candidates = only
    else:
        tracked = _tracked_files(repo)
        candidates = (
            tracked
            if tracked is not None
            else [str(p.relative_to(repo).as_posix()) for p in repo.rglob("*") if p.is_file()]
        )

    for rel_path in sorted(set(candidates)):
        if not _is_indexable(rel_path) or _excluded_by_forgeignore(rel_path, patterns):
            continue
        path = repo / rel_path
        if not path.is_file():
            # Deleted between `git ls-files` and now, or listed by a stale diff.
            continue
        text = _read_text(path)
        if text is None or not text.strip():
            continue
        yield SourceFile(
            path=path,
            rel_path=rel_path,
            language=LANGUAGES[Path(rel_path).suffix.lower()],
            text=text,
        )
