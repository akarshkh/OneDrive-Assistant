"""
Tests for the JWT validator module.
Uses unittest.mock to avoid real Azure AD calls.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from app.auth.jwt_validator import UserContext, validate_token


# ── Fixtures ──────────────────────────────────────────────────────────────────

FAKE_TENANT = "00000000-0000-0000-0000-000000000001"
FAKE_CLIENT = "00000000-0000-0000-0000-000000000002"

FAKE_PAYLOAD = {
    "oid": "user-object-id-123",
    "upn": "test.user@contoso.com",
    "name": "Test User",
    "scp": "Files.Read",
    "iss": f"https://sts.windows.net/{FAKE_TENANT}/",
    "aud": f"api://{FAKE_CLIENT}",
    "exp": int(time.time()) + 3600,
}


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    """Patch settings so we don't need a real .env file during tests."""
    from app import config

    class FakeSettings:
        azure_tenant_id = FAKE_TENANT
        azure_client_id = FAKE_CLIENT
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


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestValidateToken:
    def test_valid_token_returns_user_context(self):
        """A well-formed token with correct claims returns UserContext."""
        with (
            patch("app.auth.jwt_validator._get_jwks", return_value={"keys": [{"kid": "testkey"}]}),
            patch("app.auth.jwt_validator._find_rsa_key", return_value={"kid": "testkey"}),
            patch("jwt.algorithms.RSAAlgorithm.from_jwk", return_value=MagicMock()),
            patch("jwt.decode", return_value=FAKE_PAYLOAD),
        ):
            result = validate_token("fake.jwt.token")

        assert isinstance(result, UserContext)
        assert result.object_id == "user-object-id-123"
        assert result.upn == "test.user@contoso.com"
        assert result.display_name == "Test User"
        assert result.raw_token == "fake.jwt.token"

    def test_expired_token_raises_401(self):
        """An expired token raises HTTP 401."""
        import jwt as _jwt
        from fastapi import HTTPException

        with (
            patch("app.auth.jwt_validator._get_jwks", return_value={"keys": [{"kid": "k"}]}),
            patch("app.auth.jwt_validator._find_rsa_key", return_value={"kid": "k"}),
            patch("jwt.algorithms.RSAAlgorithm.from_jwk", return_value=MagicMock()),
            patch("jwt.decode", side_effect=_jwt.ExpiredSignatureError("expired")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                validate_token("expired.jwt.token")

        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_missing_scope_raises_403(self):
        """A token without Files.Read scope raises HTTP 403."""
        from fastapi import HTTPException

        bad_payload = {**FAKE_PAYLOAD, "scp": "User.Read"}

        with (
            patch("app.auth.jwt_validator._get_jwks", return_value={"keys": [{"kid": "k"}]}),
            patch("app.auth.jwt_validator._find_rsa_key", return_value={"kid": "k"}),
            patch("jwt.algorithms.RSAAlgorithm.from_jwk", return_value=MagicMock()),
            patch("jwt.decode", return_value=bad_payload),
        ):
            with pytest.raises(HTTPException) as exc_info:
                validate_token("bad.scope.token")

        assert exc_info.value.status_code == 403

    def test_wrong_issuer_raises_401(self):
        """A token from a different tenant raises HTTP 401."""
        from fastapi import HTTPException

        bad_payload = {**FAKE_PAYLOAD, "iss": "https://sts.windows.net/other-tenant/"}

        with (
            patch("app.auth.jwt_validator._get_jwks", return_value={"keys": [{"kid": "k"}]}),
            patch("app.auth.jwt_validator._find_rsa_key", return_value={"kid": "k"}),
            patch("jwt.algorithms.RSAAlgorithm.from_jwk", return_value=MagicMock()),
            patch("jwt.decode", return_value=bad_payload),
        ):
            with pytest.raises(HTTPException) as exc_info:
                validate_token("wrong.tenant.token")

        assert exc_info.value.status_code == 401
        assert "issuer" in exc_info.value.detail.lower()

    def test_malformed_token_raises_401(self):
        """A completely malformed token raises HTTP 401."""
        import jwt as _jwt
        from fastapi import HTTPException

        with patch(
            "jwt.get_unverified_header",
            side_effect=_jwt.exceptions.DecodeError("bad format"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                validate_token("not.a.jwt")

        assert exc_info.value.status_code == 401
