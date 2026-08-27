#!/usr/bin/env bash
#
# Require the CI gates to pass before anything merges into main.
#
#   ./scripts/enable-branch-protection.sh --dry-run   # show what would be set
#   ./scripts/enable-branch-protection.sh             # apply it
#   ./scripts/enable-branch-protection.sh --show      # read back current state
#
# Why this exists: on 2026-08-26 an unpinned action reference reached a
# published release. The repository's own gate caught it on the first pull
# request that introduced it and failed on six consecutive merges — every one
# of which was merged without reading the result. Nothing in a workflow can
# prevent that, because a workflow cannot refuse a merge. Only branch
# protection can, which is a repository setting.
#
# Requires the GitHub CLI (gh) authenticated as someone with admin on the repo.
#
# A required check that does not run is NOT treated as passed: GitHub reports it
# as "Expected" and blocks the merge indefinitely. Both workflows below
# therefore run on every pull request — their paths filters were removed for
# the pull_request trigger when this was written. Before adding a check here,
# confirm it has no paths filter on pull_request, or a docs-only pull request
# will never be mergeable again.
#
# The same hazard has a second cause that no amount of workflow review catches:
# Actions itself being unable to run them. On 2026-08-26, from roughly 15:03
# UTC, every run in this repository either reported startup_failure or sat
# queued for fifteen minutes and was then cancelled for want of a runner — the
# same three gate checks passing locally on the very tree CI was marking red.
# Turning protection on in that window would have required checks that could
# not run, wedging the repository closed with the fix for it on the wrong side
# of the gate. So this script now looks before it leaps: it refuses to apply
# while the required checks are not currently green on the target branch.
# --force overrides, for the case where you know better than the preflight.

set -euo pipefail

REPO="${REPO:-cxzyr9hm2t-del/Multi-Agent-Custom-Automation-Engine-Solution-Accelerator}"
BRANCH="${BRANCH:-main}"

# The job names as they appear as check runs — not the workflow titles.
# `Validate Action Pinning` is the workflow; `Actions pinned to digests` is its
# job. `test.yml` declares no job name, so its check run is the job key, `test`.
CHECKS=(
    "Actions pinned to digests"
    "test"
)

# Admins included. With this false the rule exists but the person most likely
# to merge can walk straight through it, which is exactly the failure this is
# meant to stop. Set ENFORCE_ADMINS=false if you need an escape hatch.
ENFORCE_ADMINS="${ENFORCE_ADMINS:-true}"

# Off deliberately. `strict` additionally requires a branch to be up to date
# with main before merging, which means re-syncing every time main moves. The
# failure being prevented here is merging a *red* branch, not merging a stale
# one, so this stays off rather than adding friction that invites bypassing.
STRICT="${STRICT:-false}"

MODE="apply"
FORCE=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) MODE="dry-run" ;;
        --show)    MODE="show" ;;
        --force)   FORCE=true ;;
        *) echo "usage: $0 [--dry-run|--show] [--force]" >&2; exit 64 ;;
    esac
done

command -v gh >/dev/null || {
    echo "error: the GitHub CLI (gh) is required. See https://cli.github.com" >&2
    exit 1
}

API="repos/$REPO/branches/$BRANCH/protection"

show_current() {
    if ! gh api "$API" >/tmp/bp.$$ 2>/dev/null; then
        echo "  no branch protection is currently set on '$BRANCH'"
        rm -f /tmp/bp.$$
        return 1
    fi
    python3 - /tmp/bp.$$ <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
rsc = d.get("required_status_checks") or {}
contexts = rsc.get("contexts") or [c.get("context") for c in rsc.get("checks", [])]
print("  required checks   : %s" % (", ".join(contexts) if contexts else "(none)"))
print("  strict (up to date): %s" % rsc.get("strict"))
print("  enforced on admins : %s" % (d.get("enforce_admins") or {}).get("enabled"))
PY
    rm -f /tmp/bp.$$
    return 0
}

# Report the current state of each required check on the branch tip, and return
# non-zero unless every one of them is a completed success. This is the same
# signal branch protection will evaluate, read from the same place, so a green
# preflight means the rule it is about to install is satisfiable right now.
#
# A check whose name is absent is the dangerous case, not the harmless one: it
# is what GitHub shows as "Expected" forever, so it is reported as MISSING and
# fails the preflight just as a red one does.
preflight_checks() {
    if ! gh api "repos/$REPO/commits/$BRANCH/check-runs" --paginate >/tmp/bpcr.$$ 2>/dev/null; then
        echo "  could not read check runs for '$BRANCH' — cannot verify the gates are running"
        rm -f /tmp/bpcr.$$
        return 1
    fi
    python3 - /tmp/bpcr.$$ "${CHECKS[@]}" <<'PY'
import json, sys

data = json.load(open(sys.argv[1]))
required = sys.argv[2:]

# --paginate concatenates pages; take every check_runs array present.
runs = data.get("check_runs", []) if isinstance(data, dict) else []
latest = {}
for r in runs:
    name = r.get("name")
    if name in required:
        prev = latest.get(name)
        # started_at ascending; keep the most recent attempt per name.
        if prev is None or (r.get("started_at") or "") >= (prev.get("started_at") or ""):
            latest[name] = r

ok = True
for name in required:
    r = latest.get(name)
    if r is None:
        print(f"  {name:<28} MISSING — never ran on this commit")
        ok = False
        continue
    status = r.get("status")
    concl = r.get("conclusion")
    if status != "completed":
        print(f"  {name:<28} {status.upper()} — still not finished")
        ok = False
    elif concl != "success":
        print(f"  {name:<28} {str(concl).upper()}")
        ok = False
    else:
        print(f"  {name:<28} success")

sys.exit(0 if ok else 1)
PY
    local rc=$?
    rm -f /tmp/bpcr.$$
    return $rc
}

echo "Repository : $REPO"
echo "Branch     : $BRANCH"
echo

if [[ "$MODE" == "show" ]]; then
    echo "Current protection:"
    show_current || true
    exit 0
fi

echo "Current protection:"
show_current || true
echo
echo "Would set:"
for c in "${CHECKS[@]}"; do echo "  required check      : $c"; done
echo "  strict (up to date) : $STRICT"
echo "  enforced on admins  : $ENFORCE_ADMINS"
echo

echo "Required checks on the tip of '$BRANCH' right now:"
if preflight_checks; then
    PREFLIGHT_OK=true
else
    PREFLIGHT_OK=false
fi
echo

if [[ "$MODE" == "dry-run" ]]; then
    if [[ "$PREFLIGHT_OK" == true ]]; then
        echo "Dry run: nothing was changed. The checks are green, so applying would be safe."
    else
        echo "Dry run: nothing was changed. Applying now would wedge '$BRANCH' — see above."
    fi
    exit 0
fi

if [[ "$PREFLIGHT_OK" != true ]]; then
    if [[ "$FORCE" == true ]]; then
        echo "Preflight failed, but --force was given. Applying anyway."
        echo "If these checks do not start passing, nothing will merge into"
        echo "'$BRANCH' until you run: gh api -X DELETE $API"
        echo
    else
        cat >&2 <<EOF
Refusing to apply: the checks this rule would require are not passing on
'$BRANCH' right now.

Branch protection does not wait for a check to become available — a required
check that has not succeeded blocks every merge, so turning it on in this
state closes the repository, with the fix for it stuck behind the same gate.

If the checks are red, fix them first. If they are MISSING or stuck queued,
this is usually Actions being unable to schedule runners rather than anything
wrong with the code; re-run this once runs are completing normally again.

To override deliberately:  $0 --force
EOF
        exit 1
    fi
fi

# Build the payload with python rather than string-concatenating JSON, so a
# check name containing a quote or a space cannot produce a malformed body.
payload="$(python3 - "$STRICT" "$ENFORCE_ADMINS" "${CHECKS[@]}" <<'PY'
import json, sys
strict = sys.argv[1] == "true"
enforce = sys.argv[2] == "true"
contexts = sys.argv[3:]
print(json.dumps({
    "required_status_checks": {"strict": strict, "contexts": contexts},
    "enforce_admins": enforce,
    # Required by the API and explicitly null: this rule is about CI passing,
    # not about mandating a second reviewer on a single-maintainer fork.
    "required_pull_request_reviews": None,
    "restrictions": None,
}))
PY
)"

echo "$payload" | gh api -X PUT "$API" --input - >/dev/null
echo "✓ applied"
echo
echo "Reading it back:"
if show_current; then
    echo
    echo "Merging a pull request whose checks have not passed is now refused."
else
    echo "error: protection did not read back after applying." >&2
    exit 1
fi

cat <<EOF

If a pull request ever sits forever on a required check that reads
"Expected" and never runs, that check did not fire for that pull request
and nothing will merge until the rule is lifted. Undo it with:

    gh api -X DELETE $API

That removes protection entirely and takes effect immediately. Re-run
this script once the check is firing again. Keeping the way out written
down is the point: a gate you cannot open is worse than no gate.
EOF
