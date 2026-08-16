# Changelog

All notable changes to this fork of the Multi-Agent Custom Automation Engine
solution accelerator are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (`X.Y.Z-rc.N` pre-releases map to
PEP 440 `X.Y.ZrcN` in the Python manifests).

## [Unreleased]

Security remediation of the forensic audit in
`docs/reports/2026-08-15-forensic-audit.md`. Sixteen of its seventeen findings
are closed; §9 of that report carries the disposition of each.

### Security

- **The backend can now be put behind an authenticating front door.** A new
  `backendAuthClientId` parameter attaches a Container Apps auth configuration
  to the backend container app (Entra ID, `Return401`). Empty by default, so an
  unconfigured deployment is unchanged. `docs/backend_api_authentication.md`
  covers the app registration, what enabling it costs, and how to roll back.
- **Per-user scoping restored in the data layer.** `get_plan_by_plan_id` bound
  `@user_id` without referencing it in the `WHERE` clause, so any plan id
  resolved for any caller; `delete_plan_by_plan_id` matched on `c.id` alone,
  so it could delete another user's plan or a team configuration.
- **The human-in-the-loop approval gate is bound to its owner.** Pending
  approvals and clarifications were keyed only by their own id, so any caller
  holding one could approve another user's plan and release their agent
  workflow. Both endpoints now return 403 on a mismatch.
- **The WebSocket authenticates before it accepts**, using a short-lived signed
  token bound to the user and the plan. The anonymous all-zeros default is gone.
- **The MCP server is on internal ingress.** It shipped publicly reachable with
  `ENABLE_AUTH=false`, leaving every domain tool callable by anyone.
- **CORS is scoped to the configured frontend origin.** A wildcard with
  `allow_credentials=True` makes Starlette reflect the caller's origin back.
- **Content-safety validation runs on every team upload.** It was guarded by
  `if not team_id:`, so `?team_id=anything` skipped it — and team configurations
  carry each agent's system message.
- **Generated images authenticate and are checked against a recorded owner.**
  Ownership is recorded by the backend, which knows whose orchestration produced
  a message; the MCP server that creates the image does not.
- `/agent_message` and `/api/user_browser_language` are authorized; the latter
  no longer writes one user's locale into process-global state.

### Fixed

- Team updates replace the stored document instead of inserting a duplicate into
  a new Cosmos partition, which left two documents with the same `team_id` and
  made which one a user saw undetermined. Default teams are now refused.
- The AVM deployment no longer scales the backend to three replicas when
  `enableScalability` is set. Its orchestration, approval and socket state lives
  in process memory, so a second replica strands approvals with no error.
- The RAI classifier is created once instead of per request, no longer renames
  the caller's team configuration in place, and requires an unambiguous verdict.
- Deliberate 400/403/404 responses reach the client instead of being converted
  to 500 by a surrounding catch-all.

### Changed

- All 27 workflows declare a top-level `permissions:` block, and the Dependabot
  auto-merge refuses to merge unless CI reports completed, non-failing checks.
- `WebWarning.svg` was a 2048×2048 base64 PNG wrapped in an SVG and displayed at
  128×128; re-encoded at 384×384, 6.2 MB → 207 KB.

## [0.1.0] - 2026-08-13

First stable release of this fork, promoting `0.1.0-rc.1` with the security
remediation below applied on top. All service versions (backend, MCP server,
frontend app and package) are set to `0.1.0`.

### Security

- Remediated the three Dependabot alerts open against `v0.1.0-rc.1`:
  - `mcp` 1.27.2 → 1.28.1 in the MCP server (CVE-2026-59950, WebSocket
    transport missing Host/Origin validation), now pinned to match the
    backend; `pydantic` moved 2.11.7 → 2.13.4 alongside it, aligning all
    three services.
  - `h2` 4.3.0 → 4.4.1 in the backend (CVE-2026-71554, request smuggling
    via duplicate Host headers), held as a transitive security pin.
  - `mem0ai` 1.0.11 → 2.0.18 in the backend (CVE-2026-7597, improper input
    validation) via a uv dependency override — every `agent-framework-mem0`
    release compatible with the pinned `agent-framework==1.6.0` caps
    `mem0ai<2`, the backend does not use the mem0 integration, and the fix
    only exists in 2.x.

## [0.1.0-rc.1] - 2026-08-13

First release candidate of this fork, cut from `main` after syncing with
upstream `microsoft/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator`
and completing the hardening work below.

### Security

- Remediated 77 confirmed dependency vulnerabilities across all five
  dependency manifests (backend, MCP server, frontend Python service,
  frontend npm tree, and CI requirements), including a reachable
  form-parsing denial-of-service in the backend and seven unpatched
  starlette advisories that a stale `fastapi==0.115.0` pin was holding in
  place on the MCP server.
- Remediated 8 further vulnerable dependencies surfaced by a follow-up OSV
  audit.
- Replaced the abandoned `PyPDF2` package, and dropped end-of-life
  `ansible-core` and the unused `semantic-kernel` dependency from the
  deployment tooling.
- Patched outstanding npm advisories in the frontend and made its container
  image build reproducible.

### Fixed

- Repaired the frontend eslint configuration (previously `npm run lint`
  failed to run at all) and cleared all 44 resulting errors.
- Resolved roughly 150 eslint warnings by typing the frontend API layer,
  websocket payloads, models, and services, eliminating non-null assertions
  and most `any` usages, and fixing all five `exhaustive-deps` warnings.
- Repaired three dead test-configuration defects: a stale root `conftest.py`
  pointing at a removed `v4/` layout, a `--cov-config` flag referencing a
  deleted `.coveragerc`, and an inert `[tool:pytest]` header in
  `src/mcp_server/pytest.ini`.

### Added

- CI gate for frontend lint errors (`frontend-lint.yml`).
- Advisory CI job reporting cross-manifest dependency version drift on pull
  requests that touch a manifest (`version-drift.yml`).
- `CLAUDE.md` contributor guide, kept current with the tree.

### Changed

- Synced the fork with upstream `microsoft/main`.
- Set all service versions (backend, MCP server, frontend app and package)
  to `0.1.0-rc.1`.

[0.1.0]: https://github.com/cxzyr9hm2t-del/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator/releases/tag/v0.1.0
[0.1.0-rc.1]: https://github.com/cxzyr9hm2t-del/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator/releases/tag/v0.1.0-rc.1
