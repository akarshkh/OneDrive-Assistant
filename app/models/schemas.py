"""
Pydantic v2 schemas for all API request and response models.
Strict types ensure clean JSON for Copilot Studio consumption.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


# ── Search ────────────────────────────────────────────────────────────────────

class SearchResultItem(BaseModel):
    """A single file returned by the OneDrive search."""

    id: str = Field(..., description="OneDrive item ID")
    name: str = Field(..., description="File name with extension")
    web_url: str = Field(..., alias="webUrl", description="Direct browser URL to the file")
    last_modified: Optional[datetime] = Field(
        None, alias="lastModifiedDateTime", description="Last modification timestamp (UTC)"
    )
    file_type: Optional[str] = Field(
        None, description="File extension (e.g. docx, pdf, xlsx)"
    )

    model_config = {"populate_by_name": True}


class SearchResponse(BaseModel):
    """Response envelope for GET /search."""

    query: str = Field(..., description="The search query that was executed")
    total: int = Field(..., description="Number of results returned")
    results: list[SearchResultItem] = Field(..., description="List of matching documents")


# ── Document Detail ───────────────────────────────────────────────────────────

class DocumentDetail(BaseModel):
    """Full metadata for a single OneDrive item."""

    id: str
    name: str
    web_url: str = Field(..., alias="webUrl")
    size: Optional[int] = Field(None, description="File size in bytes")
    created_at: Optional[datetime] = Field(None, alias="createdDateTime")
    last_modified: Optional[datetime] = Field(None, alias="lastModifiedDateTime")
    mime_type: Optional[str] = Field(None, description="MIME type of the file (if available)")
    download_url: Optional[str] = Field(
        None,
        alias="@microsoft.graph.downloadUrl",
        description="Pre-authenticated download URL (short-lived)",
    )

    model_config = {"populate_by_name": True}


# ── Summarize ─────────────────────────────────────────────────────────────────

class SummarizeRequest(BaseModel):
    """Request body for POST /summarize."""

    document_id: str = Field(
        ...,
        alias="documentId",
        description="The OneDrive item ID to summarize",
    )
    max_tokens: int = Field(
        500,
        alias="maxTokens",
        ge=100,
        le=2000,
        description="Max tokens for the AI response (controls cost)",
    )

    model_config = {"populate_by_name": True}


class SummaryResponse(BaseModel):
    """AI-generated summary returned by POST /summarize."""

    document_id: str = Field(..., alias="documentId")
    document_name: str = Field(..., alias="documentName")
    summary: str = Field(..., description="3–5 line plain-text summary of the document")
    key_points: list[str] = Field(
        ..., alias="keyPoints", description="Bullet-style key points extracted from the document"
    )
    cached: bool = Field(
        ..., description="True if this summary was served from cache (no AI cost incurred)"
    )
    model_used: Optional[str] = Field(
        None, alias="modelUsed", description="AI model that generated this summary"
    )

    model_config = {"populate_by_name": True}


# ── Errors ────────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    """Standardised error envelope."""

    error: str = Field(..., description="Short error code")
    message: str = Field(..., description="Human-readable error description")
    details: Optional[str] = Field(None, description="Additional debug information (dev only)")


# ── Health ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    ai_provider: str
