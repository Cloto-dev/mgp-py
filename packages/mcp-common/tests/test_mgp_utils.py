"""Unit tests for mcp_common.mgp_utils.MgpCapabilities builder.

The async stream-notification helpers (`send_mgp_stream_chunk`,
`write_mgp_*`) require a live MCP `ServerSession` / `MemoryObjectSendStream`
and are exercised end-to-end by `cloto-mcp-servers/servers/tests/test_streaming.py`
(an integration test that stays in that repo until Phase 2-δ moves
`mcp_stream_interceptor` and the streaming-side reference path). This file
covers the synchronous capability-builder logic that has no MCP runtime
dependency.
"""

from __future__ import annotations

from mcp_common.mgp_utils import MGP_VERSION, MgpCapabilities


def test_default_builder_emits_minimal_capabilities():
    out = MgpCapabilities().as_dict()
    assert out == {"mgp": {"version": MGP_VERSION, "extensions": ["permissions"]}}


def test_explicit_version_override():
    out = MgpCapabilities(version="0.9.9-test").as_dict()
    assert out["mgp"]["version"] == "0.9.9-test"


def test_require_permission_is_deduplicated_and_ordered():
    cap = MgpCapabilities()
    cap.require_permission("network.outbound")
    cap.require_permission("filesystem.write")
    cap.require_permission("network.outbound")  # duplicate -> no-op
    out = cap.as_dict()
    assert out["mgp"]["permissions_required"] == ["network.outbound", "filesystem.write"]


def test_trust_level_only_present_when_set():
    bare = MgpCapabilities().as_dict()["mgp"]
    assert "trust_level" not in bare

    cap = MgpCapabilities().set_trust_level("standard")
    out = cap.as_dict()
    assert out["mgp"]["trust_level"] == "standard"


def test_server_id_only_present_when_set():
    bare = MgpCapabilities().as_dict()["mgp"]
    assert "server_id" not in bare

    cap = MgpCapabilities().set_server_id("research")
    out = cap.as_dict()
    assert out["mgp"]["server_id"] == "research"


def test_add_extension_deduplicates_default_set():
    cap = MgpCapabilities()
    cap.add_extension("permissions")  # already in default set
    cap.add_extension("streaming")
    cap.add_extension("events")
    assert cap.as_dict()["mgp"]["extensions"] == [
        "permissions",
        "streaming",
        "events",
    ]


def test_builder_returns_self_for_chaining():
    cap = (
        MgpCapabilities()
        .require_permission("network.outbound")
        .set_trust_level("experimental")
        .set_server_id("my-server")
        .add_extension("streaming")
    )
    out = cap.as_dict()["mgp"]
    assert out["permissions_required"] == ["network.outbound"]
    assert out["trust_level"] == "experimental"
    assert out["server_id"] == "my-server"
    assert out["extensions"] == ["permissions", "streaming"]
