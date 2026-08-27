# Open Findings — deep dive and disposition

**Date:** 2026-08-26
**Subject:** what remains open from `2026-08-15-forensic-audit.md`, verified against the tree
rather than against the audit's own summary — plus one finding the audit could not have
contained, because it was introduced afterwards by the release automation work.

---

## 1. First, the record disagrees with itself

The audit is cited elsewhere as "sixteen of seventeen findings closed." Read against its own
tables, that sentence is wrong in three ways, and the errors compound rather than cancel.

| Claim | Where | What the document actually shows |
|---|---|---|
| "seventeen findings" | banner, §9 | §1 counts **25**: 5 critical, 5 high, 7 medium, 8 low. "Seventeen" is the C+H+M subset (5+5+7) — the substantive findings, excluding the 8 low/hygiene ones. Defensible shorthand, but never stated. |
| "M7 is partially fixed" | banner | True, **and so is C1** — the §9 table marks both `Partially fixed`. Of the seventeen substantive findings, **fifteen** are fully closed, not sixteen. |
| "L7 is deliberately not done" | banner | The §9 table says L7 is **Fixed**, and describes the `validate-action-pinning.yml` gate it added. The banner was written before that work landed and never updated. §9's own "Still open" list repeats the stale claim. |

The "Still open" list also carries **the v0.1.0 tag**, which was pushed and released earlier
today (§4 of `2026-08-26-release-automation-verification.md`). So that list contained one item
that was already fixed, one that contradicts its own table, and no mention of C1 being the
larger of the two partial fixes.

None of this changes the code. It changes how far the summary can be trusted without reading
the table underneath it — which is the reason this document verifies each item directly.

---

## 2. C1 — the one that matters

**Status: partially fixed. The remaining half is a deployment decision, not a code defect.**

The application half is done, and done well. `src/backend/auth/auth_utils.py`:

- The `sample_user` fallback is now gated on `APP_ENV == "dev"`. Outside development an absent
  principal yields *no identity*, so callers reject the request rather than landing every
  anonymous caller in one shared all-zeros account.
- `_principal_id_from_headers` prefers `x-ms-client-principal` — the base64 claims document a
  front door injects after validating the token and stripping any client copy — over the bare
  `x-ms-client-principal-id` scalar, which "looks identical whether the platform set it or the
  caller did."

The deployment half is not, and the code says so in its own docstring: *"neither source is
trustworthy unless an auth front door is actually in front of this app."* Verified in
`infra/bicep/main.bicep`:

```
:177   param backendAuthClientId string = ''      ← auth off by default
:500   ingressExternal: true                      ← backend is publicly reachable
:502   authClientId: backendAuthClientId
:685   ingressExternal: false                     ← MCP server, internal (C5, fixed)
```

So on a default deployment the backend is reachable from the internet and its identity comes
from a header the caller controls. **Every ownership check added by C2, C3, C4, H4, H5 and M7
compares against that identity.** They are correct code resting on an unverified premise: they
stop caller A from reading caller B's plan only insofar as the platform stops caller A from
*claiming* to be B, and by default nothing does.

### Why this is not mine to close

Enabling it requires an Entra app registration that does not exist, and
`docs/backend_api_authentication.md` records that turning it on breaks the image proxy and the
WebSocket, which cannot carry a bearer token. That is a decision with operational consequences
and a rollback plan — the owner's call, made deliberately, not a flag flipped by automation.

### What *is* automatable

Nothing that closes it. What automation can do is stop it being *silent*: a deployment-time
check that reports when `backendAuthClientId` is empty while `ingressExternal` is true. It must
not fail the build — that combination is the documented default and would break every run — so
its value is visibility, and it should be weighed against adding another advisory check nobody
reads. Recommended only if the owner wants the risk surfaced on every deploy rather than
recorded once here.

---

## 3. H3 — closed in substance; the note is about shape, not a hole

**Status: fixed.** `/clarification/ask` (`src/backend/api/router.py:783`) is machine-to-machine:
the MCP server calls it. It is not behind the user's auth header, which is what "unauthenticated
endpoint" in the audit's still-open list refers to. It is not unauthenticated:

- A signed, expiring session token is required; the user is derived from the signature.
- An absent, forged, expired or wrong-purpose token is a **401**.
- An unsigned `user_id` is accepted **only** when `APP_ENV == "dev"`, and logs a warning saying
  the path does not exist outside development.

The design change behind it is the substantive fix: the endpoint used to take `user_id` from the
request body, which the model had copied out of its prompt — so *model output chose who received
a clarification*. Now the model chooses the question and never the recipient. Nothing further is
open here; the still-open entry describes a design note, not a defect.

---

## 4. M7 — bounded, self-retiring, needs a date

**Status: partially fixed, by design.** Images authenticate with a signed token and are checked
against a recorded owner. Images generated *before* that record existed have no owner, and
`router.py:1792` logs and allows:

```
"Image '%s' has no ownership record; allowing on token alone"
```

That is a deliberate compatibility window: requiring a record immediately would break every
image in existing conversations. The exposure is bounded — a caller still needs a valid signed
token — and it shrinks to nothing as old conversations age out.

**This is the one open item with a clean automated ending.** The fallback should not live on
judgement forever; it should have a date. Two options, in order of preference:

1. Backfill ownership records for existing blobs from the messages that produced them, then
   delete the fallback. Closes it now, no waiting.
2. If backfill is impractical, set a cutoff: after date *D*, an image with no record is denied.
   The branch becomes dead code and is removed.

Either way the work is small and mechanical. What it needs is a decision on *which*, and for
option 2, what *D* is — both of which depend on how long conversations are retained, which is
not recorded anywhere I can read.

---

## 5. L7 — fixed, and then broken again by me

**Status: was fixed; regressed on 2026-08-26; fixed again in this change.**

L7 pinned all 70 third-party action references to 40-character commit digests and added
`validate-action-pinning.yml` to keep them that way. The gate checks three invariants: every
action pinned, every workflow parses, every workflow declares top-level `permissions`.

When I added `.github/workflows/publish-release.yml` (PR #16) I wrote:

```yaml
uses: actions/checkout@v6
```

Every other workflow in the repository — 26 of them — uses
`actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803  # v6`. I took the convention from a
file I had read earlier and did not re-check it against the tree the remediation had since
changed.

### The part that matters more than the typo

**The gate caught it immediately, and I merged anyway — six times.**

| Run | Commit | Trigger | Conclusion |
|---|---|---|---|
| 4 | `883d428` | push | success — last green run |
| 5 | `0992c92` (PR #16) | pull_request | **failure** |
| 6 | `22c6d6a` (#16 merge) | push | **failure** |
| 7 | `7964a51` (PR #17) | pull_request | **failure** |
| 8 | `5f661fa` (#17 merge) | push | **failure** |
| 9 | `a3810f3` (PR #18) | pull_request | **failure** |
| 10 | `6e61da3` (#18 merge) | push | **failure** |

I merged PRs #16 through #21 without reading a single check result. Two consequences follow.

First, **`v0.1.0` is tagged at `5f661fa`** — run 8's commit. The published release contains an
unpinned action reference. The tag is immutable and stays; the fix lands after it, as `v0.1.1`
material.

Second, `2026-08-26-release-automation-verification.md` §7 stated that the only red checks on
`main` were four Azure deployment workflows failing for pre-existing credential reasons. That
was false, and it was false because I asserted it from memory instead of enumerating the checks.
A verification document that reports CI status without querying CI is not a verification
document. §7 has been corrected.

The gate did not fail. It did exactly what the previous session built it to do, on the first PR
that broke the invariant, and kept saying so six more times. The failure was entirely in the
merging.

### The fix, and its verification

`publish-release.yml` now pins the digest the other 26 workflows use. All three gate checks were
run locally against the corrected tree:

- all action references pinned — **pass**
- 32/32 workflows parse — **pass**
- 32/32 declare top-level `permissions` — **pass**

And the negative control, because a gate that passes through broken detection is worse than no
gate: reintroducing `actions/checkout@v6` makes the check flag it again.

The digest was **not** independently resolved. `git ls-remote` returns nothing through this
environment's egress proxy, so I adopted the digest already resolved by the remediation session
via `git ls-remote 'refs/tags/v6^{}'` and in use across 26 workflows for the same action and
tag. That is a weaker provenance than resolving it fresh and is recorded as such.

---

## 6. Summary

| Finding | Real status | Blocked on |
|---|---|---|
| C1 | Partially fixed — code done, front door off by default | An Entra app registration and a deployment decision, with known consequences for the image proxy and WebSocket |
| H3 | **Fixed** — the still-open entry describes design shape, not a defect | Nothing |
| M7 | Partially fixed — legacy images fall back to token-only | A choice: backfill records, or set a cutoff date |
| L7 | Fixed → regressed → fixed | Nothing |

**Of the seventeen substantive findings, fifteen are fully closed.** C1 and M7 are partial, and
both remain partial for reasons that are decisions rather than unfinished work.

The one genuinely new defect found by this dive was introduced by the release automation, caught
by the repository's own gate on the first PR that broke it, and merged past six times without
being read.

### Recommended, in order

1. **Require the CI gates as protected checks on `main`** — the only item here that would have
   prevented the regression from reaching a release. A workflow cannot do this, because a
   workflow cannot refuse a merge; it is a repository setting and needs someone with admin.
   `scripts/enable-branch-protection.sh` applies it in one command and reads the result back:

   ```bash
   ./scripts/enable-branch-protection.sh --dry-run   # show what would change
   ./scripts/enable-branch-protection.sh             # apply, then verify
   ```

   It requires the two check runs by their **job** names — `Actions pinned to digests` and
   `test` — not their workflow titles, which is the detail that makes this fiddly by hand.
   Admins are included: with the rule not enforced on admins, the person most likely to merge
   walks straight through it, which is precisely the failure being prevented.

   **Do not run it while Actions is unhealthy** — and as of this writing, it is. The script
   now enforces that itself rather than trusting the reader: it reads the required checks on
   the branch tip and refuses to apply unless every one is a completed success, because
   protection does not wait for a check to become available. A required check that cannot run
   blocks every merge, which would close the repository with the fix for it behind the same
   gate. `--force` overrides for the case where the operator knows better. See §7.
2. **Decide M7's ending** — backfill, or a cutoff date. Small, mechanical once chosen.
3. **Decide C1** — the app registration, or accept documented risk. Everything else in the
   access-control model rests on it.

---

## 7. The Actions incident of 2026-08-26, and why it blocks recommendation 1

Recorded because it is the reason the first recommendation is not simply "run the script",
and because during it CI reported failures that were not real — which is exactly the
circumstance in which someone starts "fixing" working code.

### What the runs show

`Validate Action Pinning` and `Test Workflow with Coverage`, by run, around the transition:

| Time (UTC) | Workflow / run | Result |
|---|---|---|
| 14:47–14:51 | pinning 13, test 54, test 55 | success, ~1–1.5 min each |
| 15:03 | test 56 | `startup_failure` |
| 15:16 | test 57 | queued, never started |
| 15:17 | pinning 15 | job queued 15:17:36 → **cancelled 15:32:38** |
| 15:21 | test 59 | `startup_failure` |
| 15:30–15:37 | pinning 17, 18, test 60 | queued, never started |

The last healthy run was **14:50 UTC**. Everything from roughly **15:03** onward either failed
at startup or waited about fifteen minutes for a runner and was cancelled.

### The reporting artefact worth knowing about

Run 15 presents as `conclusion: failure` at the run level while its only job reads
`cancelled` — and, read mid-flight, the run showed `completed/failure` while the job still
said `queued`. That combination looks impossible and invites the conclusion that the API is
lying. It is not: the job was cancelled for want of a runner, and the run inherits a failure
conclusion from it, with the two endpoints briefly disagreeing while it settles.

The practical rule: **a red check here does not by itself mean a red tree.** Open the job. If
it has no steps, or its steps never ran, the result says nothing about the code.

### Proof that the tree was fine throughout

The pinning gate's three assertions, run locally against `8fbc9fe` — the same tree CI marked
red:

- every action reference pinned to a 40-character digest — **pass**
- 32/32 workflows parse as YAML — **pass**
- 32/32 workflows declare top-level `permissions` — **pass**

And the suite: **31 + 927 passing**, `flake8 --config=.flake8 src/backend` clean.

### What was changed as a result

`scripts/enable-branch-protection.sh` gained a preflight. It reads
`repos/{repo}/commits/{branch}/check-runs` — the same signal branch protection evaluates —
and refuses to apply unless every required check is a completed success. A check name that is
absent fails the preflight too, since "never ran" is what GitHub renders as `Expected`
forever, and is the more dangerous state rather than the harmless one.

Verified against a stubbed `gh` across six paths: refuses on the starved state, refuses on a
missing check name, warns without failing under `--dry-run`, reports safety under `--dry-run`
when green, applies and reads back when green, and applies under `--force` when red while
printing the undo command first.

Nothing was changed in response to the false reds, which was the other available mistake.
