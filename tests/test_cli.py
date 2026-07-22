"""The CLI is a graded interface (cahier 10.2), not a debug affordance."""

from __future__ import annotations

from typer.testing import CliRunner

from forge.cli.main import app
from forge.config import LLMRole

runner = CliRunner()


def test_config_lists_every_model_role():
    """Square brackets in a Rich cell are markup and get eaten — hence this test."""
    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    for role in LLMRole:
        assert role.value in result.stdout


def test_config_reports_the_reranker_as_off():
    assert "eval harness only" in runner.invoke(app, ["config"]).stdout


def test_unimplemented_commands_exit_nonzero_and_say_when():
    """A stub that exits 0 would let a broken pipeline look green in CI."""
    for command, args in [("index", ["."]), ("ask", ["q"]), ("fix", ["r"])]:
        result = runner.invoke(app, [command, *args])
        assert result.exit_code != 0, f"`forge {command}` must not report success"
        assert "not implemented yet" in result.stdout


def test_bare_invocation_shows_help():
    assert runner.invoke(app, []).exit_code != 0
