# 🗂️ Personal OneDrive Document Finder Agent — Backend API

A production-ready **Python FastAPI** backend that lets a Microsoft Entra ID (Azure AD)
authenticated user search their **own OneDrive**, retrieve document metadata, and
get **AI-generated summaries on demand** — designed for low-cost usage with
**Microsoft Copilot Studio** via a Custom Connector.

---

## ⚡ Cost Model at a Glance

| Endpoint | AI Cost | Typical Latency |
|---|---|---|
| `GET /search` | **Zero** | < 1 s |
| `GET /document/{id}` | **Zero** | < 0.5 s |
| `POST /summarize` | **Only when called** (cached 1 hr) | 3 – 8 s |

---

## 🏗️ Project Structure

```
onedrive-agent-api/
├── app/
│   ├── main.py              # FastAPI app factory + lifespan + middleware
│   ├── config.py            # Pydantic Settings (all env vars)
│   ├── auth/
│   │   └── jwt_validator.py # Azure AD JWT validation + JWKS caching
│   ├── graph/
│   │   └── client.py        # Async Microsoft Graph API client
│   ├── routes/
│   │   ├── search.py        # GET /search
│   │   ├── document.py      # GET /document/{id}
│   │   └── summarize.py     # POST /summarize
│   ├── services/
│   │   └── ai_service.py    # Text extraction + AI provider logic
│   └── models/
│       └── schemas.py       # Pydantic v2 request/response models
├── tests/
│   ├── test_jwt_validator.py
│   └── test_routes.py
├── docs/
│   ├── azure_app_registration.md   # Azure setup guide
│   └── copilot_connector_setup.md  # Copilot Studio integration guide
├── openapi.json             # Ready to import into Copilot Studio
├── requirements.txt
├── .env.example
├── Dockerfile
├── gunicorn.conf.py
├── pytest.ini
└── .gitignore
```

---

## 🚀 Quick Start (Local Development)

### 1. Clone and set up a virtual environment

```bash
cd onedrive-agent-api
python -m venv .venv
source .venv/bin/activate  # macOS / Linux
# .venv\Scripts\activate   # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env and fill in your Azure and AI provider values
```

Minimum required values in `.env`:

```bash
AZURE_TENANT_ID=3163c13f-b80f-426c-94b0-fa4c0bf66ad7
AZURE_CLIENT_ID=39a981b1-c5b6-4637-9c0c-a4c43055978c

# For Free Tier (Google Gemini)
AI_PROVIDER=google_ai_studio
GOOGLE_API_KEY=your_gemini_api_key

# For Paid Tier (OpenAI)
# AI_PROVIDER=openai
# OPENAI_API_KEY=sk-...
```

---

## 🌍 Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `AZURE_TENANT_ID` | ✅ | — | Azure AD Tenant ID (Single Tenant) |
| `AZURE_CLIENT_ID` | ✅ | — | App Registration Client ID |
| `AI_PROVIDER` | — | `openai` | `openai`, `azure_openai`, or `google_ai_studio` |
| `GOOGLE_API_KEY` | If Gemini | — | [Google AI Studio key](https://aistudio.google.com/) |
| `OPENAI_API_KEY` | If OpenAI | — | OpenAI API key |
| `MAX_CONTENT_BYTES` | — | `1572864` | Max doc size (1.5 MB) |
| `LOG_LEVEL` | — | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |

---

## 📡 API Reference

### `GET /search?q={query}&top={n}`
Search the authenticated user's OneDrive. **No AI.**

### `POST /summarize`
Generate an AI summary. **AI called only here, never automatically.**

---

## 🔐 Authentication
All endpoints (except `/health`) require a **Bearer token** for Microsoft Graph.
Scope: `https://graph.microsoft.com/Files.Read`

---

## 🛡️ Security Notes
- Tokens validated on **every request** (signature, audience, issuer, scope).
- JWKS (public keys) are cached in-memory for 24 hours.
- Document content is **never stored** — processed only in-memory during summarization.
- Users can only access **their own** OneDrive data via delegated tokens.
