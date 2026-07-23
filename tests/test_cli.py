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
    for command, args in [("fix", ["r"])]:
        result = runner.invoke(app, [command, *args])
        assert result.exit_code != 0, f"`forge {command}` must not report success"
        assert "not implemented yet" in result.stdout


def test_ask_is_wired_and_handles_an_unindexed_repo():
    """`forge ask` is implemented now; with nothing indexed it answers honestly
    and never reaches the LLM (the conftest index is empty)."""
    result = runner.invoke(app, ["ask", "where is anything handled"])

    assert result.exit_code == 0, result.stdout
    assert "index" in result.stdout.lower()


def test_index_reports_what_it_indexed(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("def hello():\n    return 1\n")

    result = runner.invoke(app, ["index", str(repo)])

    assert result.exit_code == 0, result.stdout
    # Rich wraps to the console width, so compare on normalised whitespace.
    output = " ".join(result.stdout.split())
    assert "full index" in output
    assert "chunks from 1 files" in output


def test_index_rejects_a_path_that_is_not_a_directory(tmp_path):
    missing = tmp_path / "nope"

    result = runner.invoke(app, ["index", str(missing)])

    assert result.exit_code == 1
    assert "Not a directory" in result.stdout


def test_bare_invocation_shows_help():
    assert runner.invoke(app, []).exit_code != 0
