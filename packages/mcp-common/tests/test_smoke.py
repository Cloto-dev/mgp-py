"""Smoke test ensuring the mcp_common package imports cleanly at the declared version."""

import mcp_common


def test_version_matches_pyproject() -> None:
    assert mcp_common.__version__ == "0.5.0"
