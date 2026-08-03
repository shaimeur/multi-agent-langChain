"""The StateGraph — assemble the nodes and persist per session (cahier §4, §7).

    START → supervisor → { retriever → answer | answer | END }
                          answer → summary → END

The supervisor routes with ``Command(goto=...)``; the retriever gathers context; the
answerer grounds a reply; the sliding-summary node keeps memory bounded. Sentinel
guardrail nodes wrap this at D10 — for now the graph is the bare skeleton.

``AsyncSqliteSaver`` checkpoints the whole state under ``thread_id = session_id``
(descope §1, SQLite not Postgres), which is what lets a session survive a process
restart: a fresh graph opened on the same database resumes the same thread. That is
the C4 proof, and it needs no LLM — the checkpointer is pure infrastructure.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from forge.config import LLMRole, Settings, get_settings
from forge.core.agents.answer import make_answer_node
from forge.core.agents.retriever import make_retriever_node
from forge.core.agents.summarize import make_summary_node
from forge.core.agents.supervisor import make_supervisor_node
from forge.core.state import ForgeState
from forge.rag import store
from forge.rag.embed import Embedder
from forge.rag.pack import DEFAULT_TOKEN_BUDGET
from forge.rag.retrieve import load_encoder
from forge.rag.sparse import BM25Encoder

_ANSWER_NUM_CTX = 8192


def make_nodes(
    *,
    settings: Settings,
    client: Any,
    embedder: Embedder,
    router_llm: BaseChatModel,
    reasoner_llm: BaseChatModel,
    encoder: BM25Encoder | None = None,
    repo: str | Path | None = None,
    repo_name: str = "the",
    collection: str = store.CODE_COLLECTION,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> dict[str, Callable]:
    """The four nodes over shared resources. Injectable so tests wire fakes."""
    return {
        "supervisor": make_supervisor_node(llm=router_llm, settings=settings),
        "retriever": make_retriever_node(
            settings=settings,
            client=client,
            embedder=embedder,
            encoder=encoder,
            repo=repo,
            collection=collection,
            token_budget=token_budget,
        ),
        "answer": make_answer_node(llm=reasoner_llm, repo_name=repo_name),
        "summary": make_summary_node(),
    }


def build_graph(nodes: dict[str, Callable], *, checkpointer: Any | None = None):
    """Wire the nodes into a compiled StateGraph bound to ``checkpointer``."""
    graph = StateGraph(ForgeState)
    graph.add_node("supervisor", nodes["supervisor"])
    graph.add_node("retriever", nodes["retriever"])
    graph.add_node("answer", nodes["answer"])
    graph.add_node("summary", nodes["summary"])

    graph.add_edge(START, "supervisor")  # supervisor then Command(goto=...) routes
    graph.add_edge("retriever", "answer")
    graph.add_edge("answer", "summary")
    graph.add_edge("summary", END)
    return graph.compile(checkpointer=checkpointer)


async def run_turn(graph, question: str, *, session_id: str) -> ForgeState:
    """Run one user turn on ``graph`` under ``session_id``.

    Only the new message and the session id are passed — everything else (messages,
    accumulated chunks, the cumulative budget) is loaded from the checkpoint, so a
    resumed process continues the same session instead of restarting it.
    """
    return await graph.ainvoke(
        {"messages": [HumanMessage(content=question)], "session_id": session_id},
        config={"configurable": {"thread_id": session_id}},
    )


def build_default_retriever(
    settings: Settings | None = None,
    *,
    client: Any | None = None,
    embedder: Embedder | None = None,
    encoder: BM25Encoder | None = None,
    collection: str = store.CODE_COLLECTION,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> Callable:
    """The RETRIEVER node alone, over real resources — what the *change* path needs.

    ``build_default_nodes`` also builds the ROUTER and REASONER models for the ask
    path; ``forge fix`` and the API already hold their own models and only want
    retrieval, so building the rest would cost two model handles for nothing.

    Resources are injectable because an *embedded* Qdrant allows exactly one client
    per path per process: a caller that already keeps one (the API's cached
    ``get_resources``) must pass it rather than let this open a second.
    """
    from forge.rag.embed import build_embedder

    settings = settings or get_settings()
    client = client if client is not None else store.build_client(settings)
    return make_retriever_node(
        settings=settings,
        client=client,
        embedder=embedder if embedder is not None else build_embedder(settings),
        encoder=encoder if encoder is not None else load_encoder(settings),
        repo=settings.target_repo,
        collection=collection,
        token_budget=token_budget,
    )


def build_default_nodes(settings: Settings | None = None, *, client: Any | None = None) -> dict:
    """The real nodes: Qdrant + the configured embedder + the ROUTER/REASONER models.

    The heavy path — used by `forge ask`. Tests build nodes with fakes via
    ``make_nodes`` instead of paying for models here.
    """
    from forge.llm.provider import build_llm
    from forge.rag.embed import build_embedder

    settings = settings or get_settings()
    client = client or store.build_client(settings)
    return make_nodes(
        settings=settings,
        client=client,
        embedder=build_embedder(settings),
        encoder=load_encoder(settings),
        router_llm=build_llm(LLMRole.ROUTER, settings=settings),
        reasoner_llm=build_llm(LLMRole.REASONER, num_ctx=_ANSWER_NUM_CTX, settings=settings),
        repo=settings.target_repo,
        repo_name=Path(settings.target_repo).name,
    )
