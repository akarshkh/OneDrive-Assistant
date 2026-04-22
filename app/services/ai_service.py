"""
AI Summarization and Chat Service.

Responsibilities:
  1. Extract readable text from raw document bytes (text/PDF/DOCX).
  2. Truncate to stay within token budget.
  3. Call OpenAI, Azure OpenAI, Google AI Studio, or Groq for summary + key points or Q&A.
  4. Cache the result to avoid repeated AI calls for the same document.
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
_summary_cache: TTLCache[str, dict[str, Any]] | None = None

def _get_cache() -> TTLCache[str, dict[str, Any]]:
    global _summary_cache
    if _summary_cache is None:
        settings = get_settings()
        _summary_cache = TTLCache(
            maxsize=settings.summary_cache_max_size,
            ttl=settings.summary_cache_ttl,
        )
    return _summary_cache


# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_text(raw_bytes: bytes, mime_type: str) -> str:
    mime = mime_type.lower().split(";")[0].strip()
    if mime in ("text/plain", "text/csv", "application/json", "text/markdown"):
        return raw_bytes.decode("utf-8", errors="replace")

    if mime == "application/pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw_bytes))
            return "\n".join(p.extract_text() or "" for p in reader.pages)
        except Exception as exc:
            logger.warning("PDF extraction failed: %s", exc)

    if mime in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/msword"):
        try:
            from docx import Document
            doc = Document(io.BytesIO(raw_bytes))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as exc:
            logger.warning("DOCX extraction failed: %s", exc)

    return raw_bytes.decode("utf-8", errors="replace")


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit(" ", 1)[0]
    return truncated + "\n\n[... document truncated for summarization ...]"


# ── Prompt Templates ──────────────────────────────────────────────────────────

_SUMMARY_SYSTEM_PROMPT = (
    "You are a precise document analyst. "
    "Respond ONLY with valid JSON — no markdown, no explanation outside the JSON. "
    "Schema: {\"summary\": \"<3-5 sentence summary>\", \"keyPoints\": [\"...\", ...]}"
)

_SUMMARY_USER_TEMPLATE = (
    "Document: {name}\n\n"
    "Content:\n{content}\n\n"
    "Provide a concise summary (3-5 sentences) and 3-7 key points as JSON."
)

_CHAT_SYSTEM_PROMPT = (
    "You are a helpful and precise document assistant. "
    "Use the provided document context to answer the user's question. "
    "Guidelines:\n"
    "1. Be concise and factual.\n"
    "2. ONLY use the provided document content. If the answer is not in the document, say 'I'm sorry, I cannot find information about that in the document.'\n"
    "3. Do not include references to yourself or your internal workings."
)

_CHAT_USER_TEMPLATE = (
    "Document: {name}\n\n"
    "Content:\n{content}\n\n"
    "Question: {question}"
)


# ── AI caller ─────────────────────────────────────────────────────────────────

async def _call_ai_raw(
    system_prompt: str,
    user_msg: str,
    max_tokens: int,
) -> tuple[str, str]:
    settings = get_settings()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    if settings.ai_provider == "openai":
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0)
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.2
        )
        return resp.choices[0].message.content or "{}", resp.model

    elif settings.ai_provider == "azure_openai":
        from openai import AsyncAzureOpenAI
        client = AsyncAzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
            timeout=60.0,
        )
        resp = await client.chat.completions.create(
            model=settings.azure_openai_deployment,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.2
        )
        return resp.choices[0].message.content or "{}", f"azure/{settings.azure_openai_deployment}"

    elif settings.ai_provider == "google_ai_studio":
        import httpx
        model = settings.google_model.replace("models/", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={settings.google_api_key}"
        all_text = f"system: {system_prompt}\n\nuser: {user_msg}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": all_text}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens}
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 400:
                raise HTTPException(status_code=502, detail=f"Gemini Error {resp.status_code}: {resp.text}")
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"], f"google/{model}"

    elif settings.ai_provider == "groq":
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.groq_api_key, base_url="https://api.groq.com/openai/v1", timeout=60.0)
        resp = await client.chat.completions.create(
            model=settings.groq_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.2
        )
        return resp.choices[0].message.content or "{}", f"groq/{settings.groq_model}"

    raise HTTPException(status_code=500, detail="Unknown AI provider")


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
    cache = _get_cache()
    if document_id in cache:
        c = cache[document_id]
        return SummaryResult(summary=c["summary"], key_points=c["key_points"], cached=True, model_used=c["model_used"])

    text = _extract_text(raw_bytes, mime_type)
    if not text.strip(): raise HTTPException(status_code=422, detail="No readable text.")

    settings = get_settings()
    user_msg = _SUMMARY_USER_TEMPLATE.format(name=document_name, content=_truncate_text(text, settings.summarize_max_chars))
    raw, model_used = await _call_ai_raw(_SUMMARY_SYSTEM_PROMPT, user_msg, max_tokens)

    try:
        parsed = json.loads(raw)
        summary = parsed.get("summary", "No summary.")
        key_points = parsed.get("keyPoints", parsed.get("key_points", []))
    except:
        summary, key_points = raw[:1000], []

    cache[document_id] = {"summary": summary, "key_points": key_points, "model_used": model_used}
    return SummaryResult(summary=summary, key_points=key_points, cached=False, model_used=model_used)


@dataclass
class ChatResult:
    answer: str
    model_used: str

async def ask_document_question(
    document_name: str,
    raw_bytes: bytes,
    mime_type: str,
    question: str,
) -> ChatResult:
    text = _extract_text(raw_bytes, mime_type)
    if not text.strip(): raise HTTPException(status_code=422, detail="No readable text.")

    settings = get_settings()
    user_msg = _CHAT_USER_TEMPLATE.format(name=document_name, content=_truncate_text(text, settings.summarize_max_chars), question=question)
    raw, model_used = await _call_ai_raw(_CHAT_SYSTEM_PROMPT, user_msg, 500)
    
    answer = raw.strip()
    if answer.startswith("{"):
        try: answer = json.loads(answer).get("answer", answer)
        except: pass

    return ChatResult(answer=answer, model_used=model_used)
