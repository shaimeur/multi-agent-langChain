"""C6, second half — the same tools over MCP (cahier §9, §16 C6).

§9 says each capability is written once and exposed twice. A test that only counted
MCP tool descriptors would pass on a server that mirrored the names and reimplemented
the bodies, which is the failure mode worth guarding against: the reimplementation is
what would quietly lose the path confinement.

So these drive a real ``mcp.Client`` against the real server object, and the two that
matter assert *behaviour* rather than shape — a call returns what the LangChain tool
returns, and a path escape is refused over MCP exactly as it is refused in
``tests/test_registry.py``. The client is connected in memory rather than over a
spawned stdio process: same protocol objects, no subprocess to leak.
"""

from __future__ import annotations

import subprocess

import mcp
import pytest

from forge.config import CacheMode, Settings
from forge.mcp import build_mcp_server, describe_mcp_tools
from forge.tools.registry import build_toolset


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "core.py").write_text(
        "def tokenize(sql):\n"
        '    """Split sql into tokens."""\n'
        "    return sql.split()\n"
        "\n"
        "\n"
        "def parse(sql):\n"
        "    return tokenize(sql)\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=root,
        check=True,
    )
    return root


@pytest.fixture
def settings(tmp_path, repo):
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        embedding_model="hashing",
        qdrant_url="",
        qdrant_path=tmp_path / "qdrant",
        target_repo=repo,
        checkpoint_db=tmp_path / "cp.sqlite",
        workspace_root=tmp_path / "ws",
    )


@pytest.fixture
def knowledge_server(settings):
    """The seven knowledge tools — no worktree, so no sandbox trio."""
    return build_mcp_server(settings=settings, include_sandbox=False)


async def test_the_mcp_server_advertises_the_toolset(knowledge_server):
    """A client discovers the tools by asking, which is the protocol's whole point."""
    async with mcp.Client(knowledge_server) as client:
        listed = await client.list_tools()
        assert client.server_info.name == "forge"

    assert {t.name for t in listed.tools} == {
        "search_code",
        "ripgrep_search",
        "find_definitions",
        "find_references",
        "read_file",
        "list_files",
        "guardrail_events",
    }
    assert all(t.description for t in listed.tools), "a tool with no description is unusable"


async def test_a_worktree_brings_the_count_to_ten(settings, repo):
    """C6 asks for ten. The sandbox trio needs a worktree to be bound to, here as there."""
    from forge.core.workspace import create_workspace, remove_workspace

    workspace = create_workspace("mcp-c6", settings=settings, repo=repo)
    try:
        server = build_mcp_server(settings=settings, workspace=workspace, session_id="mcp-c6")
        async with mcp.Client(server) as client:
            listed = await client.list_tools()
    finally:
        remove_workspace(workspace)

    assert len(listed.tools) == 10, sorted(t.name for t in listed.tools)
    assert {"run_pytest", "run_python", "run_linter"} <= {t.name for t in listed.tools}


def test_the_descriptors_are_derived_from_the_registry_not_restated(settings):
    """The anti-drift property §9 is actually asking for.

    Names and descriptions come off the ``BaseTool`` objects themselves, so a tool
    added to the registry appears over MCP with no second edit — and one renamed
    cannot keep an old name here.
    """
    tools = build_toolset(settings=settings, include_sandbox=False)
    descriptors = describe_mcp_tools(tools)

    assert [d.name for d in descriptors] == [t.name for t in tools]
    for descriptor, tool in zip(descriptors, tools, strict=True):
        assert descriptor.description == " ".join((tool.description or "").split())


async def test_a_tool_call_returns_what_the_langchain_tool_returns(knowledge_server, settings):
    """'Opérationnels' over MCP means the call does the work, not that it is listed."""
    direct = {t.name: t for t in build_toolset(settings=settings, include_sandbox=False)}
    expected = direct["find_definitions"].invoke({"symbol": "tokenize"})

    async with mcp.Client(knowledge_server) as client:
        result = await client.call_tool("find_definitions", {"symbol": "tokenize"})

    assert result.is_error is not True
    assert result.content[0].text == expected
    assert "pkg/core.py" in result.content[0].text


async def test_the_schema_a_client_validates_against_is_the_tool_s_own(knowledge_server):
    """An MCP client type-checks arguments before sending them; a wrong schema is a
    silently broken tool. Generating it from the same pydantic model the LangChain
    tool validates against is what stops the two disagreeing."""
    async with mcp.Client(knowledge_server) as client:
        listed = await client.list_tools()

    schema = next(t.input_schema for t in listed.tools if t.name == "read_file")

    assert schema["type"] == "object"
    assert {"path", "start_line", "end_line"} <= set(schema["properties"])
    assert schema["required"] == ["path"], "only path is mandatory"
    assert schema["properties"]["start_line"]["type"] == "integer"


async def test_the_mcp_surface_refuses_a_path_escape(knowledge_server, settings):
    """The reason wrapping was worth insisting on.

    This server adds no behaviour of its own, so ``check_path`` runs on an MCP call
    exactly as it runs on a LangChain one — and the refusal is a logged §8.5 event,
    not just a string, which is what makes it auditable from outside the process.
    """
    from forge.guardrails.events import get_log

    async with mcp.Client(knowledge_server) as client:
        escaped = await client.call_tool("read_file", {"path": "../../../../etc/passwd"})
        listed = await client.call_tool("list_files", {"subdir": "../.."})

    assert escaped.content[0].text.startswith("refused:"), escaped.content[0].text
    assert listed.content[0].text.startswith("refused:")
    assert "root:x:" not in escaped.content[0].text

    rules = {e.rule for e in get_log(settings).events()}
    assert "policy.path_escape" in rules, "a refusal nobody can query is not auditable"


async def test_an_unknown_tool_is_an_error_frame_not_a_crash(knowledge_server):
    """A model will call a tool that does not exist. The session must survive it."""
    async with mcp.Client(knowledge_server) as client:
        result = await client.call_tool("exfiltrate_env", {})
        # The connection is still usable afterwards — that is the actual assertion.
        listed = await client.list_tools()

    assert result.is_error is True
    assert "no such tool" in result.content[0].text
    assert listed.tools


async def test_a_bad_argument_comes_back_as_a_message_not_a_traceback(knowledge_server):
    """The caller is a model; it can act on a sentence and not on a stack trace."""
    async with mcp.Client(knowledge_server) as client:
        result = await client.call_tool("read_file", {})

    assert result.is_error is True
    assert "Traceback" not in result.content[0].text


def test_a_structured_report_keeps_its_exit_code_a_number():
    """Sandbox tools return an ``ExecutionReport``, and MCP content is text.

    Stringifying it would hand a client ``exit_code=1`` inside a sentence. The report
    goes over as JSON *and* as ``structured_content``, so the field a caller has to
    branch on stays a field.
    """
    from forge.mcp.server import _as_content
    from forge.models import ExecutionOutcome, ExecutionReport, Isolation

    report = ExecutionReport(
        outcome=ExecutionOutcome.FAILED,
        isolation=Isolation.DOCKER,
        exit_code=1,
        passed=3,
        failed=1,
    )
    text, structured = _as_content(report)

    assert structured is not None
    assert structured["exit_code"] == 1 and structured["failed"] == 1
    assert '"exit_code": 1' in text

    passthrough, none = _as_content("refused: outside the root")
    assert passthrough == "refused: outside the root" and none is None
