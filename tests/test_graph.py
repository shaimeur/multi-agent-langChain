"""The LangGraph skeleton — routing, grounded answering, and restart survival.

The headline is ``test_session_survives_a_process_restart``: it runs a turn, closes
the checkpointer, opens a *fresh* one on the same SQLite file, runs a second turn on
the same session, and shows the first turn's history is still there. That is the C4
proof (cahier §7) done with fakes and a temp DB — no network, no weights.
"""

from __future__ import annotations

import subprocess

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END

from forge.config import CacheMode, Settings
from forge.core.agents.base import get_budget
from forge.core.agents.summarize import make_summary_node
from forge.core.agents.supervisor import make_supervisor_node
from forge.core.graph import build_graph, make_nodes, run_turn
from forge.core.state import Budget, merge_chunks
from forge.models import Chunk, ChunkKind, GroundedAnswer, Route, RouteDecision
from forge.rag import store
from forge.rag.embed import HashingEmbedder
from forge.rag.ingest import index_repo


class FakeRouter:
    """A structured-output stand-in: ``with_structured_output`` returns itself and
    ``invoke`` yields a fixed RouteDecision — no model, no network."""

    def __init__(self, route: Route = Route.RETRIEVE) -> None:
        self._route = route

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        return RouteDecision(route=self._route)


@pytest.fixture
def target(tmp_path):
    repo = tmp_path / "target"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "tokenizer.py").write_text(
        'def tokenize(text):\n'
        '    """Split raw SQL into a list of tokens."""\n'
        "    return text.split()\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=repo,
        check=True,
    )
    return repo


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        embedding_model="hashing",
        qdrant_url="",
        qdrant_path=tmp_path / "qdrant",
    )


@pytest.fixture
def client(settings):
    c = store.build_client(settings)
    yield c
    c.close()


@pytest.fixture
def indexed(target, settings, client):
    index_repo(target, settings=settings, client=client, embedder=HashingEmbedder(), full=True)
    return target


def _nodes(settings, client, repo, *, route=Route.RETRIEVE, responses):
    return make_nodes(
        settings=settings,
        client=client,
        embedder=HashingEmbedder(),
        router_llm=FakeRouter(route),
        reasoner_llm=FakeListChatModel(responses=responses),
        repo=repo,
        repo_name="target",
    )


def _as_answer(value) -> GroundedAnswer:
    return value if isinstance(value, GroundedAnswer) else GroundedAnswer(**value)


# --- reducers and the budget guard (unit) ---------------------------------


def _chunk(cid):
    return Chunk(
        chunk_id=cid,
        repo="r",
        path=f"{cid}.py",
        language="python",
        kind=ChunkKind.FUNCTION,
        start_line=1,
        end_line=2,
        text="x",
        raw="x",
    )


def test_merge_chunks_dedups_and_accepts_dicts():
    merged = merge_chunks([_chunk("a")], [_chunk("a"), _chunk("b")])
    assert [c.chunk_id for c in merged] == ["a", "b"]
    # a resumed session's existing chunks may arrive as dicts
    from_dicts = merge_chunks([_chunk("a").model_dump()], [_chunk("b").model_dump()])
    assert [c.chunk_id for c in from_dicts] == ["a", "b"]


def test_budget_exceeded_reports_the_first_cap_hit():
    s = Settings(_env_file=None, max_llm_calls_per_run=2, max_tokens_per_run=100)
    assert Budget(llm_calls=1, tokens=10).exceeded(s) is None
    assert "call" in Budget(llm_calls=2).exceeded(s)
    assert "token" in Budget(tokens=100).exceeded(s)


def test_summary_folds_old_turns_and_removes_them():
    node = make_summary_node(keep_last=2, trigger_messages=4)
    msgs = [HumanMessage(content=f"q{i}", id=str(i)) for i in range(6)]
    out = node({"messages": msgs})
    assert "q0" in out["summary"] and "q3" in out["summary"]
    removes = out["messages"]
    assert len(removes) == 4 and all(isinstance(r, RemoveMessage) for r in removes)


def test_summary_is_a_noop_for_a_short_session():
    node = make_summary_node(trigger_messages=12)
    assert node({"messages": [HumanMessage(content="q", id="1")]}) == {}


# --- supervisor routing + budget guard ------------------------------------


def test_supervisor_routes_a_question_to_the_retriever(settings):
    node = make_supervisor_node(llm=FakeRouter(Route.RETRIEVE), settings=settings)
    cmd = node({"messages": [HumanMessage(content="how does x work")], "budget": Budget()})
    assert cmd.goto == "retriever"
    assert cmd.update["budget"].llm_calls == 1


def test_supervisor_stops_gracefully_when_budget_is_exhausted(settings):
    capped = settings.model_copy(update={"max_llm_calls_per_run": 1})
    node = make_supervisor_node(llm=FakeRouter(Route.RETRIEVE), settings=capped)
    # No pack and no budget left → end, not a stack trace.
    cmd = node({"messages": [HumanMessage(content="hi")], "budget": Budget(llm_calls=1)})
    assert cmd.goto == END
    assert "budget" in cmd.update["route"].rationale


# --- the graph, end to end ------------------------------------------------


async def test_graph_produces_a_verified_grounded_answer(indexed, settings, client):
    async with AsyncSqliteSaver.from_conn_string(":memory:") as cp:
        graph = build_graph(
            _nodes(settings, client, indexed, responses=["It is in the tokenizer [1]."]),
            checkpointer=cp,
        )
        state = await run_turn(graph, "how is sql split into tokens", session_id="s")

    answer = _as_answer(state["answer"])
    assert answer.grounded is True
    assert answer.citations and answer.citations[0].path == "src/tokenizer.py"
    assert [type(m).__name__ for m in state["messages"]] == ["HumanMessage", "AIMessage"]


async def test_session_survives_a_process_restart(indexed, settings, client, tmp_path):
    """C4: a fresh checkpointer on the same DB resumes the same session."""
    db = str(tmp_path / "checkpoints.sqlite")
    session = "sess-restart"

    # Turn 1 — "process A".
    async with AsyncSqliteSaver.from_conn_string(db) as cp:
        graph = build_graph(
            _nodes(
                settings, client, indexed, responses=["Splitting happens in the tokenizer [1]."]
            ),
            checkpointer=cp,
        )
        await run_turn(graph, "how is sql tokenized into a list", session_id=session)

    # Turn 2 — "process B": a brand-new checkpointer + graph on the same file.
    async with AsyncSqliteSaver.from_conn_string(db) as cp:
        graph = build_graph(
            _nodes(settings, client, indexed, responses=["The lexer drives it [1]."]),
            checkpointer=cp,
        )
        state = await run_turn(graph, "and where does the lexer fit", session_id=session)

    questions = [m.content for m in state["messages"] if isinstance(m, HumanMessage)]
    assert len(questions) == 2, "turn 1's message must survive the restart"
    assert any("tokenized" in q for q in questions)
    # The cumulative budget carried across the restart too (2 turns × supervisor+answer).
    assert get_budget(state).llm_calls >= 4


async def test_a_new_session_id_does_not_see_another_session(indexed, settings, client, tmp_path):
    db = str(tmp_path / "checkpoints.sqlite")
    async with AsyncSqliteSaver.from_conn_string(db) as cp:
        graph = build_graph(
            _nodes(settings, client, indexed, responses=["answer [1]"]),
            checkpointer=cp,
        )
        await run_turn(graph, "first session question", session_id="A")
        state = await run_turn(graph, "second session question", session_id="B")

    questions = [m.content for m in state["messages"] if isinstance(m, HumanMessage)]
    assert questions == ["second session question"], "sessions are isolated by thread_id"
