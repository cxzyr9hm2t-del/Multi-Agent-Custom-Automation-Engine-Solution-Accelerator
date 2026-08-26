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
case "${1:-}" in
    --dry-run) MODE="dry-run" ;;
    --show)    MODE="show" ;;
    "")        ;;
    *) echo "usage: $0 [--dry-run|--show]" >&2; exit 64 ;;
esac

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

if [[ "$MODE" == "dry-run" ]]; then
    echo "Dry run: nothing was changed."
    exit 0
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
