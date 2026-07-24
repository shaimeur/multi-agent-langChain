"""The CLI is a graded interface (cahier 10.2), not a debug affordance."""

from __future__ import annotations

from typer.testing import CliRunner

from forge.cli.main import app
from forge.config import LLMRole, get_settings

runner = CliRunner()


def test_config_lists_every_model_role():
    """Square brackets in a Rich cell are markup and get eaten — hence this test."""
    result = runner.invoke(app, ["config"])

    assert result.exit_code == 0
    for role in LLMRole:
        assert role.value in result.stdout


def test_config_reports_the_reranker_as_off():
    assert "eval harness only" in runner.invoke(app, ["config"]).stdout


def test_fix_without_a_configured_model_fails_readably(monkeypatch):
    """`forge fix` is implemented (D12). With no provider key it must still fail the
    way cahier §9 asks — a sentence naming the knob to turn, never a stack trace."""
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    get_settings.cache_clear()

    result = runner.invoke(app, ["fix", "make add() return a + b"])

    assert result.exit_code != 0, "`forge fix` must not report success"
    assert "No usable model" in result.stdout
    assert "LLM_PROVIDER=ollama" in result.stdout, "it names the way out"
    assert "Traceback" not in result.stdout


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
