"""D12 — the §11 session routes, SSE streaming, and the approval surface.

The DoD is that the whole workflow is drivable with streamed output, so the tests
drive it: create a session, send a message, watch the frames arrive, hit a §5.5 gate,
approve it over HTTP, and see the run finish.

The graph is scripted (B2 — no key), but everything around it is real: a real
worktree, a real SQLite checkpointer, real SSE framing, real `interrupt()` and
`Command(resume=...)`. What is under test is the interface, and the interface is
exactly the part a fake model cannot hide.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from forge.api import main as api_main
from forge.api.sessions import reset_store
from forge.api.streaming import graph_events
from forge.core.state import ForgeState
from forge.models import Citation, GroundedAnswer


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=root,
        check=True,
    )
    return root


@pytest.fixture
def client(tmp_path, repo, monkeypatch):
    """The API on an isolated workspace root, checkpoint db and event log."""
    from forge.config import get_settings
    from forge.guardrails import events as events_module
    from forge.guardrails.sentinel_in import get_rate_limiter

    monkeypatch.setenv("TARGET_REPO", str(repo))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite"))
    get_settings.cache_clear()
    events_module.reset_log()
    get_rate_limiter().reset()
    reset_store()

    yield TestClient(app=api_main.app)

    reset_store()
    api_main.set_graph_factory(None)
    events_module.reset_log()
    get_settings.cache_clear()


def _gated_graph(session, settings, checkpointer):
    """A two-node graph that pauses for approval — D9's shape, without the models."""

    def plan(state: ForgeState) -> dict:
        return {"plan_reentries": 1}

    def gate(state: ForgeState) -> Command:
        decision = interrupt({"kind": "plan_approval", "summary": "fix add()"})
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
        if not approved:
            return Command(goto=END, update={"halted": "rejected by the human"})
        return Command(goto=END, update={"approvals": ["plan:approved"]})

    graph = StateGraph(ForgeState)
    graph.add_node("planner", plan)
    graph.add_node("gate", gate)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "gate")
    return graph.compile(checkpointer=checkpointer)


def _straight_graph(session, settings, checkpointer):
    """A graph that runs to completion without pausing."""

    def work(state: ForgeState) -> dict:
        return {"patch_ok": True, "iterations": 1}

    graph = StateGraph(ForgeState)
    graph.add_node("worker", work)
    graph.add_edge(START, "worker")
    graph.add_edge("worker", END)
    return graph.compile(checkpointer=checkpointer)


def _frames(response) -> list[dict]:
    """Parse an SSE body into ``[{event, data}, ...]``."""
    frames: list[dict] = []
    event = None
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and event:
            frames.append({"event": event, "data": json.loads(line.split(":", 1)[1].strip())})
            event = None
    return frames


# --- the session lifecycle ------------------------------------------------


def test_creating_a_session_creates_a_worktree(client):
    body = client.post("/v1/sessions", json={}).json()

    assert body["session_id"]
    assert body["branch"] == f"forge/{body['session_id']}"
    from pathlib import Path

    assert Path(body["workspace"]).is_dir()
    assert (Path(body["workspace"]) / "calc.py").is_file(), "the target repo is checked out"


def test_a_session_id_can_be_supplied_to_resume_a_known_thread(client):
    first = client.post("/v1/sessions", json={"session_id": "known"}).json()
    again = client.post("/v1/sessions", json={"session_id": "known"}).json()

    assert first["session_id"] == again["session_id"] == "known"
    assert len(client.get("/v1/sessions").json()) == 1


def test_closing_a_session_removes_the_worktree(client):
    from pathlib import Path

    body = client.post("/v1/sessions", json={}).json()
    path = Path(body["workspace"])

    assert client.delete(f"/v1/sessions/{body['session_id']}").status_code == 204
    assert not path.exists()


def test_unknown_sessions_are_404_not_500(client):
    assert client.delete("/v1/sessions/nope").status_code == 404
    assert client.post("/v1/sessions/nope/messages", json={"message": "hi"}).status_code == 404


# --- streaming ------------------------------------------------------------


def test_a_message_streams_typed_events_and_ends_with_done(client):
    api_main.set_graph_factory(_straight_graph)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]

    response = client.post(f"/v1/sessions/{session_id}/messages", json={"message": "fix add"})
    frames = _frames(response)

    assert response.status_code == 200
    assert [f["event"] for f in frames if f["event"] == "node"], "node events are emitted"
    assert frames[-1]["event"] == "done", "a stream must end by saying why"


def test_a_node_frame_summarises_rather_than_dumping_state(client):
    api_main.set_graph_factory(_straight_graph)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]

    frames = _frames(client.post(f"/v1/sessions/{session_id}/messages", json={"message": "go"}))
    node = next(f for f in frames if f["event"] == "node")

    assert node["data"]["node"] == "worker"
    assert node["data"]["patch_ok"] is True


def _answering_graph(session, settings, checkpointer):
    """One node that returns a grounded answer, exactly as the ``answer`` node does."""

    def answer(state: ForgeState) -> dict:
        return {
            "answer": GroundedAnswer(
                question="where is the lexer?",
                answer="In sqlparse/lexer.py.",
                grounded=True,
                citations=[
                    Citation(chunk_id="c1", path="sqlparse/lexer.py", start_line=68, end_line=80)
                ],
            )
        }

    graph = StateGraph(ForgeState)
    graph.add_node("answer", answer)
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    return graph.compile(checkpointer=checkpointer)


def test_a_node_frame_carries_the_citations_the_browser_renders(client):
    """§15.6 shows anchored citations in the UI, so they have to survive summarisation.

    The exception to "counts, not payloads" is deliberate: a citation is what the user
    reads, and re-deriving it client-side would mean a second grounded call — a second
    model spend for something the run already computed and verified.
    """
    api_main.set_graph_factory(_answering_graph)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]

    frames = _frames(client.post(f"/v1/sessions/{session_id}/messages", json={"message": "where?"}))
    node = next(f for f in frames if f["event"] == "node")

    assert node["data"]["grounded"] is True
    assert node["data"]["citations"] == [
        {"path": "sqlparse/lexer.py", "start_line": 68, "end_line": 80}
    ]


class _OneMessageGraph:
    """The smallest thing ``graph_events`` will stream: one model message, then end.

    ``stream_mode="messages"`` frames cannot be produced by a scripted node graph —
    they come from a model call — so the provider's content shape is faked here
    instead. That shape is the whole point of the test.
    """

    def __init__(self, content):
        self._content = content

    def astream(self, payload, config=None, stream_mode=None):
        content = self._content

        async def _stream():
            yield "messages", (AIMessage(content=content), {})

        return _stream()

    async def aget_state(self, config):
        return SimpleNamespace(next=(), tasks=())


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("plain text", id="str"),
        pytest.param([{"type": "text", "text": "plain text"}], id="gemini-3x-blocks"),
        pytest.param(
            [
                {"type": "reasoning", "extras": {"signature": "opaque"}},
                {"type": "text", "text": "plain "},
                {"type": "text", "text": "text"},
            ],
            id="blocks-with-reasoning",
        ),
    ],
)
async def test_a_token_frame_carries_flat_text_whatever_shape_the_provider_returned(content):
    """Gemini 3.x returns content blocks; forwarding them raw rendered "[object Object]".

    The client is entitled to a string — this module promises normalised frames, and a
    UI should not have to know one provider's block schema to print a reply.
    """
    frames = [event async for event in graph_events(_OneMessageGraph(content), {}, {})]
    tokens = [f for f in frames if f.type == "token"]

    assert [f.data["text"] for f in tokens] == ["plain text"]
    assert all(isinstance(f.data["text"], str) for f in tokens)


async def test_a_reasoning_only_message_emits_no_token_frame():
    """Blocks carrying no answer text flatten to "", which must not become an empty bubble."""
    graph = _OneMessageGraph([{"type": "reasoning", "extras": {"signature": "opaque"}}])

    frames = [event async for event in graph_events(graph, {}, {})]

    assert not [f for f in frames if f.type == "token"]


def test_a_failing_graph_ends_the_stream_with_a_typed_error(client):
    def exploding(session, settings, checkpointer):
        def boom(state):
            raise RuntimeError("the model provider is down")

        graph = StateGraph(ForgeState)
        graph.add_node("boom", boom)
        graph.add_edge(START, "boom")
        graph.add_edge("boom", END)
        return graph.compile(checkpointer=checkpointer)

    api_main.set_graph_factory(exploding)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]

    frames = _frames(client.post(f"/v1/sessions/{session_id}/messages", json={"message": "go"}))

    assert frames[-1]["event"] == "error"
    assert "provider is down" in frames[-1]["data"]["message"]
    assert "Traceback" not in frames[-1]["data"]["message"], "typed, never a traceback"


def test_sentinel_in_refuses_a_bad_message_before_the_stream_opens(client):
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]

    response = client.post(f"/v1/sessions/{session_id}/messages", json={"message": "x" * 50_000})

    assert response.status_code == 400
    events = client.get("/v1/guardrails/events", params={"session_id": session_id}).json()
    assert "input.too_long" in {e["rule"] for e in events}


# --- the approval surface: D9's gates over HTTP ---------------------------


def test_a_run_that_hits_a_gate_ends_the_stream_with_interrupt(client):
    api_main.set_graph_factory(_gated_graph)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]

    frames = _frames(
        client.post(f"/v1/sessions/{session_id}/messages", json={"message": "fix add"})
    )

    assert frames[-1]["event"] == "interrupt"
    assert frames[-1]["data"]["payload"]["kind"] == "plan_approval"


def test_approving_over_http_resumes_the_run(client):
    """The D12 DoD in one test: paused over SSE, resumed over HTTP, finished."""
    api_main.set_graph_factory(_gated_graph)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    client.post(f"/v1/sessions/{session_id}/messages", json={"message": "fix add"})

    frames = _frames(client.post(f"/v1/sessions/{session_id}/approve", json={"approved": True}))

    assert frames[-1]["event"] == "done"
    assert any(f["data"].get("node") == "gate" for f in frames if f["event"] == "node")


def test_rejecting_over_http_halts_with_a_reason(client):
    api_main.set_graph_factory(_gated_graph)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    client.post(f"/v1/sessions/{session_id}/messages", json={"message": "fix add"})

    frames = _frames(
        client.post(
            f"/v1/sessions/{session_id}/approve",
            json={"approved": False, "feedback": "wrong file"},
        )
    )

    assert frames[-1]["event"] == "done"
    halted = [f for f in frames if f["event"] == "node" and "halted" in f["data"]]
    assert halted and "rejected" in halted[0]["data"]["halted"]


# --- history and metrics --------------------------------------------------


def test_history_is_replayed_from_the_checkpointer(client):
    api_main.set_graph_factory(_straight_graph)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    client.post(f"/v1/sessions/{session_id}/messages", json={"message": "remember this"})

    body = client.get(f"/v1/sessions/{session_id}/history").json()

    assert body["session_id"] == session_id
    assert any("remember this" in m["content"] for m in body["messages"])


def test_history_for_an_unknown_thread_is_empty_not_an_error(client):
    body = client.get("/v1/sessions/never-existed/history").json()

    assert body["messages"] == []


def test_metrics_count_turns_and_latency_per_session(client):
    api_main.set_graph_factory(_straight_graph)
    session_id = client.post("/v1/sessions", json={}).json()["session_id"]
    client.post(f"/v1/sessions/{session_id}/messages", json={"message": "one"})
    client.post(f"/v1/sessions/{session_id}/messages", json={"message": "two"})

    body = client.get("/v1/metrics").json()

    assert body["sessions"] == 1
    assert body["totals"]["turns"] == 2
    assert body["per_session"][session_id]["latency_ms_last"] > 0
    assert body["guardrail_events"] > 0, "guardrail events are counted alongside"


def test_metrics_can_be_scoped_to_one_session(client):
    api_main.set_graph_factory(_straight_graph)
    first = client.post("/v1/sessions", json={}).json()["session_id"]
    client.post("/v1/sessions", json={})
    client.post(f"/v1/sessions/{first}/messages", json={"message": "hi"})

    body = client.get("/v1/metrics", params={"session_id": first}).json()

    assert body["sessions"] == 1
    assert list(body["per_session"]) == [first]


# --- C8: the route table and indexing -------------------------------------


def test_every_cahier_11_route_is_in_the_openapi_document(client):
    """C8's proof — `curl /openapi.json | jq '.paths|keys'` shows the §11 table."""
    paths = client.get("/openapi.json").json()["paths"]

    for route in (
        "/v1/sessions",
        "/v1/sessions/{session_id}/messages",
        "/v1/sessions/{session_id}/history",
        "/v1/sessions/{session_id}/approve",
        "/v1/index",
        "/v1/guardrails/events",
        "/v1/health",
        "/v1/metrics",
    ):
        assert route in paths, f"§11 requires {route}"


def test_indexing_is_accepted_as_a_background_task(client, monkeypatch):
    """202, not 200: the work has been accepted, not finished."""
    called: dict = {}

    def fake_index(path, **kwargs):
        called["path"] = str(path)
        called["full"] = kwargs.get("full")

        class Report:
            def summary(self):
                return "ok"

        return Report()

    monkeypatch.setattr("forge.rag.ingest.index_repo", fake_index)

    response = client.post("/v1/index", json={"path": "/tmp/x", "incremental": True})

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert called["full"] is False, "incremental is the inverse of full"


def test_cors_is_configured(client):
    response = client.get("/v1/health", headers={"Origin": "http://localhost:5173"})

    assert response.headers.get("access-control-allow-origin") == "*"
