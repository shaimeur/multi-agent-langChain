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


def make_editor_node(*, llm: BaseChatModel, workspace: Workspace) -> Callable[[ForgeState], dict]:
    def editor_node(state: ForgeState) -> dict:
        plan = get_plan(state) or ChangePlan()
        budget = get_budget(state)

        patches = []
        for step in plan.steps:
            patches.extend(edit_step(llm, step, workspace).patches)
        patchset = PatchSet(summary=plan.summary, patches=patches)

        result = apply_patch_dryrun(workspace, patchset)
        return {
            "patchset": patchset,
            "patch_ok": result.ok,
            "budget": budget.spend(calls=max(len(plan.steps), 1)),
        }

    return editor_node
