"""
JWT Validator — validates Azure AD (Entra ID) access tokens.

Flow:
  1. Extract Bearer token from Authorization header.
  2. Decode header to get `kid` (key ID).
  3. Fetch (and cache) JWKS from Azure's well-known endpoint.
  4. Verify signature, audience, issuer, and required scopes.
  5. Return a UserContext with the caller's identity.

⚠️  This enforces DELEGATED permissions only — no app-only tokens accepted.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from cachetools import TTLCache
from fastapi import HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

logger = logging.getLogger(__name__)

# JWKS cached for 24 hours — Azure rotates keys infrequently
_JWKS_CACHE: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=4, ttl=86_400)

_bearer_scheme = HTTPBearer(auto_error=True)


@dataclass(frozen=True)
class UserContext:
    """Extracted identity from a validated access token."""

    object_id: str        # Azure AD object ID (oid claim) — stable unique user ID
    upn: str              # User Principal Name (email-style login)
    display_name: str     # Display name (may be empty for guest accounts)
    raw_token: str        # Original bearer token — forwarded as-is to Graph API


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_jwks(tenant_id: str) -> dict[str, Any]:
    """Fetch (or return cached) JWKS for the given tenant."""
    cache_key = tenant_id
    if cache_key in _JWKS_CACHE:
        return _JWKS_CACHE[cache_key]

    jwks_uri = (
        f"https://login.microsoftonline.com/{tenant_id}"
        "/discovery/v2.0/keys"
    )
    try:
        resp = httpx.get(jwks_uri, timeout=10)
        resp.raise_for_status()
        jwks = resp.json()
        _JWKS_CACHE[cache_key] = jwks
        logger.info("JWKS fetched and cached for tenant %s", tenant_id)
        return jwks
    except Exception as exc:
        logger.error("Failed to fetch JWKS: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Unable to fetch Azure AD signing keys. Please retry.",
        ) from exc


def _find_rsa_key(jwks: dict[str, Any], unverified_header: dict[str, Any]) -> dict[str, Any]:
    """Find the matching RSA key from the JWKS by `kid`."""
    kid = unverified_header.get("kid")
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    raise HTTPException(
        status_code=401,
        detail=f"Signing key not found for kid={kid!r}. Token may be expired or tampered.",
    )


# ── Public API ─────────────────────────────────────────────────────────────────

def validate_token(token: str) -> UserContext:
    """
    Validate an Azure AD JWT access token and return the caller's identity.

    Raises HTTP 401 on any validation failure.
    """
    settings = get_settings()

    # Expected audience values — Azure AD can issue either format
    valid_audiences = [
        f"api://{settings.azure_client_id}",
        settings.azure_client_id,
    ]
    issuer = f"https://sts.windows.net/{settings.azure_tenant_id}/"
    issuer_v2 = (
        f"https://login.microsoftonline.com/{settings.azure_tenant_id}/v2.0"
    )

    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.exceptions.DecodeError as exc:
        raise HTTPException(status_code=401, detail="Malformed token header.") from exc

    jwks = _get_jwks(settings.azure_tenant_id)
    rsa_key = _find_rsa_key(jwks, unverified_header)

    # Try each valid audience — PyJWT raises InvalidAudienceError if none match
    payload: dict[str, Any] | None = None
    last_error: Exception | None = None

    for audience in valid_audiences:
        try:
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(rsa_key)  # type: ignore[attr-defined]
            payload = jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=audience,
                options={"verify_exp": True},
            )
            break
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(status_code=401, detail="Token has expired.") from exc
        except jwt.InvalidAudienceError as exc:
            last_error = exc
            continue
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail=f"Token validation failed: {exc}") from exc

    if payload is None:
        raise HTTPException(
            status_code=401,
            detail=f"Token audience does not match any accepted value: {valid_audiences}",
        ) from last_error

    # Verify issuer
    token_issuer = payload.get("iss", "")
    if token_issuer not in (issuer, issuer_v2):
        raise HTTPException(
            status_code=401,
            detail=f"Unexpected token issuer: {token_issuer!r}",
        )

    # Ensure this is a delegated token (has 'scp' claim, not just 'roles')
    if "scp" not in payload and "roles" not in payload:
        raise HTTPException(
            status_code=401,
            detail="Token is missing 'scp' claim. Only delegated permissions are accepted.",
        )

    # Check Files.Read is in scope (scp claim is space-separated)
    scopes = set(payload.get("scp", "").split())
    required = {"Files.Read"}
    # Also accept Files.ReadWrite or Files.Read.All as supersets
    allowed_scopes = {"Files.Read", "Files.ReadWrite", "Files.ReadWrite.All", "Files.Read.All"}
    if not scopes.intersection(allowed_scopes):
        raise HTTPException(
            status_code=403,
            detail=f"Token is missing required scope. Got: {scopes}. Need one of: {allowed_scopes}",
        )

    oid = payload.get("oid") or payload.get("sub")
    if not oid:
        raise HTTPException(status_code=401, detail="Token is missing 'oid' claim.")

    upn = (
        payload.get("upn")
        or payload.get("preferred_username")
        or payload.get("email")
        or "unknown@unknown"
    )

    return UserContext(
        object_id=oid,
        upn=upn,
        display_name=payload.get("name", ""),
        raw_token=token,
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_bearer_scheme),
) -> UserContext:
    """
    FastAPI dependency — extracts and validates the Bearer token.
    Inject with `Depends(get_current_user)` in any route.
    """
    return validate_token(credentials.credentials)
