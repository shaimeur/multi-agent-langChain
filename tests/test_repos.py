"""D15b Tier 2 — choosing the target repository from the browser, safely.

The feature is a convenience; the tests are about the security property underneath it.
``settings.target_repo`` is the confinement root for ``read_file``/``list_files``, so a
route that took a path and used it would let a browser choose what the sandbox may
read — exactly the code path §8.3 claims does not exist.

The design that keeps §8.3 true is *the browser selects, it never supplies*: the server
enumerates, and a value is accepted only if it equals an entry in a freshly recomputed
enumeration. So the load-bearing tests here are the refusals, and the one that matters
most is ``test_a_traversal_outside_the_roots_is_refused_and_logged`` — it is the same
attack as the ``policy.path_escape`` case in ``evals/security``, arriving through a new
front door.
"""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from forge.api.main import app, reset_resources
from forge.api.repos import NotSelectable, list_repos, resolve_selection
from forge.config import CacheMode, Settings


def _git_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "pkg").mkdir(exist_ok=True)
    (root / "pkg" / "core.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=root,
        check=True,
    )
    return root


@pytest.fixture
def workspace(tmp_path):
    """Two selectable repos under one root, plus a secret outside it."""
    roots = tmp_path / "repos"
    alpha = _git_repo(roots / "alpha")
    bravo = roots / "bravo"  # source, but no git — ask works, fix cannot
    (bravo / "src").mkdir(parents=True)
    (bravo / "src" / "thing.py").write_text("VALUE = 2\n")
    (roots / "notsource").mkdir()
    (roots / "notsource" / "photo.jpeg").write_bytes(b"\x00\x01")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secrets.py").write_text("API_KEY = 'leaked'\n")

    return {"roots": roots, "alpha": alpha, "bravo": bravo, "outside": outside}


@pytest.fixture
def settings(tmp_path, workspace):
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        embedding_model="hashing",
        qdrant_url="",
        qdrant_path=tmp_path / "qdrant",
        target_repo=workspace["alpha"],
        checkpoint_db=tmp_path / "cp.sqlite",
        repo_roots=str(workspace["roots"]),
    )


# --- enumeration -----------------------------------------------------------


def test_only_source_directories_under_the_roots_are_offered(settings, workspace):
    names = {o.name for o in list_repos(settings)}

    assert names == {"alpha", "bravo"}
    assert "notsource" not in names, "a directory with no indexable files is not a repo"
    assert "outside" not in names, "outside the configured roots is not selectable"


def test_the_listing_says_which_repos_can_be_patched(settings):
    by_name = {o.name: o for o in list_repos(settings)}

    assert by_name["alpha"].is_git is True
    assert by_name["bravo"].is_git is False, "no git means no worktree, so no `forge fix`"
    assert by_name["alpha"].is_current is True


def test_the_current_repo_is_always_offered_even_from_outside_the_roots(tmp_path, workspace):
    """Otherwise a deployment whose roots exclude its own target shows no way back."""
    settings = Settings(
        _env_file=None,
        target_repo=workspace["outside"],
        repo_roots=str(workspace["roots"]),
        checkpoint_db=tmp_path / "cp.sqlite",
    )

    current = [o for o in list_repos(settings) if o.is_current]

    assert len(current) == 1
    assert current[0].name == "outside"


def test_roots_default_to_the_parent_of_the_current_target(tmp_path, workspace):
    """A stock install reaches exactly the one directory it already exposes."""
    settings = Settings(
        _env_file=None,
        target_repo=workspace["alpha"],
        repo_roots="",
        checkpoint_db=tmp_path / "cp.sqlite",
    )

    assert {o.name for o in list_repos(settings)} == {"alpha", "bravo"}


# --- validation: the security property --------------------------------------


def test_an_enumerated_repo_resolves(settings, workspace):
    assert resolve_selection(str(workspace["bravo"]), settings) == workspace["bravo"].resolve()


@pytest.mark.parametrize(
    "attack",
    [
        "/etc",
        "../../../../etc/passwd",
        "{outside}",
        "{roots}/alpha/../../outside",
        "{roots}/notsource",
        "{roots}",
    ],
)
def test_anything_not_in_the_enumeration_is_refused(settings, workspace, attack):
    """Traversal is refused because it is not in the list — no `..` parsing required.

    That is the point of comparing against an enumeration rather than writing a
    containment check: the cases a hand-rolled check gets subtly wrong (symlinks, `..`
    after a valid prefix, the root itself) are all simply absent from the list.
    """
    path = attack.format(outside=workspace["outside"], roots=workspace["roots"])

    with pytest.raises(NotSelectable):
        resolve_selection(path, settings)


def test_a_symlink_into_the_roots_does_not_launder_an_outside_path(settings, workspace):
    """The enumeration alone was not enough, and this test is why.

    A link inside a root pointing outside it was offered as selectable — the listing
    resolved it and handed back the outside path. Creating the link needs local write
    access, which is a stronger position than "can use the browser", but the claim
    this module makes is *only what is under the roots*, and that claim has to be
    true. Containment is now checked on the resolved path.
    """
    (workspace["roots"] / "sneaky").symlink_to(workspace["outside"])

    resolved = {o.path for o in list_repos(settings)}

    assert str(workspace["outside"].resolve()) not in resolved
    with pytest.raises(NotSelectable):
        resolve_selection(str(workspace["roots"] / "sneaky"), settings)


def test_a_repo_named_like_a_build_directory_is_still_selectable(tmp_path, workspace):
    """The bug that only a real browser found, and it was a one-way door.

    ``walker.SKIP_DIRS`` prunes directories *inside* a repository being walked, so it
    lists build output — ``build``, ``dist``, and ``target``. Filtering *candidate
    repositories* through it excluded FORGE's own demo repo, which lives at
    ``data/target``. It still appeared, but only via the "always offer the current
    one" exemption — so switching away from it worked and switching back returned
    "not a selectable repository". Unit tests missed it because their fixtures were
    called alpha and bravo.
    """
    roots = tmp_path / "roots"
    for name in ("target", "build", "dist"):
        _git_repo(roots / name)

    settings = Settings(
        _env_file=None,
        target_repo=roots / "build",  # deliberately NOT `target`, so no exemption
        repo_roots=str(roots),
        checkpoint_db=tmp_path / "cp.sqlite",
    )

    assert {o.name for o in list_repos(settings)} == {"target", "build", "dist"}
    assert resolve_selection(str(roots / "target"), settings) == (roots / "target").resolve()


def test_forge_s_own_working_directories_are_never_offered(tmp_path, workspace):
    """With the default roots these are siblings of the target.

    ``workspaces/`` holds live session worktrees and is full of Python, so it looks
    exactly like a repository to the source check. Selecting it would make every
    session's worktree readable through the file tools, since the target repo is what
    confines them.
    """
    roots = workspace["roots"]
    worktrees = roots / "workspaces"
    _git_repo(worktrees / "session-a")

    settings = Settings(
        _env_file=None,
        target_repo=workspace["alpha"],
        repo_roots=str(roots),
        workspace_root=worktrees,
        qdrant_path=roots / "qdrant",
        checkpoint_db=tmp_path / "cp.sqlite",
    )

    assert "workspaces" not in {o.name for o in list_repos(settings)}
    with pytest.raises(NotSelectable):
        resolve_selection(str(worktrees), settings)


# --- the route --------------------------------------------------------------


@pytest.fixture
def api(settings, monkeypatch, tmp_path, workspace):
    from forge.config import get_settings
    from forge.guardrails import events as events_module

    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "wired.sqlite"))
    monkeypatch.setenv("TARGET_REPO", str(workspace["alpha"]))
    monkeypatch.setenv("REPO_ROOTS", str(workspace["roots"]))
    get_settings.cache_clear()
    events_module.reset_log()
    reset_resources()
    yield TestClient(app)
    reset_resources()
    events_module.reset_log()
    get_settings.cache_clear()


def test_the_repos_route_lists_what_may_be_selected(api):
    response = api.get("/v1/repos")

    assert response.status_code == 200
    assert {r["name"] for r in response.json()} == {"alpha", "bravo"}


def test_switching_the_target_takes_effect(api, workspace):
    response = api.post("/v1/target", json={"path": str(workspace["bravo"])})

    assert response.status_code == 200
    body = response.json()
    assert body["target_repo"] == str(workspace["bravo"].resolve())
    # And the listing agrees afterwards — the switch is real, not just reported.
    assert [r for r in api.get("/v1/repos").json() if r["is_current"]][0]["name"] == "bravo"


def test_switching_reports_that_nothing_is_indexed_for_the_new_repo(api, workspace):
    """A silent switch would answer questions from the previous repo's chunks."""
    body = api.post("/v1/target", json={"path": str(workspace["bravo"])}).json()

    assert body["indexed"] is False


def test_a_traversal_outside_the_roots_is_refused_and_logged(api, workspace):
    """The §8.3 invariant, through the new front door: a 400 and a §8.5 event."""
    from forge.config import get_settings
    from forge.guardrails.events import get_log

    response = api.post("/v1/target", json={"path": str(workspace["outside"])})

    assert response.status_code == 400
    assert "not a selectable repository" in response.json()["detail"]

    rules = {e.rule for e in get_log(get_settings()).events()}
    assert "policy.target_denied" in rules, "a refusal nobody can query is not auditable"
    # …and the target really did not move.
    assert get_settings().target_repo == workspace["alpha"]


def test_an_accepted_switch_is_logged_too(api, workspace):
    """`allowed` is logged as deliberately as `blocked` — §8.5."""
    from forge.config import get_settings
    from forge.guardrails.events import get_log

    api.post("/v1/target", json={"path": str(workspace["bravo"])})

    rules = {e.rule for e in get_log(get_settings()).events()}
    assert "policy.target_switch" in rules
