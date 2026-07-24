"""Fixtures and the pass-rate report for the adversarial suite.

The headline number is derived from the pytest run itself rather than kept in a
counter somebody has to update: every test is tagged with its attack id, the terminal
summary pairs outcomes back to ``attacks.ATTACKS``, and what gets printed is what
actually happened. A hand-maintained "24/25" on a slide is exactly the kind of claim
this project exists not to make.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.security.attacks import BY_ID, deviations
from forge.config import PROJECT_ROOT, CacheMode, Settings
from forge.guardrails.events import GuardrailLog

RESULTS = PROJECT_ROOT / "evals" / "results" / "security.json"


@pytest.fixture
def log(tmp_path) -> GuardrailLog:
    """An isolated event log, so one case cannot see another's events."""
    return GuardrailLog(tmp_path / "security-events.sqlite")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        workspace_root=tmp_path / "ws",
        checkpoint_db=tmp_path / "security-events.sqlite",
        # Short, because two cases have to sit through it.
        sandbox_timeout_s=5,
    )


@pytest.fixture
def workspace_dir(tmp_path) -> Path:
    root = tmp_path / "worktree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("x = 1\n")
    return root


def _attack_id(item) -> str:
    """The SEC-NN this test covers, from its ``attack`` marker."""
    marker = item.get_closest_marker("attack")
    return marker.args[0] if marker else ""


def pytest_configure(config):
    config.addinivalue_line("markers", "attack(id): the §13.4 case this test covers")


def pytest_collection_modifyitems(config, items):
    """Map nodeid → attack id at collection.

    A ``TestReport`` carries no markers — ``own_markers`` lives on the ``Item`` — so
    the association has to be captured here, while the items still exist, and looked
    up by nodeid afterwards.
    """
    config.stash_attack_ids = {item.nodeid: _attack_id(item) for item in items}


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Report "N of M attacks mitigated", and name every case that was not."""
    by_nodeid = getattr(config, "stash_attack_ids", {})
    outcomes: dict[str, str] = {}
    for outcome in ("passed", "failed", "skipped"):
        for report in terminalreporter.stats.get(outcome, []):
            # A skip raised at setup reports with when="setup"; a real result is a call.
            if getattr(report, "when", None) not in ("call", "setup"):
                continue
            attack_id = by_nodeid.get(report.nodeid, "")
            # A call result supersedes a setup one for the same test.
            if attack_id and (outcome != "skipped" or attack_id not in outcomes):
                outcomes[attack_id] = outcome

    covered = {k: v for k, v in outcomes.items() if k in BY_ID}
    if not covered:
        return

    mitigated = sorted(k for k, v in covered.items() if v == "passed")
    breached = sorted(k for k, v in covered.items() if v == "failed")
    untested = sorted(k for k, v in covered.items() if v == "skipped")
    total = len(covered) - len(untested)

    write = terminalreporter.write_line
    write("")
    write("=" * 62)
    write(f"  ADVERSARIAL SUITE (cahier §13.4) — {len(mitigated)}/{total} attacks mitigated")
    write("=" * 62)
    for attack_id in breached:
        write(f"  BREACHED  {attack_id}  {BY_ID[attack_id].title}")
    for attack_id in untested:
        write(f"  not run   {attack_id}  {BY_ID[attack_id].title} (needs Docker)")
    for attack in deviations():
        if attack.attack_id in covered:
            write(f"  deviation {attack.attack_id}  §13.4 expects: {attack.cahier_expected}")
            write(f"                     FORGE does:     {attack.expected}")
    if not breached:
        write("  No breaches. Deviations above are deliberate — docs/limitations.md §6.")
    write("=" * 62)

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(
        json.dumps(
            {
                "total_run": total,
                "mitigated": len(mitigated),
                "breached": breached,
                "not_run": untested,
                "deviations": [a.attack_id for a in deviations() if a.attack_id in covered],
            },
            indent=2,
        )
    )
