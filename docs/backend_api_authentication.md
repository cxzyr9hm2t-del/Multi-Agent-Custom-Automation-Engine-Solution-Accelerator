# Putting the backend API behind authentication

The backend container app deploys with **public ingress and no authentication**.
It derives the caller's identity from the `x-ms-client-principal-id` request
header, which is only meaningful when an authenticating front door sets it and
strips any copy the caller sent. Nothing provisions such a front door by default,
so in a default deployment any caller can present any principal id.

This is finding **C1** in `docs/reports/2026-08-15-forensic-audit.md`. Every
per-user check in the application — plan access, the human-in-the-loop approval
gate, the WebSocket, team ownership — compares against that identity, so all of
them rest on this.

`docs/azure_app_service_auth_setup.md` covers the **frontend App Service** only.
Following it end to end still leaves the API reachable directly.

---

## What the parameter does

`backendAuthClientId` attaches a Container Apps built-in auth configuration to
the backend container app:

- identity provider: Microsoft Entra ID, issuer
  `https://login.microsoftonline.com/<tenant>/v2.0` (tenant defaults to the
  deployment tenant)
- `unauthenticatedClientAction: Return401` — unauthenticated callers are
  rejected outright rather than redirected to a login page, which is what an API
  wants
- accepted audiences: `api://<client-id>` and `<client-id>`

It is **empty by default**, and while empty nothing is created and the
deployment behaves exactly as it does today. No client secret is needed, because
the browser login/redirect flow is not used.

---

## Read this before enabling it

Turning this on rejects any request that does not carry a valid bearer token.
Two things in this application cannot carry one:

| What | Why | Consequence |
|---|---|---|
| `GET /api/v4/images/{blob}` | The browser loads these through a plain `<img src>` in the markdown renderer. An image request carries no `Authorization` header. | Generated images stop rendering — every image request 401s. |
| `GET /api/v4/socket/{plan_id}` | Browsers cannot set headers on a WebSocket handshake. This is why the socket takes its identity as a query parameter in the first place. | Live plan streaming stops connecting. |

Neither is solved by the front door. Both need a separate mechanism, and both
should be settled **before** you enable this in an environment people are using:

- **Images** — mint short-lived SAS URLs at generation time
  (`src/mcp_server/services/image_service.py`) and drop the proxy, or issue a
  short-lived signed token the frontend appends to the image URL.
- **WebSocket** — pass a short-lived backend-issued token as a query parameter
  and validate it in the handler, or move the socket behind the same origin as
  the frontend so a session cookie applies.

Until then, an enabled front door secures the HTTP API and breaks those two
features. That trade may well be the right one for a locked-down environment —
it just should not be a surprise.

The frontend also has to acquire and send a token for the API's audience. It
currently sends `Authorization: Bearer` from `localStorage.token`
(`src/App/src/api/httpClient.ts`), which nothing in the app populates. Wiring
that up (MSAL, or the App Service token store) is the remaining piece.

---

## Creating the app registration

The accelerator's own choice here is a **separate registration for the API**,
with the frontend as a known client application.

1. **Entra ID → App registrations → New registration.**
   Name it for the API, e.g. `macae-backend-api`. No redirect URI is needed —
   this registration never performs an interactive login.

2. **Expose an API.**
   Under *Expose an API*, set the Application ID URI to `api://<client-id>`
   (the portal offers this as the default). Add a scope, e.g.
   `user_impersonation`, with admin-and-user consent.

3. **Add the frontend as a known client application.**
   Still under *Expose an API*, add the frontend App Service's client id under
   *Authorized client applications* and tick the scope from step 2. This is what
   lets the frontend acquire tokens for the API without a second consent prompt.

4. **Grant the frontend permission.**
   On the *frontend's* registration: *API permissions* → *Add a permission* →
   *My APIs* → the API registration → the scope from step 2 → *Grant admin
   consent*.

5. **Deploy with the parameter set.**

   ```bash
   azd env set BACKEND_AUTH_CLIENT_ID <api-registration-client-id>
   azd up
   ```

   Or pass `backendAuthClientId` directly to the Bicep deployment. The tenant is
   taken from the deployment tenant automatically; override `authTenantId` on
   the container-app module only for a cross-tenant setup.

---

## Verifying it

```bash
# Expect 401
curl -i https://<backend-fqdn>/api/v4/plans

# Expect 401 — a spoofed principal header is no longer sufficient
curl -i -H "x-ms-client-principal-id: 00000000-0000-0000-0000-000000000000" \
     https://<backend-fqdn>/api/v4/plans

# Expect 200 with a token whose audience is api://<client-id>
curl -i -H "Authorization: Bearer $TOKEN" https://<backend-fqdn>/api/v4/plans
```

Once the front door is in place, `x-ms-client-principal` — the base64 claims
document it injects, having stripped any client-supplied copy — becomes the
trustworthy identity. `auth/auth_utils.py` already prefers that blob's
object-id claim over the bare `x-ms-client-principal-id` header, falling back to
the scalar so local development is unaffected.

---

## Rolling it back

Clear the parameter and redeploy:

```bash
azd env set BACKEND_AUTH_CLIENT_ID ""
azd up
```

The auth configuration is removed and the app returns to unauthenticated public
ingress.
