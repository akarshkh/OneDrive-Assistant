"""
Integration-style tests for the FastAPI routes.
Uses FastAPI TestClient with mocked auth and Graph dependencies.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt_validator import UserContext

# ── Shared fake data ───────────────────────────────────────────────────────────

FAKE_USER = UserContext(
    object_id="user-oid-123",
    upn="test@contoso.com",
    display_name="Test User",
    raw_token="fake-token",
)

FAKE_SEARCH_ITEMS = [
    {
        "id": "item-001",
        "name": "Budget.xlsx",
        "webUrl": "https://onedrive.live.com/edit?id=item-001",
        "lastModifiedDateTime": "2024-11-15T09:23:00Z",
        "fileType": "xlsx",
    },
    {
        "id": "item-002",
        "name": "Report.pdf",
        "webUrl": "https://onedrive.live.com/view?id=item-002",
        "lastModifiedDateTime": "2024-11-10T14:05:00Z",
        "fileType": "pdf",
    },
]

FAKE_ITEM = {
    "id": "item-001",
    "name": "Budget.xlsx",
    "webUrl": "https://onedrive.live.com/edit?id=item-001",
    "size": 524288,
    "createdDateTime": "2024-10-01T08:00:00Z",
    "lastModifiedDateTime": "2024-11-15T09:23:00Z",
    "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    from app import config

    class FakeSettings:
        azure_tenant_id = "fake-tenant"
        azure_client_id = "fake-client"
        ai_provider = "openai"
        openai_api_key = "sk-test"
        openai_model = "gpt-4o-mini"
        azure_openai_api_key = ""
        azure_openai_endpoint = ""
        azure_openai_deployment = "gpt-4o-mini"
        azure_openai_api_version = "2024-02-01"
        max_content_bytes = 1_572_864
        summarize_max_chars = 12_000
        summary_cache_ttl = 3600
        summary_cache_max_size = 500
        graph_base_url = "https://graph.microsoft.com/v1.0"
        graph_timeout_seconds = 30
        allowed_origins = ["*"]
        log_level = "DEBUG"

    monkeypatch.setattr(config, "get_settings", lambda: FakeSettings())


@pytest.fixture()
def client():
    """Return a TestClient with Graph client init mocked."""
    with (
        patch("app.graph.client.init_client", new_callable=AsyncMock),
        patch("app.graph.client.close_client", new_callable=AsyncMock),
    ):
        from app.main import create_app

        app = create_app()

        # Override auth dependency for all tests
        from app.auth.jwt_validator import get_current_user

        app.dependency_overrides[get_current_user] = lambda: FAKE_USER

        with TestClient(app) as c:
            yield c


# ── Health ─────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body


# ── Search ─────────────────────────────────────────────────────────────────────

class TestSearch:
    def test_search_returns_results(self, client):
        with patch(
            "app.graph.client.search_drive",
            new_callable=AsyncMock,
            return_value=FAKE_SEARCH_ITEMS,
        ):
            resp = client.get("/search?q=budget")

        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "budget"
        assert body["total"] == 2
        assert body["results"][0]["name"] == "Budget.xlsx"
        assert body["results"][0]["fileType"] == "xlsx"

    def test_search_requires_query(self, client):
        resp = client.get("/search")
        assert resp.status_code == 422   # FastAPI validation error

    def test_search_empty_query_rejected(self, client):
        resp = client.get("/search?q=")
        assert resp.status_code == 422

    def test_search_returns_x_request_id(self, client):
        with patch(
            "app.graph.client.search_drive",
            new_callable=AsyncMock,
            return_value=[],
        ):
            resp = client.get("/search?q=test")
        assert "x-request-id" in resp.headers
        assert "x-response-time-ms" in resp.headers


# ── Document ───────────────────────────────────────────────────────────────────

class TestDocument:
    def test_get_document_returns_metadata(self, client):
        with patch(
            "app.graph.client.get_item",
            new_callable=AsyncMock,
            return_value=FAKE_ITEM,
        ):
            resp = client.get("/document/item-001")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "item-001"
        assert body["name"] == "Budget.xlsx"
        assert body["size"] == 524288

    def test_get_document_404(self, client):
        from fastapi import HTTPException

        with patch(
            "app.graph.client.get_item",
            new_callable=AsyncMock,
            side_effect=HTTPException(status_code=404, detail="Not found"),
        ):
            resp = client.get("/document/nonexistent-id")

        assert resp.status_code == 404


# ── Summarize ──────────────────────────────────────────────────────────────────

class TestSummarize:
    def test_summarize_returns_summary(self, client):
        from app.services.ai_service import SummaryResult

        with (
            patch(
                "app.graph.client.get_item",
                new_callable=AsyncMock,
                return_value=FAKE_ITEM,
            ),
            patch(
                "app.graph.client.get_item_content",
                new_callable=AsyncMock,
                return_value=(b"Hello world document content", "text/plain", 28),
            ),
            patch(
                "app.services.ai_service.summarize_document",
                new_callable=AsyncMock,
                return_value=SummaryResult(
                    summary="This is a budget document.",
                    key_points=["Revenue up 12%", "Expenses within budget"],
                    cached=False,
                    model_used="gpt-4o-mini",
                ),
            ),
        ):
            resp = client.post(
                "/summarize",
                json={"documentId": "item-001", "maxTokens": 500},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["documentId"] == "item-001"
        assert "summary" in body
        assert isinstance(body["keyPoints"], list)
        assert body["cached"] is False

    def test_summarize_missing_document_id(self, client):
        resp = client.post("/summarize", json={"maxTokens": 500})
        assert resp.status_code == 422

    def test_summarize_max_tokens_out_of_range(self, client):
        resp = client.post(
            "/summarize", json={"documentId": "item-001", "maxTokens": 9999}
        )
        assert resp.status_code == 422
