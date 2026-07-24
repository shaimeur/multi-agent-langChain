"""D7 — the sandbox: structured reports, and the hardening that earns them.

Two halves. The parser tests are pure text and always run. The execution tests run
against every backend available on the machine — the fallback always, the container
whenever a Docker socket answers — because the whole point of ``Isolation`` is that
the two are not interchangeable and both have to behave.

The hardening tests are the ones that matter. They assert *containment*, by trying
the thing and checking it was refused: egress, a fork bomb, a write to the root
filesystem, unbounded output, an infinite loop. A sandbox nobody attacked is a
sandbox nobody has evidence for.
"""

from __future__ import annotations

import subprocess

import pytest
from fastapi.testclient import TestClient

from forge.api.main import app
from forge.config import CacheMode, SandboxBackend, Settings
from forge.core.workspace import session_workspace
from forge.models import ExecutionOutcome, Isolation
from forge.sandbox.report import parse_pytest_output, parse_ruff_violations, pytest_outcome
from forge.sandbox.runner import docker_available
from forge.sandbox.tools import make_sandbox_tools, run_linter, run_pytest, run_python

# --- parsing --------------------------------------------------------------

_TRANSCRIPT = """\
============================= test session starts ==============================
collected 4 items

tests/test_calc.py .F.s                                                  [100%]

=================================== FAILURES ===================================
________________________________ test_broken ___________________________________
>       assert add(2, 3) == 5
E       assert -1 == 5
=========================== short test summary info ============================
FAILED tests/test_calc.py::test_broken - assert -1 == 5
1 failed, 2 passed, 1 skipped in 0.11s
"""


def test_parses_counts_and_failing_test_ids():
    counts = parse_pytest_output(_TRANSCRIPT)

    assert (counts.passed, counts.failed, counts.skipped) == (2, 1, 1)
    assert [f.test_id for f in counts.failures] == ["tests/test_calc.py::test_broken"]
    assert counts.failures[0].message == "assert -1 == 5"


def test_parses_the_bare_quiet_summary():
    """`-q` prints no banner; the duration suffix is the only marker."""
    counts = parse_pytest_output("....\n4 passed in 0.02s\n")

    assert counts.passed == 4
    assert counts.failed == 0


def test_parses_collection_errors_as_errors_not_failures():
    text = "ERROR tests/test_x.py - ImportError: no module named nope\n1 error in 0.01s\n"
    counts = parse_pytest_output(text)

    assert counts.errors == 1
    assert [f.test_id for f in counts.failures] == ["tests/test_x.py"]


def test_parses_coverage_total():
    assert parse_pytest_output("TOTAL   120   30   75%\n1 passed in 0.1s").coverage_percent == 75.0


def test_a_truncated_transcript_degrades_to_zero_rather_than_raising():
    """Output is cut at 64 KB, so the summary line can simply be missing."""
    counts = parse_pytest_output("collected 900 items\ntests/test_a.py ......")

    assert (counts.passed, counts.failed, counts.errors) == (0, 0, 0)
    assert counts.failures == []


@pytest.mark.parametrize(
    "exit_code, outcome",
    [
        (0, ExecutionOutcome.PASSED),
        (1, ExecutionOutcome.FAILED),
        (2, ExecutionOutcome.ERROR),  # interrupted
        (4, ExecutionOutcome.ERROR),  # usage error
        (5, ExecutionOutcome.ERROR),  # nothing collected is not a pass
        (None, ExecutionOutcome.TIMEOUT),
    ],
)
def test_exit_code_decides_the_outcome(exit_code, outcome):
    """The exit code is the authority — text can be truncated away, it cannot."""
    assert pytest_outcome(exit_code) is outcome


def test_ruff_violations_parse_into_the_failure_shape():
    violations = parse_ruff_violations(
        "calc.py:1:8: F401 [*] `os` imported but unused\nFound 1 error.\n"
    )

    assert [v.test_id for v in violations] == ["calc.py:1:8"]
    assert violations[0].message.startswith("F401")


# --- execution, on every backend this machine has -------------------------

_BACKENDS = [SandboxBackend.SUBPROCESS] + ([SandboxBackend.DOCKER] if docker_available() else [])


@pytest.fixture
def repo(tmp_path):
    """A tiny git repo: one passing test, one failing, one unused import."""
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "calc.py").write_text("import os\n\n\ndef add(a, b):\n    return a - b\n")
    (root / "tests" / "test_calc.py").write_text(
        "from calc import add\n\n\n"
        "def test_ok():\n    assert add(2, 0) == 2\n\n\n"
        "def test_broken():\n    assert add(2, 3) == 5\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=root,
        check=True,
    )
    return root


@pytest.fixture(params=_BACKENDS, ids=[b.value for b in _BACKENDS])
def settings(request, tmp_path, repo):
    # A 5 s deadline rather than the shipped 60 s: the timeout tests have to
    # actually wait it out, and 60 s x several tests is not a suite anyone runs.
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        target_repo=repo,
        workspace_root=tmp_path / "ws",
        sandbox_backend=request.param,
        sandbox_timeout_s=5,
    )


@pytest.fixture
def workspace(settings, repo):
    with session_workspace("sandbox-test", settings=settings, repo=repo) as ws:
        yield ws


@pytest.fixture
def container_only(settings):
    """Skip the run whose backend is the fallback.

    Not a machine-level "is Docker installed" skip: these four properties — no
    egress, a pid cap, a read-only root, an invisible host filesystem — are things
    only the container provides, and their *absence* in the fallback is precisely
    the gap docs/limitations.md §1 exists to state rather than hide. Asserting them
    against the fallback would be asserting a guarantee FORGE does not make.
    """
    if settings.sandbox_backend is not SandboxBackend.DOCKER:
        pytest.skip("container-only hardening — the fallback does not provide it")


def test_pytest_runs_in_the_sandbox_and_returns_a_structured_report(workspace, settings):
    """D7's DoD, first half. Fields, not a transcript — this is what D8 routes on."""
    report = run_pytest(workspace, settings=settings)

    assert report.outcome is ExecutionOutcome.FAILED
    assert report.exit_code == 1
    assert (report.passed, report.failed) == (1, 1)
    assert [f.test_id for f in report.failures] == ["tests/test_calc.py::test_broken"]
    assert report.duration_s > 0
    assert report.isolation is Isolation(settings.sandbox_backend.value)


def test_a_passing_subset_reports_passed(workspace, settings):
    report = run_pytest(workspace, ["tests/test_calc.py::test_ok"], settings=settings)

    assert report.ok
    assert (report.passed, report.failed) == (1, 0)


def test_coverage_is_reported_when_asked_for(workspace, settings):
    report = run_pytest(
        workspace, ["tests/test_calc.py::test_ok"], coverage=True, settings=settings
    )

    assert report.coverage_percent is not None


def test_linter_returns_violations_as_failures(workspace, settings):
    report = run_linter(workspace, settings=settings)

    assert report.outcome is ExecutionOutcome.FAILED  # a result, not a crash
    assert any(v.message.startswith("F401") for v in report.failures)


def test_pytest_leaves_no_cache_directory_in_the_worktree(workspace, settings):
    """The worktree is what the session's diff is built from — it must stay clean."""
    run_pytest(workspace, settings=settings)

    assert not (workspace.path / ".pytest_cache").exists()


# --- hardening ------------------------------------------------------------


def test_an_infinite_loop_is_killed_at_the_deadline(workspace, settings):
    """D7's DoD, second half."""
    report = run_python(workspace, "while True:\n    pass\n", settings=settings)

    assert report.outcome is ExecutionOutcome.TIMEOUT
    assert report.exit_code is None, "a killed run has no exit status to report"
    assert report.duration_s < 30, "it was stopped at the deadline, not left running"


def test_the_process_survives_a_runaway_and_keeps_serving(workspace, settings):
    """...and the API stays up — the other half of the DoD sentence.

    A sandbox that takes its host down with the runaway has not contained anything,
    so the assertion is that the *next* thing still works: another sandbox run, and
    the health endpoint the compose healthcheck gates on.
    """
    run_python(workspace, "while True:\n    pass\n", settings=settings)

    assert run_python(workspace, "print('alive')", settings=settings).ok
    assert TestClient(app).get("/v1/health").json()["status"] == "ok"


def test_unbounded_output_is_capped_instead_of_read_into_memory(workspace, settings):
    """32 MB written, 500x the cap — the report must not grow with it."""
    report = run_python(
        workspace,
        "import sys\nchunk = 'x' * 4096\nfor _ in range(8192):\n    sys.stdout.write(chunk)\n",
        settings=settings,
    )

    assert len(report.stdout) <= settings.sandbox_max_output_bytes
    assert report.truncated


def test_a_memory_hog_is_refused(workspace, settings):
    """1 GB against a 512 MB cap. Touched page by page so it cannot be lazily mapped."""
    report = run_python(
        workspace,
        "b = bytearray(1024 * 1024 * 1024)\n"
        "b[::4096] = b'x' * (len(b) // 4096)\n"
        "print('ALLOCATED')\n",
        settings=settings,
    )

    assert not report.ok
    assert "ALLOCATED" not in report.stdout


def test_the_sandbox_does_not_inherit_the_provider_api_keys(workspace, settings, monkeypatch):
    """Env inheritance is the leak: this process holds the provider credentials.

    Asserted on both backends — the fallback shares the host filesystem, so keeping
    the credentials out of its environment is the one thing it can still do.
    """
    monkeypatch.setenv("GOOGLE_API_KEY", "sentinel-must-not-reach-the-sandbox")

    report = run_python(
        workspace,
        "import os\n"
        "print('GOOGLE_API_KEY' in os.environ, 'sentinel' in ''.join(os.environ.values()))\n",
        settings=settings,
    )

    assert report.ok
    assert report.stdout.strip() == "False False"


def test_network_egress_is_refused(container_only, workspace, settings):
    report = run_python(
        workspace,
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "    print('EGRESS-OK')\n"
        "except OSError:\n"
        "    print('EGRESS-REFUSED')\n",
        settings=settings,
    )

    assert "EGRESS-REFUSED" in report.stdout
    assert "EGRESS-OK" not in report.stdout


def test_a_fork_bomb_is_contained_by_the_pid_cap(container_only, workspace, settings):
    """It never gets past 128 processes, and the host is still usable afterwards."""
    report = run_python(
        workspace,
        "import os\nwhile True:\n    try:\n        os.fork()\n    except OSError:\n        pass\n",
        settings=settings,
    )

    assert report.outcome in (ExecutionOutcome.TIMEOUT, ExecutionOutcome.FAILED)
    assert report.duration_s < 30
    assert run_python(workspace, "print('alive')", settings=settings).ok


def test_the_root_filesystem_is_read_only(container_only, workspace, settings):
    report = run_python(
        workspace,
        "try:\n"
        "    open('/usr/local/bin/backdoor', 'w').write('x')\n"
        "    print('WROTE')\n"
        "except OSError:\n"
        "    print('REFUSED')\n",
        settings=settings,
    )

    assert "REFUSED" in report.stdout


def test_it_runs_unprivileged_and_sees_only_the_worktree(container_only, workspace, settings, repo):
    report = run_python(
        workspace,
        "import os\nprint('uid', os.getuid())\nprint('host_repo', os.path.exists("
        f"{str(repo)!r}))\nprint('cwd', os.getcwd())\n",
        settings=settings,
    )

    assert "uid 0" not in report.stdout, "the sandbox must never run as root"
    assert "host_repo False" in report.stdout, "no host path other than the worktree"
    assert "cwd /work" in report.stdout


def test_the_worktree_is_writable_so_tests_can_actually_run(container_only, workspace, settings):
    """The confinement is worthless if it also stops the work — /work must be rw."""
    report = run_python(
        workspace, "open('scratch.txt', 'w').write('ok')\nprint('WROTE')\n", settings=settings
    )

    assert "WROTE" in report.stdout
    assert (workspace.path / "scratch.txt").read_text() == "ok"


# --- the agent-facing surface ---------------------------------------------


def test_targets_that_escape_the_worktree_are_refused(workspace, settings):
    for escape in ("../../etc/passwd", "/etc/passwd", "src/../../outside.py"):
        with pytest.raises(ValueError):
            run_pytest(workspace, [escape], settings=settings)


def test_a_flag_is_not_a_target(workspace, settings):
    """`--rootdir=/` would let a model choose what the sandbox executes."""
    for flag in ("--rootdir=/", "-p", "--no-header"):
        with pytest.raises(ValueError):
            run_pytest(workspace, [flag], settings=settings)


def test_the_langchain_tools_return_reports_never_prose(workspace, settings):
    tools = make_sandbox_tools(workspace, settings=settings)

    assert [t.name for t in tools] == ["run_pytest", "run_python", "run_linter"]
    report = next(t for t in tools if t.name == "run_pytest").invoke({"targets": []})
    assert report.outcome is ExecutionOutcome.FAILED
    assert report.failed == 1


def test_a_rejected_target_comes_back_as_a_report_not_an_exception(workspace, settings):
    """A raise here would kill the graph turn and lose the run's context."""
    tools = make_sandbox_tools(workspace, settings=settings)

    report = next(t for t in tools if t.name == "run_pytest").invoke({"targets": ["/etc/passwd"]})

    assert report.outcome is ExecutionOutcome.ERROR
    assert "escapes the worktree" in report.stderr
