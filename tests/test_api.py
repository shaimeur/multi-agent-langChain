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
    assert "/v1/health" in schema.json()["paths"]
