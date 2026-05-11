"""Unit tests for mcp_common.isolation γ-semantics helpers.

The upstream `cloto-mcp-servers` tested these helpers only indirectly via
`test_cpersona_isolation.py` and `test_cscheduler_isolation.py`, which are
server-level integration tests. This file covers the pure logic directly.
"""

from __future__ import annotations

from mcp_common.isolation import (
    coerce_for_read,
    coerce_for_write,
    extract_axes_for_read,
    extract_axes_for_write,
    gamma_clause,
)

# ── gamma_clause ─────────────────────────────────────────────────────────────


def test_gamma_clause_none_returns_empty_fragment():
    fragment, params = gamma_clause("project_id", None)
    assert fragment == ""
    assert params == []


def test_gamma_clause_empty_string_matches_only_global_pool():
    fragment, params = gamma_clause("project_id", "")
    assert fragment == "project_id = ?"
    assert params == [""]


def test_gamma_clause_named_bucket_unions_with_global_pool():
    fragment, params = gamma_clause("agent_id", "agent.sapphy")
    assert fragment == "agent_id IN (?, ?)"
    assert params == ["agent.sapphy", ""]


def test_gamma_clause_column_name_interpolated_verbatim():
    # Trusted-identifier contract: caller is responsible for the column name.
    fragment, _ = gamma_clause("t.agent_id", "x")
    assert fragment == "t.agent_id IN (?, ?)"


# ── coerce_for_write ─────────────────────────────────────────────────────────


def test_coerce_for_write_preserves_strings_including_empty():
    assert coerce_for_write("project-a") == "project-a"
    assert coerce_for_write("") == ""


def test_coerce_for_write_collapses_non_string_to_empty():
    assert coerce_for_write(None) == ""
    assert coerce_for_write(42) == ""
    assert coerce_for_write({"a": 1}) == ""
    assert coerce_for_write([]) == ""


# ── coerce_for_read ──────────────────────────────────────────────────────────


def test_coerce_for_read_preserves_strings_including_empty():
    assert coerce_for_read("project-a") == "project-a"
    assert coerce_for_read("") == ""


def test_coerce_for_read_collapses_non_string_to_none():
    assert coerce_for_read(None) is None
    assert coerce_for_read(42) is None
    assert coerce_for_read({"a": 1}) is None


# ── extract_axes_for_write / extract_axes_for_read ───────────────────────────


def test_extract_axes_for_write_defaults_missing_to_empty():
    args = {"project_id": "project-a"}
    project_id, agent_id = extract_axes_for_write(args, "project_id", "agent_id")
    assert project_id == "project-a"
    assert agent_id == ""


def test_extract_axes_for_read_defaults_missing_to_none():
    args = {"project_id": "project-a"}
    project_id, agent_id = extract_axes_for_read(args, "project_id", "agent_id")
    assert project_id == "project-a"
    assert agent_id is None


def test_extract_axes_for_read_distinguishes_empty_from_missing():
    # '' = "global pool only" filter; None = "no filter on this axis".
    args = {"project_id": ""}
    (project_id,) = extract_axes_for_read(args, "project_id")
    assert project_id == ""
