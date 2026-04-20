"""
POST /summarize

ON-DEMAND AI summarization — called ONLY when the user explicitly requests it.

Pipeline:
  1. Validate JWT.
  2. Check in-memory summary cache → return immediately if hit (zero AI cost).
  3. Download document content from Graph (size-gated).
  4. Extract text (PDF / DOCX / plain-text).
  5. Truncate to token budget.
  6. Call OpenAI / Azure OpenAI.
  7. Cache result.
  8. Return structured summary + key points.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth.jwt_validator import UserContext, get_current_user
from app.graph import client as graph
from app.models.schemas import SummarizeRequest, SummaryResponse
from app.services import ai_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Summarize"])


@router.post(
    "/summarize",
    response_model=SummaryResponse,
    summary="Summarize a document (AI — on-demand only)",
    description=(
        "Downloads the document content and sends it to an AI model for summarization. "
        "**AI is ONLY invoked by this endpoint — never automatically.** "
        "Results are cached in-memory (TTL: 1 hour) to prevent repeated billing "
        "for the same document."
    ),
    responses={
        200: {"description": "Summary returned"},
        401: {"description": "Missing or invalid access token"},
        403: {"description": "Access denied"},
        404: {"description": "Document not found"},
        413: {"description": "Document too large for summarization (> 1.5 MB)"},
        422: {"description": "Unable to extract text from document"},
        502: {"description": "AI provider error"},
        504: {"description": "Graph API timeout"},
    },
)
async def summarize_document(
    body: SummarizeRequest,
    user: UserContext = Depends(get_current_user),
) -> SummaryResponse:
    """
    Summarize a OneDrive document using AI.

    - Checks cache first; returns immediately if already summarized.
    - Downloads document bytes (max 1.5 MB).
    - Extracts text from PDF, DOCX, or plain-text formats.
    - Sends truncated text to OpenAI / Azure OpenAI.
    - Returns 3–5 sentence summary + bullet key points.
    """
    document_id = body.document_id
    max_tokens = body.max_tokens

    logger.info(
        "SUMMARIZE | user=%s | document_id=%s | max_tokens=%d",
        user.upn, document_id, max_tokens,
    )

    # ── Step 1: Fetch item metadata (to get the file name) ────────────────
    item = await graph.get_item(token=user.raw_token, item_id=document_id)
    document_name: str = item.get("name", "Unknown Document")

    # ── Step 2: Check cache before downloading content ────────────────────
    from app.services.ai_service import _get_cache  # noqa: PLC0415

    cache = _get_cache()
    if document_id in cache:
        cached = cache[document_id]
        logger.info("SUMMARIZE | cache HIT | document_id=%s", document_id)
        return SummaryResponse(
            documentId=document_id,
            documentName=document_name,
            summary=cached["summary"],
            keyPoints=cached["key_points"],
            cached=True,
            modelUsed=cached["model_used"],
        )

    # ── Step 3: Download content (size-gated inside graph.get_item_content) ──
    logger.info("SUMMARIZE | downloading content | document_id=%s", document_id)
    raw_bytes, mime_type, content_length = await graph.get_item_content(
        token=user.raw_token, item_id=document_id
    )
    logger.info(
        "SUMMARIZE | downloaded %d bytes | mime=%s | document_id=%s",
        content_length, mime_type, document_id,
    )

    # ── Step 4: Summarize (extract → truncate → AI → cache) ──────────────
    result = await ai_service.summarize_document(
        document_id=document_id,
        document_name=document_name,
        raw_bytes=raw_bytes,
        mime_type=mime_type,
        max_tokens=max_tokens,
    )

    logger.info(
        "SUMMARIZE | complete | cached=%s | model=%s | document_id=%s",
        result.cached, result.model_used, document_id,
    )

    return SummaryResponse(
        documentId=document_id,
        documentName=document_name,
        summary=result.summary,
        keyPoints=result.key_points,
        cached=result.cached,
        modelUsed=result.model_used,
    )
