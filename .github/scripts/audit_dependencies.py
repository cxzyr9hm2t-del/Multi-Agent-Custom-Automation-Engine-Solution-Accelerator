#!/usr/bin/env python3
"""Audit the installed dependency closure against the OSV database.

Why this exists
---------------
A manifest-only audit is not an audit. The six Python manifests declare about
95 direct pins, but the three `uv.lock` files resolve close to 300, and
`uv sync --frozen` installs that closure — so an audit scoped to manifests is
blind to every transitive package. That blind spot let advisories in `h2`,
`mcp` and `mem0ai` sit unreported while a hand-rolled manifest-only pass
returned zero findings; Dependabot caught them instead. `check_version_drift.py`
shares the blind spot by design: it compares declared pins, so transitive drift
never shows up there either.

This does not replace `scheduled-security-sweep.yml`, which has audited the
resolved lockfiles with pip-audit since 2026-08 and remains the scheduled
authority. This covers what that sweep's flags exclude — dev dependencies
(`--no-dev` hides eight packages in src/backend alone, `pytest` among them,
whose advisory was a real finding here), the two `infra/**/requirements.txt`
sets, and `tests/e2e-test` — in a single pass that also runs on the pull
request that changes a lock, rather than up to a week later.

Three rules keep this honest:

  1. Read what is installed - the lockfiles and the hash-pinned
     `src/App/requirements.txt` - not what is declared.
  2. Never skip silently. Every non-blank line this cannot parse is counted
     and printed, so an under-collecting parser cannot report a clean result.
  3. State what was deliberately excluded (first-party lock entries) and what
     could not be reached: if OSV is unreachable the result is UNKNOWN, never
     clean.

A lock may carry several versions of one package, one per resolution fork.
Findings therefore print the fork's `resolution-markers`: only the fork that
matches the runtime is actually installed, so a flagged version confined to,
say, a Python 3.14 fork is not necessarily shipped. Confirm before acting.

Advisory only. Exits 0 even with findings, matching check_version_drift.py.
Stdlib only - no install step required.
"""
import json
import re
import sys
import tomllib
import urllib.error
import urllib.request

PY_LOCKS = [
    "src/backend/uv.lock",
    "src/mcp_server/uv.lock",
    "src/App/uv.lock",
]
PY_REQS = [
    ".github/requirements.txt",
    "src/App/requirements.txt",
    "infra/scripts/post-provision/requirements.txt",
    "infra/vscode_web/requirements.txt",
    "tests/e2e-test/requirements.txt",
]
NPM_LOCKS = ["src/App/package-lock.json", "package-lock.json"]

OSV_BATCH = "https://api.osv.dev/v1/querybatch"
PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*==\s*(?P<ver>[A-Za-z0-9_.\-+!]+)"
)


def collect_python():
    """Return pins, the lines that could not be parsed, and entries deliberately excluded."""
    pins, unparsed, excluded = {}, [], []

    for lock in PY_LOCKS:
        try:
            data = tomllib.load(open(lock, "rb"))
        except FileNotFoundError:
            print(f"!! missing lockfile: {lock}", file=sys.stderr)
            continue
        for pkg in data.get("package", []):
            if "name" not in pkg or "version" not in pkg:
                # A lock entry with no version is this repository's own project
                # (a local source with a dynamic version). It is not a published
                # package and OSV has nothing to say about it, so excluding it is
                # correct — but it is recorded rather than dropped, because a
                # parser that quietly discards entries is how the manifest-only
                # audit this script replaces came to report a clean result.
                excluded.append((lock, pkg.get("name", "<unnamed>")))
                continue
            key = (pkg["name"].lower(), pkg["version"])
            entry = pins.setdefault(key, {"sources": set(), "markers": set()})
            entry["sources"].add(lock)
            for marker in pkg.get("resolution-markers") or []:
                entry["markers"].add(marker)

    for req in PY_REQS:
        try:
            lines = open(req).read().splitlines()
        except FileNotFoundError:
            print(f"!! missing requirements file: {req}", file=sys.stderr)
            continue
        for lineno, raw in enumerate(lines, 1):
            line = raw.strip()
            # Blank, comment, hash continuation and pip flags are not pins.
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            line = line.rstrip("\\").strip()
            line = line.split(";")[0].split("#")[0].strip()
            match = PIN_RE.match(line)
            if match:
                key = (match.group("name").lower(), match.group("ver"))
                pins.setdefault(key, {"sources": set(), "markers": set()})
                pins[key]["sources"].add(req)
            else:
                unparsed.append((req, lineno, raw[:90]))

    return pins, unparsed, excluded


def collect_npm():
    packages = {}
    for lock in NPM_LOCKS:
        try:
            data = json.load(open(lock))
        except FileNotFoundError:
            continue
        for path, meta in (data.get("packages") or {}).items():
            if not path or "version" not in meta:
                continue
            name = path.split("node_modules/")[-1]
            entry = packages.setdefault(
                (name, meta["version"]), {"dev": meta.get("dev", False), "sources": set()}
            )
            entry["sources"].add(lock)
            if not meta.get("dev", False):
                entry["dev"] = False
    return packages


def query_osv(keys, ecosystem):
    queries = [
        {"package": {"name": name, "ecosystem": ecosystem}, "version": version}
        for name, version in keys
    ]
    results = []
    for start in range(0, len(queries), 500):
        body = json.dumps({"queries": queries[start:start + 500]}).encode()
        request = urllib.request.Request(
            OSV_BATCH, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                results.extend(json.load(response)["results"])
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"!! OSV query failed ({exc}) - results are INCOMPLETE", file=sys.stderr)
            return None
    return results


def main():
    pins, unparsed, excluded = collect_python()
    npm_packages = collect_npm()

    print("# Dependency audit (OSV)\n")
    print(f"Python: {len(pins)} distinct (package, version) pairs "
          f"from {len(PY_LOCKS)} lockfiles and {len(PY_REQS)} requirements files.")
    print(f"npm:    {len(npm_packages)} distinct (package, version) pairs "
          f"from {len(NPM_LOCKS)} lockfiles.\n")

    if unparsed:
        print(f"**{len(unparsed)} non-blank line(s) could not be parsed and were "
              f"NOT audited.** Unpinned entries cannot be checked by version:\n")
        for path, lineno, text in unparsed:
            print(f"- `{path}:{lineno}`: `{text}`")
        print()

    if excluded:
        print(f"Excluded {len(excluded)} first-party lock entry/entries "
              f"(this repo's own projects, not published packages):\n")
        for lock, name in excluded:
            print(f"- `{name}` in `{lock}`")
        print()

    python_keys = sorted(pins)
    npm_keys = sorted(npm_packages)
    python_results = query_osv(python_keys, "PyPI")
    npm_results = query_osv(npm_keys, "npm")
    if python_results is None or npm_results is None:
        print("\nAudit incomplete - OSV unreachable. Treat as UNKNOWN, not clean.")
        return 0

    findings = 0

    print("## Python\n")
    python_hits = 0
    for key, result in zip(python_keys, python_results):
        if not result.get("vulns"):
            continue
        python_hits += 1
        name, version = key
        ids = sorted({vuln["id"] for vuln in result["vulns"]})
        print(f"### `{name}=={version}`\n")
        for source in sorted(pins[key]["sources"]):
            print(f"- in `{source}`")
        for marker in sorted(pins[key]["markers"]):
            print(f"- fork: `{marker}`")
        print(f"- advisories: {', '.join(ids)}\n")
    if not python_hits:
        print("No known advisories.\n")

    print("## npm\n")
    npm_hits = 0
    for key, result in zip(npm_keys, npm_results):
        if not result.get("vulns"):
            continue
        npm_hits += 1
        name, version = key
        ids = sorted({vuln["id"] for vuln in result["vulns"]})
        scope = "dev" if npm_packages[key]["dev"] else "**production**"
        print(f"- `{name}@{version}` ({scope}): {', '.join(ids)}")
    if not npm_hits:
        print("No known advisories.")

    findings = python_hits + npm_hits
    print(f"\n**Total: {findings} vulnerable package(s).**")
    print("\nAdvisory only - this never fails the build. A finding confined to a "
          "resolution fork the project does not build is not necessarily shipped; "
          "confirm with `uv sync --frozen` and `importlib.metadata.version(...)` "
          "before acting.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
