"""
AI Summarization Service.

Responsibilities:
  1. Extract readable text from raw document bytes (text/PDF/DOCX).
  2. Truncate to stay within token budget.
  3. Call OpenAI or Azure OpenAI for summary + key points.
  4. Cache the result to avoid repeated AI calls for the same document.

Cost control:
  - summarize_max_chars limit (~3 000 tokens) before sending to AI.
  - TTLCache prevents billing for the same document twice within the TTL window.
  - gpt-4o-mini default (~$0.00015/1K input tokens) keeps per-call cost under $0.005.
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from cachetools import TTLCache
from fastapi import HTTPException

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── Summary cache ─────────────────────────────────────────────────────────────
# Keyed by document item ID — avoids repeated AI calls within the TTL window.
_summary_cache: TTLCache[str, dict[str, Any]] | None = None


def _get_cache() -> TTLCache[str, dict[str, Any]]:
    global _summary_cache
    if _summary_cache is None:
        settings = get_settings()
        _summary_cache = TTLCache(
            maxsize=settings.summary_cache_max_size,
            ttl=settings.summary_cache_ttl,
        )
        logger.info(
            "Summary cache initialised (max=%d, ttl=%ds)",
            settings.summary_cache_max_size,
            settings.summary_cache_ttl,
        )
    return _summary_cache


# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_text(raw_bytes: bytes, mime_type: str) -> str:
    """
    Best-effort text extraction from document bytes.
    Supports: plain text, PDF (via pypdf), DOCX (via python-docx).
    Falls back to lossy UTF-8 decode for unknown types.
    """
    mime = mime_type.lower().split(";")[0].strip()

    # ── Plain text / CSV / JSON ────────────────────────────────────────────
    if mime in ("text/plain", "text/csv", "application/json", "text/markdown"):
        return raw_bytes.decode("utf-8", errors="replace")

    # ── PDF ───────────────────────────────────────────────────────────────
    if mime == "application/pdf":
        try:
            from pypdf import PdfReader  # type: ignore[import]

            reader = PdfReader(io.BytesIO(raw_bytes))
            pages: list[str] = []
            for page in reader.pages:
                text = page.extract_text() or ""
                pages.append(text)
            return "\n".join(pages)
        except ImportError:
            logger.warning("pypdf not installed; falling back to raw decode for PDF.")
        except Exception as exc:
            logger.warning("PDF extraction failed: %s", exc)

    # ── DOCX ──────────────────────────────────────────────────────────────
    if mime in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ):
        try:
            from docx import Document  # type: ignore[import]

            doc = Document(io.BytesIO(raw_bytes))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except ImportError:
            logger.warning("python-docx not installed; falling back to raw decode for DOCX.")
        except Exception as exc:
            logger.warning("DOCX extraction failed: %s", exc)

    # ── Fallback ──────────────────────────────────────────────────────────
    return raw_bytes.decode("utf-8", errors="replace")


def _truncate_text(text: str, max_chars: int) -> str:
    """
    Truncate text to `max_chars` characters, breaking at a word boundary.
    Appends an ellipsis notice so the AI knows the text was cut.
    """
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit(" ", 1)[0]
    return truncated + "\n\n[... document truncated for summarization ...]"


# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a precise document analyst. "
    "Respond ONLY with valid JSON — no markdown, no explanation outside the JSON. "
    "Schema: {\"summary\": \"<3-5 sentence summary>\", \"keyPoints\": [\"...\", ...]}"
)

_USER_PROMPT_TEMPLATE = (
    "Document: {name}\n\n"
    "Content:\n{content}\n\n"
    "Provide a concise summary (3-5 sentences) and 3-7 key points as JSON."
)


# ── AI caller ─────────────────────────────────────────────────────────────────

async def _call_ai(
    document_name: str,
    text: str,
    max_tokens: int,
) -> tuple[str, list[str], str]:
    """
    Call OpenAI or Azure OpenAI and return (summary, key_points, model_used).
    Raises HTTP 502 on AI errors.
    """
    settings = get_settings()
    truncated = _truncate_text(text, settings.summarize_max_chars)
    user_msg = _USER_PROMPT_TEMPLATE.format(name=document_name, content=truncated)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    if settings.ai_provider == "openai":
        try:
            from openai import AsyncOpenAI  # type: ignore[import]

            client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                timeout=60.0,
            )
            response = await client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=0.2,   # Low temperature = consistent, factual output
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or "{}"
            model_used = response.model
        except Exception as exc:
            err_type = type(exc).__name__
            logger.error("OpenAI call failed (%s): %s", err_type, exc)
            raise HTTPException(
                status_code=502,
                detail=f"AI summarization failed ({err_type}): {exc}",
            ) from exc

    elif settings.ai_provider == "azure_openai":
        try:
            from openai import AsyncAzureOpenAI  # type: ignore[import]

            client = AsyncAzureOpenAI(
                api_key=settings.azure_openai_api_key,
                azure_endpoint=settings.azure_openai_endpoint,
                api_version=settings.azure_openai_api_version,
                timeout=60.0,
            )
            response = await client.chat.completions.create(
                model=settings.azure_openai_deployment,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or "{}"
            model_used = f"azure/{settings.azure_openai_deployment}"
        except Exception as exc:
            err_type = type(exc).__name__
            logger.error("Azure OpenAI call failed (%s): %s", err_type, exc)
            raise HTTPException(
                status_code=502,
                detail=f"AI summarization failed ({err_type}): {exc}",
            ) from exc

    elif settings.ai_provider == "google_ai_studio":
        try:
            import httpx
            # Native Gemini REST API
            model = settings.google_model.replace("models/", "")
            # Using v1beta as Gemini 2.0 features are often more stable there for REST
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={settings.google_api_key}"
            
            # Convert OpenAI-style messages to Gemini-style contents.
            # Native Gemini requires alternating roles, so we merge sequential user messages.
            all_text = "\n\n".join([m.get("content", "") for m in messages if m.get("role") == "user"])
            contents = [{
                "role": "user",
                "parts": [{"text": all_text}]
            }]

            payload = {
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": max_tokens,
                    "responseMimeType": "application/json"
                }
            }

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code >= 400:
                    error_body = resp.text
                    logger.error("Gemini API Error (%d): %s", resp.status_code, error_body)
                    raise HTTPException(
                        status_code=502,
                        detail=f"Gemini API Error {resp.status_code}: {error_body}"
                    )
                data = resp.json()

            # Extract generated text
            try:
                candidate = data["candidates"][0]
                raw = candidate["content"]["parts"][0]["text"]
                model_used = f"google/{model}"
            except (KeyError, IndexError) as e:
                logger.error("Failed to parse Gemini response: %s", data)
                raise ValueError(f"Incomplete response from Gemini API: {data}") from e

        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise exc
            err_type = type(exc).__name__
            logger.error("Google AI Studio call failed (%s): %s", err_type, exc)
            raise HTTPException(
                status_code=502,
                detail=f"AI summarization failed (Native API Error: {err_type}): {exc}",
            ) from exc

    elif settings.ai_provider == "groq":
        try:
            from openai import AsyncOpenAI  # type: ignore[import]

            client = AsyncOpenAI(
                api_key=settings.groq_api_key,
                base_url="https://api.groq.com/openai/v1",
                timeout=60.0,
            )
            response = await client.chat.completions.create(
                model=settings.groq_model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=0.2,
            )
            raw = response.choices[0].message.content or "{}"
            model_used = f"groq/{settings.groq_model}"
        except Exception as exc:
            err_type = type(exc).__name__
            logger.error("Groq call failed (%s): %s", err_type, exc)
            raise HTTPException(
                status_code=502,
                detail=f"AI summarization failed (Groq Error: {err_type}): {exc}",
            ) from exc

    else:
        raise HTTPException(
            status_code=500,
            detail=f"Unknown AI provider: {settings.ai_provider!r}",
        )

    # Parse the JSON response from the AI
    try:
        parsed = json.loads(raw)
        summary: str = parsed.get("summary", "No summary available.")
        key_points: list[str] = parsed.get("keyPoints", parsed.get("key_points", []))
        if not isinstance(key_points, list):
            key_points = [str(key_points)]
    except (json.JSONDecodeError, ValueError):
        # Graceful fallback — return raw text as summary
        logger.warning("AI returned non-JSON response; using raw text as summary.")
        summary = raw[:1000]
        key_points = []

    return summary, key_points, model_used


# ── Public API ─────────────────────────────────────────────────────────────────

@dataclass
class SummaryResult:
    summary: str
    key_points: list[str]
    cached: bool
    model_used: str


async def summarize_document(
    document_id: str,
    document_name: str,
    raw_bytes: bytes,
    mime_type: str,
    max_tokens: int = 500,
) -> SummaryResult:
    """
    Main entry point for the summarization service.

    Checks cache first. On cache miss: extracts text, calls AI, caches result.
    """
    cache = _get_cache()

    # ── Cache hit ──────────────────────────────────────────────────────────
    if document_id in cache:
        logger.info("Summary cache HIT for document_id=%s", document_id)
        cached_data = cache[document_id]
        return SummaryResult(
            summary=cached_data["summary"],
            key_points=cached_data["key_points"],
            cached=True,
            model_used=cached_data["model_used"],
        )

    # ── Cache miss — run the pipeline ──────────────────────────────────────
    logger.info(
        "Summary cache MISS for document_id=%s — extracting text and calling AI", document_id
    )

    text = _extract_text(raw_bytes, mime_type)

    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail=(
                "Unable to extract readable text from this document. "
                "Binary or encrypted files are not supported for summarization."
            ),
        )

    summary, key_points, model_used = await _call_ai(document_name, text, max_tokens)

    # Store in cache
    cache[document_id] = {
        "summary": summary,
        "key_points": key_points,
        "model_used": model_used,
    }

    return SummaryResult(
        summary=summary,
        key_points=key_points,
        cached=False,
        model_used=model_used,
    )
