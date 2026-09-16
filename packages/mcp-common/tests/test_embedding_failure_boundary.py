"""Three defects fixed in a downstream vendored copy of this client, brought back here.

The copy that one server ships had diverged from this library: it carried these fixes and
this library did not, so the next mirror of the library into that server would have removed
them again. The tests move with the fixes.

1. The OpenAI-compatible path paired vectors with inputs by arrival order. The API documents
   `index` as each embedding's position in the input list and does not promise the order
   items arrive in, so an out-of-order answer stored one text's vector against another text
   with ok=true. Vectors are now placed by `index`, and a response whose indices are not
   exactly 0..n-1 is refused.
2. `float()` raises OverflowError for an integer with more digits than float64 holds. That is
   an ArithmeticError, outside the failure boundary's except tuple, so it escaped
   `embed_with_outcome` instead of becoming a failed outcome.
3. A failed request was logged with the raw exception before the sanitised evidence was built.
   httpx puts the request URL in its own message, so credentials in a configured URL reached
   the log while the returned error was already clean. The token report's failure path had
   the same shape and is covered here too.
"""

import logging

import httpx
import pytest
from mcp_common.embedding_client import EmbeddingClient


def _api_client(handler, url="https://api.example/v1/embeddings"):
    client = EmbeddingClient(mode="api", api_url=url, api_key="k", model="m")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def _api_answering(items):
    def handler(request):
        return httpx.Response(200, json={"data": items})

    return handler


# --- 1. pairing by index ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_out_of_order_api_answer_pairs_each_text_with_its_own_vector():
    vectors, outcome = await _api_client(
        _api_answering(
            [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ]
        )
    ).embed_with_outcome(["first", "second"])

    assert outcome.ok, outcome
    # The api path L2-normalises, so these unit vectors come back unchanged.
    assert vectors[0] == pytest.approx([1.0, 0.0]), vectors
    assert vectors[1] == pytest.approx([0.0, 1.0]), vectors


@pytest.mark.asyncio
async def test_an_in_order_api_answer_still_lands_where_it_always_did():
    """Control: the ordinary case is what the fix must not move."""
    vectors, outcome = await _api_client(
        _api_answering(
            [
                {"index": 0, "embedding": [1.0, 0.0]},
                {"index": 1, "embedding": [0.0, 1.0]},
            ]
        )
    ).embed_with_outcome(["first", "second"])

    assert outcome.ok, outcome
    assert vectors[0] == pytest.approx([1.0, 0.0]), vectors
    assert vectors[1] == pytest.approx([0.0, 1.0]), vectors


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "items,why",
    [
        (
            [{"embedding": [1.0, 0.0]}, {"embedding": [0.0, 1.0]}],
            "no index at all: the correspondence cannot be established",
        ),
        (
            [{"index": 0, "embedding": [1.0, 0.0]}, {"index": 0, "embedding": [0.0, 1.0]}],
            "a duplicated index leaves one input unanswered",
        ),
        (
            [{"index": 0, "embedding": [1.0, 0.0]}, {"index": 7, "embedding": [0.0, 1.0]}],
            "an index outside 0..n-1 names no input",
        ),
        (
            [{"index": "0", "embedding": [1.0, 0.0]}, {"index": "1", "embedding": [0.0, 1.0]}],
            "a string is not a position",
        ),
        (
            [{"index": False, "embedding": [1.0, 0.0]}, {"index": True, "embedding": [0.0, 1.0]}],
            "bool is an int subclass and would otherwise index slots 0 and 1",
        ),
    ],
)
async def test_an_api_answer_whose_indices_are_not_a_permutation_is_refused(items, why):
    vectors, outcome = await _api_client(_api_answering(items)).embed_with_outcome(
        ["first", "second"]
    )

    assert vectors is None, f"{why}: {vectors}"
    assert outcome.attempted and not outcome.ok, f"{why}: {outcome}"
    assert "index" in outcome.error, f"{why}: {outcome.error}"


# --- 2. an oversized integer --------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_integer_too_large_for_float64_stays_inside_the_failure_boundary():
    def handler(request):
        # Written as bytes because JSON has no integer ceiling: a body may carry more
        # digits than float64 holds.
        return httpx.Response(
            200,
            content=b'{"data": [{"index": 0, "embedding": [1' + b"0" * 400 + b", 1.0]}]}",
        )

    vectors, outcome = await _api_client(handler).embed_with_outcome(["x"])

    assert vectors is None, vectors
    assert outcome.attempted and not outcome.ok, outcome
    assert "float64" in outcome.error, outcome.error


# --- 3. credentials in the log ------------------------------------------------------------

SECRET_PASSWORD = "s3cretpw"
SECRET_QUERY = "qsecret"


def _failing(request):
    return httpx.Response(500, text="boom")


@pytest.mark.asyncio
async def test_a_failed_embed_keeps_endpoint_credentials_out_of_the_log(caplog):
    client = _api_client(
        _failing, url=f"https://user:{SECRET_PASSWORD}@api.example/v1/embeddings?key={SECRET_QUERY}"
    )

    with caplog.at_level(logging.WARNING):
        vectors, outcome = await client.embed_with_outcome(["x"])

    assert vectors is None and outcome.attempted and not outcome.ok
    # The returned value was already clean before the fix; it is the control showing the
    # sanitiser works, so a log assertion passing because the line vanished looks different.
    assert SECRET_PASSWORD not in outcome.error and SECRET_QUERY not in outcome.error
    assert "POST" in outcome.error and "api.example" in outcome.error
    assert SECRET_PASSWORD not in caplog.text and SECRET_QUERY not in caplog.text, caplog.text
    assert "Embedding request failed" in caplog.text, "the failure stopped being reported at all"


@pytest.mark.asyncio
async def test_a_failed_token_report_keeps_endpoint_credentials_out_of_the_log(caplog):
    client = EmbeddingClient(
        mode="http",
        http_url=f"http://user:{SECRET_PASSWORD}@127.0.0.1:8401/embed?key={SECRET_QUERY}",
    )
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_failing))

    with caplog.at_level(logging.WARNING):
        report, outcome = await client.count_tokens_with_outcome(["x"])

    assert report is None and outcome.attempted and not outcome.ok
    assert SECRET_PASSWORD not in outcome.error and SECRET_QUERY not in outcome.error
    assert "/count_tokens" in outcome.error
    assert SECRET_PASSWORD not in caplog.text and SECRET_QUERY not in caplog.text, caplog.text
    assert "Token count request failed" in caplog.text, "the failure stopped being reported at all"
