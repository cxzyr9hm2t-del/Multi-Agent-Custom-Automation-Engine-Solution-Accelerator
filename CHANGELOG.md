# Changelog

All notable changes to this fork of the Multi-Agent Custom Automation Engine
solution accelerator are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (`X.Y.Z-rc.N` pre-releases map to
PEP 440 `X.Y.ZrcN` in the Python manifests).

## [Unreleased]

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

[0.1.0-rc.1]: https://github.com/cxzyr9hm2t-del/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator/releases/tag/v0.1.0-rc.1
