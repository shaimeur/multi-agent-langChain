"""Hardened execution of model-written code — cahier §8.3.

``runner.py`` is the confinement (container first, documented fallback second),
``report.py`` turns raw output into structured fields, ``tools.py`` is what the
SANDBOX_ENGINEER actually calls. Security-sensitive: see CLAUDE.md before changing
a container flag, a resource cap or a path allowlist.
"""

from __future__ import annotations

from forge.sandbox.runner import (
    SandboxUnavailable,
    active_isolation,
    docker_available,
    run_in_sandbox,
)
from forge.sandbox.tools import make_sandbox_tools, run_linter, run_pytest, run_python

__all__ = [
    "SandboxUnavailable",
    "active_isolation",
    "docker_available",
    "make_sandbox_tools",
    "run_in_sandbox",
    "run_linter",
    "run_pytest",
    "run_python",
]
