#!/usr/bin/env bash
#
# Publish a release: tag a commit and create the matching GitHub release,
# using the CHANGELOG.md section for that version as the release notes.
#
#   ./scripts/publish-release.sh v0.1.0                  # tag HEAD of origin/main
#   ./scripts/publish-release.sh v0.1.0 452ff53          # tag a specific commit
#   DRY_RUN=1 ./scripts/publish-release.sh v0.1.0        # print actions, change nothing
#
# A version carrying a pre-release suffix (v1.2.3-rc.1, -beta.2, ...) is
# published as a GitHub pre-release. Re-running is safe: an existing tag or
# release is reported and left alone rather than overwritten.
#
# Requires: git, and the GitHub CLI (gh) authenticated with repo write access.

set -euo pipefail

VERSION="${1:-}"
COMMITISH="${2:-}"
DRY_RUN="${DRY_RUN:-}"

if [[ -z "$VERSION" ]]; then
    echo "usage: $0 <version-tag> [commit-ish]   e.g. $0 v0.1.0 452ff53" >&2
    exit 64
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

CHANGELOG="$REPO_ROOT/CHANGELOG.md"
[[ -f "$CHANGELOG" ]] || { echo "error: $CHANGELOG not found" >&2; exit 1; }

command -v gh >/dev/null || {
    echo "error: the GitHub CLI (gh) is required. See https://cli.github.com" >&2
    exit 1
}

run() {
    if [[ -n "$DRY_RUN" ]]; then
        printf 'DRY_RUN: %s\n' "$*"
    else
        "$@"
    fi
}

# CHANGELOG headings omit the leading "v": v0.1.0 -> ## [0.1.0]
BARE_VERSION="${VERSION#v}"

# Extract this version's section: from its heading up to (not including) the
# next "## " heading. Emitted without the heading line itself, since the
# GitHub release already carries the version as its title.
NOTES="$(awk -v want="## [$BARE_VERSION]" '
    index($0, want) == 1 { found = 1; next }
    found && /^## / { exit }
    found { print }
' "$CHANGELOG")"

# Trim leading/trailing blank lines.
NOTES="$(printf '%s\n' "$NOTES" | sed -e '/./,$!d' | awk 'NF {p = NR} {l[NR] = $0} END {for (i = 1; i <= p; i++) print l[i]}')"

if [[ -z "$NOTES" ]]; then
    echo "error: no '## [$BARE_VERSION]' section found in CHANGELOG.md" >&2
    exit 1
fi

# Resolve the commit to tag: explicit argument, else the tip of origin/main.
if [[ -z "$COMMITISH" ]]; then
    git fetch --quiet origin main
    COMMITISH="$(git rev-parse origin/main)"
fi
TARGET_SHA="$(git rev-parse "$COMMITISH^{commit}")"

# GitHub marks anything with a suffix after the patch number as a pre-release.
PRERELEASE_FLAG=()
IS_PRERELEASE="no"
if [[ "$BARE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+.+$ ]]; then
    PRERELEASE_FLAG=(--prerelease)
    IS_PRERELEASE="yes"
fi

echo "Publishing $VERSION"
echo "  commit:     $TARGET_SHA"
echo "  pre-release: $IS_PRERELEASE"
echo "  notes:      $(printf '%s\n' "$NOTES" | wc -l) lines from CHANGELOG.md"
echo

# 1. Tag. Never move a tag that already points somewhere: a published tag is
#    an immutable reference, and silently repointing it rewrites history for
#    anyone who already fetched it.
# ^{commit} dereferences annotated tags, whose ref resolves to the tag object.
if existing="$(git rev-parse -q --verify "refs/tags/$VERSION^{commit}")"; then
    if [[ "$existing" != "$TARGET_SHA" ]]; then
        echo "error: local tag $VERSION already exists at ${existing:0:7}," >&2
        echo "       but ${TARGET_SHA:0:7} was requested. Delete it first if that is intended." >&2
        exit 1
    fi
    echo "✓ local tag $VERSION already at ${TARGET_SHA:0:7}"
else
    run git tag -a "$VERSION" "$TARGET_SHA" -m "$VERSION"
    echo "✓ created local tag $VERSION"
fi

if git ls-remote --exit-code --tags origin "refs/tags/$VERSION" >/dev/null 2>&1; then
    echo "✓ tag $VERSION already on origin"
else
    run git push origin "$VERSION"
    echo "✓ pushed tag $VERSION to origin"
fi

# 2. Release. The tag is immutable once published, but the notes are metadata:
#    when the changelog says something the published release does not, the
#    changelog is the source of truth and the release is brought up to date.
#    This is what keeps a correction to a shipped version's entry from having
#    to be applied to GitHub by hand.
if gh release view "$VERSION" >/dev/null 2>&1; then
    published_notes="$(gh release view "$VERSION" --json body --jq .body)"
    if [[ "$published_notes" == "$NOTES" ]]; then
        echo "✓ release $VERSION already published, notes match CHANGELOG.md"
    elif [[ -n "$DRY_RUN" ]]; then
        printf 'DRY_RUN: gh release edit %s --notes <%s lines>  (notes differ)\n' \
            "$VERSION" "$(printf '%s\n' "$NOTES" | wc -l)"
    else
        gh release edit "$VERSION" --notes "$NOTES"
        echo "✓ release $VERSION already published — notes re-synced from CHANGELOG.md"
    fi
    gh release view "$VERSION" --json url --jq .url
else
    if [[ -n "$DRY_RUN" ]]; then
        printf 'DRY_RUN: gh release create %s --title %s --notes <%s lines> %s\n' \
            "$VERSION" "$VERSION" "$(printf '%s\n' "$NOTES" | wc -l)" "${PRERELEASE_FLAG[*]:-}"
    else
        gh release create "$VERSION" \
            --title "$VERSION" \
            --notes "$NOTES" \
            --target "$TARGET_SHA" \
            "${PRERELEASE_FLAG[@]}"
    fi
    echo "✓ created release $VERSION"
fi
