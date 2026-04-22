"""
FastAPI application factory.

Startup / shutdown lifecycle:
  - Initialises the shared httpx.AsyncClient used by the Graph client.
  - Closes the client gracefully on shutdown.

Middleware:
  - CORS (restrict origins in production via ALLOWED_ORIGINS env var)
  - Request logging (X-Request-ID header injected for tracing)

Routes:
  - GET  /health          — liveness probe (no auth)
  - GET  /search          — search OneDrive (delegated auth required)
  - GET  /document/{id}   — get document metadata (delegated auth required)
  - POST /summarize       — AI summary on demand (delegated auth required)

Docs:
  - Swagger UI: /docs
  - ReDoc:      /redoc
  - OpenAPI JSON: /openapi.json
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth.jwt_validator import UserContext, get_current_user
from app.config import get_settings
from app.graph import client as graph_client
from app.models.schemas import ErrorResponse, HealthResponse
from app.routes import document, search, summarize

# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

APP_VERSION = "1.0.0"


# ── Lifespan (startup + shutdown) ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.getLogger().setLevel(settings.log_level)

    logger.info("🚀  OneDrive Agent API v%s starting up", APP_VERSION)
    logger.info("   AI provider : %s", settings.ai_provider)
    logger.info("   Tenant ID   : %s", settings.azure_tenant_id)
    logger.info("   Client ID   : %s", settings.azure_client_id)
    logger.info("   Max doc size: %.1f MB", settings.max_content_bytes / 1_048_576)

    await graph_client.init_client()

    yield  # ← application runs here

    logger.info("🛑  OneDrive Agent API shutting down")
    await graph_client.close_client()


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Personal OneDrive Document Finder Agent",
        description=(
            "A Microsoft Copilot Studio–compatible API that lets a signed-in user "
            "search their OneDrive, retrieve document metadata, and optionally "
            "generate AI summaries **on demand** to minimise cost.\n\n"
            "**Authentication:** Bearer token (Azure AD delegated flow — "
            "`Files.Read` scope required)."
        ),
        version=APP_VERSION,
        license_info={
            "name": "MIT",
        },
        contact={
            "name": "API Support",
            "email": "support@example.com",
        },
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    # ── Request ID + timing middleware ────────────────────────────────────────
    @app.middleware("http")
    async def request_middleware(request: Request, call_next):  # type: ignore[return]
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.perf_counter()
        response: Response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
        logger.info(
            "%s %s → %d [%.1f ms] req_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
        )
        return response

    # ── Global exception handler ──────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="internal_server_error",
                message="An unexpected error occurred. Please retry.",
                details=str(exc) if settings.log_level == "DEBUG" else None,
            ).model_dump(by_alias=True, exclude_none=True),
        )

    # ── Health check (unauthenticated — used by Azure App Service probes) ─────
    @app.get(
        "/health",
        response_model=HealthResponse,
        tags=["Health"],
        summary="Liveness probe",
        description="Returns 200 OK when the service is ready. No authentication required.",
        include_in_schema=True,
    )
    async def health_check() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=APP_VERSION,
            ai_provider=settings.ai_provider,
        )

    # ── Diagnostics (Internal) ────────────────────────────────────────────────
    @app.get(
        "/debug/graph-status",
        tags=["Health"],
        summary="Check Graph API access",
        description="Attempts a simple GET /me call to verify if the account is accessible or blocked.",
    )
    async def graph_status(user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
        try:
            resp = await graph_client._client().get(
                f"{settings.graph_base_url}/me",
                headers={"Authorization": f"Bearer {user.raw_token}"},
            )
            return {
                "status_code": str(resp.status_code),
                "graph_response": resp.json() if resp.status_code < 300 else resp.text,
                "note": "If this returns 423, the entire account is restricted."
            }
        except Exception as exc:
            return {"error": str(exc)}

    @app.get(
        "/debug/ai-status",
        tags=["Health"],
        summary="Check AI configuration",
        description="Verifies the AI provider settings and network reachability to the provider's API.",
    )
    async def ai_status() -> dict[str, Any]:
        reachability = "Checking..."
        try:
            client = graph_client._client()
            resp = await client.get("https://api.openai.com/v1/models", timeout=5.0)
            reachability = f"Connected (Status: {resp.status_code})"
        except Exception as exc:
            reachability = f"Unreachable: {type(exc).__name__} - {str(exc)}"

        return {
            "provider": settings.ai_provider,
            "model": settings.openai_model if settings.ai_provider == "openai" else settings.azure_openai_deployment,
            "has_key": bool(settings.openai_api_key or settings.azure_openai_api_key),
            "network_reachability": reachability,
            "note": "A status 401 on reachability is actually GOOD — it means we reached OpenAI."
        }

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(search.router)
    app.include_router(document.router)
    app.include_router(summarize.router)

    return app


# ── Entry point ───────────────────────────────────────────────────────────────
app = create_app()
