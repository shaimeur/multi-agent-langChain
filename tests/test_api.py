"""The health probe is what the compose healthcheck gates `depends_on` upon."""

from __future__ import annotations

from fastapi.testclient import TestClient

from forge.api.main import app

client = TestClient(app)


def test_health_reports_ok():
    response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_reports_the_offline_posture():
    """The demo runs offline; the probe has to make that visible, not implicit."""
    body = client.get("/v1/health").json()

    assert set(body) >= {"llm_provider", "cache_mode", "offline"}
    assert body["offline"] is (body["cache_mode"] == "replay")


def test_unversioned_alias_matches():
    assert client.get("/health").json() == client.get("/v1/health").json()


def test_openapi_schema_is_generated():
    """Cahier C8 — an accessible OpenAPI document is an acceptance criterion."""
    schema = client.get("/openapi.json")

    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/v1/health" in paths
    assert "/v1/search" in paths
    assert "/v1/ask" in paths


def test_search_route_returns_hits(monkeypatch):
    """The route parses the request, routes by query shape and shapes the hits.
    Retrieval itself is stubbed — its behaviour is covered by test_retrieve."""
    from forge.models import Chunk, ChunkKind, Retriever, SearchHit

    hit = SearchHit(
        chunk=Chunk(
            chunk_id="a",
            repo="r",
            path="sqlparse/engine/grouping.py",
            language="python",
            kind=ChunkKind.FUNCTION,
            symbol="group_functions",
            start_line=10,
            end_line=20,
            text="t",
            raw="t",
        ),
        score=0.42,
        retrievers=[Retriever.DENSE, Retriever.SPARSE],
    )
    monkeypatch.setattr("forge.api.main.hybrid_search", lambda *a, **k: [hit])

    response = client.post("/v1/search", json={"query": "how are functions grouped", "k": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "semantic"
    assert body["hits"][0]["path"] == "sqlparse/engine/grouping.py"
    assert body["hits"][0]["retrievers"] == "dense+sparse"


def test_ask_route_returns_a_grounded_answer(monkeypatch):
    """The route hands back the GroundedAnswer schema. The answer path is stubbed —
    its grounding logic is covered by test_answer."""
    from forge.models import Citation, GroundedAnswer

    canned = GroundedAnswer(
        question="q",
        answer="Statements are split by StatementSplitter [1].",
        grounded=True,
        citations=[
            Citation(
                chunk_id="a",
                path="sqlparse/engine/statement_splitter.py",
                start_line=11,
                end_line=144,
            )
        ],
    )
    monkeypatch.setattr("forge.api.main.answer_question", lambda *a, **k: canned)

    response = client.post("/v1/ask", json={"question": "how are statements split", "k": 4})

    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is True
    assert body["citations"][0]["path"] == "sqlparse/engine/statement_splitter.py"
