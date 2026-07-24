"""The two mandatory human control points — ``interrupt()`` (cahier §5.5, §2).

§5.5 names exactly two: **before the plan is executed**, and **before any patch
touches the disk**. Both are implemented here as graph nodes that call
``interrupt()``; LangGraph checkpoints the whole state at that point and the run
stops. It resumes with ``Command(resume=...)`` — which may be seconds later from a
test, or hours later from ``POST /v1/sessions/{id}/approve``, because a checkpointed
interrupt costs nothing to keep waiting.

Placing the patch gate *before* the write rather than after is the point. The EDITOR
builds a ``PatchSet`` and ``git apply --check`` validates it, but nothing has been
written when the human is asked — so "reject" needs no rollback, it simply never
happens. That is what makes the approval real rather than a notification.

Also here: the loop-pathology escalation of §4. Three disagreements about the same
file is a signal that neither more iterations nor more budget will help, and the
right response is to ask a human rather than to keep paying.

**Resume values are external input** — they arrive from an API caller — so they are
parsed defensively: a bool, a string, or a dict all work, and anything unrecognised
is treated as *not* approved. Defaulting an unparseable answer to "approved" would
make the gate worse than useless.
"""

from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END
from langgraph.types import Command, interrupt

from forge.core.agents.base import get_plan
from forge.core.state import ForgeState
from forge.core.workspace import Workspace
from forge.models import ChangePlan, PatchSet
from forge.tools.patch import build_diff

_AFFIRMATIVE = {"approve", "approved", "yes", "y", "ok", "accept", "true"}


def parse_decision(decision: object) -> tuple[bool, str]:
    """A resume value → (approved, feedback). Unrecognised means not approved."""
    if isinstance(decision, bool):
        return decision, ""
    if isinstance(decision, str):
        return decision.strip().lower() in _AFFIRMATIVE, ""
    if isinstance(decision, dict):
        raw = decision.get("approved", decision.get("approve", decision.get("decision")))
        feedback = str(decision.get("feedback", "") or "")
        if isinstance(raw, bool):
            return raw, feedback
        if isinstance(raw, str):
            return raw.strip().lower() in _AFFIRMATIVE, feedback
        return False, feedback
    return False, ""


def _get_patchset(state: ForgeState) -> PatchSet:
    patchset = state.get("patchset")
    if isinstance(patchset, PatchSet):
        return patchset
    return PatchSet(**patchset) if isinstance(patchset, dict) else PatchSet()


def make_plan_approval_node(
    *, next_node: str, enabled: bool = True
) -> Callable[[ForgeState], Command]:
    """Control point 1 — the human approves the plan before any of it is executed."""

    def plan_approval_node(state: ForgeState) -> Command:
        plan = get_plan(state) or ChangePlan()
        if not enabled:
            return Command(goto=next_node)
        if not plan.steps:
            return Command(goto=END, update={"halted": "no plan to approve"})

        decision = interrupt(
            {
                "kind": "plan_approval",
                "summary": plan.summary,
                "steps": [
                    {"intent": s.intent, "target_path": s.target_path, "rationale": s.rationale}
                    for s in plan.steps
                ],
                "blast_radius": plan.blast_radius,
            }
        )
        approved, feedback = parse_decision(decision)
        if not approved:
            because = f": {feedback}" if feedback else ""
            return Command(
                goto=END,
                update={
                    "halted": f"the plan was rejected by the human{because}",
                    "approvals": ["plan:rejected"],
                },
            )
        return Command(goto=next_node, update={"approvals": ["plan:approved"]})

    return plan_approval_node


def make_patch_approval_node(
    *, workspace: Workspace, next_node: str, enabled: bool = True
) -> Callable[[ForgeState], Command]:
    """Control point 2 — the human sees the diff before it is written anywhere."""

    def patch_approval_node(state: ForgeState) -> Command:
        if not enabled:
            return Command(goto=next_node)
        if not state.get("patch_ok"):
            # Nothing appliable to approve; let the reviewer record why.
            return Command(goto=next_node)

        patchset = _get_patchset(state)
        try:
            diff = build_diff(workspace, patchset)
        except Exception as error:
            diff = f"(could not render the diff: {error})"

        decision = interrupt(
            {
                "kind": "patch_approval",
                "summary": patchset.summary,
                "files": sorted({p.path for p in patchset.patches}),
                "diff": diff,
            }
        )
        approved, feedback = parse_decision(decision)
        if not approved:
            because = f": {feedback}" if feedback else ""
            return Command(
                goto=END,
                update={
                    "halted": f"the patch was rejected by the human{because}",
                    "approvals": ["patch:rejected"],
                },
            )
        return Command(goto=next_node, update={"approvals": ["patch:approved"]})

    return patch_approval_node


def make_escalation_node(*, enabled: bool = True) -> Callable[[ForgeState], Command]:
    """Loop pathology — the editor and reviewer keep disagreeing about one file (§4).

    Escalating rather than ending is the distinction from the iteration cap: the cap
    stops the run, this asks the human whether to keep going, and only their answer
    spends more budget.
    """

    def escalation_node(state: ForgeState) -> Command:
        contested = state.get("contested", {})
        worst = max(contested, key=contested.get) if contested else "(unknown)"
        if not enabled:
            return Command(goto=END, update={"halted": f"loop pathology on {worst}"})

        decision = interrupt(
            {
                "kind": "loop_pathology",
                "file": worst,
                "disagreements": contested.get(worst, 0),
                "question": "the editor and reviewer keep disagreeing about this file — retry?",
            }
        )
        approved, feedback = parse_decision(decision)
        if approved:
            # The human bought another round: clear the count so the cap applies afresh.
            return Command(
                goto="editor",
                update={"contested": {}, "approvals": ["pathology:retry"]},
            )
        return Command(
            goto=END,
            update={
                "halted": f"stopped on {worst} after {contested.get(worst, 0)} disagreements"
                + (f": {feedback}" if feedback else ""),
                "approvals": ["pathology:stopped"],
            },
        )

    return escalation_node
