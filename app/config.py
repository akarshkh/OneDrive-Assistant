"""
Pydantic Settings — reads from .env or environment variables.
All configuration for the application lives here.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Azure / Entra ID ─────────────────────────────────────────────────────
    azure_tenant_id: str = Field(..., description="Azure AD Tenant ID (GUID)")
    azure_client_id: str = Field(
        ..., description="App Registration Client ID (used as token audience)"
    )

    # ── AI Provider ──────────────────────────────────────────────────────────
    ai_provider: Literal["openai", "azure_openai", "google_ai_studio"] = Field(
        "openai",
        description="'openai', 'azure_openai', or 'google_ai_studio'",
    )

    # OpenAI
    openai_api_key: str = Field("", description="OpenAI API key (if ai_provider=openai)")
    openai_model: str = Field(
        "gpt-4o-mini", description="OpenAI model to use for summarization"
    )

    # Azure OpenAI
    azure_openai_api_key: str = Field("", description="Azure OpenAI API key")
    azure_openai_endpoint: str = Field(
        "", description="Azure OpenAI endpoint URL (e.g. https://myresource.openai.azure.com)"
    )
    azure_openai_deployment: str = Field(
        "gpt-4o-mini", description="Azure OpenAI deployment name"
    )
    azure_openai_api_version: str = Field(
        "2024-02-01", description="Azure OpenAI API version"
    )
    
    # Google AI Studio (Gemini)
    google_api_key: str = Field("", description="Google Gemini API key")
    google_model: str = Field(
        "gemini-2.0-flash", description="Gemini model (e.g. gemini-2.0-flash)"
    )

    # ── Cost / Limits ─────────────────────────────────────────────────────────
    max_content_bytes: int = Field(
        1_572_864,  # 1.5 MB
        description="Max document size accepted for summarization (bytes)",
    )
    summarize_max_chars: int = Field(
        12_000,
        description="Max characters of document text sent to AI (~3 000 tokens)",
    )
    summary_cache_ttl: int = Field(
        3600, description="Seconds before a cached summary expires"
    )
    summary_cache_max_size: int = Field(
        500, description="Max number of summaries to hold in memory"
    )

    # ── Graph API ─────────────────────────────────────────────────────────────
    graph_base_url: str = Field(
        "https://graph.microsoft.com/v1.0",
        description="Microsoft Graph base URL",
    )
    graph_timeout_seconds: int = Field(
        30, description="Timeout for Graph API calls in seconds"
    )

    # ── Server ────────────────────────────────────────────────────────────────
    allowed_origins: list[str] = Field(
        ["*"],
        description="CORS allowed origins (restrict in production)",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field("INFO")

    @field_validator("azure_tenant_id", "azure_client_id", mode="before")
    @classmethod
    def _not_empty(cls, v: str, info) -> str:  # type: ignore[override]
        if not v or v.strip() == "":
            raise ValueError(f"{info.field_name} must not be empty")
        return v.strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()  # type: ignore[call-arg]
