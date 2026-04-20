"""
GET /search?q={query}

The PRIMARY endpoint — fast, cheap, NO AI.
Searches the authenticated user's OneDrive and returns file metadata only.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.jwt_validator import UserContext, get_current_user
from app.graph import client as graph
from app.models.schemas import SearchResponse, SearchResultItem

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Search"])


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="Search OneDrive documents",
    description=(
        "Search the authenticated user's OneDrive for documents matching the query. "
        "**No AI is used. No document content is fetched.** Designed to be fast and low-cost."
    ),
    responses={
        200: {"description": "Search results returned successfully"},
        400: {"description": "Invalid query parameter"},
        401: {"description": "Missing or invalid access token"},
        403: {"description": "Insufficient permissions"},
        504: {"description": "Graph API timed out"},
    },
)
async def search_documents(
    q: Annotated[
        str,
        Query(
            min_length=1,
            max_length=256,
            description="Search query string (filename, keyword, or phrase)",
            example="Q4 budget report",
        ),
    ],
    top: Annotated[
        int,
        Query(ge=1, le=50, description="Max number of results to return"),
    ] = 25,
    user: UserContext = Depends(get_current_user),
) -> SearchResponse:
    """
    Search the signed-in user's OneDrive.

    - Uses Microsoft Graph `search(q='...')` with delegated permissions.
    - Returns file name, web URL, last modified date, and file type.
    - ⚡ Zero AI cost — suitable for high-frequency Copilot Studio agent calls.
    """
    logger.info(
        "SEARCH | user=%s | query=%r | top=%d", user.upn, q, top
    )

    raw_items = await graph.search_drive(token=user.raw_token, query=q, top=top)

    results: list[SearchResultItem] = []
    for item in raw_items:
        try:
            results.append(
                SearchResultItem(
                    id=item["id"],
                    name=item.get("name", ""),
                    webUrl=item.get("webUrl", ""),
                    lastModifiedDateTime=item.get("lastModifiedDateTime"),
                    fileType=item.get("fileType", ""),
                )
            )
        except Exception:
            # Skip malformed items — don't let one bad item break the whole response
            logger.debug("Skipping malformed item: %s", item.get("id"))

    logger.info(
        "SEARCH | user=%s | query=%r | found=%d results", user.upn, q, len(results)
    )

    return SearchResponse(query=q, total=len(results), results=results)
