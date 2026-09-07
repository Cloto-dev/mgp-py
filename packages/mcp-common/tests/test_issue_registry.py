"""The issue registry is checked by CI, not only by hand.

`scripts/verify_issues.py` is useful to run ad hoc, but a checker nobody runs is
a checker that does not exist. Driving it from the test suite puts it on the
same footing as every other gate: the existing CI job runs pytest, so this needs
no workflow of its own and cannot be skipped by forgetting a step.

The subprocess is deliberate. These tests measure the artifact that ships --
the script as invoked from a shell -- rather than importing its internals and
measuring a different thing that happens to share a name.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "verify_issues.py"
REGISTRY = REPO_ROOT / "qa" / "issue-registry.json"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_every_registry_entry_holds():
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr


def test_registry_ids_are_unique():
    issues = json.loads(REGISTRY.read_text(encoding="utf-8"))["issues"]
    ids = [i["id"] for i in issues]
    assert len(ids) == len(set(ids)), "duplicate issue ids"


def test_no_pattern_contains_a_field_delimiter(tmp_path):
    """A sibling registry lost rows to this, so the shape is pinned here.

    Nothing in this repository joins fields with a delimiter -- that is why the
    checker is Python. The assertion keeps the registry portable to a reader
    that does, rather than trusting that no such reader will ever exist.
    """
    issues = json.loads(REGISTRY.read_text(encoding="utf-8"))["issues"]
    offenders = [i["id"] for i in issues if "|" in i["pattern"] or "|" in i["title"]]
    assert not offenders, f"'|' in pattern or title of: {offenders}"


# --- the checker must be able to fail -------------------------------------
# A gate is only a gate once it has been seen to go red. Each case below breaks
# one thing and asserts the specific failure, so a future edit that turns the
# checker into an unconditional pass fails here rather than passing quietly.


def _registry_with(tmp_path: Path, mutate) -> Path:
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_a_pattern_that_matches_nothing_fails(tmp_path):
    def mutate(doc):
        doc["issues"][0]["pattern"] = "THIS_STRING_IS_NOT_IN_THE_FILE"

    result = _run("--registry", str(_registry_with(tmp_path, mutate)))
    assert result.returncode == 1
    assert "watching nothing" in result.stdout


def test_a_missing_file_fails(tmp_path):
    def mutate(doc):
        doc["issues"][0]["file"] = "packages/mcp-common/src/mcp_common/not_a_module.py"

    result = _run("--registry", str(_registry_with(tmp_path, mutate)))
    assert result.returncode == 1
    assert "file not found" in result.stdout


def test_an_empty_pattern_fails(tmp_path):
    """An empty pattern matches every line, which would read as a trivial pass."""

    def mutate(doc):
        doc["issues"][0]["pattern"] = ""

    result = _run("--registry", str(_registry_with(tmp_path, mutate)))
    assert result.returncode == 1
    assert "missing required field" in result.stdout


def test_an_uncompilable_pattern_fails(tmp_path):
    def mutate(doc):
        doc["issues"][0]["pattern"] = "unbalanced ("

    result = _run("--registry", str(_registry_with(tmp_path, mutate)))
    assert result.returncode == 1
    assert "does not compile" in result.stdout


def test_a_duplicate_id_fails(tmp_path):
    def mutate(doc):
        doc["issues"].append(dict(doc["issues"][0]))

    result = _run("--registry", str(_registry_with(tmp_path, mutate)))
    assert result.returncode == 1
    assert "duplicate id" in result.stdout


def test_an_unreadable_registry_fails(tmp_path):
    """Broken JSON must fail, not read as an empty registry with nothing wrong."""
    path = tmp_path / "registry.json"
    path.write_text("{ not json", encoding="utf-8")
    result = _run("--registry", str(path))
    assert result.returncode == 1
    assert "not valid JSON" in result.stdout


def test_an_empty_registry_fails(tmp_path):
    def mutate(doc):
        doc["issues"] = []

    result = _run("--registry", str(_registry_with(tmp_path, mutate)))
    assert result.returncode == 1
    assert "nothing was checked" in result.stdout
