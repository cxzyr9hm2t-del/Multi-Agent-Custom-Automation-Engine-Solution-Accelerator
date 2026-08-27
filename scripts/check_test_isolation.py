#!/usr/bin/env python3
"""Fail when a test module replaces a first-party module and never puts it back.

Why this exists
---------------
A test file that writes into ``sys.modules`` at import time installs a mock that
outlives it. Every module imported afterwards inherits that mock, and a
``MagicMock`` answers *any* attribute truthily instead of raising. A test can
then pass while exercising nothing at all.

That is not a theoretical concern here. ``scripts/backfill_image_ownership.py``
had its message-extraction helper silently replaced this way. Its tests passed
run alone and failed in the full suite; had the order been reversed, the script
would have reported a clean run over a database it never touched.

What counts as an offence
-------------------------
Writing ``sys.modules['<first-party>'] = ...`` at module scope without a
``teardown_module`` (or an explicit restore map) that puts the original back.

Third-party names are exempt. ``agent_framework``, ``azure`` and friends are
stubbed deliberately in ``src/tests/backend/conftest.py`` because the real
distributions are not installed, and they are *meant* to persist for the whole
session. Restoring those would break every file collected afterwards.

``sys.modules.setdefault(...)`` is also exempt: it cannot displace a real
module, so it cannot cause the failure this guards against.

Exit codes
----------
0  no unrestored first-party clobbering (or --report given)
1  at least one offender
2  the scan itself failed

Stdlib only, so CI needs no install step.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys

# Top-level packages that belong to this repository. Anything else is a
# third-party stub and deliberately left alone -- see the module docstring.
FIRST_PARTY = {
    "agents",
    "api",
    "auth",
    "callbacks",
    "common",
    "config",
    "middleware",
    "models",
    "orchestration",
    "patches",
    "services",
    "tools",
}

# A file is considered to restore what it took if it defines a teardown that
# writes sys.modules back. Both spellings in use here are accepted.
RESTORE_MARKERS = ("teardown_module", "_ORIGINAL_MODULES")


def clobbered_first_party(path: str) -> list[str]:
    """Names this file assigns into sys.modules that belong to this repo.

    Only plain assignment counts. setdefault is excluded on purpose: it leaves
    a real module in place if one is already imported, so it cannot produce the
    silent-mock failure this script exists to catch.
    """
    with open(path, encoding="utf-8-sig") as handle:
        source = handle.read()
    tree = ast.parse(source, filename=path)

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "modules"
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                names.add(target.slice.value)

    return sorted(n for n in names if n.split(".")[0] in FIRST_PARTY)


def restores(path: str) -> bool:
    with open(path, encoding="utf-8-sig") as handle:
        source = handle.read()
    return any(marker in source for marker in RESTORE_MARKERS)


def scan(root: str) -> list[tuple[str, list[str]]]:
    offenders: list[tuple[str, list[str]]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".venv"}]
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            try:
                names = clobbered_first_party(path)
            except SyntaxError as exc:
                print(f"::warning file={path}::could not parse: {exc}")
                continue
            if names and not restores(path):
                offenders.append((path, names))
    return offenders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="src/tests")
    parser.add_argument(
        "--report",
        action="store_true",
        help="list offenders but exit 0 -- for adopting this on a tree that "
             "already has some",
    )
    parser.add_argument(
        "--baseline",
        type=int,
        default=None,
        help="exit non-zero only if the offender count EXCEEDS this. Lets the "
             "gate hold a known backlog flat while refusing new ones.",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.root):
        print(f"::error::no such directory: {args.root}")
        return 2

    offenders = scan(args.root)

    if offenders:
        print(f"{len(offenders)} test module(s) replace a first-party module "
              f"and never restore it:\n")
        for path, names in offenders:
            print(f"  {path}")
            for name in names:
                print(f"      {name}")
            print()
        print("Each leaves a MagicMock in sys.modules for every module imported")
        print("afterwards. A MagicMock answers any attribute truthily, so a test")
        print("downstream can pass while exercising nothing.")
        print()
        print("Fix: capture the originals before the first replacement and put")
        print("them back in teardown_module. See")
        print("src/tests/backend/services/test_plan_service.py for the pattern,")
        print("including why the azure.* names there are deliberately excluded.")
    else:
        print(f"No unrestored first-party sys.modules clobbering under {args.root}.")

    if args.report:
        return 0
    if args.baseline is not None:
        if len(offenders) > args.baseline:
            print(f"\n::error::{len(offenders)} offenders exceeds the agreed "
                  f"baseline of {args.baseline}. Do not add new ones.")
            return 1
        if len(offenders) < args.baseline:
            print(f"\nBaseline is {args.baseline} and there are now "
                  f"{len(offenders)}. Lower --baseline in the workflow to lock "
                  f"the improvement in.")
        return 0
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())
