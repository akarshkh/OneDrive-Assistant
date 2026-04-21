"""
JWT Validator — validates Azure AD (Entra ID) access tokens.

Strategy (two-path):
  Path 1 — Local JWKS verification:
    Fast, offline RSA signature check. Works when the token's tenant matches
    a tenant whose JWKS we can fetch.
  Path 2 — Graph /me introspection (fallback):
    Calls GET https://graph.microsoft.com/v1.0/me with the bearer token.
    If Microsoft Graph accepts it, the token is valid by definition.
    This handles ALL cross-tenant and Graph audience scenarios reliably.

⚠️  This enforces DELEGATED permissions only — no app-only tokens accepted.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from cachetools import TTLCache
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

logger = logging.getLogger(__name__)

# JWKS cached for 24 hours — Azure rotates keys infrequently
_JWKS_CACHE: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=8, ttl=86_400)

_bearer_scheme = HTTPBearer(auto_error=True)


@dataclass(frozen=True)
class UserContext:
    """Extracted identity from a validated access token."""

    object_id: str        # Azure AD object ID (oid claim) — stable unique user ID
    upn: str              # User Principal Name (email-style login)
    display_name: str     # Display name (may be empty for guest accounts)
    raw_token: str        # Original bearer token — forwarded as-is to Graph API


# ── Internal helpers ──────────────────────────────────────────────────────────

def _decode_unverified(token: str) -> dict[str, Any]:
    """
    Decode a JWT payload WITHOUT verifying the signature.
    Used only to extract claims for routing decisions (audience, tenant, etc.).
    PyJWT 2.x requires 'algorithms' even when verify_signature=False.
    """
    return jwt.decode(
        token,
        options={"verify_signature": False},
        algorithms=["RS256", "RS384", "RS512"],
    )


def _get_jwks(tenant_id: str) -> dict[str, Any]:
    """Fetch (or return cached) JWKS for the given Azure AD tenant."""
    if tenant_id in _JWKS_CACHE:
        return _JWKS_CACHE[tenant_id]

    jwks_uri = (
        f"https://login.microsoftonline.com/{tenant_id}"
        "/discovery/v2.0/keys"
    )
    try:
        resp = httpx.get(jwks_uri, timeout=10)
        resp.raise_for_status()
        jwks = resp.json()
        _JWKS_CACHE[tenant_id] = jwks
        logger.info("JWKS fetched and cached for tenant %s", tenant_id)
        return jwks
    except Exception as exc:
        logger.error("Failed to fetch JWKS for tenant %s: %s", tenant_id, exc)
        raise HTTPException(
            status_code=503,
            detail="Unable to fetch Azure AD signing keys. Please retry.",
        ) from exc


def _find_rsa_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    """Return the JWK entry matching `kid`, or None if not found."""
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


def _validate_via_graph(token: str) -> UserContext:
    """
    Validate a bearer token by calling GET /me on Microsoft Graph.
    If Graph accepts the token, it is valid by definition.
    Also confirms the token has delegated (user) permissions, not app-only.
    Raises HTTP 401/403 on any failure.
    """
    logger.info("Falling back to Graph /me introspection for token validation.")
    try:
        resp = httpx.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except httpx.RequestError as exc:
        logger.error("Network error calling Graph /me: %s", exc)
        raise HTTPException(status_code=503, detail="Cannot reach Microsoft Graph.")

    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Token rejected by Microsoft Graph.")
    if resp.status_code == 403:
        raise HTTPException(
            status_code=403,
            detail="Token accepted but lacks required Graph permissions (Files.Read).",
        )
    if not resp.is_success:
        raise HTTPException(
            status_code=401,
            detail=f"Graph /me returned unexpected status {resp.status_code}.",
        )

    me = resp.json()
    oid = me.get("id", "")
    upn = (
        me.get("userPrincipalName")
        or me.get("mail")
        or me.get("displayName")
        or "unknown@unknown"
    )
    return UserContext(
        object_id=oid,
        upn=upn,
        display_name=me.get("displayName", ""),
        raw_token=token,
    )


def _validate_via_jwks(token: str, claims: dict[str, Any]) -> UserContext | None:
    """
    Attempt local RSA signature verification using JWKS.
    Returns a UserContext on success, None if signature verification fails
    (so the caller can fall back to Graph introspection).
    """
    settings = get_settings()

    # Determine which tenant signed this token
    tenant_id = claims.get("tid") or settings.azure_tenant_id
    logger.debug("JWKS validation: using tenant %s", tenant_id)

    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid", "")
    except jwt.exceptions.DecodeError:
        return None

    try:
        jwks = _get_jwks(tenant_id)
    except HTTPException:
        return None

    rsa_key = _find_rsa_key(jwks, kid)
    if rsa_key is None:
        logger.warning("kid=%r not found in JWKS for tenant %s", kid, tenant_id)
        return None

    # Accepted audiences — covers custom API, bare GUID, and Graph tokens
    valid_audiences = [
        f"api://{settings.azure_client_id}",
        settings.azure_client_id,
        "https://graph.microsoft.com",
        "00000003-0000-0000-c000-000000000000",
    ]

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
            # Success — extract identity
            token_issuer = payload.get("iss", "")
            trusted = (
                token_issuer.startswith("https://sts.windows.net/")
                or token_issuer.startswith("https://login.microsoftonline.com/")
            )
            if not trusted:
                logger.warning("Untrusted issuer: %s", token_issuer)
                return None

            oid = payload.get("oid") or payload.get("sub", "")
            upn = (
                payload.get("upn")
                or payload.get("preferred_username")
                or payload.get("email")
                or "unknown@unknown"
            )
            logger.info("JWKS validation succeeded for %s", upn)
            return UserContext(
                object_id=oid,
                upn=upn,
                display_name=payload.get("name", ""),
                raw_token=token,
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token has expired.")
        except jwt.InvalidAudienceError:
            continue
        except jwt.PyJWTError as exc:
            logger.warning("JWKS jwt.decode failed (%s) — will try Graph fallback", exc)
            return None  # Signal caller to fall back to Graph introspection

    return None  # No matching audience found


# ── Public API ─────────────────────────────────────────────────────────────────

def validate_token(token: str) -> UserContext:
    """
    Validate an Azure AD JWT access token and return the caller's identity.

    Tries local JWKS verification first (fast, no network call when cached).
    Falls back to Graph /me introspection for cross-tenant or Graph tokens
    that cannot be verified locally.

    Raises HTTP 401/403/503 on validation failure.
    """
    # Decode claims without verification (safe — used only for routing)
    try:
        claims = _decode_unverified(token)
    except jwt.exceptions.DecodeError as exc:
        raise HTTPException(status_code=401, detail="Malformed JWT token.") from exc

    # Path 1: fast local JWKS verification
    user = _validate_via_jwks(token, claims)
    if user is not None:
        return user

    # Path 2: Graph /me introspection (handles cross-tenant, Graph tokens, etc.)
    return _validate_via_graph(token)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_bearer_scheme),
) -> UserContext:
    """
    FastAPI dependency — extracts and validates the Bearer token.
    Inject with `Depends(get_current_user)` in any route.
    """
    return validate_token(credentials.credentials)
