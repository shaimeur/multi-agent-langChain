"""EDITOR node — one plan step → a PatchSet, never written to disk (cahier §4/A3).

The editor reads the target file from the session worktree and emits structured
search/replace edits; it never writes. ``tools/patch.py`` turns the edits into a diff
and dry-runs ``git apply --check`` — so a patch that would not apply cleanly is caught
before it ever reaches a human or the sandbox.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from forge.core.agents.base import get_budget, get_plan
from forge.core.state import ForgeState
from forge.core.workspace import Workspace
from forge.models import ChangePlan, PatchSet, PlanStep, RevisionRequest
from forge.tools.patch import apply_patch_dryrun

_SYSTEM = (
    "You are the EDITOR for FORGE. Given a plan step and the current contents of the target "
    "file, produce a PatchSet of minimal search/replace edits. Each patch's old_string must be "
    "an EXACT, UNIQUE substring of the file — copy it verbatim, including indentation — and "
    "new_string is its replacement. Change only what the step requires. Never output diff "
    "syntax; only the structured edits."
)


def _messages(step: PlanStep, file_text: str, revision: RevisionRequest | None = None) -> list:
    human = f"Target file: {step.target_path}\nStep: {step.intent}\n\n<file>\n{file_text}\n</file>"
    if revision is not None:
        # The failing test ids and stderr, not a prose complaint — the editor gets to
        # see exactly what the sandbox saw (cahier §4/A4).
        human += f"\n\n<previous_attempt>\n{revision.as_evidence()}\n</previous_attempt>"
    return [SystemMessage(_SYSTEM), HumanMessage(human)]


def edit_step(
    llm: BaseChatModel,
    step: PlanStep,
    workspace: Workspace,
    revision: RevisionRequest | None = None,
) -> PatchSet:
    """One plan step → a PatchSet. Reads the target from the worktree; never writes.

    On a repair iteration ``revision`` carries the previous attempt's failures, so the
    editor is revising against evidence rather than guessing a second time.
    """
    editor = llm.with_structured_output(PatchSet)
    file_text = workspace.read(step.target_path) if workspace.exists(step.target_path) else ""
    patchset = editor.invoke(_messages(step, file_text, revision))
    if not isinstance(patchset, PatchSet):
        return PatchSet()
    # A weak model may leave the path blank; anchor it to the step's target.
    for patch in patchset.patches:
        if not patch.path:
            patch.path = step.target_path
    return patchset


def get_revision(state: ForgeState) -> RevisionRequest | None:
    """A checkpoint can hand the revision back as a dict; normalise either form."""
    revision = state.get("revision")
    if revision is None or isinstance(revision, RevisionRequest):
        return revision
    return RevisionRequest(**revision) if isinstance(revision, dict) else None


def make_editor_node(*, llm: BaseChatModel, workspace: Workspace) -> Callable[[ForgeState], dict]:
    """Build the patch for the current step and dry-run it. **Writes nothing.**

    The write is a separate node (``core/loop.py``'s ``apply``) so the D9 approval
    gate can sit between the two — which is what lets a human reject a patch without
    anything needing to be rolled back.

    One step per entry rather than the whole plan: the repair loop re-enters this node
    with a ``RevisionRequest`` naming which step to redo, and re-editing every step on
    each iteration would undo work the reviewer had already accepted.
    """

    def editor_node(state: ForgeState) -> dict:
        plan = get_plan(state) or ChangePlan()
        revision = get_revision(state)
        budget = get_budget(state)
        if not plan.steps:
            return {"patch_ok": False, "budget": budget}

        index = min(revision.target_step if revision else 0, len(plan.steps) - 1)
        patchset = edit_step(llm, plan.steps[index], workspace, revision)
        result = apply_patch_dryrun(workspace, patchset)

        return {
            "patchset": PatchSet(summary=plan.summary, patches=patchset.patches),
            "patch_ok": result.ok,
            "budget": budget.spend(calls=1),
            # Consumed: a stale revision would have the next pass fixing a failure
            # that no longer exists.
            "revision": None,
        }

    return editor_node
