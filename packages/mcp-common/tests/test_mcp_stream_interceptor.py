"""Tests for ``mcp_common.mcp_stream_interceptor``.

The upstream ``test_streaming.py`` lives in ``cloto-mcp-servers/servers/tests/``
and exercises the interceptor as part of the full LLM streaming round-trip
in ``common.llm_provider``. Because ``llm_provider`` is not migrated until
Step 2-ε, this file covers the interceptor in isolation with a hand-rolled
mock ``StreamState`` so the module stays testable standalone.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import JSONRPCNotification, JSONRPCRequest
from mcp_common.mcp_stream_interceptor import (
    _ERROR_INVALID_PARAMS,
    _handle_mgp_cancel,
    _handle_mgp_gap,
    mgp_message_interceptor,
)


def _make_state(
    *,
    accumulated_text: str = "",
    chunk_buffer: list[tuple[int, dict]] | None = None,
) -> SimpleNamespace:
    """Build a duck-typed ``StreamState`` substitute.

    The interceptor only touches four attributes: ``cancel_event``,
    ``cancelled_reason``, ``accumulated_text``, and ``chunk_buffer``. We do
    not depend on the real dataclass (which ships with Step 2-ε).
    """
    return SimpleNamespace(
        cancel_event=asyncio.Event(),
        cancelled_reason="",
        accumulated_text=accumulated_text,
        chunk_buffer=list(chunk_buffer or []),
    )


# ── _handle_mgp_cancel ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_mgp_cancel_active_stream_sets_event_and_replies():
    """Active stream: set cancel event, remember the reason, reply with partial_result."""
    state = _make_state(accumulated_text="hello world")
    active_streams = {7: state}
    request = JSONRPCRequest(
        jsonrpc="2.0",
        id=42,
        method="mgp/stream/cancel",
        params={"request_id": 7, "reason": "user_abort"},
    )
    target = "mcp_common.mcp_stream_interceptor.write_mgp_method_response"
    with patch(target, new_callable=AsyncMock) as mock_resp:
        await _handle_mgp_cancel(request, AsyncMock(), active_streams)

    assert state.cancel_event.is_set()
    assert state.cancelled_reason == "user_abort"
    mock_resp.assert_awaited_once()
    args, _ = mock_resp.await_args
    # Signature: write_mgp_method_response(write_stream, request_id, payload).
    assert args[1] == 42
    assert args[2] == {
        "cancelled": True,
        "partial_result": {"content": "hello world"},
    }


@pytest.mark.asyncio
async def test_handle_mgp_cancel_inactive_stream_replies_not_active():
    """Unknown request_id: reply ``cancelled=False, reason='not_active'`` without raising."""
    request = JSONRPCRequest(
        jsonrpc="2.0",
        id=42,
        method="mgp/stream/cancel",
        params={"request_id": 999, "reason": "user_abort"},
    )
    target = "mcp_common.mcp_stream_interceptor.write_mgp_method_response"
    with patch(target, new_callable=AsyncMock) as mock_resp:
        await _handle_mgp_cancel(request, AsyncMock(), {})

    mock_resp.assert_awaited_once()
    args, _ = mock_resp.await_args
    assert args[1] == 42
    assert args[2] == {"cancelled": False, "reason": "not_active"}


@pytest.mark.asyncio
async def test_handle_mgp_cancel_rejects_non_int_request_id():
    """Malformed params (request_id not int): respond with -32602 INVALID_PARAMS."""
    state = _make_state()
    request = JSONRPCRequest(
        jsonrpc="2.0",
        id=42,
        method="mgp/stream/cancel",
        params={"request_id": "not-an-int"},
    )
    err_target = "mcp_common.mcp_stream_interceptor.write_mgp_method_response"
    write_err_target = "mcp_common.mgp_utils.write_mgp_method_error"
    with (
        patch(err_target, new_callable=AsyncMock) as mock_resp,
        patch(write_err_target, new_callable=AsyncMock) as mock_err,
    ):
        await _handle_mgp_cancel(request, AsyncMock(), {7: state})

    mock_resp.assert_not_awaited()
    mock_err.assert_awaited_once()
    args, _ = mock_err.await_args
    # write_mgp_method_error(write_stream, request_id, code, message, data=None).
    assert args[1] == 42
    assert args[2] == _ERROR_INVALID_PARAMS
    assert not state.cancel_event.is_set()


# ── _handle_mgp_gap ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_mgp_gap_retransmits_matching_indices():
    """For each missing index found in chunk_buffer, emit a stream.chunk retransmit."""
    state = _make_state(
        chunk_buffer=[
            (0, {"text": "alpha"}),
            (1, {"text": "beta"}),
            (2, {"text": "gamma"}),
        ],
    )
    notification = JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/mgp.stream.gap",
        params={"request_id": 7, "missing_indices": [2, 0]},
    )
    target = "mcp_common.mcp_stream_interceptor.write_mgp_stream_chunk"
    with patch(target, new_callable=AsyncMock) as mock_chunk:
        await _handle_mgp_gap(notification, AsyncMock(), {7: state})

    assert mock_chunk.await_count == 2
    indices = [kwargs["index"] for _, kwargs in mock_chunk.await_args_list]
    contents = [kwargs["content"] for _, kwargs in mock_chunk.await_args_list]
    # Indices are emitted in sorted order regardless of the request order.
    assert indices == [0, 2]
    assert contents == [{"text": "alpha"}, {"text": "gamma"}]
    # Every retransmit chunk is tagged via _mgp meta.
    for _, kwargs in mock_chunk.await_args_list:
        assert kwargs["mgp_meta"] == {"retransmit": True}
        assert kwargs["done"] is False


@pytest.mark.asyncio
async def test_handle_mgp_gap_emits_unrecoverable_for_evicted_index():
    """A missing index outside the buffer triggers ``gap_unrecoverable`` and stops."""
    state = _make_state(chunk_buffer=[(5, {"text": "late"})])
    notification = JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/mgp.stream.gap",
        params={"request_id": 7, "missing_indices": [1, 5]},
    )
    chunk_t = "mcp_common.mcp_stream_interceptor.write_mgp_stream_chunk"
    notif_t = "mcp_common.mcp_stream_interceptor.write_mgp_notification"
    with (
        patch(chunk_t, new_callable=AsyncMock) as mock_chunk,
        patch(notif_t, new_callable=AsyncMock) as mock_notif,
    ):
        await _handle_mgp_gap(notification, AsyncMock(), {7: state})

    # Sorted iteration hits index 1 first, finds it evicted, emits
    # gap_unrecoverable, and returns before processing index 5.
    mock_notif.assert_awaited_once()
    args, _ = mock_notif.await_args
    assert args[1] == "notifications/mgp.stream.gap_unrecoverable"
    assert args[2]["request_id"] == 7
    assert args[2]["missing_index"] == 1
    assert args[2]["reason"] == "chunk_evicted"
    mock_chunk.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_mgp_gap_emits_unrecoverable_for_completed_stream():
    """If the stream no longer exists, kernel gets a single ``stream_completed`` notice."""
    notification = JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/mgp.stream.gap",
        params={"request_id": 999, "missing_indices": [0]},
    )
    target = "mcp_common.mcp_stream_interceptor.write_mgp_notification"
    with patch(target, new_callable=AsyncMock) as mock_notif:
        await _handle_mgp_gap(notification, AsyncMock(), {})

    mock_notif.assert_awaited_once()
    args, _ = mock_notif.await_args
    assert args[1] == "notifications/mgp.stream.gap_unrecoverable"
    assert args[2] == {"request_id": 999, "reason": "stream_completed"}


@pytest.mark.asyncio
async def test_handle_mgp_gap_ignores_malformed_params():
    """Non-int request_id or non-list missing_indices: silent skip, no writes."""
    notification = JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/mgp.stream.gap",
        params={"request_id": "oops", "missing_indices": "not-a-list"},
    )
    chunk_t = "mcp_common.mcp_stream_interceptor.write_mgp_stream_chunk"
    notif_t = "mcp_common.mcp_stream_interceptor.write_mgp_notification"
    with (
        patch(chunk_t, new_callable=AsyncMock) as mock_chunk,
        patch(notif_t, new_callable=AsyncMock) as mock_notif,
    ):
        await _handle_mgp_gap(notification, AsyncMock(), {})

    mock_chunk.assert_not_awaited()
    mock_notif.assert_not_awaited()


# ── mgp_message_interceptor (top-level pump) ────────────────────────────────


@pytest.mark.asyncio
async def test_mgp_message_interceptor_forwards_non_mgp_messages():
    """Non-MGP traffic must reach ``inner_send`` byte-for-byte."""
    import anyio

    raw_send, raw_recv = anyio.create_memory_object_stream(2)
    inner_send, inner_recv = anyio.create_memory_object_stream(2)
    write_send = AsyncMock()

    plain = SimpleNamespace(
        message=SimpleNamespace(
            root=SimpleNamespace(jsonrpc="2.0", id=1, method="tools/list"),
        ),
    )
    await raw_send.send(plain)
    await raw_send.aclose()

    await mgp_message_interceptor(raw_recv, inner_send, write_send, {})

    forwarded = []
    async with inner_recv:
        async for item in inner_recv:
            forwarded.append(item)
    assert forwarded == [plain]
    write_send.send.assert_not_called()


@pytest.mark.asyncio
async def test_mgp_message_interceptor_dispatches_mgp_cancel():
    """A ``mgp/stream/cancel`` request is consumed and routed to ``_handle_mgp_cancel``."""
    import anyio

    raw_send, raw_recv = anyio.create_memory_object_stream(2)
    inner_send, inner_recv = anyio.create_memory_object_stream(2)
    write_send = AsyncMock()

    cancel = JSONRPCRequest(
        jsonrpc="2.0",
        id=9,
        method="mgp/stream/cancel",
        params={"request_id": 0, "reason": "test"},
    )
    msg = SimpleNamespace(message=SimpleNamespace(root=cancel))
    await raw_send.send(msg)
    await raw_send.aclose()

    target = "mcp_common.mcp_stream_interceptor._handle_mgp_cancel"
    with patch(target, new_callable=AsyncMock) as mock_handler:
        await mgp_message_interceptor(raw_recv, inner_send, write_send, {})

    mock_handler.assert_awaited_once()
    # The MGP request must NOT leak through to the inner stream.
    async with inner_recv:
        forwarded = [item async for item in inner_recv]
    assert forwarded == []
