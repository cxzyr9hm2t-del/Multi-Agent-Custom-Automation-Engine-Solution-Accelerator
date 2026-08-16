# Remediation Record — audit of v0.1.0 and the work that followed

**Date:** 2026-08-16
**Branch:** `claude/claude-rc-t59dbs`
**Base:** `origin/main` at `452ff5378e17fc838d45a5b6f6fbe777d68436ee`
**Pull request:** [#13](https://github.com/cxzyr9hm2t-del/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator/pull/13)

This is the evidence record for the work. The audit itself is
`2026-08-15-forensic-audit.md` (its §9 carries the per-finding disposition);
this file records **what was changed, what was verified, and what could not be
completed** — including the two things that exist nowhere else once the session
that produced them is gone.

Every figure below was produced by re-running the check at branch head
`83802b992fb0987e955387e8ad4b4055f0736120`, not carried forward from earlier in
the work.

---

## 1. Commits

Eleven commits, `origin/main..HEAD`, in order. Each commit message states the
finding it closes and the reasoning; they are the primary record and are
deliberately detailed.

| SHA | Subject | Findings |
|---|---|---|
| `b98041c` | docs: add full forensic audit of the tree at v0.1.0 | — |
| `7cece16` | fix: close the publicly-reachable front door | C1 (partial), C5, H1 |
| `ecfbd6e` | fix: scope plan reads and deletes to the owning user | C2, H4 |
| `fb1a341` | fix: bind the approval gate and the WebSocket to their owner | C3, C4 |
| `c0de940` | fix: close the RAI upload bypass and the cross-user message write | H2, H5, H3 (partial) |
| `d6f2ee0` | fix: update teams in place instead of inserting a duplicate | M1 |
| `007c30e` | fix: remediate the remaining medium audit findings | M2–M7 |
| `6392e5d` | feat: add the backend authenticating front door | C1 |
| `f33cd82` | feat: authenticate image and WebSocket requests that cannot send a header | C1 follow-ons |
| `ff9820b` | feat: record image ownership so the proxy can check it | M7 |
| `83802b9` | chore: clear the audit's hygiene backlog and record remediation status | L1–L8 |

**Diffstat vs `origin/main`:** 54 files changed, 2,901 insertions, 299 deletions.

---

## 2. Verification at branch head

Re-run in full on 2026-08-16 against `83802b9`, working tree clean.

| Check | Command | Result |
|---|---|---|
| Backend, isolated | `pytest src/tests/backend/test_app.py --cov=src/backend` | **31 passed** |
| Backend, remainder | `pytest src/tests/backend --cov-append --ignore=…/test_app.py` | **903 passed**, 52 subtests |
| Coverage vs 80% CI floor | `--cov=src/backend` | **86%** (4,083 statements, 571 uncovered) |
| Lint | `flake8 --config=.flake8 src/backend` | **clean (exit 0)** |
| MCP server | `pytest src/tests/mcp_server` | **29 passed** |
| Backend lock | `pip-audit` on `uv export --frozen` | **0 known vulnerabilities** |
| MCP server lock | `pip-audit` on `uv export --frozen` | **0 known vulnerabilities** |
| App lock | `pip-audit` on `uv export --frozen` | **0 known vulnerabilities** |
| CI requirements | `pip-audit -r .github/requirements.txt` | **0 known vulnerabilities** |
| Frontend packages | `npm audit --omit=dev` | **0 vulnerabilities** |
| Frontend lint | `npm run lint` | **0 errors** (13 pre-existing `no-explicit-any` warnings) |
| Frontend build | `npm run build` | **succeeds** |
| Workflow syntax | `yaml.safe_load` over `.github/workflows/*.yml` | **27/27 parse**, all with top-level `permissions:` |

The frontend numbers required a full `npm ci` first: the container's
`node_modules` was incomplete, which pulled a newer TypeScript and produced a
`tsconfig` deprecation error unrelated to any change here. That was confirmed
against a stashed baseline before being dismissed.

**Test counts across the work:** 63 tests added. **14 existing tests were
rewritten because they asserted the defective behaviour** — see §4.

---

## 3. What could not be completed

### 3.1 The release tags — blocked, not forgotten

`v0.1.0` and `v0.1.0-rc.1` still do not exist on the remote. This is the third
report in a row to end this way, so the evidence is recorded here in full rather
than restated as an intention.

**Attempted, all from this environment, all rejected with `HTTP/1.1 403 Forbidden`:**

1. `git push origin v0.1.0`
2. `git push origin refs/tags/v0.1.0`
3. `git push origin v0.1.0-rc.1 v0.1.0`
4. `git push origin --tags`
5. The same push from a **fresh `git clone`** with its own remote and config

**Evidence that this is credential scope, not a broken setup:**

- Branch pushes to `claude/claude-rc-t59dbs` succeed with the same credentials,
  repeatedly, throughout the work.
- `git fetch` and `git clone` succeed — read access is intact.
- `GET /repos/…/git/ref/tags/v0.1.0` returns **404**, confirming nothing landed.
- GitHub returns a bare 403 with no message body.

The grant covers `refs/heads/claude/*` and not `refs/tags/*`. No amount of
retrying from here will change it; it needs credentials that are not
branch-scoped.

### 3.2 The tag objects, preserved

The annotated tags were created locally and verified, but live only in an
ephemeral container. Their content is recorded here so nothing is lost.

| Tag | Target commit | Verified |
|---|---|---|
| `v0.1.0-rc.1` | `627f18a90c06c14b7b067d63fbe01ff21fb8943c` | ancestor of `origin/main`; all version sources read `0.1.0rc1` / `0.1.0-rc.1`, both `uv.lock`s synced |
| `v0.1.0` | `452ff5378e17fc838d45a5b6f6fbe777d68436ee` | ancestor of `origin/main`; all four version sources read `0.1.0` |

**To create them**, from a clone authenticated as a user with tag-push rights:

```bash
git fetch origin main
git tag -a v0.1.0-rc.1 627f18a90c06c14b7b067d63fbe01ff21fb8943c -m "v0.1.0-rc.1"
git tag -a v0.1.0      452ff5378e17fc838d45a5b6f6fbe777d68436ee -m "v0.1.0"
git push origin v0.1.0-rc.1 v0.1.0
```

Or through the web UI: Releases → *Draft a new release* → type the tag name →
*Create new tag on publish* → set Target to the SHA above. Tick **Set as a
pre-release** for `v0.1.0-rc.1`. Use the matching `CHANGELOG.md` sections as
notes.

**One decision worth preserving:** `627f18a` is the commit at which the RC was
*cut*, before the three Dependabot alerts found against it were fixed. Those
landed afterwards in PR #10 (`7fd0c49`) and ship in `v0.1.0`. Tagging the cut
point makes `v0.1.0-rc.1` mean "what was released as the RC" rather than "the RC
plus later fixes". Both are defensible; this is the one chosen, and changing it
means retargeting the tag at `7fd0c49`.

### 3.3 L7 — action SHA pinning

Not done, deliberately. Pinning GitHub Actions to commit digests requires
resolving each tag to a SHA. Guessing them would be worse than leaving the tags
in place, so this is left for `pinact` or an equivalent run with network access.

### 3.4 Findings that remain open by design

- **C1 is built but not enabled.** The `backendAuthClientId` parameter and the
  Container Apps auth configuration exist and default to off. Until it is turned
  on, every ownership check added by this work compares against a
  client-supplied identity. See `docs/backend_api_authentication.md`.
- **H3's `/clarification/ask` is still unauthenticated.** The clarification can
  no longer be answered by anyone but its owner, and outside dev it can no
  longer be misdelivered — but a question can still be pushed into a user's UI.
  The root cause is that `ask_user` takes its `user_id` from the model; the
  durable fix is for the backend to supply it from the invoking orchestration.
- **M7 falls back for pre-existing images.** Images generated before ownership
  was recorded have no record and are served on a valid token alone, so existing
  conversations keep rendering. The handler can require a record once history
  has turned over.

---

## 4. Tests that asserted the defective behaviour

Recorded because it bears on how much the suite could be trusted in this area.
Fourteen existing tests encoded a defect as the expected contract and were
rewritten:

| Test | Asserted |
|---|---|
| `test_cors_middleware_allows_all_origins` | `"*" in allow_origins` — the wildcard-with-credentials bug |
| `test_get_plan_by_plan_id_found` | the query text without its `user_id` predicate |
| `test_no_active_plan` (approval) | the 500 produced by a swallowed 404, with a comment explaining it |
| `test_no_plan_id`, `test_plan_not_found` | 500 for what should be 400 and 404 |
| `test_team_not_found` (×2) | 400 for what should be 404 |
| `test_connect_default_user` | the anonymous all-zeros WebSocket default |
| `test_plan_service_error` (agent message) | the unauthorized write path |
| `test_success_with_team_id` | only a 200 — passed against the duplicate-insert bug |
| `test_rai_success_response_contains_false` | that prose containing "FALSE" passes safety |
| `test_create_rai_agent_success` | that the caller's config *is* mutated in place |
| `test_get_agent_response_exception` (×2) | the `"TRUE"` error sentinel |

A related detail: `test_create_rai_agent_success`'s setup already stubbed
`model_copy`, anticipating a defensive copy the production code never made — and
then asserted the mutation instead.

---

## 5. Reproducing this record

```bash
git checkout claude/claude-rc-t59dbs

PYTHONPATH=src:src/backend python -m pytest src/tests/backend/test_app.py --cov=src/backend -q
PYTHONPATH=src:src/backend python -m pytest src/tests/backend --cov=src/backend --cov-append \
  --ignore=src/tests/backend/test_app.py
flake8 --config=.flake8 src/backend

cd src/mcp_server && uv sync --frozen --extra dev && cd ../..
PYTHONPATH=src:src/mcp_server src/mcp_server/.venv/bin/python -m pytest src/tests/mcp_server -q

cd src/App && npm ci && npm run lint && npm run build && npm audit --omit=dev
```

`test_app.py` must run in its own process — running the whole tree in one pytest
invocation aborts collection. `PYTHONPATH=src:src/backend` is required. Both are
documented in `CLAUDE.md`.
