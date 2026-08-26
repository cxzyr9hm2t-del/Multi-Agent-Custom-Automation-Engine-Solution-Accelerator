# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Multi-Agent Custom Automation Engine (MACAE) — a Microsoft solution accelerator where teams of specialized AI agents plan and execute business tasks with human-in-the-loop approval. Built on Azure AI Foundry and the `agent_framework` packages, deployed to Azure Container Apps via `azd`.

## Three independent services

Each service has its own `pyproject.toml`, lockfile and virtual environment, and runs in its own terminal.

| Service | Directory | Entry point | Port | Python |
|---|---|---|---|---|
| Backend API | `src/backend` | `python app.py` | 8000 | `>=3.11` |
| MCP server | `src/mcp_server` | `python mcp_server.py --transport streamable-http --host 0.0.0.0 --port 9000` | 9000 | `>=3.10` |
| Frontend | `src/App` | `python frontend_server.py` (serves the Vite `build/` output) | 3000 | `>=3.11` |

Setup per Python service, run inside the service directory:

```bash
uv sync --frozen          # installs from that service's uv.lock
```

`--extra dev` applies to **`src/mcp_server` only** — it is the one service that declares `[project.optional-dependencies] dev`. `src/backend` keeps its test toolchain (`pytest`, `pytest-asyncio`, `pytest-cov`) in `[dependency-groups] dev`, which `uv sync` installs by default — no extra flag needed. Its Dockerfile passes `--no-dev`, so the group stays out of the production image.

Frontend (in `src/App`): `npm ci`, then `npm run build` (`tsc && vite build`, output goes to `build/`). `npm run dev` starts the Vite dev server; `python frontend_server.py` serves the built app and can proxy API/WS requests to the backend (`PROXY_API_REQUESTS`).

Backend configuration lives in `src/backend/.env` (copy from `.env.sample`). For local dev set `APP_ENV=dev`, `BACKEND_API_URL=http://localhost:8000`, `FRONTEND_SITE_NAME=*`, `MCP_SERVER_ENDPOINT=http://localhost:9000/mcp`. Running locally requires real Azure resources (Cosmos DB, AI Foundry, AI Search) and `az login` with the RBAC roles described in `docs/LocalDevelopmentSetup.md`.

## Tests

The canonical test tree is `src/tests/` (`agents/`, `backend/`, `mcp_server/`). CI runs it from the repository root — see `.github/workflows/test.yml`.

**`PYTHONPATH=src:src/backend` is required.** Backend modules import each other as top-level packages (`from common.config.app_config import config`, `from orchestration.orchestration_manager import ...`), so without it pytest aborts during collection with `ModuleNotFoundError` before running a single test. The root `pyproject.toml` sets `pythonpath = ["src"]`, which is *not* sufficient on its own — `src/backend` must also be on the path. This is the single most common way to get a confusing failure here.

How many modules fail, and which, depends on the installed dependency set, so don't anchor on a specific count: with CI's `.github/requirements.txt` it is 2 (`orchestration.orchestration_manager`, `tools.clarification_tool`), while a `src/backend`-lock environment fails 4, adding `agent_framework` import errors on top.

```bash
# What CI actually runs
PYTHONPATH=src:src/backend python -m pytest src/tests/backend/test_app.py --cov=src/backend -q
PYTHONPATH=src:src/backend python -m pytest src/tests/backend --cov=src/backend --cov-append --ignore=src/tests/backend/test_app.py

# A single file / single test
PYTHONPATH=src:src/backend python -m pytest src/tests/backend/services/test_team_service.py
PYTHONPATH=src:src/backend python -m pytest src/tests/backend -k test_name

# MCP server tests
PYTHONPATH=src:src/mcp_server python -m pytest src/tests/mcp_server
```

`test_app.py` is run first and separately — it needs process isolation. CI enforces an **80% coverage floor**; the suite currently sits at ~86%.

Two things about how the suite is invoked, worth knowing before you debug them:

- **`test_app.py` must run in its own process.** Running the whole tree in one pytest invocation aborts collection with `No module named 'orchestration.orchestration_manager'` — `test_app.py` disturbs import state for the rest of the tree. This is why CI runs it first and separately, and why the two commands above are two commands. Split that way, the full suite passes (29 + 834).
- `asyncio_mode` is **not** configured anywhere in this repo, despite what older docs claim. The root `pyproject.toml` only sets `addopts = "-p pytest_asyncio"`.

Three config defects were found while writing this file and fixed in the same change, so you will not hit them — noted here only because older checkouts still have them: the root `conftest.py` was dead code pointing above the repository at a `v4/` layout that no longer exists (deleted); `.github/workflows/test.yml` passed `--cov-config=.coveragerc` for a file that no longer exists (flag removed — coverage is unchanged at 86%, since `[tool.coverage.*]` in the root `pyproject.toml` now applies); and `src/mcp_server/pytest.ini` used the `setup.cfg` header `[tool:pytest]`, which meant its settings were silently inert (corrected to `[pytest]`).

Frontend: `npm test` (vitest) in `src/App`. Note there are currently **no frontend test files** — vitest exits non-zero with "No test files found".

## Lint

```bash
flake8 --config=.flake8 src/backend      # what CI checks
```

`.flake8`: `max-line-length = 88`, `extend-ignore = E501`, `ignore = E203, W503, G004, G200, E402`; `exclude` covers `.venv` and the JS/TS file globs.

Frontend: `npm run lint` / `npm run lint:fix` (eslint) in `src/App`, gated in CI by `.github/workflows/frontend-lint.yml`. The gate is on **errors only** — eslint exits zero on warnings, and the codebase deliberately carries ~147 warnings (mostly `no-explicit-any`). Don't add `--max-warnings=0` until that backlog is cleared.

## Architecture

### Backend (`src/backend`)

There is **no `v4/` directory** — that layout was flattened. Modules sit directly under `src/backend/`:

- `app.py` — FastAPI app. Configures logging plus Application Insights/OpenTelemetry and mounts the router.
- `api/router.py` — all HTTP endpoints, mounted under the `/api/v4` prefix (the prefix survived the flattening even though the directory did not). Also hosts the WebSocket endpoint `/api/v4/socket/{process_id}` that streams orchestration progress to the UI, and performs RAI validation on inbound content.
- `orchestration/` — `orchestration_manager.py` builds and runs the Magentic multi-agent workflow; `plan_review_helpers.py` implements the human approval gates (the generated plan is surfaced over the WebSocket and execution waits for approval/clarification); `user_interaction_agent.py` represents the human in the loop; `connection_config.py` and `helper/` support both.
- `agents/` — `agent_factory.py` instantiates agents from team-configuration JSON; `agent_template.py` creates Azure AI Foundry agents with per-agent capability flags (RAG over Azure AI Search, MCP tools, Bing, reasoning, coding tools).
- `services/` — service layer between the router and Foundry/the database: `plan_service.py`, `team_service.py`, `foundry_service.py`, `mcp_service.py`, `base_api_service.py`.
- `common/` — shared infrastructure. `config/app_config.py` holds the env-driven `config` singleton; `database/` has `DatabaseFactory` → `CosmosDBClient` (Cosmos stores plans, sessions, teams, messages); `utils/` has `team_utils.py` (includes RAI validation of team configs), `agent_utils.py`, `event_utils.py`, `markdown_utils.py`, `otlp_tracing.py`.
- `models/` — Pydantic domain models (`messages.py`, `plan_models.py`).
- `auth/` — user identity from Azure App Service EasyAuth headers; a sample user is provided for local dev.
- `callbacks/`, `middleware/`, `config/`, `patches/` — supporting concerns.

### Content packs (core domain concept)

Agent teams are defined as JSON documents under **`content_packs/`** (not `data/`, which no longer exists): `hr_onboarding`, `marketing_press_release`, `rfp_evaluation`, `contract_compliance`, `retail_customer`, `content_gen`, `example_pack`. Each pack has an `agent_teams/` directory, and some have `datasets/` for RAG content. Each team definition lists its agents with system messages, model deployment and capability flags.

Packs are loaded by the post-provision scripts in `infra/scripts/post-provision/` — `upload_team_config.py`, `index_datasets.py`, `seed_knowledge_bases.py`, `seed_vector_stores.py`, `seed_kb_connections.py`, `upload_images_to_cosmos.py`. Teams can also be uploaded through the UI, where they are RAI-validated first.

### MCP server (`src/mcp_server`)

FastMCP server exposing domain tools to agents. `core/factory.py` defines `MCPToolFactory` / `MCPToolBase` and a `Domain` enum; the classes in `services/` (`hr_service.py`, `marketing_service.py`, `product_service.py`, `tech_support_service.py`, `general_service.py`, `data_tool_service.py`, `image_service.py`, `ask_user_service.py`) subclass `MCPToolBase` and are registered in `mcp_server.py`. Optional JWT auth (Azure AD) via `config/settings.py`. To add tools: create or extend a service class and register it with the factory.

### Frontend (`src/App`)

React 18 + TypeScript + Vite, Fluent UI, Redux Toolkit. `src/pages/` holds the main views, `src/api/` wraps backend HTTP calls, `src/store/` holds Redux state, and WebSocket streaming updates drive the plan view. Built output lands in `build/` and is served by `frontend_server.py` in production.

### Infrastructure (`infra/`)

Bicep templates (`main.bicep`, plus a WAF variant) deployed with `azd up`. Dockerfiles live at `src/backend/Dockerfile`, `src/mcp_server/Dockerfile` and `src/App/Dockerfile`; all three pin `ghcr.io/astral-sh/uv:0.6.3`, so regenerating a `uv.lock` with a much newer uv is worth sanity-checking against that version. Deployment docs are in `docs/DeploymentGuide.md`.

Note `src/App/Dockerfile` installs from `requirements.txt` when that file is present and only falls back to `pyproject.toml` otherwise — so `src/App/uv.lock` is not what gets installed unless `requirements.txt` is absent.

## Conventions

- Backend imports assume both `src/` and `src/backend/` are on `sys.path`. Set `PYTHONPATH=src:src/backend` when running anything from the repo root.
- Read all runtime configuration through the `config` object in `common/config/app_config.py` rather than touching `os.environ` directly.
- User-facing input and uploaded team configs must pass RAI content-safety checks — see the helpers in `common/utils/team_utils.py` and their use in `api/router.py`.
- Each service owns its own dependency set. A package pinned in one `pyproject.toml` is frequently at a different version in another, and `.github/requirements.txt` is a fourth, separate set used only by CI — check the specific manifest you are changing rather than assuming repo-wide consistency. An advisory CI job (`.github/workflows/version-drift.yml`) reports cross-manifest version drift on PRs that touch a manifest; it never fails the build, but read its report before applying a security bump to only one manifest — drift is exactly how one copy of a package gets hardened while the others stay vulnerable.
- **Audit the lockfiles, not the manifests.** The manifests declare roughly 95 direct pins; the three `uv.lock` files resolve close to 300, and `uv sync --frozen` installs that closure — the exact counts move, the order-of-magnitude gap is the point. `src/App/requirements.txt` (hash-pinned, one `pkg==ver \` per line) is what the App image installs, not `src/App/pyproject.toml`. A hand-rolled manifest-only pass cannot see a transitive package at all, so it reports a clean result it has not earned: that is how advisories in `h2`, `mcp` and `mem0ai` went unreported by one and had to be caught from Dependabot instead. `version-drift.yml` shares the blind spot by design — it compares declared pins.
- Three things already audit dependencies, and they cover different ground. **`scheduled-security-sweep.yml`** is the scheduled authority: pip-audit over the resolved lockfiles every Monday, and it *fails* on a finding. **`dependency-audit.yml`** is the pull-request complement, advisory-only, and covers what the sweep's flags exclude — dev dependencies (`--no-dev` hides eight packages in `src/backend` alone, `pytest` among them), the two `infra/**/requirements.txt` sets, and `tests/e2e-test`. **Dependabot** remains the primary alarm. Run the same check locally with `python .github/scripts/audit_dependencies.py` (stdlib only). Whatever you add, make it report the lines it could **not** parse instead of skipping them silently — silent under-collection is what makes a false "clean" possible.
- A lock can hold several versions of one package, one per resolution fork, each tagged with `resolution-markers`; only the fork matching the runtime installs. Check the marker before treating a flagged version as shipped, and confirm by installing and reading `importlib.metadata.version(...)`. An unbounded `requires-python` widens this: `mcp_server` declares `>=3.10`, so uv resolves a Python 3.14 fork even though the Dockerfile is 3.11 (CI is 3.11, 3.13 for e2e) — which is why `mcp` and `pydantic` carry the pins and comments they do. A bump that satisfies 3.11 can still fail to resolve on 3.14.
