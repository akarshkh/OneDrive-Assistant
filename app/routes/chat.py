"""
POST /chat

High-interactivity Document Q&A. 
Downloads content, extracts text, and asks a specific user question.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth.jwt_validator import UserContext, get_current_user
from app.graph import client as graph
from app.models.schemas import ChatRequest, ChatResponse
from app.services import ai_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Document"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Ask a question about a document (AI Q&A)",
    description=(
        "Downloads the document content and asks a specific question about it. "
        "The AI will ONLY use information found within the document to answer."
    ),
    responses={
        200: {"description": "Answer returned"},
        401: {"description": "Missing or invalid access token"},
        403: {"description": "Access denied"},
        404: {"description": "Document not found"},
        422: {"description": "Unable to extract text from document"},
        502: {"description": "AI provider error"},
    },
)
async def chat_with_document(
    body: ChatRequest,
    user: UserContext = Depends(get_current_user),
) -> ChatResponse:
    """
    Query a document using AI.
    """
    document_id = body.document_id
    question = body.question

    logger.info(
        "CHAT | user=%s | document_id=%s | question=%s",
        user.upn, document_id, question[:50],
    )

    # 1. Fetch item metadata
    item = await graph.get_item(token=user.raw_token, item_id=document_id)
    document_name: str = item.get("name", "Unknown Document")

    # 2. Download content
    raw_bytes, mime_type, _ = await graph.get_item_content(
        token=user.raw_token, item_id=document_id
    )

    # 3. Ask question
    result = await ai_service.ask_document_question(
        document_name=document_name,
        raw_bytes=raw_bytes,
        mime_type=mime_type,
        question=question,
    )

    return ChatResponse(
        answer=result.answer,
        documentName=document_name,
        modelUsed=result.model_used,
    )
