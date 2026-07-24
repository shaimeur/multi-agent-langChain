"""REVIEWER — the quality barrier, five fixed points (cahier §4/A5).

The checklist is fixed rather than improvised: a reviewer that picks its own criteria
each run cannot be held to any of them. What matters more is *who decides each point*:

======================  ============  =========================================
1. Grounding            **code**      every cited chunk resolves in the ContextPack
2. Plan conformance     model         does the patch do the step, and only the step?
3. Tests                **code**      did they run, did they pass — read, not argued
4. Security             **code**      secrets, eval/exec, egress, deps, deleted tests
5. Regression risk      model         blast_radius left untouched when it should not be
======================  ============  =========================================

Three of the five never reach a model. That is the design, not an optimisation: the
model is handed a ``ReviewJudgement`` schema that has fields for points 2 and 5 and
*no fields at all* for 1, 3 and 4, so "the tests actually passed" is not a claim it is
able to make. The cahier's own formulation is that tests are the one oracle that
cannot be persuaded; a reviewer that could be argued out of a red suite would be worth
nothing, and the cheapest way to guarantee it cannot be is to never ask.

**Model family.** §4/A5 wants the reviewer on a different family from the editor — a
critic sharing the editor's blind spots is worth much less. That is a configuration
concern (``LLMRole.REASONER`` vs ``LLMRole.CODER``); under a single provider the two
coincide, which ``shares_family_with_editor`` reports rather than hides.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.types import Command

from forge.config import LLMRole, Settings, get_settings
from forge.core.agents.base import (
    get_budget,
    get_pack,
    get_patchset,
    get_plan,
    get_report,
)
from forge.core.agents.editor import get_revision
from forge.core.state import ForgeState
from forge.models import (
    ChangePlan,
    ContextPack,
    ExecutionReport,
    PatchSet,
    ReviewCheck,
    ReviewCheckId,
    ReviewJudgement,
    ReviewVerdict,
    Verdict,
)

_SYSTEM = (
    "You are the REVIEWER for FORGE. You are given a plan step and the patch that claims to "
    "implement it. Judge exactly two things and nothing else. (1) plan_conformance: does the "
    "patch implement the planned step, and ONLY that step — no unrelated refactoring, no scope "
    "creep? (2) regression_risk_ok: given the files the plan expected to affect, is it safe that "
    "the listed files were left untouched? Do not comment on whether the tests passed or whether "
    "citations resolve — those are decided elsewhere and your opinion on them is not used."
)

# --- point 4's patterns ---------------------------------------------------
# Deliberately narrow and literal. A broad heuristic here produces false positives on
# ordinary code, and a security check that cries wolf gets switched off.
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(api[_-]?key|secret|password|passwd|token|credential)\w*
    \s*[=:]\s*
    ['"][^'"\s]{8,}['"]
    """
)
_SECRET_SHAPES = re.compile(r"(AKIA[0-9A-Z]{12,}|sk-[A-Za-z0-9]{16,}|AIza[\w-]{30,}|ghp_\w{20,})")
_DANGEROUS_CALL = re.compile(r"\b(eval|exec|__import__|compile)\s*\(")
_EGRESS = re.compile(
    r"""(?x)
    ^\s*(import|from)\s+(socket|subprocess|requests|httpx|urllib|telnetlib|ftplib|smtplib)\b
    | \bos\.system\s*\(
    | \bsubprocess\.\w+\s*\(
    | \b\w*\.popen\s*\(
    """
)
_DEPENDENCY_FILE = ("pyproject.toml", "requirements", "setup.py", "setup.cfg", "Pipfile")
_TEST_DEF = re.compile(r"^\s*def\s+test_\w+")


def shares_family_with_editor(settings: Settings | None = None) -> bool:
    """True when reviewer and editor resolve to the same model — §4/A5's caveat.

    Not an error: under one free-tier provider it is simply what you get. Reported so
    a run can say the critic shared the editor's blind spots rather than imply it did not.
    """
    settings = settings or get_settings()
    return settings.model_name(LLMRole.REASONER) == settings.model_name(LLMRole.CODER)


def _added_and_removed(patchset: PatchSet) -> tuple[list[str], list[str]]:
    """Lines a patch really introduces and really deletes.

    Line-set difference per patch rather than the whole ``new_string``: a
    search/replace block carries unchanged context through it, and flagging an
    ``import subprocess`` that was already there would make point 4 useless.
    """
    added: list[str] = []
    removed: list[str] = []
    for patch in patchset.patches:
        before = set(patch.old_string.splitlines())
        after = set(patch.new_string.splitlines())
        added += [
            line for line in patch.new_string.splitlines() if line.strip() and line not in before
        ]
        removed += [
            line for line in patch.old_string.splitlines() if line.strip() and line not in after
        ]
    return added, removed


# --- the three programmatic points ---------------------------------------


def check_grounding(plan: ChangePlan, pack: ContextPack) -> ReviewCheck:
    """Point 1. Every step's evidence must resolve into the pack that was retrieved."""
    ungrounded = plan.ungrounded_steps(pack)
    if not plan.steps:
        return ReviewCheck(
            check=ReviewCheckId.GROUNDING,
            passed=False,
            justification="the plan has no steps, so nothing is grounded",
            programmatic=True,
        )
    passed = not ungrounded
    detail = (
        "every step cites evidence present in the ContextPack"
        if passed
        else f"{len(ungrounded)} step(s) cite evidence that was never retrieved: "
        + ", ".join(s.target_path for s in ungrounded)
    )
    return ReviewCheck(
        check=ReviewCheckId.GROUNDING, passed=passed, justification=detail, programmatic=True
    )


def check_tests(report: ExecutionReport | None) -> ReviewCheck:
    """Point 3. Two questions — did they run, and did they pass. Both from the report."""
    if report is None:
        detail, passed = "no ExecutionReport: the tests were never run", False
    elif report.outcome.value == "timeout":
        detail, passed = "the suite hit the sandbox deadline and was killed", False
    elif report.outcome.value == "error":
        detail, passed = f"the suite could not run (exit {report.exit_code})", False
    elif report.passed == 0 and report.failed == 0 and report.errors == 0:
        # Green exit with nothing collected is the failure mode this point exists for.
        detail, passed = "the run collected no tests at all — passing vacuously", False
    elif not report.ok:
        detail, passed = f"{report.failed} test(s) failing, {report.passed} passing", False
    else:
        detail, passed = f"{report.passed} test(s) ran and passed", True
    return ReviewCheck(
        check=ReviewCheckId.TESTS, passed=passed, justification=detail, programmatic=True
    )


def check_security(patchset: PatchSet) -> ReviewCheck:
    """Point 4. The cahier's five prohibitions, each as a literal scan of the diff."""
    added, removed = _added_and_removed(patchset)
    added_text = "\n".join(added)
    problems: list[str] = []

    if _SECRET_ASSIGNMENT.search(added_text) or _SECRET_SHAPES.search(added_text):
        problems.append("a hardcoded credential")
    if _DANGEROUS_CALL.search(added_text):
        problems.append("eval/exec/__import__ on constructed input")
    if any(_EGRESS.search(line) for line in added):
        problems.append("a new network call or subprocess")
    if any(
        patch.path.startswith(_DEPENDENCY_FILE)
        or any(marker in patch.path for marker in _DEPENDENCY_FILE)
        for patch in patchset.patches
    ):
        problems.append("a dependency file edited")
    if any(_TEST_DEF.match(line) for line in removed):
        problems.append("a test deleted")

    return ReviewCheck(
        check=ReviewCheckId.SECURITY,
        passed=not problems,
        justification="; ".join(problems)
        if problems
        else "no prohibited pattern in the added lines",
        programmatic=True,
    )


# --- the two judged points ------------------------------------------------


def _untouched_blast_radius(plan: ChangePlan, patchset: PatchSet) -> list[str]:
    edited = {patch.path for patch in patchset.patches}
    return [path for path in plan.blast_radius if path not in edited]


def judge(
    llm: BaseChatModel, plan: ChangePlan, patchset: PatchSet, step_index: int = 0
) -> ReviewJudgement:
    """Ask the model for points 2 and 5 only. Never for the other three."""
    step = plan.steps[step_index] if step_index < len(plan.steps) else None
    edits = "\n\n".join(
        f"--- {p.path}\n<<< old\n{p.old_string}\n>>> new\n{p.new_string}" for p in patchset.patches
    )
    human = (
        f"Planned step: {step.intent if step else '(none)'}\n"
        f"Target file: {step.target_path if step else '(none)'}\n"
        f"Declared blast radius: {plan.blast_radius or '(none declared)'}\n"
        f"Blast-radius files left untouched: "
        f"{_untouched_blast_radius(plan, patchset) or '(none)'}\n\n"
        f"<patch>\n{edits}\n</patch>"
    )
    reviewer = llm.with_structured_output(ReviewJudgement)
    result = reviewer.invoke([SystemMessage(_SYSTEM), HumanMessage(human)])
    return result if isinstance(result, ReviewJudgement) else ReviewJudgement()


def _judged_checks(
    llm: BaseChatModel | None, plan: ChangePlan, patchset: PatchSet, step_index: int
) -> list[ReviewCheck]:
    if llm is None:
        # No reviewer model configured (B2). Say so on the check itself rather than
        # letting an unasked question read as a satisfied one.
        note = "not evaluated — no reviewer model configured"
        return [
            ReviewCheck(check=ReviewCheckId.PLAN_CONFORMANCE, passed=True, justification=note),
            ReviewCheck(check=ReviewCheckId.REGRESSION_RISK, passed=True, justification=note),
        ]
    try:
        judgement = judge(llm, plan, patchset, step_index)
    except Exception as error:  # a reviewer that crashes must not approve by default
        note = f"the reviewer model failed: {error}"
        return [
            ReviewCheck(check=ReviewCheckId.PLAN_CONFORMANCE, passed=False, justification=note),
            ReviewCheck(check=ReviewCheckId.REGRESSION_RISK, passed=False, justification=note),
        ]
    return [
        ReviewCheck(
            check=ReviewCheckId.PLAN_CONFORMANCE,
            passed=judgement.plan_conformance,
            justification=judgement.plan_conformance_why,
        ),
        ReviewCheck(
            check=ReviewCheckId.REGRESSION_RISK,
            passed=judgement.regression_risk_ok,
            justification=judgement.regression_risk_why,
        ),
    ]


def review(
    *,
    plan: ChangePlan,
    pack: ContextPack,
    patchset: PatchSet,
    report: ExecutionReport | None,
    llm: BaseChatModel | None = None,
    step_index: int = 0,
    patch_applied: bool = True,
) -> ReviewVerdict:
    """Run all five points and combine them. Any failed point means REVISE."""
    checks = [
        check_grounding(plan, pack),
        check_tests(report),
        check_security(patchset),
        *_judged_checks(llm, plan, patchset, step_index),
    ]
    if not patch_applied:
        checks.append(
            ReviewCheck(
                check=ReviewCheckId.PLAN_CONFORMANCE,
                passed=False,
                justification="the patch did not apply to the worktree",
                programmatic=True,
            )
        )

    failed = [c for c in checks if not c.passed]
    return ReviewVerdict(
        verdict=Verdict.APPROVE if not failed else Verdict.REVISE,
        checks=checks,
        feedback=[f"{c.check.value}: {c.justification}" for c in failed],
        target_step=step_index,
    )


def order_checks(checks: Iterable[ReviewCheck]) -> list[ReviewCheck]:
    """The five points in their canonical order, for a stable trace and UI."""
    order = list(ReviewCheckId)
    return sorted(checks, key=lambda c: order.index(c.check))


def make_reviewer(
    *, llm: BaseChatModel | None = None, settings: Settings | None = None
) -> Callable[..., ReviewVerdict]:
    """A reviewer bound to its model, for the graph node to call."""
    settings = settings or get_settings()

    def run(plan, pack, patchset, report, *, step_index=0, patch_applied=True) -> ReviewVerdict:
        return review(
            plan=plan,
            pack=pack,
            patchset=patchset,
            report=report,
            llm=llm,
            step_index=step_index,
            patch_applied=patch_applied,
        )

    return run


def make_reviewer_node(
    *,
    llm: BaseChatModel | None = None,
    settings: Settings | None = None,
    escalate_after: int | None = None,
) -> Callable[[ForgeState], Command]:
    """REVIEWER + routing. Approve ends the run; revise goes back to the editor.

    ``llm=None`` runs the three programmatic points only and records the other two as
    unevaluated — the honest posture when no reviewer model is configured, rather than
    a silent pass.
    """
    settings = settings or get_settings()
    escalate_after = escalate_after or settings.max_iterations_per_step
    reviewer = make_reviewer(llm=llm, settings=settings)

    def reviewer_node(state: ForgeState) -> Command:
        plan = get_plan(state) or ChangePlan()
        pack = get_pack(state) or ContextPack()
        report = get_report(state)
        patchset = get_patchset(state)
        iterations = state.get("iterations", 0) + 1
        budget = get_budget(state)

        revision_in = get_revision(state)
        step_index = revision_in.target_step if revision_in else 0
        verdict = reviewer(
            plan,
            pack,
            patchset,
            report,
            step_index=step_index,
            patch_applied=bool(state.get("patch_ok")),
        )
        update: dict = {"iterations": iterations, "review": verdict}

        if verdict.approved:
            return Command(goto=END, update={**update, "revision": None})

        # Which file the disagreement is about, for the pathology counter.
        contested = dict(state.get("contested", {}))
        path = plan.steps[step_index].target_path if step_index < len(plan.steps) else "?"
        contested[path] = contested.get(path, 0) + 1
        update["contested"] = contested

        exhausted = budget.exceeded(settings)
        if exhausted:
            return Command(goto=END, update={**update, "halted": exhausted})
        if contested[path] >= escalate_after:
            return Command(goto="escalate", update=update)
        if iterations >= settings.max_iterations_per_step:
            return Command(
                goto=END,
                update={
                    **update,
                    "halted": f"stopped after {iterations} attempts: "
                    + "; ".join(verdict.feedback[:2]),
                },
            )
        return Command(goto="editor", update={**update, "revision": verdict.as_revision(report)})

    return reviewer_node
