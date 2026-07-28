#!/usr/bin/env python3
"""Report packages pinned at different versions across the repo's Python manifests.

Each service owns its own dependency set here, which is deliberate — but nothing
kept those sets aligned, so a security bump applied to one file silently left the
others behind. The July 2026 audit found python-multipart spanning 0.0.20 to
0.0.32 across four manifests, with CI's copy already hardened while both
production surfaces stayed vulnerable. Someone fixed one file and never
propagated it, and no check existed to notice.

This is advisory by default: it prints a report and exits 0. Pass --strict to
fail the build instead, once the existing drift has been resolved.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Manifests worth comparing. Lockfiles are excluded: they pin transitive trees
# that legitimately differ per service, so including them buries the signal.
MANIFESTS = [
    ".github/requirements.txt",
    "src/backend/pyproject.toml",
    "src/mcp_server/pyproject.toml",
    "src/App/pyproject.toml",
    "infra/scripts/post-provision/requirements.txt",
    "infra/vscode_web/requirements.txt",
]

# name==version, tolerating extras (`uvicorn[standard]==0.38.0`) and quotes.
PIN = re.compile(r'^\s*["\']?([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==([A-Za-z0-9._+!-]+)["\']?,?\s*$')


def pins(path: Path) -> dict[str, str]:
    """Extract name -> version pins, skipping comments and commented-out lines."""
    found: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.match(line)
        if match:
            found[match.group(1).lower()] = match.group(2)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when drift is found (default: report only)",
    )
    args = parser.parse_args()

    versions: dict[str, dict[str, str]] = defaultdict(dict)
    checked = []
    for rel in MANIFESTS:
        path = REPO / rel
        if not path.is_file():
            continue
        checked.append(rel)
        for name, version in pins(path).items():
            versions[name][rel] = version

    drift = {
        name: locations
        for name, locations in versions.items()
        if len(set(locations.values())) > 1
    }

    print(f"Compared {len(checked)} manifests:")
    for rel in checked:
        print(f"  {rel}")
    print()

    if not drift:
        print("No version drift found.")
        return 0

    print(f"{len(drift)} package(s) pinned at differing versions:\n")
    for name in sorted(drift):
        locations = drift[name]
        spread = sorted(set(locations.values()))
        print(f"  {name}: {', '.join(spread)}")
        for rel in sorted(locations):
            print(f"      {locations[rel]:<12} {rel}")
        print()

    print(
        "Drift is not automatically a bug — services may legitimately need "
        "different versions.\nIt is a hazard because a security bump applied to "
        "one manifest does not reach\nthe others. Confirm each divergence is "
        "intentional."
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
