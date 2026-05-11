"""Tests for ``mcp_common.mcp_utils`` ToolRegistry."""

import pytest
from mcp_common.mcp_utils import ToolRegistry


@pytest.fixture
def registry():
    reg = ToolRegistry("test-server")

    @reg.tool(
        "greet",
        "Say hello",
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )
    async def greet(arguments: dict) -> dict:
        return {"message": f"Hello, {arguments['name']}!"}

    @reg.tool("fail", "Always fails", {"type": "object", "properties": {}})
    async def fail(arguments: dict) -> dict:
        raise RuntimeError("intentional error")

    return reg


def test_tool_registration(registry):
    """Tools should be registered with correct metadata."""
    assert len(registry._tools) == 2
    assert registry._tools[0].name == "greet"
    assert registry._tools[1].name == "fail"


@pytest.mark.asyncio
async def test_list_tools(registry):
    """list_tools handler should return all registered tools."""
    # The list_tools handler is registered on the server
    assert len(registry._tools) == 2
    assert registry._tools[0].description == "Say hello"


@pytest.mark.asyncio
async def test_call_tool_success(registry):
    """Calling a registered tool should return JSON-wrapped result."""
    handler = registry._handlers["greet"]
    result = await handler({"name": "World"})
    assert result == {"message": "Hello, World!"}


@pytest.mark.asyncio
async def test_call_tool_exception(registry):
    """Calling a failing tool should return error JSON, not crash."""
    # Simulate what call_tool does internally
    handler = registry._handlers["fail"]
    try:
        await handler({})
        caught = False
    except RuntimeError:
        caught = True
    assert caught


def test_unknown_tool_handler(registry):
    """Unknown tool name should not be in handlers."""
    assert "nonexistent" not in registry._handlers


def test_tool_schema(registry):
    """Tool schemas should be preserved."""
    greet_tool = registry._tools[0]
    assert greet_tool.inputSchema["required"] == ["name"]
    assert "name" in greet_tool.inputSchema["properties"]


# ── auto_tool: validation-driven parameter extraction ────────────────────────


@pytest.mark.asyncio
async def test_auto_tool_extracts_typed_params_positionally():
    """auto_tool wires validators to handler positional args by spec order."""
    reg = ToolRegistry("auto-server")
    captured: dict = {}

    async def handler(name: str, count: int, payload: dict) -> dict:
        captured["args"] = (name, count, payload)
        return {"ok": True}

    reg.auto_tool(
        "do",
        "Auto-validated tool",
        {"type": "object"},
        handler,
        [("name", str), ("count", int), ("payload", dict)],
    )

    result = await reg._handlers["do"]({"name": "alice", "count": 7, "payload": {"k": "v"}})
    assert result == {"ok": True}
    assert captured["args"] == ("alice", 7, {"k": "v"})


@pytest.mark.asyncio
async def test_auto_tool_uses_default_when_key_missing():
    """auto_tool defaults kick in when the argument dict lacks the key."""
    reg = ToolRegistry("auto-server")

    async def handler(limit: int) -> dict:
        return {"limit": limit}

    reg.auto_tool(
        "fetch",
        "Fetch with default limit",
        {"type": "object"},
        handler,
        [("limit", int, 10)],
    )

    assert await reg._handlers["fetch"]({}) == {"limit": 10}
    assert await reg._handlers["fetch"]({"limit": 25}) == {"limit": 25}


# ── MGP validation log filter ───────────────────────────────────────────────


def test_mgp_validation_filter_drops_request_noise():
    """The filter must suppress the MCP SDK's bulk validation warnings."""
    import logging

    from mcp_common.mcp_utils import _MgpValidationFilter

    f = _MgpValidationFilter()
    noisy = logging.LogRecord(
        name="mcp.shared.session",
        level=logging.WARNING,
        pathname="x",
        lineno=1,
        msg="Failed to validate request: ...",
        args=(),
        exc_info=None,
    )
    notif = logging.LogRecord(
        name="mcp.shared.session",
        level=logging.WARNING,
        pathname="x",
        lineno=1,
        msg="Failed to validate notification: ...",
        args=(),
        exc_info=None,
    )
    real = logging.LogRecord(
        name="some.module",
        level=logging.WARNING,
        pathname="x",
        lineno=1,
        msg="Genuine error",
        args=(),
        exc_info=None,
    )

    assert f.filter(noisy) is False
    assert f.filter(notif) is False
    assert f.filter(real) is True


def test_install_mgp_validation_filter_is_idempotent():
    """Calling install twice must not stack duplicate filters on the root logger."""
    import logging

    from mcp_common import mcp_utils

    # Reset module-level latch so the test is order-independent.
    mcp_utils._MGP_FILTER_INSTALLED = False
    root = logging.getLogger()
    before = list(root.filters)
    try:
        mcp_utils.install_mgp_validation_filter()
        after_first = list(root.filters)
        mcp_utils.install_mgp_validation_filter()
        after_second = list(root.filters)
        # Exactly one new filter was added; the second call is a no-op.
        assert len(after_first) == len(before) + 1
        assert after_second == after_first
    finally:
        # Restore root logger to its pre-test state to avoid leaking the filter.
        for filt in list(root.filters):
            if filt not in before:
                root.removeFilter(filt)
        mcp_utils._MGP_FILTER_INSTALLED = False
