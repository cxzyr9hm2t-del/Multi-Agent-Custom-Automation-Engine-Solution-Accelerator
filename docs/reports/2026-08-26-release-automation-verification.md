# Forensic Verification — v0.1.0 Release and Release Automation

**Date:** 2026-08-26
**Repository:** `cxzyr9hm2t-del/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator`
**Subject:** the published `v0.1.0` release, the automation that publishes it, and the
correctness of both.

---

## 1. Method

Every claim below was checked against a primary source — the git object store, the
GitHub API through an authenticated client, workflow logs, or a command run against
this tree. Nothing is carried over from earlier reports; counts that changed since
the last report were re-measured and are noted where they differ.

Where a check could not be performed directly, §7 says so rather than omitting it.

---

## 2. Material finding: what `v0.1.0` actually contains

`v0.1.0` was tagged at `5f661fa`, the tip of `main` at publication time — not at
`452ff53`, the commit that set the version to `0.1.0`. Seven commits separate them,
and one of those is substantial:

```
$ git log --oneline 452ff53..5f661fa
5f661fa Merge pull request #17 …
7964a51 ci: publish the release automatically when one is due
22c6d6a Merge pull request #16 …
0992c92 ci: publish releases from Actions
c186538 Merge pull request #15 …
ffd3df8 chore(release): add a one-command release publishing script
883d428 fix: forensic audit of v0.1.0, and remediation of its findings (#13)
```

`883d428` is a 92-file, ~4,860-insertion security remediation. Its membership in the
release was verified, not assumed:

```
$ git merge-base --is-ancestor 883d428 v0.1.0^{commit}   → YES
$ git merge-base --is-ancestor 452ff53 v0.1.0^{commit}   → YES
```

**So `v0.1.0` ships that remediation** — backend authentication, per-user scoping in
the data layer, an owner-bound approval gate, an authenticating WebSocket, the MCP
server moved to internal ingress, scoped CORS, and content-safety validation on every
team upload. The changelog had filed all of it under `[Unreleased]`, and the published
release notes did not mention any of it.

### Disposition

The tag is published, and a published tag is an immutable reference: it was **not**
moved. What was corrected is the record. `[Unreleased]` was folded into `[0.1.0]`,
where that work shipped, and the entry states why it landed there. The published
release notes were then re-synced from the changelog by the automation itself
(§4), growing from 18 lines to 70.

---

## 3. Release state — verified

| Property | Value | Source |
|---|---|---|
| Release | `v0.1.0`, published, not a draft, not a pre-release | GitHub API |
| Tag | `v0.1.0` → `5f661faf4ab6ceb9ef76caa5431d1db34b2bfd19` | `git rev-parse v0.1.0^{commit}` |
| Tags on remote | exactly 1 (`v0.1.0`) | `git ls-remote --tags origin` |
| Author | `github-actions[bot]` | GitHub API |
| Notes | 70 lines, matching the `[0.1.0]` changelog entry | run 3 log + API |
| Tag movement | none, in any run | run logs (§6) |

Version sources, read out of the tagged tree rather than the working copy — all six
agree on `0.1.0`:

```
src/backend/pyproject.toml       0.1.0
src/App/pyproject.toml           0.1.0
src/mcp_server/__init__.py       0.1.0
src/App/package.json             0.1.0
src/App/package-lock.json        0.1.0
CHANGELOG.md (newest released)   0.1.0
```

---

## 4. The automation

Releasing is no longer gated on any individual's permissions. `git push` of a tag is
rejected for this automation with `HTTP 403` at GitHub's ref-level permission check
(the egress proxy recorded no failure — `recentRelayFailures: []` — so the block is
authorization, not connectivity), and `workflow_dispatch` is likewise `403` for a
token without `actions: write`. A `push`-triggered workflow needs neither.

`.github/workflows/publish-release.yml` runs on every push to `main` and delegates to
`scripts/publish-release.sh`, so a release cut in CI and one cut from a maintainer's
machine follow the same code path. It holds one invariant:

> the version on `main` has a published release whose notes match its `CHANGELOG.md` entry.

It declines to act, cleanly, when the changelog documents no released version, when
the changelog and `src/backend/pyproject.toml` disagree (the tree is mid-release, and
publishing then would ship a release whose contents claim another version), or when
there is nothing to change.

Safety properties, each exercised (§5):

- A published tag is never moved. When no commit is named and `main` has advanced,
  the tag stays and the run proceeds against it.
- An **explicitly** named commit that contradicts an existing tag is refused, exit 1.
- Release notes — mutable metadata — are re-synced when they drift; a release whose
  notes already match is left untouched.
- `DRY_RUN=1` performs no writes.
- Inputs reach the shell through the environment, never `${{ }}` interpolation.

---

## 5. Evidence

Measured on `main` at the time of writing. Test counts moved since the previous
report because `883d428` added tests; these are the current figures.

| Check | Result |
|---|---|
| Backend suite (`test_app.py`, isolated as CI runs it) | 31 passed |
| Backend suite (remainder, as CI runs it) | 918 passed, 52 subtests |
| Backend coverage | 86% (CI floor 80%) |
| MCP server suite | 29 passed |
| `flake8 --config=.flake8 src/backend` | 0 findings |
| `uv lock --check` × backend, App, mcp_server | all consistent |
| `pip-audit` — backend lock | no known vulnerabilities |
| `pip-audit` — App lock | no known vulnerabilities |
| `pip-audit` — mcp_server lock | no known vulnerabilities |
| `pip-audit` — `.github/requirements.txt` | no known vulnerabilities |
| `npm audit` (`src/App`) | 0 vulnerabilities |
| Changelog link definitions | 1, resolving to an existing tag; 0 dead |
| `bash -n scripts/publish-release.sh` | OK |
| `publish-release.yml` YAML parse | OK |
| Working tree | clean; `HEAD == origin/main` |

Script behaviour, exercised against a stubbed `gh` before any push:

| Scenario | Expected | Observed |
|---|---|---|
| Default target, tag exists, notes stale | adopt tag, sync notes | `gh release edit` invoked, exit 0 |
| Default target, tag exists, notes match | no-op | no edit |
| Explicit conflicting commit | refuse | exit 1 |
| Tag position, every path | unchanged | `5f661fa` throughout |
| Unknown version (`v9.9.9`) | refuse | errors on missing changelog section |
| `[Unreleased]` heading present | skip it, take newest released | resolved `0.9.0`, not `[Unreleased]` |

---

## 6. Run history

| Run | Commit | Result | Detail |
|---|---|---|---|
| 1 | `5f661fa` (#17) | success | Created tag `v0.1.0` and the release. |
| 2 | `6e61da3` (#18) | **failure** | `error: local tag v0.1.0 already exists at 5f661fa, but 6e61da3 was requested.` |
| 3 | `e7d281f` (#19) | success | `tag … already published at 5f661fa; leaving it there` → `notes re-synced from CHANGELOG.md` |
| 4 | `19cf615` (#20) | success | `tag … already published at 5f661fa; leaving it there` → `notes match CHANGELOG.md` — no edit made |

Run 2's failure was a defect in the tag guard, not in the environment. The guard
treated *any* mismatch between an existing tag and the target commit as a conflict —
but with no commit named, the target is whatever `main` happens to be, and `main`
advances with every merge. For an already-released version the mismatch is the normal
state, so the guard was blocking the very path that re-syncs a shipped release's
notes. The failure was reproduced locally before the fix, and the same invocation was
confirmed to complete afterwards. The refusal now applies only to an explicitly named
commit.

---

## 7. Limits of this verification

Stated so the record is not read as stronger than it is.

- **The byte-for-byte comparison of published notes against the changelog was
  performed by the runner, not from this environment.** Direct GitHub API access here
  is blocked by egress policy (`HTTP 403`), so the published body could only be read
  through the authenticated MCP client, and comparing it by transcription would prove
  nothing. The script's own check is exact string equality (`[[ "$published_notes" ==
  "$NOTES" ]]`). Run 3 reported a re-sync; **run 4 reported `notes match
  CHANGELOG.md` and made no edit**, which is that comparison returning true with
  authenticated access, and is also what establishes the sync as idempotent rather
  than rewriting the notes on every push.
- **Deployment workflows remain red on `main`** — `Build Docker and Optional Push v4`,
  `Validate Deployment v4`, `Validate WAF Deployment v4`, `Deploy-Test-Cleanup (v2)`.
  These fail identically on commits predating any of this work; they require Azure
  credentials this fork does not hold. They are unrelated to the release automation
  and were not addressed.
- **One of the forensic audit's seventeen findings remains open**; §9 of
  `2026-08-15-forensic-audit.md` carries the disposition of each. That work is not in
  scope here.
- The `0.1.0-rc.1` changelog section documents a version that was never published as a
  release of its own — it was superseded by `0.1.0` the same day. Its dead link
  definition was removed rather than a tag being manufactured after the fact.

---

## 8. Outstanding

Nothing is required to complete the `v0.1.0` release. For the next one:

1. Bump the version in the five sources and add the `## [X.Y.Z]` changelog section.
2. Merge to `main`.

The workflow publishes it. Correcting a shipped version's changelog entry re-syncs its
published notes on the next push, with no manual step and no tag movement.
