"""Which repositories the UI may point FORGE at — D15b, Tier 2.

The feature is "choose the target repository from the browser". The danger is that
``settings.target_repo`` is the **confinement root** for the ``read_file`` and
``list_files`` tools (``tools/registry._root_for``), so a route that accepted a path
and used it would be handing a browser the ability to choose what the sandbox may
read. That is precisely the code path §8.3 claims does not exist.

So this module is built around one rule, and the rule is the feature:

    **The browser selects. It never supplies.**

``list_repos`` enumerates, server-side, the directories under the configured roots.
``resolve_selection`` takes the string a client sent and accepts it *only* if its
``realpath`` equals the ``realpath`` of something in that enumeration — recomputed on
every request, never cached, never trusted from the client. A traversal like
``../../../../etc`` cannot match an enumerated entry, so it is refused without the
validator ever needing to reason about ``..`` at all. That is the same shape as
``guardrails.policy.check_path``: resolve first, compare second, and let containment
be a property of the filesystem rather than of string parsing.

The roots themselves come from ``REPO_ROOTS`` — deployment configuration, not user
input. Empty defaults to the parent of the current target, which keeps a stock
install reaching exactly one directory it already exposes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from forge.config import Settings, get_settings
from forge.rag.walker import LANGUAGES, SKIP_DIRS

_NEVER_A_REPO = frozenset(
    {".git", ".hg", ".svn", "node_modules", "__pycache__", "site-packages", ".venv", "venv"}
)
"""Directory names that are never a project you would target.

Deliberately **not** ``walker.SKIP_DIRS``, which is a different question with a
different answer. That list exists to prune directories *inside a repository being
walked*, so it contains build-output names — ``build``, ``dist``, and ``target``.
Reusing it here excluded FORGE's own demo repository, which lives at ``data/target``:
the picker offered it only through the "always offer the current one" exemption, so
you could switch away from it and then had no way back. A top-level directory an
operator pointed ``REPO_ROOTS`` at is a candidate whatever it happens to be called.
"""

_SCAN_LIMIT = 300
"""Entries examined per directory when deciding "does this look like source". A cap,
not a heuristic: `node_modules` alone can make an unbounded walk take seconds, and this
runs on a route a UI polls."""


@dataclass(frozen=True)
class RepoOption:
    """One selectable repository."""

    name: str
    path: str
    """Absolute, already realpath-resolved — the value a client sends back."""
    is_git: bool
    """Without git there are no worktrees, so `forge fix` cannot run on it. Ask can."""
    is_current: bool


def allowed_roots(settings: Settings | None = None) -> list[Path]:
    """The configured roots, resolved and filtered to those that exist."""
    settings = settings or get_settings()
    if settings.repo_roots.strip():
        candidates = [Path(p) for p in settings.repo_roots.split(os.pathsep) if p.strip()]
    else:
        candidates = [Path(settings.target_repo).resolve().parent]
    roots: list[Path] = []
    for root in candidates:
        resolved = Path(os.path.realpath(str(root)))
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)
    return roots


def _contained(resolved: str, roots: list[Path]) -> bool:
    """Is ``resolved`` at or beneath one of ``roots``? Both sides already realpath'd.

    The enumeration alone is not sufficient, and a test proved it: a symlink placed
    inside a root points at a directory outside it, and listing the root would then
    offer that outside directory as selectable. Creating the link needs local write
    access — a far stronger position than "can use the browser" — but the property
    this module claims is *"only what is under the roots"*, and without this check
    that sentence is false. Same shape as ``guardrails.policy.check_path``: compare
    real paths, and let containment be a fact about the filesystem.
    """
    for root in roots:
        base = str(root)
        if resolved == base or resolved.startswith(base + os.sep):
            return True
    return False


def _managed_paths(settings: Settings) -> set[str]:
    """FORGE's own working directories, which are never a target repository.

    With the default roots (the parent of ``target_repo``, i.e. ``data/``) these are
    siblings of the real target: ``workspaces/`` holds live session worktrees and does
    contain Python, so without this it would be offered as a repository — and
    selecting it would make every session's worktree readable through the file tools.
    """
    return {
        os.path.realpath(str(p))
        for p in (settings.workspace_root, settings.qdrant_path, settings.fixtures_dir)
        if p
    }


def _looks_like_source(directory: Path) -> bool:
    """A git checkout, or something with indexable files in its top two levels.

    Bounded on purpose — see ``_SCAN_LIMIT``. Two levels because a great many projects
    have no source at the top at all, only ``src/`` and a README.
    """
    if (directory / ".git").exists():
        return True
    try:
        for child in islice(directory.iterdir(), _SCAN_LIMIT):
            if child.is_file() and child.suffix.lower() in LANGUAGES:
                return True
            if child.is_dir() and child.name not in SKIP_DIRS and not child.name.startswith("."):
                for grandchild in islice(child.iterdir(), _SCAN_LIMIT):
                    if grandchild.is_file() and grandchild.suffix.lower() in LANGUAGES:
                        return True
    except OSError:
        return False
    return False


def list_repos(settings: Settings | None = None) -> list[RepoOption]:
    """Every selectable repository, recomputed from the filesystem.

    Recomputed rather than cached because this list is the authority
    ``resolve_selection`` checks against: a stale copy would be a permission that
    outlived the directory it referred to.
    """
    settings = settings or get_settings()
    current = os.path.realpath(str(settings.target_repo))
    roots = allowed_roots(settings)
    managed = _managed_paths(settings)
    options: dict[str, RepoOption] = {}

    for root in roots:
        try:
            entries = sorted(islice(root.iterdir(), _SCAN_LIMIT), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or entry.name in _NEVER_A_REPO or entry.name.startswith("."):
                continue
            resolved = os.path.realpath(str(entry))
            if resolved in options or resolved in managed:
                continue
            # Containment is checked on the *resolved* path, so a symlink out of the
            # root is dropped here rather than being offered under an innocent name.
            if not _contained(resolved, roots) or not _looks_like_source(Path(resolved)):
                continue
            options[resolved] = RepoOption(
                name=entry.name,
                path=resolved,
                is_git=(Path(resolved) / ".git").exists(),
                is_current=(resolved == current),
            )

    # The repository in use is always offered, even when it sits outside the roots —
    # otherwise a deployment whose REPO_ROOTS does not contain its own target shows a
    # picker with no way back to where it started.
    if current not in options and Path(current).is_dir():
        options[current] = RepoOption(
            name=Path(current).name,
            path=current,
            is_git=(Path(current) / ".git").exists(),
            is_current=True,
        )
    return sorted(options.values(), key=lambda o: o.name)


class NotSelectable(ValueError):
    """The client sent a path that is not in the server's enumeration."""


def resolve_selection(path: str, settings: Settings | None = None) -> Path:
    """The client's string → a vetted path, or ``NotSelectable``.

    Equality against a freshly enumerated list, on ``realpath`` both sides. Nothing
    here parses ``..``, strips prefixes or compares string prefixes — a traversal is
    refused because it is not in the list, which is a much harder thing to get subtly
    wrong than a containment check written by hand.
    """
    wanted = os.path.realpath(path.strip())
    for option in list_repos(settings):
        if option.path == wanted:
            return Path(wanted)
    raise NotSelectable(f"{path!r} is not a selectable repository")


__all__ = ["NotSelectable", "RepoOption", "allowed_roots", "list_repos", "resolve_selection"]
