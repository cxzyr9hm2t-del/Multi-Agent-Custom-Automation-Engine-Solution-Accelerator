# Forensic Audit — code, security and history

> **Remediation status (updated 2026-08-16).** Everything below has since been
> addressed on `claude/claude-rc-t59dbs`. Sixteen of the seventeen findings are
> fixed; **M7** is partially fixed and **L7** is deliberately not done. See
> §9 for the disposition of each, and `docs/backend_api_authentication.md` for
> the authentication work that C1 turned into.

**Date:** 2026-08-15
**Repository:** `cxzyr9hm2t-del/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator` (fork of `microsoft/…`)
**Commit audited:** `452ff53` (= `origin/main`, v0.1.0)
**Scope:** full-tree review — backend code, security posture, and the complete commit history.

---

## 1. Executive summary

Dependency and release hygiene are in good shape and were **re-verified in this session** rather than
carried over from the previous report: 29 + 834 backend tests pass at 86% coverage, the MCP server
suite passes, flake8 is clean, and `pip-audit` + `npm audit` report **zero known vulnerabilities**
across all five manifests.

The application's access-control model is a different matter. **25 findings, 5 of them critical.**
They compose into one story: there is no server-side authentication anywhere in the request path,
and the data layer's per-user scoping has a hole in the one query that matters most.

| Severity | Count |
|---|---|
| Critical | 5 |
| High | 5 |
| Medium | 7 |
| Low / hygiene | 8 |

Two framing points. First, **no fork commit has touched application logic** in `src/backend/api/`,
`orchestration/` or `services/` — every finding below is inherited from upstream and is worth
reporting to `microsoft/` rather than only patching here. Second, the deployment shape matters:
`infra/bicep/main.bicep` puts both the backend and the MCP server on *public* Container Apps
ingress, which turns several latent issues into reachable ones.

---

## 2. Critical findings

### C1 — User identity is supplied by the client and never verified

`src/backend/auth/auth_utils.py:6–32` · `src/App/src/api/httpClient.ts:229–243` ·
`src/App/src/api/config.tsx:125–131` · `infra/bicep/main.bicep:497`

`get_authenticated_user_details()` reads `x-ms-client-principal-id` straight from the request
headers and returns it as the authenticated principal. That header is only trustworthy when an
authenticating proxy injects it and strips any client-supplied copy. No such proxy is provisioned:
there is no `authConfig` block anywhere in `infra/`, and the backend Container App is deployed with
`ingressExternal: true` on port 8000.

The frontend confirms the model rather than contradicting it: `httpClient` sets the header itself
from a client-side value, and `getUserId()` falls back to `00000000-0000-0000-0000-000000000000`.
When the header is absent, the backend substitutes a hard-coded `sample_user` carrying that same
all-zeros principal — unauthenticated callers do not fail closed, they land in a shared identity.

The documented auth setup (`docs/azure_app_service_auth_setup.md`) configures Entra ID on the
*frontend App Service* only. Following it end to end still leaves the API open.

**Fix:** put the backend behind a Container Apps `authConfig` with Entra ID, or on internal-only
ingress reached solely through the authenticated frontend. Validate the principal server-side
(verify the `x-ms-client-principal` JWT rather than trusting the id header) and delete the
`sample_user` fallback outside `APP_ENV=dev`.

### C2 — Cross-user plan read: the user filter is bound but never applied

`src/backend/common/database/cosmosdb.py:189–198` · `src/backend/api/router.py:1418–1470`

`get_plan_by_plan_id()` builds a parameter list containing `@user_id` — and then omits it from the
`WHERE` clause. The leftover binding is evidence of a filter that was intended and lost:

```python
query = "SELECT * FROM c WHERE c.id=@plan_id AND c.data_type=@data_type"
parameters = [
    {"name": "@plan_id",   "value": plan_id},
    {"name": "@data_type", "value": DataType.plan},
    {"name": "@user_id",   "value": self.user_id},   # <-- bound, never referenced
]
```

`GET /api/v4/plan?plan_id=…` returns the plan, its team configuration and every agent message on it.
Sibling queries (`get_all_plans`, `get_all_plans_by_team_id`) filter correctly, which makes this a
single dropped predicate rather than a design choice. The same unfiltered method backs `get_plan()`,
so it also underwrites C4 and H5.

**Fix:** add `AND c.user_id=@user_id`. Then audit the neighbours that share the gap:
`get_agent_messages`, `get_mplan`, `get_steps_by_plan` and `get_step`.

### C3 — The human-in-the-loop approval gate is not bound to a user

`src/backend/api/router.py:539–547` · `src/backend/orchestration/connection_config.py:28, 46–58`

Pending approvals live in a process-global dict keyed only by `m_plan_id`. The `/plan_approval`
endpoint's sole authorization check is membership in that dict:

```python
if human_feedback.m_plan_id in orchestration_config.approvals:
    orchestration_config.set_approval_result(m_plan_id, approved)
```

Nothing ties the `m_plan_id` to the caller. Any request carrying a live pending id can approve or
reject another user's plan — releasing an agent workflow to execute against Foundry, MCP tools and
connected data on someone else's behalf. This is the product's central safety control, and it is the
one place where an ownership check is absent.

**Fix:** store the owning `user_id` alongside each pending approval and require it to match the
authenticated caller. Apply the same change to `clarifications`, which has the identical shape.

### C4 — WebSocket stream accepts any connection for any process

`src/backend/api/router.py:36–45`

The endpoint accepts the socket before doing anything else, then takes `user_id` from the query
string and defaults it to the all-zeros principal:

```python
await websocket.accept()
user_id = user_id or "00000000-0000-0000-0000-000000000000"
```

There is no check that `process_id` belongs to that user. A connection to
`/api/v4/socket/{plan_id}` receives the live orchestration stream: agent reasoning, tool calls, plan
content and the final result. Plan ids are UUIDv4 and not guessable, but they travel in URLs,
telemetry and logs — this is the layer that is supposed to stop a leaked id becoming a live feed.

**Fix:** resolve the authenticated principal before `accept()`, load the plan, and close with a
policy-violation code unless `plan.user_id` matches. The plan lookup already happens a few lines
later for telemetry; it just isn't used as a gate.

### C5 — MCP server is publicly exposed with authentication switched off

`infra/bicep/main.bicep:671–672, 717–719` · `src/mcp_server/config/settings.py:36` ·
`src/mcp_server/mcp_server.py:60–75`

The MCP container is deployed with `ingressExternal: true` on port 9000 and `ENABLE_AUTH=false`.
`_build_auth()` returns `None` when that flag is off, so every FastMCP server is constructed with
`auth=None` and every domain tool — HR, marketing, product, tech support, general, data tools, image
generation — is callable by anyone who finds the hostname. The JWT verifier is implemented and
wired; it is simply disabled by the shipped template.

**Fix:** set `ingressExternal: false` — the backend reaches the MCP server over the Container Apps
internal network. If external reachability is genuinely required, set `ENABLE_AUTH=true` and
populate `JWKS_URI`, `ISSUER` and `AUDIENCE`.

---

## 3. High findings

| # | Finding | Location |
|---|---|---|
| H1 | **CORS allows every origin with credentials.** `allow_origins=["*"]` with `allow_credentials=True` makes Starlette reflect the request Origin back when credentials are present. `frontend_url` is assigned two lines above and never used — the intended restriction is dead code beside the wildcard that replaced it. | `app.py:98, 120–126` |
| H2 | **Content-safety validation skipped when `team_id` is supplied.** The RAI gate is guarded by `if not team_id:`, so `?team_id=anything` bypasses `rai_validate_team_config()` entirely — the path by which unvetted agent `system_message`s reach the system prompt. The `Config_RAI_Validation_Passed` event fires unconditionally, including on the branch that validated nothing. | `router.py:1003–1017` |
| H3 | **Unauthenticated endpoint injects prompts into a user's session.** `POST /api/v4/clarification/ask` has no auth and takes the target `user_id` from the request body. It blocks up to 300s per call (resource exhaustion), and `send_status_update_async` will deliver to the sole connected user when the id doesn't match — so the injection lands even on a wrong guess. | `router.py:636–681` · `connection_config.py:242–261` |
| H4 | **Plan rejection deletes any document by id.** `delete_plan_by_plan_id()` filters on `c.id` alone — no `user_id`, no `data_type`. Reachable: only `m_plan_id` is checked against pending approvals, so starting one's own plan and rejecting it with someone else's `plan_id` destroys their document. | `plan_service.py:164–173` · `cosmosdb.py:443–463` |
| H5 | **Any caller can write to and complete another user's plan.** `POST /api/v4/agent_message` appends to whatever `plan_id` the body names and, on `is_final`, overwrites `streaming_message` and marks the plan `completed`. When the plan lookup returns `None` the next line dereferences it; the `AttributeError` is swallowed and reported as success. | `router.py:838–933` · `plan_service.py:180–216` |

---

## 4. Medium findings

| # | Finding | Location |
|---|---|---|
| M1 | **Updating a team silently creates a duplicate.** The `team_id` query param overrides `team_id`/`id` but leaves a freshly generated `session_id` — the partition key — so `create_item` inserts into a different partition instead of conflicting. `get_team()` then returns `teams[0]` from an unordered result. | `router.py:1086–1090` · `team_service.py:163–184` |
| M2 | **Deliberate 404s converted to 500s.** Both handlers raise `HTTPException(404)` inside a `try` whose `except Exception` re-raises a 500. Other handlers in the same file get this right with `except HTTPException: raise`. | `router.py:1441–1478, 539–627` |
| M3 | **Correctness depends on never scaling past one replica.** Orchestrations, approvals, clarifications and socket registrations are module-level dicts. `maxReplicas: 1` is what keeps this working, but nothing records that the pin is load-bearing — it reads like a cost setting. | `connection_config.py:22–36` · `infra/bicep/main.bicep` |
| M4 | **RAI classifier creates and destroys a Foundry agent per check** — on every user message. It also mutates the caller's `TeamConfiguration` in place (`team.team_id = "rai_team"`), and parses the verdict with a substring test (`if "FALSE" in verdict`). | `team_utils.py:56–115, 145–178` |
| M5 | **Unauthenticated endpoint mutates process-wide state.** `POST /api/user_browser_language` writes to `os.environ` for the whole process, so one user's locale overwrites everyone's. | `app.py:135–164` |
| M6 | **Access-control parameters accepted and ignored.** `get_team_configuration(team_id, user_id)` and `delete_team_configuration(…)` document `user_id` as "for access control" and never use it. Scoping *is* enforced in the Cosmos queries, so there is no live vulnerability — the risk is that the service layer looks like it enforces ownership when it doesn't. | `team_service.py:186–210, 258–298` |
| M7 | **Image proxy is unauthenticated.** Path traversal is correctly blocked, but any leaked blob name is served to anyone. Separately, `plan_approval` returns `null` with a 200 when `m_plan_id` is absent. | `router.py:1481–1506` |

---

## 5. Low-severity and hygiene

| # | Item | Location |
|---|---|---|
| L1 | Seven `print()` calls in production code paths, bypassing logging config | `plan_service.py:140,162,176` · `team_service.py:244` · `orchestration_manager.py:295,308,342` |
| L2 | Dead code: `frontend_url` assigned never read; `AppConfig._get_bool` never called | `app.py:98` · `app_config.py:212` |
| L3 | Comments cite `localspec/bugs/framework/F1-tool-history-leak.md` as the tracking doc for two active monkey-patches; that path does not exist in the repo | `orchestration_manager.py:38` · `patches/tool_history_leak.py:13` |
| L4 | `delete_team_agent` annotated `-> None` but returns `True` | `cosmosdb.py:506–528` |
| L5 | Dependabot auto-merge force-pushes a `rebase -X theirs` and merges with no test gate. Scoped to the `dependabotchanges` base branch, which limits blast radius | `.github/workflows/scheduled-Dependabot-PRs-Auto-Merge.yml` |
| L6 | 11 of 27 workflows declare no top-level `permissions:` block | `.github/workflows/` |
| L7 | Actions pinned to mutable tags rather than commit SHAs. `pull_request_target` appears once, correctly (read-only, no PR checkout) | `.github/workflows/` |
| L8 | A 6.0 MB SVG ships in the frontend source tree | `src/App/src/assets/WebWarning.svg` |

---

## 6. History forensics

**Secrets — clean.** No `.env`, `.pem`, `.pfx`, `.key` or `.p12` blob exists anywhere in reachable
history, confirmed three ways (`git log` pathspec, literal pathspec, and a full
`rev-list --objects` scan). Only `.env.sample` / `.env.template` / `.env.example` are tracked. A
pattern sweep for credential-shaped literals across all Python, TypeScript, JSON, YAML and Bicep
sources returns three hits, all mock values in test fixtures. No evidence of history rewriting.

**Fork lineage.** The fork diverges from `microsoft/main` at `4bca7ec`. Everything since spans 71
files — dependency remediation, an eslint repair and ~150-warning cleanup, two new CI jobs, test
config fixes, and the `0.1.0-rc.1` → `0.1.0` release. No fork commit has touched application logic.

**Release integrity.** Versions are consistent at `0.1.0` across all four sources and both
lockfiles; the CHANGELOG matches. **But `git tag -l` returns nothing.** The CHANGELOG declares
"First stable release of this fork" under `[0.1.0] - 2026-08-13`, yet neither `v0.1.0-rc.1` nor
`v0.1.0` was ever tagged and no GitHub release exists. The previous report flagged the tag push as
blocked on credentials; it was never completed. There is currently no immutable marker for what
"v0.1.0" refers to.

---

## 7. Verification

Re-run in this session against a CI-equivalent environment built from `.github/requirements.txt`.

| Check | Result |
|---|---|
| `pytest src/tests/backend/test_app.py` | 29 passed |
| `pytest src/tests/backend --ignore=…/test_app.py` | 834 passed, 52 subtests |
| Coverage vs 80% CI floor | 86% |
| `flake8 --config=.flake8 src/backend` | exit 0 |
| `pytest src/tests/mcp_server` | 29 passed |
| `pip-audit` — backend lock | 0 vulnerabilities |
| `pip-audit` — mcp_server lock | 0 vulnerabilities |
| `pip-audit` — App lock | 0 vulnerabilities |
| `pip-audit -r .github/requirements.txt` | 0 vulnerabilities |
| `npm audit --omit=dev` (src/App) | 0 vulnerabilities |
| Frontend XSS sinks (`dangerouslySetInnerHTML`/`innerHTML`) | none found |

The MCP server suite cannot run against the CI requirement set (`pydantic_settings` is absent from
it) and needs its own `uv sync --frozen --extra dev` environment, as CLAUDE.md documents. That is a
known environment split, not a regression.

---

## 8. Recommendations, in order

1. **Close the front door.** Backend behind Container Apps auth or internal-only ingress; MCP server
   `ingressExternal: false`; CORS wildcard → the `FRONTEND_SITE_NAME` value already loaded; drop the
   `sample_user` fallback outside dev. *(C1, C5, H1 — configuration only, shippable immediately.)*
2. **Restore per-user scoping in the data layer.** Add the missing `user_id` predicate to
   `get_plan_by_plan_id` and `delete_plan_by_plan_id` (which also needs `data_type`); audit the
   plan-scoped siblings. *(C2, H4 — small diffs.)*
3. **Bind the approval gate to its owner.** Record the owning `user_id` with each pending approval
   and clarification and require a match. Add a test asserting a foreign approval is rejected.
   *(C3, C4, H3, H5.)*
4. **Repair the two correctness bugs users will hit:** the unconditional RAI skip on `?team_id=`,
   and the team-update path that inserts a duplicate into a new partition. *(H2, M1.)*
5. **Tag the release that already shipped.** Create and push `v0.1.0` (and retroactively
   `v0.1.0-rc.1`) and publish the GitHub releases. *(§6 — outstanding across two reports.)*
6. **Document the single-replica constraint, then design it out.** Comment the dependency at both
   ends, then move approval state and socket fan-out off process memory. *(M3.)*
7. **Take the inherited findings upstream** to `microsoft/…` so they are fixed for every deployment
   of the accelerator, rather than carried as a permanent private patch set.
8. **Tighten CI supply chain and clear hygiene debt:** pin actions to SHAs, add `permissions:`
   blocks, gate the Dependabot auto-merge on tests, then the L-series items.

---

## 9. Remediation status

Added 2026-08-16, after the findings above were worked through. "Fixed" means
the defect is closed and covered by a test; where that is not the whole story
the note says so.

The evidence behind this table — the commits, the re-run verification figures,
and what could not be completed — is in
[`2026-08-16-remediation-record.md`](2026-08-16-remediation-record.md).

| # | Status | Note |
|---|---|---|
| C1 | **Partially fixed** | An opt-in Container Apps `authConfig` is wired through both flavours behind `backendAuthClientId`, empty by default. `auth_utils` now prefers the platform's `x-ms-client-principal` claims blob over the bare id header. It is not *enabled* by default — that needs an app registration, and enabling it has consequences documented in `docs/backend_api_authentication.md`. |
| C2 | Fixed | `get_plan_by_plan_id` now constrains `c.user_id`. |
| C3 | Fixed | Approvals and clarifications record an owner; both endpoints 403 on a mismatch, and an unrecorded owner is a denial. |
| C4 | Fixed | The WebSocket is authorized before `accept()`, and now takes a signed token bound to the user and the plan. |
| C5 | Fixed | The MCP server is on internal ingress. |
| H1 | Fixed | CORS is scoped to `FRONTEND_SITE_NAME`. |
| H2 | Fixed | RAI validation runs on every upload; the "passed" event no longer fires when nothing was validated. |
| H3 | Fixed | The clarification can no longer be *answered* by anyone else (C3), and outside dev it can no longer be misdelivered. The endpoint no longer takes its target from the caller either: `AgentFactory` mints a signed, expiring clarify token when it builds an agent with `user_responses=true`, `ask_user` passes that token instead of a `user_id`, and `/clarification/ask` derives the user from the signature. An absent, forged, expired or wrong-purpose token is a 401, so the model chooses the *question* but never the *recipient*. |
| H4 | Fixed | `delete_plan_by_plan_id` constrains `data_type` and `user_id`. |
| H5 | Fixed | `/agent_message` authorizes against the plan it targets and requires a `plan_id`. |
| M1 | Fixed | Updates go through `update_team_configuration`, preserving the stored document's identity and partition key. Default teams are refused with a 403 — an authorization gap the audit missed. |
| M2 | Fixed | Deliberate status codes re-raise ahead of the catch-all in all four places. |
| M3 | Fixed | Documented at both ends, and the **AVM** flavour's `maxReplicas: enableScalability ? 3 : 1` was corrected — worse than this report recorded, since it would have run three replicas of a single-replica-only backend. |
| M4 | Fixed | The RAI agent is built once and reused, discarded on failure; it no longer mutates the caller's config; the verdict must be unambiguous. |
| M5 | Fixed | Authenticated and keyed per user rather than written to `os.environ`. |
| M6 | Fixed | Docstrings state where enforcement actually lives. |
| M7 | **Partially fixed** | Images authenticate with a signed token and are checked against a recorded owner (`common/utils/image_assets.py`). Images generated before the record existed have none and fall back to token-only protection, so existing conversations keep rendering; the handler can require a record once history has turned over. |
| L1 | Fixed | `print()` replaced with logging, or removed where an adjacent `logger.error` already covered it. |
| L2 | Fixed | `_get_bool` deleted; `frontend_url` is now used by the CORS fix. |
| L3 | Fixed | Dangling `localspec/` references removed. |
| L4 | Fixed | `delete_team_agent` is annotated `-> bool`. |
| L5 | Fixed | The Dependabot auto-merge now refuses to merge unless CI reports completed, non-failing checks. |
| L6 | Fixed | All 27 workflows carry a top-level `permissions:` block, following the repository's own `azure-dev.yml` convention. |
| L7 | **Not done** | Pinning actions to commit SHAs needs each tag resolved to a digest. Guessing them would be worse than leaving them, so this is left for `pinact` or an equivalent tool run with network access. |
| L8 | Fixed | `WebWarning.svg` was not a vector: it was one 2048×2048 base64 PNG (4.4 MB decoded) wrapped in an SVG and displayed at 128×128. Re-encoded at 384×384 — 6.2 MB → 207 KB, 3.3% of the original. |

### Follow-on work this produced

Closing C1 surfaced three things that were not in the original findings, all now
done: images and the WebSocket needed a way to authenticate without a header
(short-lived signed tokens in `common/utils/resource_tokens.py`), and the
frontend needed to acquire a bearer token for the API audience.

### Still open

- **C1 is not enabled.** The parameter exists; the app registration and the
  decision to turn it on are yours. Until then every ownership check in the
  application still compares against a client-supplied identity.
- **H3's unauthenticated endpoint**, and the design change behind it.
- **L7**, above.
- **The v0.1.0 tag** (§6) — still never pushed.
