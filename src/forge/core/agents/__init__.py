"""The agent nodes (cahier §4), and the roster that describes them.

Each agent is a small factory that closes over its injected dependencies — the LLM,
the retrieval resources — and returns a plain ``node(state) -> update`` callable the
graph wires together.

``ROSTER`` is the same set as data. It exists because C1 asks for *specialised agents
with distinct responsibilities*, and the honest way to check that is to assert the
distinctness rather than to count files: two "agents" sharing a prompt, a tool set and
an output schema are one agent invoked twice, however many modules they occupy.

The entries hold **references to the real constants**, not copies. If someone changes
the EDITOR's prompt the roster changes with it and the test still means something — a
hand-written description would have drifted into fiction by D15.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.config import LLMRole
from forge.core.agents import editor, planner, reviewer, supervisor, tester
from forge.models import (
    ChangePlan,
    ContextPack,
    GeneratedTest,
    PatchSet,
    ReviewJudgement,
    RouteDecision,
)


@dataclass(frozen=True)
class AgentSpec:
    """One agent's identity: what it decides, on what evidence, in what shape."""

    name: str
    """The cahier §4 identifier — SUPERVISOR, RETRIEVER, PLANNER, ..."""
    module: str
    responsibility: str
    output_schema: type
    """The typed payload it emits. Prose crossing an agent boundary is what §5.4
    forbids, so every agent has one."""
    llm_role: LLMRole | None
    """None for a deterministic agent. The RETRIEVER is one — a design decision
    (descope §8.2), not an omission."""
    system_prompt: str | None
    tools: tuple[str, ...] = field(default=())


ROSTER: tuple[AgentSpec, ...] = (
    AgentSpec(
        name="SUPERVISOR",
        module="forge.core.agents.supervisor",
        responsibility="Routing and budget only — never content.",
        output_schema=RouteDecision,
        llm_role=LLMRole.ROUTER,
        system_prompt=supervisor._SYSTEM,
    ),
    AgentSpec(
        name="RETRIEVER",
        module="forge.core.agents.retriever",
        responsibility="All knowledge access: hybrid search, ripgrep, AST symbol lookup.",
        output_schema=ContextPack,
        # Deliberately no LLM: descope §8.2 argues that rewriting a literal symbol
        # query costs latency and *degrades* precision versus feeding it to ripgrep.
        llm_role=None,
        system_prompt=None,
        tools=("hybrid_search", "ripgrep_search", "find_definitions", "find_references"),
    ),
    AgentSpec(
        name="PLANNER",
        module="forge.core.agents.planner",
        responsibility="A citation-backed ChangePlan; a step that cites nothing is rejected.",
        output_schema=ChangePlan,
        llm_role=LLMRole.REASONER,
        system_prompt=planner._SYSTEM,
    ),
    AgentSpec(
        name="EDITOR",
        module="forge.core.agents.editor",
        responsibility="One plan step to a validated PatchSet. Never writes to disk.",
        output_schema=PatchSet,
        llm_role=LLMRole.CODER,
        system_prompt=editor._SYSTEM,
        tools=("apply_patch_dryrun", "build_diff"),
    ),
    AgentSpec(
        name="SANDBOX_ENGINEER",
        module="forge.core.agents.tester",
        responsibility="Writes the failing regression test, then runs everything in the sandbox.",
        output_schema=GeneratedTest,
        llm_role=LLMRole.CODER,
        system_prompt=tester._SYSTEM,
        tools=("run_pytest", "run_python", "run_linter"),
    ),
    AgentSpec(
        name="REVIEWER",
        module="forge.core.agents.reviewer",
        responsibility="The five-point quality barrier; three points never reach a model.",
        output_schema=ReviewJudgement,
        llm_role=LLMRole.REASONER,
        system_prompt=reviewer._SYSTEM,
    ),
)

BY_NAME: dict[str, AgentSpec] = {spec.name: spec for spec in ROSTER}

__all__ = ["ROSTER", "BY_NAME", "AgentSpec"]
