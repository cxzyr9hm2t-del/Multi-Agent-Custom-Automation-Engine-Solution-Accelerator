# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Multi-Agent Custom Automation Engine (MACAE) — a Microsoft solution accelerator where teams of specialized AI agents plan and execute business tasks with human-in-the-loop approval. Built on Azure AI Foundry and the `agent_framework` / `agent_framework_orchestrations` Magentic workflow packages, deployed to Azure Container Apps via `azd`.

## Three independent services

Each service has its own virtual environment and runs in its own terminal. Python services use `uv` and Python 3.12; dependencies are declared in each service's `pyproject.toml`.

| Service | Directory | Entry point | Port |
|---|---|---|---|
| Backend API | `src/backend` | `python app.py` | 8000 |
| MCP server | `src/mcp_server` | `python mcp_server.py --transport streamable-http --host 0.0.0.0 --port 9000` | 9000 |
| Frontend | `src/App` | `python frontend_server.py` (serves the Vite `build/` output) | 3000 |

Setup per Python service (run inside the service directory):

```bash
uv venv .venv && source .venv/bin/activate
uv sync --python 3.12 --extra dev   # --extra dev only exists for src/backend (pytest etc.)
```

Frontend (in `src/App`): `npm install`, then `npm run build` (tsc + vite build). `npm run dev` starts the Vite dev server; `python frontend_server.py` serves the built app and can proxy API/WS requests to the backend (`PROXY_API_REQUESTS`).

Backend configuration lives in `src/backend/.env` (copy from `.env.sample`, values come from the deployed Azure Container App). For local dev set `APP_ENV=dev`, `BACKEND_API_URL=http://localhost:8000`, `FRONTEND_SITE_NAME=*`, `MCP_SERVER_ENDPOINT=http://localhost:9000/mcp`. Running locally requires Azure resources (Cosmos DB, AI Foundry, AI Search) and `az login` with the RBAC roles described in `docs/LocalDevelopmentSetup.md`.

## Tests

The canonical test tree is `src/tests/` (this is what CI runs — see `.github/workflows/test.yml`). Root `conftest.py` adds `src/` and `src/backend/` to `sys.path`, so run pytest **from the repository root**:

```bash
# Backend unit tests (what CI runs, with backend deps installed)
python -m pytest src/tests/backend

# A single file / single test
python -m pytest src/tests/backend/v4/orchestration/test_orchestration_manager.py
python -m pytest src/tests/backend -k test_name

# MCP server tests
python -m pytest src/tests/mcp_server

# With coverage as CI does
python -m pytest src/tests/backend --cov=src/backend --cov-config=.coveragerc
```

Root `pytest.ini` enforces `asyncio_mode = strict` — async tests need `@pytest.mark.asyncio`.

Notes:
- `src/backend/tests/` is a second, older test tree; it is excluded from coverage (`.coveragerc`) and flake8. Prefer adding tests under `src/tests/`.
- `tests/e2e-test/` holds Playwright/pytest UI automation run by separate workflows against deployed environments; it has its own `requirements.txt` and README.
- Frontend tests: `npm test` (vitest) in `src/App`.

## Lint

CI runs flake8 on the backend only:

```bash
flake8 --config=.flake8 src/backend
```

Config: max-line-length 88 with E501/E203/W503/G004/G200/E402 ignored. Frontend: `npm run lint` / `npm run lint:fix` (eslint) in `src/App`.

## Architecture

### Backend (`src/backend`)

- `app.py` — FastAPI app. Configures logging + Application Insights/OpenTelemetry, mounts the v4 router, and on shutdown cleans up all Foundry agents via `v4/config/agent_registry.py` (a global weak-ref registry of live agent instances).
- `v4/api/router.py` — all HTTP endpoints under `/api/v4`, plus the WebSocket endpoint `/api/v4/socket/{process_id}` that streams orchestration progress to the UI. Message shapes for the socket are in `v4/models/messages.py` (`WebsocketMessageType`).
- `v4/orchestration/orchestration_manager.py` — `OrchestrationManager` builds and runs the Magentic multi-agent workflow (`MagenticBuilder` from `agent_framework_orchestrations`) for an input task.
- `v4/orchestration/human_approval_manager.py` — `HumanApprovalMagenticManager` extends `StandardMagenticManager` to insert human approval gates: the generated plan is surfaced to the user over the WebSocket and execution waits for approval/clarification.
- `v4/magentic_agents/` — `MagenticAgentFactory` instantiates agents from team-configuration JSON. `FoundryAgentTemplate` creates Azure AI Foundry agents with optional capabilities per agent config flags: `use_rag` (Azure AI Search index), `use_mcp` (tools from the MCP server), `use_bing`, `use_reasoning`, `coding_tools`. `ProxyAgent` represents the human in the loop.
- `v4/common/services/` — service layer (`plan_service`, `team_service`, `foundry_service`, `mcp_service`, ...) between the router and the database/Foundry.
- `common/` — shared infrastructure: `config/app_config.py` (env-driven `config` singleton used everywhere), `database/` (`DatabaseFactory` → `CosmosDBClient`; Cosmos DB stores plans, sessions, teams, messages), `models/messages_af.py` (Pydantic domain models: `InputTask`, `Plan`, `TeamConfiguration`, ...), `utils/utils_af.py` (includes RAI content-safety validation of user input and team configs).
- `auth/` — user identity from Azure App Service EasyAuth headers (`auth_utils.py`); `sample_user.py` provides a fake user for local dev.

### Team configurations (core domain concept)

Agent teams are defined as JSON documents (samples in `data/agent_teams/`: HR, marketing, retail, RFP analysis, contract compliance). Each defines the team's agents with their system messages, model deployment, and capability flags. Teams are uploaded to Cosmos DB (`infra/scripts/upload_team_config.py`) and can also be uploaded via the UI (RAI-validated on upload). `data/datasets/` holds sample data indexed into Azure AI Search (`infra/scripts/index_datasets.py`) for RAG-enabled agents.

### MCP server (`src/mcp_server`)

FastMCP server exposing domain tools to agents. `core/factory.py` defines `MCPToolFactory`/`MCPToolBase` and a `Domain` enum; services in `services/` (hr, marketing, product, tech_support, ...) subclass `MCPToolBase` and are registered in `mcp_server.py`. Supports optional JWT auth (Azure AD) via `config/settings.py`. To add tools: create/extend a service class and register it with the factory.

### Frontend (`src/App`)

React 18 + TypeScript + Vite, Fluent UI components, Redux Toolkit. `src/pages/` has the two main views (`HomePage`, `PlanPage`); `src/api/` wraps backend HTTP calls; WebSocket streaming updates drive the plan view. In production it is served by `frontend_server.py` (FastAPI) from the `build/` directory.

### Infrastructure (`infra/`)

Bicep templates (`main.bicep`, WAF variant) deployed with `azd up` (azd ≥ 1.18.0). Post-deploy: `infra/scripts/build_and_push_images.sh` builds/pushes the three container images to ACR, and `infra/scripts/selecting_team_config_and_data.sh` uploads team configs and indexes sample data. Deployment docs live in `docs/DeploymentGuide.md`.

## Conventions

- Backend imports assume both `src/` and `src/backend/` are on `sys.path` (e.g. `from common.config.app_config import config`, `from v4.orchestration...`) — that's why tests must run from the repo root with the root `conftest.py`.
- All runtime configuration is read through the `config` object in `common/config/app_config.py`; add new env vars there rather than reading `os.environ` directly.
- User-facing input and uploaded team configs must pass RAI (content safety) checks — see `rai_success` / `rai_validate_team_config` in `common/utils/utils_af.py`.
- CI coverage intentionally omits `v4/api/router.py`, the MCP server, and agent test harnesses (`.coveragerc`).
