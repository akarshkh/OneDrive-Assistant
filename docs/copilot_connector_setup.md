# Copilot Studio Custom Connector Setup Guide

## Overview

This guide shows how to import the `openapi.json` file as a **Custom Connector**
in Microsoft Copilot Studio, and wire up OAuth 2.0 authentication so the agent
calls the API on behalf of the signed-in user.

---

## Prerequisites

- The OneDrive Agent API is **deployed and publicly accessible** (e.g., Azure App Service)
- Azure App Registration is complete (see `azure_app_registration.md`)
- You have a **Copilot Studio** environment with the **maker** role

---

## Part 1 — Create the Custom Connector

### Option A — Import via OpenAPI file (Recommended)

1. Go to [make.powerautomate.com](https://make.powerautomate.com).
2. In the left sidebar → **Data** → **Custom connectors**.
3. Click **+ New custom connector** → **Import an OpenAPI file**.
4. Name: `OneDrive Document Finder Agent`.
5. Browse and upload the `openapi.json` file from this project.
6. Click **Continue**.

### Option B — Import via URL

If your API is deployed, you can import directly from the `/openapi.json` endpoint:

1. Click **+ New custom connector** → **Import from URL**.
2. Enter: `https://your-api-host.azurewebsites.net/openapi.json`.
3. Click **Import** → **Continue**.

---

## Part 2 — General Settings

After import, verify the **General** tab:

| Field | Value |
|---|---|
| **Scheme** | HTTPS |
| **Host** | `onedrive-agent-api.onrender.com` |
| **Base URL** | `/` |

Upload an icon and set a description if desired.

---

## Part 3 — Security (OAuth 2.0)

1. On the **Security** tab, set **Authentication type** to **OAuth 2.0**.
2. Fill in the fields:

   | Field | Value |
   |---|---|
   | **Identity Provider** | Azure Active Directory |
   | **Client id** | `<Application (client) ID from Step 1>` |
   | **Client secret** | `<Secret from Step 5 of App Registration>` |
   | **Authorization URL** | `https://login.microsoftonline.com/3163c13f-b80f-426c-94b0-fa4c0bf66ad7/oauth2/v2.0/authorize` |
   | **Token URL** | `https://login.microsoftonline.com/3163c13f-b80f-426c-94b0-fa4c0bf66ad7/oauth2/v2.0/token` |
   | **Refresh URL** | `https://login.microsoftonline.com/3163c13f-b80f-426c-94b0-fa4c0bf66ad7/oauth2/v2.0/token` |
   | **Resource URL** | `https://graph.microsoft.com` |
   | **Scope** | `https://graph.microsoft.com/Files.Read offline_access` |

3. Click **Create connector**.
4. After saving, copy the auto-generated **Redirect URL** shown at the top of the Security tab.
5. Go back to your Azure App Registration → **Authentication** → paste the redirect URL.
6. Click **Save** in Azure Portal.

---

## Part 4 — Test the Connector

1. Click the **Test** tab in the connector editor.
2. Click **+ New connection** — this will trigger an OAuth sign-in pop-up.
3. Sign in with a user account that has OneDrive files.
4. After signing in, select the new connection.
5. Test the **searchDocuments** operation:
   - `q`: `budget`
   - Click **Test operation**.
6. You should see a `200 OK` response with file results.

---

## Part 5 — Use in Copilot Studio Agent

1. Open **Copilot Studio** → your agent (or create a new one).
2. Go to **Actions** → **+ Add an action**.
3. Select **Custom connector** → choose `OneDrive Document Finder Agent`.
4. You will see three available actions:
   - `searchDocuments` — Search OneDrive
   - `getDocument` — Get document details
   - `summarizeDocument` — AI summary (on demand)
5. Add each action and configure the **input parameters** to map from the conversation:

### Example Topic: "Find my document"

```yaml
trigger: "Find [document name] in my OneDrive"
steps:
  - action: searchDocuments
    inputs:
      q: {entity: "document name"}
    outputs:
      - results → store as documentResults
  - message: "I found {documentResults.total} documents matching '{q}':"
  - adaptive card: show {documentResults.results}
```

### Example Topic: "Summarize this document"

```yaml
trigger: "Summarize [document name]"
steps:
  - action: searchDocuments
    inputs:
      q: {entity: "document name"}
  - action: summarizeDocument
    inputs:
      documentId: {first result id}
      maxTokens: 500
  - message: "Here's a summary of {documentName}:"
  - message: "{summary}"
  - message: "Key points:\n{keyPoints}"
```

---

## Part 6 — Connector Sharing

To allow other users in your org to use this connector:

1. In Power Automate → **Custom connectors** → select your connector.
2. Click **Share** → add users or the entire organization.

---

## Troubleshooting

| Issue | Solution |
|---|---|
| `401 Unauthorized` | Ensure the redirect URI in Azure matches the one in the connector |
| `403 Forbidden` | Check that admin consent was granted for `Files.Read` |
| `No results` returned | The user's OneDrive may have no matching files; try a broader query |
| `413 Content Too Large` | The document exceeds 1.5 MB; summarization is not available |
| Connector not appearing | Refresh the Copilot Studio environment; connectors can take ~5 min to propagate |

---

## API Endpoints Reference

| Method | Path | Purpose | AI Cost |
|---|---|---|---|
| `GET` | `/health` | Liveness probe | None |
| `GET` | `/search?q={query}` | Search OneDrive | ⚡ Zero |
| `GET` | `/document/{id}` | Get document metadata | ⚡ Zero |
| `POST` | `/summarize` | AI summary (on demand) | 💰 Only when called |
