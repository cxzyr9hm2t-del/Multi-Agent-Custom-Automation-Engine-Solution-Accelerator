# Release Report — v0.1.0-rc.1 and Dependabot Remediation

**Date:** 2026-08-13
**Repository:** `cxzyr9hm2t-del/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator` (fork of `microsoft/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator`)
**Scope:** first release candidate of this fork, plus triage and remediation of the three Dependabot alerts open against it.

---

## 1. Executive summary

Two changes landed on `main` today:

| PR | Title | Head commit | Merge commit | Result |
|---|---|---|---|---|
| [#9](https://github.com/cxzyr9hm2t-del/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator/pull/9) | chore(release): cut v0.1.0-rc.1 release candidate | `6ea70b1` | `627f18a` | Merged |
| [#10](https://github.com/cxzyr9hm2t-del/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator/pull/10) | fix: remediate the three Dependabot alerts open against v0.1.0-rc.1 | `09307c7` | `7fd0c49` | Merged |

After both merges, every version source in the repository identifies the release candidate (`0.1.0rc1` PEP 440 / `0.1.0-rc.1` semver), a `CHANGELOG.md` documents the release, and a fresh `pip-audit` + `npm audit` sweep of all five dependency manifests reports **zero known vulnerabilities**. The full backend test suite (29 + 834 tests, 86% coverage against an 80% CI floor) and the MCP server suite (29 tests) pass, and all code-related CI workflows on `main` are green.

One step remains that could not be completed from this environment: pushing the `v0.1.0-rc.1` git tag and creating the GitHub pre-release (see §5).

---

## 2. Release candidate v0.1.0-rc.1 (PR #9)

### 2.1 Version alignment

No git tags or GitHub releases existed before this work; all manifests carried `0.1.0`. The RC was therefore cut as **v0.1.0-rc.1**, the first pre-release of 0.1.0. Every version source was updated:

| File | Before | After | Notes |
|---|---|---|---|
| `src/backend/pyproject.toml` | `0.1.0` | `0.1.0rc1` | PEP 440 pre-release form |
| `src/App/pyproject.toml` | `0.1.0` | `0.1.0rc1` | PEP 440 pre-release form |
| `src/mcp_server/__init__.py` | `0.1.0` | `0.1.0rc1` | hatchling sources the package's dynamic version from `__version__` here |
| `src/App/package.json` | `0.1.0` | `0.1.0-rc.1` | semver pre-release form |
| `src/App/package-lock.json` | `0.1.0` (2 fields) | `0.1.0-rc.1` | root and `packages[""]` entries synced so `npm ci` stays consistent |
| `src/backend/uv.lock` | `backend 0.1.0` | `backend 0.1.0rc1` | the lockfile records the project's own version; without this sync, `uv sync --frozen` in the Dockerfile would fail |
| `src/App/uv.lock` | `frontend-react 0.1.0` | `frontend-react 0.1.0rc1` | same reasoning |
| `src/mcp_server/uv.lock` | — | — | no change needed: its version is dynamic, so the lock carries no version field for the project itself |

### 2.2 Changelog

A new root `CHANGELOG.md` (Keep a Changelog format) documents what constitutes 0.1.0-rc.1: the 77 + 8 dependency-vulnerability remediations across all five manifests, the eslint repair and ~150-warning cleanup, the three dead test-configuration fixes, the frontend-lint and version-drift CI jobs, and the upstream `microsoft/main` sync.

### 2.3 Validation (pre-merge)

- Backend suite, run exactly as CI runs it (`test_app.py` isolated first, then the rest): **29 + 834 passed**, 86% coverage.
- `flake8 --config=.flake8 src/backend`: clean.
- `uv lock --check`: green for all three services after the lockfile version syncs.

---

## 3. Dependabot alert triage and remediation (PR #10)

### 3.1 Triage method

The Dependabot API was not reachable from this session, so the three alerts (1 high, 1 moderate, 1 low) were reproduced locally: each service's `uv.lock` was exported to requirements form (`uv export --frozen --no-emit-project`) and audited with `pip-audit`, `.github/requirements.txt` was audited directly, and the frontend npm tree was audited with `npm audit`. The sweep found **exactly three vulnerabilities**, matching the alert counts. The npm tree and CI requirements were clean.

### 3.2 Findings and fixes

| CVE / advisory | Package | Location | Severity | Fix applied |
|---|---|---|---|---|
| CVE-2026-59950 · PYSEC-2026-3483 · GHSA-vj7q-gjh5-988w — MCP Python SDK WebSocket server transport does not validate Host/Origin | `mcp` 1.27.2 | `src/mcp_server` (transitive via `fastmcp==3.2.0`) | High | → **1.28.1**, pinned explicitly in `pyproject.toml`, matching the pin `src/backend` already carried |
| CVE-2026-7597 · PYSEC-2026-2636 · GHSA-xqxw-r767-67m7 — mem0 improper input validation | `mem0ai` 1.0.11 | `src/backend` (transitive via `agent-framework-mem0`) | Moderate | → **2.0.18** via `[tool.uv] override-dependencies` (rationale in §3.3) |
| CVE-2026-71554 · PYSEC-2026-3628 · GHSA-6hr6-w5qg-qmwg — h2 accepts duplicate Host headers (request smuggling) | `h2` 4.3.0 | `src/backend` (transitive via `httpx[http2]`) | Low | → **4.4.1**, held as a direct security pin per the manifest's existing convention (same pattern as `starlette`) |

### 3.3 The mem0ai constraint conflict

The mem0ai fix required going outside declared constraints, and the reasoning is worth recording:

- `agent-framework==1.6.0` (deliberately pinned, with documented conflicts against neighbouring versions) pulls `agent-framework-core[all]==1.6.0`, whose `all` extra includes `agent-framework-mem0` with **no version bound**.
- Every `agent-framework-mem0` release compatible with core 1.6.0 caps `mem0ai<2`. The releases that pair with the fixed `mem0ai>=2.0.0` (b260709 and later) require `agent-framework-core>=1.11`.
- Therefore **no constraint-respecting resolution under the pinned framework can reach a fixed mem0ai**. The alternatives were: (a) upgrade the whole agent framework 1.6.0 → ≥1.11 (a significant, risky change out of scope for an alert fix), (b) exclude the unused integration, or (c) override the version cap.
- Option (b) was implemented first and **rejected on evidence**: uv keeps marker-gated packages listed in `uv.lock`, and Dependabot parses lock entries without evaluating markers, so the vulnerable `mem0ai 1.0.11` entry would have kept the alert open.
- Option (c) shipped: `override-dependencies = ["mem0ai==2.0.18"]`. This is safe because nothing in the backend imports `mem0` or `agent_framework_mem0` (verified by search), so the pairing of the old integration shim with the new mem0ai is never exercised at runtime. The manifest documents the exit path: drop the override when `agent-framework` is upgraded to ≥1.11.

### 3.4 Companion change

`mcp 1.28.1` requires `pydantic>=2.12` on Python ≥3.14. mcp_server pinned `pydantic==2.11.7` with `requires-python = ">=3.10"` (open-ended), so `uv lock` failed on the 3.14 resolution split. `pydantic` moved **2.11.7 → 2.13.4** — the version `src/backend` and `src/App` already resolve, taking the three services to a drift-free state on it (`pydantic-core` followed, 2.33.2 → 2.46.4; `hpack` 4.1.0 → 4.2.0 accompanied the h2 bump).

### 3.5 Validation (pre-merge)

- `pip-audit`: **zero known vulnerabilities** across the backend lock, mcp_server lock, App lock, and `.github/requirements.txt`; `npm audit`: 0 vulnerabilities.
- Backend suite as CI runs it: **29 + 834 passed**.
- MCP server suite on a fresh `uv sync --frozen --extra dev` of the updated lock: **29 passed** — this exercises the new pydantic 2.13.4 at runtime.
- Backend production install (`uv sync --frozen --no-dev`) succeeds from the updated lock; `mem0` imports at 2.0.18 and `h2` at 4.4.1 in that environment.
- Note: pip cannot re-resolve the overridden requirement set (it has no override concept and reports a conflict on `agent-framework-mem0`'s `mem0ai<2` cap); this does not affect deployment, because `uv sync` installs from the lock without re-resolving.

---

## 4. CI status on `main` after both merges

Verified against the Actions history for merge commit `7fd0c49`:

| Workflow | Status | Assessment |
|---|---|---|
| Test Workflow with Coverage | ✅ success | The merge gate; green on the PR head and on `main` |
| Dependency Version Drift | ✅ success (PR) | Advisory job; no drift flagged |
| Build Docker and Optional Push v4 (PR event) | ✅ success (PR) | Image builds reproduce |
| Broken Link Checker, PR Title Checker | ✅ success (PR) | |
| CodeQL Advanced, Frontend Lint, PyLint | ✅ success (previous `main` push; not re-triggered or green as applicable) | |
| Build Docker and Optional Push v4 (push), Validate Deployment v4, Validate WAF Deployment v4, Deploy-Test-Cleanup (v2) | ❌ failure | **Pre-existing on this fork** — identical failures on the prior `main` commit (`627f18a`); these workflows need Azure credentials/secrets the fork does not have. Not caused by, and not fixable by, these changes |

---

## 5. Remaining follow-ups

1. **Push the tag and create the pre-release** (requires repo-owner credentials; this session's git access is policy-scoped to its work branch and tag pushes were rejected with HTTP 403):

   ```bash
   git fetch origin main
   git tag -a v0.1.0-rc.1 627f18a90c06c14b7b067d63fbe01ff21fb8943c -m "v0.1.0-rc.1"
   git push origin v0.1.0-rc.1
   ```

   Then GitHub → Releases → *Draft a new release* → tag `v0.1.0-rc.1` → check **Set as a pre-release**, pasting the `[0.1.0-rc.1]` section of `CHANGELOG.md` as the notes. (Tagging `627f18a` marks the RC as cut; tagging `7fd0c49` would fold the alert fixes into the RC — either is defensible, the commands above tag the RC as released.)

2. **Dependabot alerts should auto-close** on GitHub's next scan of the updated `main` lockfiles. If any alert remains open after a rescan, it warrants a fresh look rather than dismissal.

3. **Promotion to 0.1.0 final**: when ready, repeat the version alignment (`rc1` → final), move the changelog's Unreleased section under `0.1.0`, tag, and publish a full release.

---

## 6. Verification snapshot (origin/main at `7fd0c49`)

```
src/backend/pyproject.toml      version = "0.1.0rc1"
src/App/pyproject.toml          version = "0.1.0rc1"
src/App/package.json            "version": "0.1.0-rc.1"
src/mcp_server/__init__.py      __version__ = "0.1.0rc1"
src/backend/uv.lock             mem0ai 2.0.18, h2 4.4.1, mcp 1.28.1
src/mcp_server/uv.lock          mcp 1.28.1, pydantic 2.13.4
pip-audit (all Python manifests) 0 known vulnerabilities
npm audit (src/App)              0 vulnerabilities
backend tests                    29 + 834 passed, 86% coverage
mcp_server tests                 29 passed
```
