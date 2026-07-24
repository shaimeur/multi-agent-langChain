"""SANDBOX_ENGINEER — writes the regression test, then runs the suite (cahier §4).

Two jobs, deliberately separated into two nodes because they happen at different
points in the loop:

``regression`` runs **once, before any patch**. It asks the coder model for a pytest
module that reproduces the bug, writes it into the session worktree, and runs it
expecting it to *fail*. A regression test that passes against unfixed code pins
nothing — the loop would then "repair" the bug and the test would be just as green
as before, proving only that the suite still runs. So the red result is recorded as
``regression_red`` rather than assumed, and a test that comes back green is reported
as the failure it is.

``verify`` runs **every iteration**, after the editor has applied its patch. It runs
the suite in the sandbox and returns an ``ExecutionReport``; the reviewer routes on
that report and nothing else.

Neither node interprets output. The parsing already happened in ``sandbox/report.py``
and the verdict is the exit code — a model is never asked whether the tests passed.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from forge.config import Settings, get_settings
from forge.core.agents.base import get_plan, latest_user_text
from forge.core.state import ForgeState
from forge.core.workspace import Workspace
from forge.models import ChangePlan, ExecutionOutcome, GeneratedTest, PlanStep
from forge.sandbox.tools import run_pytest

_SYSTEM = (
    "You are the SANDBOX_ENGINEER for FORGE. Write ONE self-contained pytest module that "
    "reproduces the reported bug against the CURRENT (unfixed) code. It must FAIL now and "
    "pass once the bug is fixed. Import the code under test by its repo-relative module "
    "path. Assert the CORRECT behaviour — never assert the buggy behaviour to make it pass. "
    "No network, no fixtures outside the file, no mocks of the function under test. "
    "Return the file path (conventionally tests/test_<name>.py) and its full source."
)


def _messages(request: str, step: PlanStep, file_text: str) -> list:
    human = (
        f"Bug report / change request: {request}\n"
        f"Target file: {step.target_path}\n"
        f"Planned fix: {step.intent}\n\n"
        f"<file path={step.target_path}>\n{file_text}\n</file>"
    )
    return [SystemMessage(_SYSTEM), HumanMessage(human)]


def write_regression_test(
    llm: BaseChatModel, request: str, step: PlanStep, workspace: Workspace
) -> GeneratedTest:
    """Ask for a failing regression test. Does not write it — the node does that."""
    tester = llm.with_structured_output(GeneratedTest)
    file_text = workspace.read(step.target_path) if workspace.exists(step.target_path) else ""
    generated = tester.invoke(_messages(request, step, file_text))
    if not isinstance(generated, GeneratedTest) or generated.is_empty:
        return GeneratedTest(path="", source="")
    # A weak model may answer with a bare filename or an absolute path; normalise it
    # into the tests/ directory so the write stays somewhere predictable.
    if not generated.path or generated.path.startswith(("/", "..")):
        generated.path = f"tests/test_forge_regression_{step.target_path.replace('/', '_')}"
    return generated


def make_regression_node(
    *, llm: BaseChatModel, workspace: Workspace, settings: Settings | None = None
) -> Callable[[ForgeState], dict]:
    """Write the regression test, run it, and record whether it actually went red."""
    settings = settings or get_settings()

    def regression_node(state: ForgeState) -> dict:
        plan = get_plan(state) or ChangePlan()
        if not plan.steps:
            return {"regression_red": False}
        step = plan.steps[0]

        generated = write_regression_test(llm, latest_user_text(state), step, workspace)
        if generated.is_empty:
            return {"regression_red": False}

        # resolve() is the escape guard — a generated path is model output like any
        # other and must not be able to address a file outside the worktree.
        target = workspace.resolve(generated.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generated.source, encoding="utf-8")

        report = run_pytest(workspace, [generated.path], settings=settings)
        # Red is what we want here: FAILED means the test reproduces the bug.
        red = report.outcome is ExecutionOutcome.FAILED and report.failed > 0
        return {
            "test_path": generated.path,
            "regression_red": red,
            "report": report,
        }

    return regression_node


def make_verify_node(
    *, workspace: Workspace, settings: Settings | None = None
) -> Callable[[ForgeState], dict]:
    """Run the suite in the sandbox. No LLM — this node is measurement, not judgement."""
    settings = settings or get_settings()

    def verify_node(state: ForgeState) -> dict:
        return {"report": run_pytest(workspace, settings=settings)}

    return verify_node
