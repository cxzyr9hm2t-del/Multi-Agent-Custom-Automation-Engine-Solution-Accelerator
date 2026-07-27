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

`--extra dev` applies to **`src/mcp_server` only** — it is the one service that declares `[project.optional-dependencies] dev`. `src/backend` currently carries `pytest`, `pytest-asyncio` and `pytest-cov` in `[project.dependencies]` instead, so they install unconditionally.

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

Known rough edges in the test setup, worth knowing before you debug them:

- The root `conftest.py` is vestigial. It inserts `<repo>/../../backend/v4/magentic_agents` onto `sys.path` — a path that resolves *above* the repository and refers to a `v4/` layout that no longer exists. It does nothing; don't rely on it and don't be misled by it.
- `.github/workflows/test.yml` passes `--cov-config=.coveragerc`, but `.coveragerc` no longer exists — coverage settings were consolidated into `[tool.coverage.*]` in the root `pyproject.toml`. Coverage still runs, but the `omit` list may not apply as written.
- `src/mcp_server/pytest.ini` uses a `[tool:pytest]` header, which is the `setup.cfg` spelling. In a `pytest.ini` the header must be `[pytest]`, so this file's settings are likely inert.
- Despite what older docs claim, `asyncio_mode` is **not** configured anywhere in this repo. The root `pyproject.toml` only sets `addopts = "-p pytest_asyncio"`.

Frontend: `npm test` (vitest) in `src/App`. Note there are currently **no frontend test files** — vitest exits non-zero with "No test files found".

## Lint

```bash
flake8 --config=.flake8 src/backend      # what CI checks
```

`.flake8`: `max-line-length = 88`, `extend-ignore = E501`, `ignore = E203, W503, G004, G200, E402`. Its `exclude` list still names `src/backend/tests`, a directory that no longer exists.

Frontend: `npm run lint` / `npm run lint:fix` (eslint) in `src/App`.

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
- Each service owns its own dependency set. A package pinned in one `pyproject.toml` is frequently at a different version in another, and `.github/requirements.txt` is a fourth, separate set used only by CI — check the specific manifest you are changing rather than assuming repo-wide consistency.
