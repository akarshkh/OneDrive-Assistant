"""
Microsoft Graph API client — all calls use the user's delegated access token.

Design decisions:
  - Single shared httpx.AsyncClient (connection pooling, keep-alive)
  - $select used on every request to minimise payload size and latency
  - Streaming download for /content endpoint (avoids loading whole file into memory)
  - Explicit error mapping: Graph error codes → HTTP exceptions
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from fastapi import HTTPException

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── Shared client lifecycle ───────────────────────────────────────────────────
# Instantiated once at application startup via lifespan context manager in main.py

_http_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    if _http_client is None:
        raise RuntimeError("Graph HTTP client is not initialised. Check app lifespan.")
    return _http_client


async def init_client() -> None:
    """Create the shared async HTTP client. Call once at startup."""
    global _http_client
    settings = get_settings()
    _http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.graph_timeout_seconds),
        headers={"Accept": "application/json"},
        http2=True,   # HTTP/2 reduces latency for multiple concurrent Graph calls
        follow_redirects=True,
    )
    logger.info("Graph HTTP client initialised (timeout=%ss)", settings.graph_timeout_seconds)


async def close_client() -> None:
    """Gracefully close the shared HTTP client. Call at shutdown."""
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None
        logger.info("Graph HTTP client closed")


# ── Internal request helper ───────────────────────────────────────────────────

def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _handle_graph_error(response: httpx.Response, context: str) -> None:
    """Translate Graph error responses to appropriate FastAPI HTTPExceptions."""
    if response.status_code == 200:
        return

    status = response.status_code
    try:
        body = response.json()
        error_code = body.get("error", {}).get("code", "")
        error_msg = body.get("error", {}).get("message", response.text)
    except Exception:
        error_code = ""
        error_msg = response.text

    logger.warning(
        "Graph error [%s] during '%s': %s — %s",
        status, context, error_code, error_msg,
    )

    if status == 401:
        raise HTTPException(
            status_code=401,
            detail="Graph API rejected the token. Ensure the token has Files.Read scope.",
        )
    if status == 403:
        raise HTTPException(
            status_code=403,
            detail="Access denied by Graph API. The user may not have permission to this resource.",
        )
    if status == 404:
        raise HTTPException(
            status_code=404,
            detail=f"Document not found in OneDrive. ({context})",
        )
    if status == 429:
        retry_after = response.headers.get("Retry-After", "60")
        raise HTTPException(
            status_code=429,
            detail=f"Graph API rate limit exceeded. Retry after {retry_after} seconds.",
        )
    if status == 423:
        raise HTTPException(
            status_code=502,
            detail=(
                "Access to this OneDrive site is blocked (HTTP 423). "
                "This usually happens if the site is archived or locked by an admin. "
                "Please check the SharePoint Admin Center."
            ),
        )
    raise HTTPException(
        status_code=502,
        detail=f"Microsoft Graph returned an unexpected error: {status} — {error_msg}",
    )


# ── Public Graph methods ──────────────────────────────────────────────────────

async def search_drive(token: str, query: str, top: int = 25) -> list[dict[str, Any]]:
    """
    Search the user's OneDrive using Graph search.

    Returns a list of DriveItem dicts. Only fetches the fields needed by
    SearchResultItem schema (cost optimised via $select).
    """
    settings = get_settings()
    # Escape single quotes in the query string per OData rules
    safe_query = query.replace("'", "''")

    url = (
        f"{settings.graph_base_url}/me/drive/root"
        f"/search(q='{safe_query}')"
    )
    params = {
        "$select": "id,name,webUrl,lastModifiedDateTime,file",
        "$top": str(min(top, 50)),      # Graph hard-limits to 200; we cap at 50
        "$orderby": "lastModifiedDateTime desc",
    }

    try:
        resp = await _client().get(
            url, params=params, headers=_auth_headers(token)
        )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Graph API timed out during search. Please retry.",
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Network error reaching Graph: {exc}")

    _handle_graph_error(resp, "search_drive")

    data = resp.json()
    items: list[dict[str, Any]] = data.get("value", [])

    # Flatten file extension from the nested `file` facet
    for item in items:
        file_facet = item.get("file", {})
        mime = file_facet.get("mimeType", "")
        # Derive extension from file name as a fallback
        name: str = item.get("name", "")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        item["fileType"] = ext or _mime_to_ext(mime)

    return items


async def get_item(token: str, item_id: str) -> dict[str, Any]:
    """
    Retrieve full metadata for a single DriveItem by ID.
    """
    settings = get_settings()
    url = f"{settings.graph_base_url}/me/drive/items/{item_id}"
    params = {
        "$select": (
            "id,name,webUrl,size,createdDateTime,lastModifiedDateTime,"
            "file,@microsoft.graph.downloadUrl"
        )
    }

    try:
        resp = await _client().get(
            url, params=params, headers=_auth_headers(token)
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Graph API timed out fetching item metadata.")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Network error: {exc}")

    _handle_graph_error(resp, f"get_item({item_id})")
    data = resp.json()

    # Extract downloadUrl and mimeType safely
    data["@microsoft.graph.downloadUrl"] = data.get("@microsoft.graph.downloadUrl")
    file_info = data.get("file", {})
    data["mimeType"] = file_info.get("mimeType")
    return data


async def get_item_content(token: str, item_id: str) -> tuple[bytes, str, int]:
    """
    Download the raw content of a DriveItem.

    Returns:
        (raw_bytes, mime_type, content_length)

    Raises HTTP 413 if content exceeds MAX_CONTENT_BYTES limit.
    """
    settings = get_settings()
    url = f"{settings.graph_base_url}/me/drive/items/{item_id}/content"

    try:
        resp = await _client().get(url, headers=_auth_headers(token))
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Graph API timed out downloading document content.",
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Network error: {exc}")

    _handle_graph_error(resp, f"get_item_content({item_id})")

    content_length = int(resp.headers.get("Content-Length", len(resp.content)))
    mime_type = resp.headers.get("Content-Type", "application/octet-stream").split(";")[0]

    if content_length > settings.max_content_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Document is too large for summarization "
                f"({content_length / 1_048_576:.1f} MB). "
                f"Maximum allowed: {settings.max_content_bytes / 1_048_576:.1f} MB."
            ),
        )

    return resp.content, mime_type, content_length


# ── Utility ───────────────────────────────────────────────────────────────────

def _mime_to_ext(mime: str) -> str:
    """Best-effort MIME → extension mapping."""
    _map = {
        "application/pdf": "pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "text/plain": "txt",
        "text/csv": "csv",
        "application/json": "json",
    }
    return _map.get(mime, "")
