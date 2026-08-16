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
Two things in this application cannot send one — generated images, loaded by a
plain `<img src>`, and the WebSocket, because browsers cannot set headers on a
handshake.

**Both are handled**, by short-lived signed tokens that travel in the query
string instead (`src/backend/common/utils/resource_tokens.py`):

| Endpoint | How it authenticates | Bound to |
|---|---|---|
| `GET /api/v4/images/{blob}` | `?token=` from `POST /api/v4/image_token` | the user; 15 min |
| `GET /api/v4/socket/{plan}` | `?token=` from `POST /api/v4/socket_token` | the user **and** that plan; 2 min |

Both minting endpoints are ordinary authenticated HTTP calls, so they work
behind the front door. The frontend fetches a socket token immediately before
connecting, and keeps an image token refreshed in the background.

Tokens are optional on both endpoints: when none is supplied the previous
behaviour applies, so a deployment without the front door is unaffected.

One limit worth stating plainly: an **image token proves the requester is an
authenticated user, not that they own that image**. Generated blobs are stored
under a `uuid4` name with no ownership record, so there is nothing to check
against. This closes anonymous access, not cross-user access. Recording an owner
at generation time (`src/mcp_server/services/image_service.py`) is what would
allow the stronger check.

### The signing key

Tokens are signed with `API_TOKEN_SIGNING_KEY` when set. When it is not, a
random key is generated once per process — sound while the backend runs at a
single replica, which it does by design (see `OrchestrationConfig`). The cost is
that a restart invalidates outstanding tokens; they are short-lived and re-minted
on demand, so that is a refresh at worst. **Set the key explicitly if you ever
lift the single-replica constraint**, or replicas will reject each other's
tokens.

### The frontend's own token

The frontend must also send a bearer token for the API's audience on ordinary
requests. At startup it reads one from the App Service token store
(`/.auth/me`) and caches it; that requires the frontend's registration to have
been granted the API scope in step 4 below. If the endpoint is absent the call
is a silent no-op, so nothing changes for deployments without a front door.

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
