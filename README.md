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
│   │   └── ai_service.py    # Text extraction + OpenAI summarization + TTL cache
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

```powershell
cd onedrive-agent-api
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure environment variables

```powershell
copy .env.example .env
# Edit .env and fill in your Azure and OpenAI values
```

Minimum required values in `.env`:

```bash
AZURE_TENANT_ID=3163c13f-b80f-426c-94b0-fa4c0bf66ad7
AZURE_CLIENT_ID=39a981b1-c5b6-4637-9c0c-a4c43055978c
OPENAI_API_KEY=sk-...           # or use AZURE_OPENAI_* variables
```

### 4. Run the development server

```powershell
uvicorn app.main:app --reload --port 8000
```

- **Swagger UI** → http://localhost:8000/docs
- **ReDoc** → http://localhost:8000/redoc
- **OpenAPI JSON** → http://localhost:8000/openapi.json
- **Health check** → http://localhost:8000/health

---

## 🧪 Run Tests

```powershell
pytest -v
```

---

## 🐳 Docker (Production)

```powershell
# Build
docker build -t onedrive-agent-api .

# Run
docker run -p 8000:8000 --env-file .env onedrive-agent-api
```

---

## 📡 API Reference

### `GET /search?q={query}&top={n}`

Search the authenticated user's OneDrive. **No AI. No content fetch.**

```bash
curl -H "Authorization: Bearer <token>" \
     "http://localhost:8000/search?q=budget+report&top=10"
```

**Response:**
```json
{
  "query": "budget report",
  "total": 2,
  "results": [
    {
      "id": "01BYE5RZ...",
      "name": "Q4 Budget Report.xlsx",
      "webUrl": "https://onedrive.live.com/edit.aspx?...",
      "lastModifiedDateTime": "2024-11-15T09:23:00Z",
      "fileType": "xlsx"
    }
  ]
}
```

---

### `GET /document/{id}`

Get full metadata for a document. **No AI.**

```bash
curl -H "Authorization: Bearer <token>" \
     "http://localhost:8000/document/01BYE5RZ..."
```

---

### `POST /summarize`

Generate an AI summary. **AI called only here, never automatically.**

```bash
curl -X POST \
     -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"documentId": "01BYE5RZ...", "maxTokens": 500}' \
     "http://localhost:8000/summarize"
```

**Response:**
```json
{
  "documentId": "01BYE5RZ...",
  "documentName": "Q4 Budget Report.xlsx",
  "summary": "The Q4 Budget Report outlines the financial performance for Q4...",
  "keyPoints": [
    "Revenue exceeded targets by 12%",
    "Operating expenses within budget",
    "Net profit up 8% year-over-year"
  ],
  "cached": false,
  "modelUsed": "gpt-4o-mini"
}
```

---

## 🔐 Authentication

All endpoints (except `/health`) require a **Bearer token** obtained via the
Azure AD OAuth 2.0 **Authorization Code** flow (delegated permissions).

Required scope: `https://graph.microsoft.com/Files.Read`

See [`docs/azure_app_registration.md`](docs/azure_app_registration.md) for setup.

---

## 🔌 Copilot Studio Integration

1. Deploy this API to Azure App Service (or any HTTPS endpoint).
2. Import `openapi.json` as a Custom Connector in Power Automate.
3. Configure OAuth 2.0 with your App Registration credentials.
4. Use the connector actions in your Copilot Studio agent topics.

See [`docs/copilot_connector_setup.md`](docs/copilot_connector_setup.md) for the full guide.

---

## 🌍 Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `AZURE_TENANT_ID` | ✅ | `3163c13f-b80f-426c-94b0-fa4c0bf66ad7` | Azure AD Tenant ID |
| `AZURE_CLIENT_ID` | ✅ | `39a981b1-c5b6-4637-9c0c-a4c43055978c` | App Registration Client ID |
| `AI_PROVIDER` | — | `openai` | `openai` or `azure_openai` |
| `OPENAI_API_KEY` | If OpenAI | — | OpenAI API key |
| `OPENAI_MODEL` | — | `gpt-4o-mini` | Model name |
| `AZURE_OPENAI_*` | If Azure | — | Azure OpenAI settings |
| `MAX_CONTENT_BYTES` | — | `1572864` | Max doc size for summarization (1.5 MB) |
| `SUMMARIZE_MAX_CHARS` | — | `12000` | Chars sent to AI (~3K tokens) |
| `SUMMARY_CACHE_TTL` | — | `3600` | Cache TTL in seconds |
| `LOG_LEVEL` | — | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |

---

## 📋 Required Graph Permissions

| Permission | Type | Reason |
|---|---|---|
| `Files.Read` | Delegated | Search and read user's own OneDrive files |

> ⚠️ No application permissions. No `Files.ReadWrite`. No `Files.Read.All`.

---

## 🛡️ Security Notes

- Tokens validated on **every request** (signature, audience, issuer, expiry, scope)
- JWKS cached 24 h — no repeated calls to Azure AD on every request
- Document content is **never stored** — used only during the summarization pipeline
- Users can only access **their own** OneDrive via delegated token
- Size limit prevents oversized documents from hitting the AI

---

## 📄 License

MIT
