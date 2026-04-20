# Azure App Registration Setup Guide

## Overview

This guide walks you through registering your API in **Microsoft Entra ID** (Azure AD)
so that Copilot Studio can call it on behalf of signed-in users using the
**delegated permissions** flow.

---

## Prerequisites

- An active **Azure subscription**
- **Global Administrator** or **Application Administrator** role in your tenant
- Access to the [Azure Portal](https://portal.azure.com)

---

## Step 1 — Create the App Registration

1. Sign in to the [Azure Portal](https://portal.azure.com).
2. Navigate to **Microsoft Entra ID** → **App registrations**.
3. Click **+ New registration**.
4. Fill in the form:
   | Field | Value |
   |---|---|
   | **Name** | `OneDrive Agent API` |
   | **Supported account types** | **Single tenant** (recommended) or Multitenant |
   | **Redirect URI** | Leave blank for now (added in Step 4) |
5. Click **Register**.

> **Copy the following values — you will need them in `.env`:**
> - **Application (client) ID** → `AZURE_CLIENT_ID`
> - **Directory (tenant) ID** → `AZURE_TENANT_ID`

---

## Step 2 — Expose an API (Set Application ID URI)

Your API needs an audience that the tokens will be issued for.

1. In your App registration, go to **Expose an API**.
2. Click **+ Add** next to **Application ID URI**.
3. Accept the default value: `api://<your-client-id>` and click **Save**.
4. Click **+ Add a scope**:

   | Field | Value |
   |---|---|
   | **Scope name** | `Files.Read` |
   | **Who can consent** | Admins and users |
   | **Admin consent display name** | Read user's OneDrive files |
   | **Admin consent description** | Allows the API to search and read files in the user's OneDrive |
   | **User consent display name** | Read my OneDrive files |
   | **User consent description** | Allow this API to search and read your OneDrive files |
   | **State** | Enabled |

5. Click **Add scope**.

> The full scope URI will be: `api://<client-id>/Files.Read`

---

## Step 3 — Add Microsoft Graph Delegated Permissions

1. Go to **API permissions** → **+ Add a permission**.
2. Select **Microsoft APIs** → **Microsoft Graph**.
3. Choose **Delegated permissions**.
4. Search for and add:
   - ✅ `Files.Read` — Read user's files
5. Click **Add permissions**.
6. Click **Grant admin consent for \<your tenant\>** → **Yes**.

> The status column should show ✅ **Granted for \<tenant\>** for `Files.Read`.

---

## Step 4 — Configure Authentication (for Copilot Studio OAuth)

1. Go to **Authentication** → **+ Add a platform** → **Web**.
2. Add the **Copilot Studio redirect URI**:
   ```
   https://global.consent.azure-apim.net/redirect
   ```
3. Under **Implicit grant and hybrid flows**, leave everything **unchecked**.
4. Under **Advanced settings**, set **Allow public client flows** to **Yes**
   (required for interactive user sign-in from Copilot Studio).
5. Click **Save**.

---

## Step 5 — Create a Client Secret (for Copilot Studio Connector Auth)

Copilot Studio's custom connector uses OAuth 2.0 Authorization Code flow and
needs a client secret to exchange codes for tokens.

1. Go to **Certificates & secrets** → **+ New client secret**.
2. Set a description: `Copilot Studio Connector`.
3. Set expiry: **24 months** (rotate before expiry).
4. Click **Add**.
5. **Copy the secret Value immediately** — it won't be shown again.

> Store it securely in **Azure Key Vault** or your secrets manager.

---

## Step 6 — Token Configuration (Optional but Recommended)

Add optional claims to include user details in the token:

1. Go to **Token configuration** → **+ Add optional claim**.
2. Select token type: **Access**.
3. Add these claims:
   - ✅ `upn` — User Principal Name (login email)
   - ✅ `email`
   - ✅ `given_name`, `family_name`
4. Click **Add**.

---

## Summary of Values for `.env`

```bash
AZURE_TENANT_ID=<Directory (tenant) ID>
AZURE_CLIENT_ID=<Application (client) ID>
```

And for Copilot Studio connector configuration:

| Setting | Value |
|---|---|
| **Authorization URL** | `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize` |
| **Token URL** | `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token` |
| **Client ID** | Your Application (client) ID |
| **Client Secret** | The secret created in Step 5 |
| **Scope** | `api://{client-id}/Files.Read offline_access` |

---

## Required Graph Permissions Summary

| Permission | Type | Purpose |
|---|---|---|
| `Files.Read` | Delegated | Search and read user's OneDrive files |

> ⚠️ Do **NOT** add `Files.Read.All` or any application (non-delegated) permissions.
> The API is designed exclusively for delegated access to the signed-in user's own files.
