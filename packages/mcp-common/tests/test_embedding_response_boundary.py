"""What the embedding client accepts from a backend, and what it refuses.

The backend sits outside this process's authentication boundary, so its response
is untrusted input. These tests pin the refusals: each one names a response that
must not reach a caller, because a caller packs what it is given into storage and
a bad vector there is not recoverable by reading it back.

Every rejection is an ``EmbeddingResponseError``, which is a ``ValueError``, so it
reaches existing callers through the failure path they already have.
"""

import math
import struct

import pytest
from mcp_common.embedding_client import (
    DEFAULT_MAX_BATCH_SIZE,
    DEFAULT_MAX_DIMENSION,
    EmbeddingClient,
    EmbeddingResponseError,
)

DIM = 8


class StubResponse:
    """A backend reply. `content` and `headers` are what the byte budget reads."""

    def __init__(self, payload, *, body: bytes | None = None, declared: str | None = None):
        self._payload = payload
        self.status_code = 200
        self.content = body if body is not None else b"{}"
        self.headers = {} if declared is None else {"Content-Length": declared}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class StubTransport:
    def __init__(self, response):
        self._response = response
        self.call_count = 0

    async def post(self, url, json=None, **kwargs):
        self.call_count += 1
        return self._response

    async def aclose(self):
        pass


def _client(response, **kwargs) -> EmbeddingClient:
    client = EmbeddingClient(mode="http", http_url="http://localhost:8401/embed", **kwargs)
    client._client = StubTransport(response)
    return client


def _vector(width=DIM, fill=0.5):
    return [fill] * width


# ---------------------------------------------------------------------------
# Accepted — the shapes that must keep working (the "no regression" side)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_vector_is_returned_unchanged():
    client = _client(StubResponse({"embeddings": [_vector()]}))
    assert await client.embed(["hello"]) == [_vector()]


@pytest.mark.asyncio
async def test_dimension_one_is_accepted():
    client = _client(StubResponse({"embeddings": [[0.25]]}))
    assert await client.embed(["hello"]) == [[0.25]]


@pytest.mark.asyncio
async def test_expected_dimension_is_accepted_when_it_matches():
    client = _client(StubResponse({"embeddings": [_vector()]}), expected_dimension=DIM)
    assert await client.embed(["hello"]) == [_vector()]


@pytest.mark.asyncio
async def test_integer_elements_are_accepted_as_numbers():
    """JSON has one number type; a backend may serialise 0.0 as 0."""
    client = _client(StubResponse({"embeddings": [[0, 1, -1] + [0] * (DIM - 3)]}))
    result = await client.embed(["hello"])
    assert result == [[0.0, 1.0, -1.0] + [0.0] * (DIM - 3)]


@pytest.mark.asyncio
async def test_full_batch_is_accepted():
    payload = {"embeddings": [_vector() for _ in range(4)]}
    client = _client(StubResponse(payload))
    assert len(await client.embed(["a", "b", "c", "d"])) == 4


@pytest.mark.asyncio
async def test_empty_embeddings_list_keeps_its_existing_report():
    """A 2xx carrying no embeddings is already reported as a failure, with its own
    wording. Validation must not take that case over and change the message."""
    client = _client(StubResponse({"embeddings": []}))
    result, outcome = await client.embed_with_outcome(["hello"])
    assert result == []
    assert outcome.ok is False
    assert "returned no embeddings" in outcome.error


# ---------------------------------------------------------------------------
# Refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_element",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "inf", "-inf"],
)
async def test_non_finite_element_is_refused(bad_element):
    payload = {"embeddings": [[bad_element] + _vector(DIM - 1)]}
    client = _client(StubResponse(payload))
    with pytest.raises(EmbeddingResponseError, match="not finite"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_finite_but_unstorable_element_is_refused():
    """1e300 is a finite float64 and an inf float32. The store is float32."""
    with pytest.raises(OverflowError):
        struct.pack("<f", 1e300)
    client = _client(StubResponse({"embeddings": [[1e300] + _vector(DIM - 1)]}))
    with pytest.raises(EmbeddingResponseError, match="float32"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_string_inside_vector_is_refused():
    client = _client(StubResponse({"embeddings": [["0.5"] + _vector(DIM - 1)]}))
    with pytest.raises(EmbeddingResponseError, match="expected a number"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_bool_inside_vector_is_refused():
    """bool is a subclass of int, so a bare numeric check would pack True as 1.0."""
    client = _client(StubResponse({"embeddings": [[True] + _vector(DIM - 1)]}))
    with pytest.raises(EmbeddingResponseError, match="expected a number"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_null_inside_vector_is_refused():
    client = _client(StubResponse({"embeddings": [[None] + _vector(DIM - 1)]}))
    with pytest.raises(EmbeddingResponseError, match="expected a number"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_zero_length_vector_is_refused():
    client = _client(StubResponse({"embeddings": [[]]}))
    with pytest.raises(EmbeddingResponseError, match="is empty"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_million_dimensional_vector_is_refused_before_packing():
    width = DEFAULT_MAX_DIMENSION + 1
    client = _client(StubResponse({"embeddings": [_vector(width)]}))
    with pytest.raises(EmbeddingResponseError, match="dimension cap"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_batch_count_mismatch_is_refused():
    """Two vectors for one text: zip() would pair them silently."""
    client = _client(StubResponse({"embeddings": [_vector(), _vector()]}))
    with pytest.raises(EmbeddingResponseError, match="2 vectors for 1 texts"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_partial_embedding_list_is_refused():
    """The dangerous direction: a short list keeps the wrong text's vector."""
    client = _client(StubResponse({"embeddings": [_vector(), _vector()]}))
    with pytest.raises(EmbeddingResponseError, match="2 vectors for 3 texts"):
        await client._embed_via_http(["a", "b", "c"])


@pytest.mark.asyncio
async def test_oversized_batch_is_refused():
    payload = {"embeddings": [_vector(1) for _ in range(DEFAULT_MAX_BATCH_SIZE + 1)]}
    client = _client(StubResponse(payload))
    with pytest.raises(EmbeddingResponseError, match="vector cap"):
        await client._embed_via_http(["x"] * (DEFAULT_MAX_BATCH_SIZE + 1))


@pytest.mark.asyncio
async def test_expected_dimension_mismatch_is_refused():
    client = _client(StubResponse({"embeddings": [_vector(DIM + 1)]}), expected_dimension=DIM)
    with pytest.raises(EmbeddingResponseError, match=f"expected {DIM}"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_nested_malformed_shape_is_refused():
    client = _client(StubResponse({"embeddings": [{"vector": _vector()}]}))
    with pytest.raises(EmbeddingResponseError, match="expected a list of numbers"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_embeddings_not_a_list_is_refused():
    client = _client(StubResponse({"embeddings": "not-a-list"}))
    with pytest.raises(EmbeddingResponseError, match="expected a list of vectors"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_response_not_an_object_is_refused():
    client = _client(StubResponse([_vector()]))
    with pytest.raises(EmbeddingResponseError, match="expected an object"):
        await client._embed_via_http(["hello"])


# ---------------------------------------------------------------------------
# Byte budget (step 1 — before the parse)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declared_oversized_body_is_refused_without_parsing():
    """A backend that announces a huge body is refused on its own word."""

    class ExplodingResponse(StubResponse):
        def json(self):
            raise AssertionError("parsed a body that was over the budget")

    response = ExplodingResponse({"embeddings": [_vector()]}, declared="999999999")
    client = _client(response, max_response_bytes=1024)
    with pytest.raises(EmbeddingResponseError, match="declares 999999999 bytes"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_received_oversized_body_is_refused_when_length_is_not_declared():
    response = StubResponse({"embeddings": [_vector()]}, body=b"x" * 2048)
    client = _client(response, max_response_bytes=1024)
    with pytest.raises(EmbeddingResponseError, match="over the 1024-byte budget"):
        await client._embed_via_http(["hello"])


@pytest.mark.asyncio
async def test_body_within_budget_is_parsed():
    response = StubResponse({"embeddings": [_vector()]}, body=b"x" * 16, declared="16")
    client = _client(response, max_response_bytes=1024)
    assert await client._embed_via_http(["hello"]) == [_vector()]


@pytest.mark.asyncio
async def test_unparsable_declared_length_does_not_break_the_call():
    response = StubResponse({"embeddings": [_vector()]}, declared="not-a-number")
    client = _client(response)
    assert await client._embed_via_http(["hello"]) == [_vector()]


# ---------------------------------------------------------------------------
# Dimension consistency (step 7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_valid_response_pins_the_dimension():
    """A backend that changes model mid-process would otherwise write two
    incompatible widths into one store."""
    client = EmbeddingClient(mode="http", http_url="http://localhost:8401/embed")
    client._client = StubTransport(StubResponse({"embeddings": [_vector(DIM)]}))
    assert await client._embed_via_http(["first"]) == [_vector(DIM)]

    client._client = StubTransport(StubResponse({"embeddings": [_vector(DIM * 2)]}))
    with pytest.raises(EmbeddingResponseError, match=f"expected {DIM}"):
        await client._embed_via_http(["second"])


# ---------------------------------------------------------------------------
# How a rejection reaches a caller
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejection_is_a_valueerror_so_existing_callers_catch_it():
    assert issubclass(EmbeddingResponseError, ValueError)


@pytest.mark.asyncio
async def test_rejection_surfaces_as_a_failed_outcome_not_an_exception():
    """embed() returns None and the outcome carries the reason — the same shape a
    transport failure already produces, so no caller needs a new branch."""
    client = _client(StubResponse({"embeddings": [[float("nan")] + _vector(DIM - 1)]}))
    result, outcome = await client.embed_with_outcome(["hello"])
    assert result is None
    assert outcome.attempted is True
    assert outcome.ok is False
    assert "not finite" in outcome.error


@pytest.mark.asyncio
async def test_a_refused_response_is_not_cached():
    """A cached bad vector would outlive the backend fault that produced it."""
    client = _client(StubResponse({"embeddings": [[float("nan")] + _vector(DIM - 1)]}))
    await client.embed(["hello"])
    assert client._cache_get("hello") is None


@pytest.mark.asyncio
async def test_nan_never_reaches_the_packer():
    """The end the finding is about: what a caller would have stored."""
    client = _client(StubResponse({"embeddings": [[float("nan")] + _vector(DIM - 1)]}))
    embeddings = await client.embed(["hello"])
    assert embeddings is None
    packed = EmbeddingClient.pack_embedding(_vector())
    assert all(math.isfinite(v) for v in EmbeddingClient.unpack_embedding(packed))
