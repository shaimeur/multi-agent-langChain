"""C1 — specialised agents with genuinely distinct responsibilities.

Counting files proves nothing: six modules that share a prompt, a tool set and an
output schema are one agent invoked six times. So the ``distinct`` tests below assert
the three things that actually make an agent a separate agent, and they read the real
constants through ``ROSTER`` rather than a description someone typed once.

C1's proof command is ``uv run pytest tests/test_agents.py -k distinct``.
"""

from __future__ import annotations

import importlib

import pytest

from forge.config import LLMRole
from forge.core.agents import BY_NAME, ROSTER

# The cahier §4 roster, minus SENTINEL — §4/S is explicit that the sentinel is a
# deterministic guard layer and *not* a conversational agent, so it is not one here.
CAHIER_AGENTS = {
    "SUPERVISOR",
    "RETRIEVER",
    "PLANNER",
    "EDITOR",
    "SANDBOX_ENGINEER",
    "REVIEWER",
}


def test_the_roster_matches_the_cahier():
    assert {spec.name for spec in ROSTER} == CAHIER_AGENTS


def test_at_least_four_specialised_agents():
    """C1's floor is four. There are six."""
    assert len(ROSTER) >= 4


def test_agents_have_distinct_responsibilities():
    responsibilities = [spec.responsibility for spec in ROSTER]

    assert len(set(responsibilities)) == len(ROSTER)
    assert all(r.strip() for r in responsibilities)


def test_agents_have_distinct_system_prompts():
    """Two agents with the same prompt are one agent with two names."""
    prompts = [spec.system_prompt for spec in ROSTER if spec.system_prompt is not None]

    assert len(set(prompts)) == len(prompts)
    assert len(prompts) >= 4, "at least four agents actually instruct a model"


def test_agents_have_distinct_output_schemas():
    """§5.4: typed payloads, not prose, cross an agent boundary."""
    schemas = [spec.output_schema for spec in ROSTER]

    assert len(set(schemas)) == len(ROSTER)


def test_agents_have_distinct_tool_sets():
    """Tool sets may be empty — a reasoner needs none — but no two may be the same
    non-empty set, which would mean two agents doing the same job on the same data."""
    tool_sets = [frozenset(spec.tools) for spec in ROSTER if spec.tools]

    assert len(set(tool_sets)) == len(tool_sets)
    assert tool_sets, "at least one agent is tool-using"


def test_distinct_model_tiers_are_actually_used():
    """§12.2 splits the roles to control quota; a roster that only ever asked for one
    tier would make that split decorative."""
    roles = {spec.llm_role for spec in ROSTER if spec.llm_role is not None}

    assert roles == {LLMRole.ROUTER, LLMRole.REASONER, LLMRole.CODER}


# --- the roster describes the code, not a wish ----------------------------


@pytest.mark.parametrize("spec", ROSTER, ids=[s.name for s in ROSTER])
def test_every_agent_module_imports_and_exposes_a_node_factory(spec):
    module = importlib.import_module(spec.module)

    factories = [name for name in dir(module) if name.startswith("make_") and name.endswith("node")]
    assert factories, f"{spec.module} exposes no node factory"


@pytest.mark.parametrize("spec", ROSTER, ids=[s.name for s in ROSTER])
def test_a_prompted_agent_really_uses_its_prompt(spec):
    """Guards against the roster drifting into fiction: the prompt in the spec must
    be the module's own constant, not a copy that stopped matching."""
    if spec.system_prompt is None:
        pytest.skip(f"{spec.name} is deterministic — no prompt by design")

    module = importlib.import_module(spec.module)
    assert getattr(module, "_SYSTEM", None) is spec.system_prompt


def test_the_retriever_is_deterministic_by_design():
    """Not an oversight — descope §8.2 argues the query rewrite off for symbols."""
    assert BY_NAME["RETRIEVER"].llm_role is None
    assert BY_NAME["RETRIEVER"].tools, "it is tool-driven instead"
