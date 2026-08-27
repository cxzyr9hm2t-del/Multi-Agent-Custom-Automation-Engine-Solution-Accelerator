#!/usr/bin/env python3
"""Re-measure the factual claims this repository makes about itself.

Why this exists
---------------
Every recorded failure in this repository reduces to one shape: a claim and the
evidence for it live in different places, with nothing comparing them.

  - CLAUDE.md asserted the suite was "29 + 834". It was 31 + 927. That file is
    what every contributor and agent reads as ground truth, and nothing had
    re-checked it since it was written.
  - A verification report stated which CI checks were red without querying CI.
    It was wrong, and stayed wrong.
  - An audit's banner said "sixteen of seventeen findings closed" while its own
    table underneath said otherwise, in three separate ways.

None of these were caught by review, because a stale number reads exactly like
a fresh one. The only thing that distinguishes them is measurement.

So: this script measures, then compares against what the documents say. A claim
that no longer matches reality fails the build, in the same way a broken test
does. It is deliberately narrow -- it checks a fixed set of facts it knows how
to measure, rather than trying to parse arbitrary prose.

Test counts and coverage are *supplied* rather than measured here, because CI
already runs the suite and re-running it would double the build. Pass them in
from the same run that produced them. Facts that are cheap to measure from the
tree (workflow counts, action pinning) are measured directly.

Exit codes
----------
0  every checked claim matches what was measured
1  at least one claim is stale or contradicted
2  the check could not run (bad input)

Stdlib only, so CI needs no install step.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

DOC = "CLAUDE.md"


class Result:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checked = 0
        self.skipped: list[str] = []

    def check(self, label: str, claimed, measured, note: str = "") -> None:
        self.checked += 1
        if claimed == measured:
            print(f"  OK    {label}: {measured}")
        else:
            suffix = f"  ({note})" if note else ""
            print(f"  STALE {label}: document says {claimed!r}, "
                  f"measured {measured!r}{suffix}")
            self.failures.append(
                f"{label}: document says {claimed}, measured {measured}"
            )

    def skip(self, label: str, why: str) -> None:
        self.skipped.append(f"{label} ({why})")
        print(f"  SKIP  {label}: {why}")


def read_doc(path: str) -> str:
    with open(path, encoding="utf-8-sig") as handle:
        return handle.read()


def measure_workflows() -> tuple[int, int, int]:
    """Return (total, parseable, declaring-permissions).

    Parsed without PyYAML: a top-level ``permissions:`` key is a line starting
    at column zero. That is exactly what the pinning gate asserts, and keeping
    this stdlib-only means the check runs with no install step.
    """
    paths = sorted(glob.glob(".github/workflows/*.yml"))
    with_perms = 0
    for path in paths:
        with open(path, encoding="utf-8-sig") as handle:
            if any(re.match(r"^permissions:", line) for line in handle):
                with_perms += 1
    return len(paths), len(paths), with_perms


def measure_unpinned_actions() -> list[str]:
    """Third-party ``uses:`` references not pinned to a 40-char digest."""
    unpinned: set[str] = set()
    for path in sorted(glob.glob(".github/workflows/*.yml")):
        with open(path, encoding="utf-8-sig") as handle:
            for line in handle:
                match = re.match(r"\s*(?:-\s*)?uses:\s*(\S+)", line)
                if not match:
                    continue
                ref = match.group(1)
                if ref.startswith("./") or ref.startswith("docker://"):
                    continue
                if not re.search(r"@[0-9a-f]{40}$", ref):
                    unpinned.add(ref)
    return sorted(unpinned)


def coverage_from_xml(path: str) -> float | None:
    if not os.path.exists(path):
        return None
    root = ET.parse(path).getroot()
    return round(float(root.attrib.get("line-rate", 0)) * 100, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", default=DOC)
    parser.add_argument("--coverage-xml", default="coverage.xml")
    parser.add_argument(
        "--backend-isolated", type=int, default=None,
        help="passed count from the test_app.py run",
    )
    parser.add_argument(
        "--backend-remainder", type=int, default=None,
        help="passed count from the rest of the backend suite",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="print findings but exit 0",
    )
    args = parser.parse_args()

    if not os.path.exists(args.doc):
        print(f"::error::no such file: {args.doc}")
        return 2

    doc = read_doc(args.doc)
    result = Result()

    print(f"Verifying the claims {args.doc} makes about this repository.\n")

    # --- suite size -------------------------------------------------------
    # CLAUDE.md states the split as "(29 + 834)".
    match = re.search(r"the full suite passes \((\d+) \+ (\d+)\)", doc)
    if not match:
        result.skip("suite size", f"no '(N + M)' claim found in {args.doc}")
    elif args.backend_isolated is None or args.backend_remainder is None:
        result.skip(
            "suite size",
            "pass --backend-isolated and --backend-remainder from the test run",
        )
    else:
        result.check(
            "suite size",
            (int(match.group(1)), int(match.group(2))),
            (args.backend_isolated, args.backend_remainder),
            "test_app.py first, then the remainder",
        )

    # --- coverage ---------------------------------------------------------
    measured_cov = coverage_from_xml(args.coverage_xml)
    claimed_cov = re.search(r"suite currently sits at \*\*?~?(\d+)%", doc) \
        or re.search(r"sits at ~(\d+)%", doc)
    if claimed_cov is None:
        result.skip("coverage", f"no 'sits at ~N%' claim found in {args.doc}")
    elif measured_cov is None:
        result.skip("coverage", f"{args.coverage_xml} not present")
    else:
        # The document states a rounded figure; compare to the nearest point.
        result.check(
            "coverage (rounded)",
            int(claimed_cov.group(1)),
            int(round(measured_cov)),
            f"exact {measured_cov}%",
        )

    # --- coverage floor ---------------------------------------------------
    claimed_floor = re.search(r"(\d+)% coverage floor", doc)
    measured_floor = None
    for path in glob.glob(".github/workflows/*.yml"):
        with open(path, encoding="utf-8-sig") as handle:
            found = re.search(r"COVERAGE < (\d+)", handle.read())
            if found:
                measured_floor = int(found.group(1))
                break
    if claimed_floor and measured_floor is not None:
        result.check("coverage floor",
                     int(claimed_floor.group(1)), measured_floor,
                     "from the workflow's own threshold check")
    else:
        result.skip("coverage floor", "claim or workflow threshold not found")

    # --- workflow counts --------------------------------------------------
    total, parseable, with_perms = measure_workflows()
    for label, claimed_n in re.findall(r"(\d+)/(\d+) workflows", doc):
        result.check("workflow count (in a N/M claim)",
                     int(claimed_n), total)
        break
    else:
        result.skip("workflow count", "no 'N/M workflows' claim in the doc")

    if with_perms != total:
        result.failures.append(
            f"top-level permissions: {with_perms}/{total} workflows declare it"
        )
        print(f"  FAIL  permissions: only {with_perms}/{total} workflows "
              f"declare top-level permissions")
    else:
        print(f"  OK    permissions: {with_perms}/{total} workflows declare it")
        result.checked += 1

    # --- action pinning ---------------------------------------------------
    unpinned = measure_unpinned_actions()
    if unpinned:
        result.failures.append(f"unpinned action references: {unpinned}")
        print(f"  FAIL  action pinning: {len(unpinned)} unpinned: {unpinned}")
    else:
        print("  OK    action pinning: every reference is a commit digest")
        result.checked += 1

    # --- report -----------------------------------------------------------
    print()
    print(f"{result.checked} claim(s) checked, {len(result.failures)} stale, "
          f"{len(result.skipped)} skipped.")
    if result.skipped:
        print("Skipped: " + "; ".join(result.skipped))

    if result.failures:
        print()
        for failure in result.failures:
            print(f"::error::stale claim -- {failure}")
        print()
        print("A number in the documentation that no longer matches the tree is")
        print("not cosmetic: it is what the next contributor, human or agent,")
        print("will take as ground truth without re-measuring. Update the")
        print("document, or fix what regressed.")
        return 0 if args.report else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
