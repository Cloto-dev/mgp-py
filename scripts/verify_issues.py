#!/usr/bin/env python3
"""Check every entry in qa/issue-registry.json against the file it names.

An entry claims that a pattern is present in a file (an open defect, or a fix
marker) or absent from it (a defect that was removed). This script decides
whether the claim still holds, so a registry cannot quietly describe code that
has moved on.

Usage:
    python scripts/verify_issues.py [--filter open|fixed] [--registry PATH]

Exit codes:
    0  every entry checked holds
    1  at least one entry no longer holds, or could not be checked

Why this is Python rather than a shell script wrapping grep. Two failure modes
were observed in sibling registries and both are silent, which is the worst
property a checker can have:

  * A shell reader that joins fields with a delimiter loses any row whose
    pattern contains that delimiter -- the row shifts, and the entry drops out
    of verification while the run still reports success.
  * "grep -P, falling back to grep -E" is two regular-expression dialects, so
    the same registry can be checked differently on a developer's machine and
    in CI. Here every pattern is compiled once, by `re`, everywhere.

An entry whose pattern matches nothing while claiming presence is a failure,
not a pass: a pin that matches nothing reports nothing, so it would let the
defect it names disappear without anyone noticing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = REPO_ROOT / "qa" / "issue-registry.json"

REQUIRED_FIELDS = ("id", "title", "severity", "status", "file", "pattern", "expected")
VALID_EXPECTED = ("present", "absent")


class Failure(Exception):
    """An entry that could not be checked, or whose claim no longer holds."""


def _load(registry: Path) -> list[dict]:
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Failure(f"registry not found: {registry}") from None
    except json.JSONDecodeError as exc:
        # A registry that does not parse must fail loudly. Reporting "no issues"
        # for an unreadable file is how a broken registry passes for an empty one.
        raise Failure(f"registry is not valid JSON: {exc}") from None
    issues = data.get("issues")
    if not isinstance(issues, list):
        raise Failure("registry has no 'issues' list")
    _check_schema_pointer(data)
    return issues


def _check_schema_pointer(data: dict) -> None:
    """The registry has to name the document describing its shape, and that name
    has to lead somewhere.

    `$schema` is the only link from the data to its description, and a link
    nothing follows is free to be wrong in the one way that matters. In a sibling
    registry the field held a URL that had rotted into a 404, and in another it
    named a file that repository does not contain -- both with their gates green
    for months. A repo-relative path is the only form a checker that must work
    offline can follow, so it is the only form accepted, and it is resolved
    against the repository root exactly as `file` is.
    """
    schema = data.get("$schema")
    if not schema:
        raise Failure(
            "registry names no '$schema'. That field is the only pointer from the data "
            "to the document saying what its shape is; without it the shape is whatever "
            "the reader assumes"
        )
    if "://" in schema or os.path.isabs(schema):
        raise Failure(
            f"'$schema' is {schema!r}; it must be a path relative to the repository "
            f"root, because that is the only form this check can follow"
        )
    target = (REPO_ROOT / schema).resolve()
    if not (target.is_relative_to(REPO_ROOT) and target.is_file()):
        raise Failure(
            f"'$schema' points at {schema!r}, which is not a file in this repository"
        )


def _check(entry: dict) -> tuple[str, str]:
    """Return (verdict, detail). Raises Failure when the entry does not hold."""
    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        raise Failure(f"missing required field(s): {', '.join(missing)}")

    expected = entry["expected"]
    if expected not in VALID_EXPECTED:
        raise Failure(f"'expected' must be one of {VALID_EXPECTED}, got {expected!r}")

    try:
        pattern = re.compile(entry["pattern"])
    except re.error as exc:
        raise Failure(f"pattern does not compile: {exc}") from None

    target = REPO_ROOT / entry["file"]
    if not target.is_file():
        if expected == "absent":
            return "FIXED", f"file deleted: {entry['file']} (pattern trivially absent)"
        raise Failure(f"file not found: {entry['file']}")

    matches = sum(
        1 for line in target.read_text(encoding="utf-8").splitlines() if pattern.search(line)
    )

    if expected == "present":
        if matches == 0:
            raise Failure(
                f"pattern not found in {entry['file']} -- the entry describes code that is "
                f"no longer there, or the pattern never matched. Either way it is watching nothing."
            )
        return "VERIFIED", f"pattern found in {entry['file']} ({matches} match(es))"

    if matches:
        raise Failure(f"pattern still present in {entry['file']} ({matches} match(es))")
    return "FIXED", f"pattern no longer present in {entry['file']}"


def run(registry: Path, status_filter: str | None = None) -> int:
    issues = _load(registry)
    seen: set[str] = set()
    checked = failed = 0

    for entry in issues:
        entry_id = entry.get("id", "<no id>")
        if entry_id in seen:
            print(f"  [ERROR]    {entry_id}: duplicate id")
            failed += 1
            continue
        seen.add(entry_id)

        if status_filter and entry.get("status") != status_filter:
            continue

        checked += 1
        try:
            verdict, detail = _check(entry)
        except Failure as exc:
            print(f"  [ERROR]    {entry_id} ({entry.get('severity', '?')}): {exc}")
            failed += 1
            continue
        label = f"[{verdict}]".ljust(11)
        print(f"  {label}{entry_id} ({entry['severity']}): {entry['title']}")
        print(f"             {detail}")

    print()
    print(f"checked {checked} of {len(issues)} entries, {failed} failing")
    if checked == 0:
        # An empty run is not a pass. A filter that matches nothing, or a registry
        # that lost its entries, must not look like a clean bill of health.
        print("nothing was checked -- treating that as a failure, not as success")
        return 1
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--filter", dest="status_filter", choices=("open", "fixed"))
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args()
    try:
        return run(args.registry, args.status_filter)
    except Failure as exc:
        print(f"  [ERROR] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
