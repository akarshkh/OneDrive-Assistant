"""
GET /document/{id}

Returns rich metadata for a single OneDrive document by its item ID.
No AI. No content download.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Path

from app.auth.jwt_validator import UserContext, get_current_user
from app.graph import client as graph
from app.models.schemas import DocumentDetail

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Document"])


@router.get(
    "/document/{item_id}",
    response_model=DocumentDetail,
    summary="Get document details",
    description=(
        "Retrieve metadata and a short-lived download URL for a specific OneDrive document. "
        "**No AI is used. Document content is not read.**"
    ),
    responses={
        200: {"description": "Document metadata returned"},
        401: {"description": "Missing or invalid access token"},
        403: {"description": "Access denied — user does not own this document"},
        404: {"description": "Document not found in user's OneDrive"},
    },
)
async def get_document(
    item_id: str = Path(
        ...,
        min_length=1,
        description="OneDrive item ID (obtained from /search results)",
        example="01BYE5RZ6QN3ZWBTUCIRGZDCTLIGEN4U7XT",
    ),
    user: UserContext = Depends(get_current_user),
) -> DocumentDetail:
    """
    Get detailed metadata for a single document.

    Returns size, creation/modification timestamps, web URL, MIME type,
    and a pre-authenticated download URL (valid for ~1 hour).
    """
    logger.info("DOCUMENT | user=%s | item_id=%s", user.upn, item_id)

    item = await graph.get_item(token=user.raw_token, item_id=item_id)

    return DocumentDetail(
        id=item["id"],
        name=item.get("name", ""),
        webUrl=item.get("webUrl", ""),
        size=item.get("size"),
        createdDateTime=item.get("createdDateTime"),
        lastModifiedDateTime=item.get("lastModifiedDateTime"),
        mimeType=item.get("mimeType"),
        **{
            "@microsoft.graph.downloadUrl": item.get(
                "@microsoft.graph.downloadUrl"
            )
        }
        if item.get("@microsoft.graph.downloadUrl")
        else {},
    )
