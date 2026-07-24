"""The D8 repair benchmark: four seeded bugs in the pinned target repo.

``bugs.py`` is the manifest, ``harness.py`` seeds and grades. Run it with
``uv run python evals/run_swe_mini.py --verify`` (no model needed) or without
``--verify`` to put the repair loop against it.
"""

from __future__ import annotations

from evals.swe_mini.bugs import BUGS, SeededBug, by_id
from evals.swe_mini.harness import BugResult, grade, seed, verify

__all__ = ["BUGS", "BugResult", "SeededBug", "by_id", "grade", "seed", "verify"]
