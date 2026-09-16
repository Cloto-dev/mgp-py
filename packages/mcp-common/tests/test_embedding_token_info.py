"""`count_tokens()` asks the embedding server where its window closes on each text.

A caller uses the answer to decide where to split stored text, so a wrong answer does not
fail loudly: it silently leaves part of a record outside every embedded node. These tests
pin that an answer is only returned when it agrees with its own text, that every other case
comes back as `None` (unknown, never "fits"), and that the outcome says which case it was
without carrying a credential from the configured URL.
"""

import httpx
import pytest
from mcp_common.embedding_client import EmbeddingClient, TokenInfo

URL = "http://127.0.0.1:8401/embed"
COUNT_URL = "http://127.0.0.1:8401/count_tokens"
SECRET_USER = "hunter2"
SECRET_QUERY = "s3cr3t-token"
CREDENTIALED_URL = f"http://admin:{SECRET_USER}@127.0.0.1:8401/embed?token={SECRET_QUERY}"


class Response:
    def __init__(self, data, status_code: int = 200, url: str = COUNT_URL):
        self._data = data
        self.status_code = status_code
        self._url = url

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Client error '{self.status_code}' for url '{self._url}'",
                request=httpx.Request("POST", self._url),
                response=httpx.Response(self.status_code),
            )


class Transport:
    def __init__(self, reply):
        self.reply = reply
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url: str, json: dict = None, **kwargs):
        self.posts.append((url, json))
        if isinstance(self.reply, Exception):
            raise self.reply
        if isinstance(self.reply, Response):
            return self.reply
        return Response(self.reply(json["texts"]) if callable(self.reply) else self.reply, url=url)

    async def aclose(self):
        pass


def _entry(text: str, window: int = 8) -> dict:
    count = len(text.split()) + 1
    truncated = count > window
    end = len(" ".join(text.split()[: window - 1])) if truncated else len(text)
    return {"count": count, "window": window, "truncated": truncated, "window_end_char": end}


def _client(reply, *, mode: str = "http", url: str = URL) -> tuple[EmbeddingClient, Transport]:
    client = EmbeddingClient(mode=mode, http_url=url, api_url=url, api_key="never-in-evidence")
    transport = Transport(reply)
    client._client = transport
    return client, transport


# --- a report that agrees with itself -------------------------------------------------


@pytest.mark.asyncio
async def test_the_report_is_read_from_the_route_beside_embed():
    texts = ["one two three", "a b c d e f g h i j"]
    client, transport = _client(lambda ts: {"token_info": [_entry(t) for t in ts]})

    result, outcome = await client.count_tokens_with_outcome(texts)

    assert transport.posts == [(COUNT_URL, {"texts": texts})]
    assert outcome.ok is True and outcome.attempted is True
    assert result == [
        TokenInfo(count=4, window=8, truncated=False, window_end_char=len(texts[0])),
        TokenInfo(count=11, window=8, truncated=True, window_end_char=len("a b c d e f g")),
    ]
    assert await client.count_tokens(texts) == result


@pytest.mark.asyncio
async def test_the_query_string_travels_to_the_count_route():
    client, transport = _client(
        lambda ts: {"token_info": [_entry(t) for t in ts]}, url=CREDENTIALED_URL
    )
    await client.count_tokens(["x"])
    assert transport.posts[0][0] == CREDENTIALED_URL.replace("/embed?", "/count_tokens?")


# --- unknown is not "fits" ------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_backend_issues_no_request_and_is_not_a_failure():
    client = EmbeddingClient(mode="none")
    result, outcome = await client.count_tokens_with_outcome(["x"])
    assert result is None
    assert (outcome.attempted, outcome.ok, outcome.error) == (False, False, None)


@pytest.mark.asyncio
async def test_api_mode_has_no_report_and_says_so_without_calling():
    client, transport = _client({"token_info": []}, mode="api")
    result, outcome = await client.count_tokens_with_outcome(["x"])
    assert result is None and transport.posts == []
    assert outcome.attempted is False and "mode=api" in outcome.error


@pytest.mark.asyncio
async def test_a_url_that_is_not_an_embed_route_is_not_guessed_at():
    client, transport = _client({"token_info": []}, url="http://127.0.0.1:8401/v1/vectors")
    result, outcome = await client.count_tokens_with_outcome(["x"])
    assert result is None and transport.posts == []
    assert outcome.attempted is False and "/embed" in outcome.error


@pytest.mark.asyncio
async def test_a_provider_that_cannot_see_its_tokens_is_unknown():
    client, _ = _client({"token_info": None})
    result, outcome = await client.count_tokens_with_outcome(["x"])
    assert result is None
    assert outcome.attempted is True and outcome.ok is False
    assert "cannot see its tokens" in outcome.error


@pytest.mark.asyncio
async def test_a_server_without_the_route_is_named_as_older():
    client, _ = _client(Response({}, status_code=404))
    result, outcome = await client.count_tokens_with_outcome(["x"])
    assert result is None and outcome.ok is False
    assert "predates the token report" in outcome.error


@pytest.mark.parametrize(
    "mentioned",
    [CREDENTIALED_URL, CREDENTIALED_URL.replace("/embed?", "/count_tokens?")],
    ids=["embed-url", "count-url"],
)
@pytest.mark.asyncio
async def test_a_dead_endpoint_is_reported_without_credentials(mentioned):
    client, _ = _client(httpx.ConnectError(f"cannot reach {mentioned}"), url=CREDENTIALED_URL)
    result, outcome = await client.count_tokens_with_outcome(["x"])
    assert result is None and outcome.attempted is True
    assert "ConnectError" in outcome.error and "/count_tokens" in outcome.error
    assert SECRET_USER not in outcome.error and SECRET_QUERY not in outcome.error


# --- a report that contradicts itself is refused --------------------------------------

TEXT = "a b c"
GOOD = {"count": 4, "window": 8, "truncated": False, "window_end_char": len(TEXT)}


@pytest.mark.parametrize(
    "token_info",
    [
        "not a list",
        [],  # wrong cardinality
        [GOOD, GOOD],  # wrong cardinality
        ["not an object"],
        [{**GOOD, "count": True}],  # bool is not a count
        [{**GOOD, "window": "8"}],
        [{k: v for k, v in GOOD.items() if k != "truncated"}],
        [{**GOOD, "window": 0}],
        [{**GOOD, "count": -1}],
        [{**GOOD, "window_end_char": len(TEXT) + 1}],  # past the end of its text
        [{**GOOD, "count": 9}],  # over the window but not marked truncated
        [{**GOOD, "truncated": True}],  # marked truncated while inside the window
        [{**GOOD, "window_end_char": 2}],  # not truncated yet ends early
    ],
)
@pytest.mark.asyncio
async def test_a_malformed_or_inconsistent_report_is_refused(token_info):
    client, _ = _client({"token_info": token_info})
    result, outcome = await client.count_tokens_with_outcome([TEXT])
    assert result is None
    assert outcome.attempted is True and outcome.ok is False
    assert "EmbeddingResponseError" in outcome.error


@pytest.mark.asyncio
async def test_a_consistent_truncated_entry_is_accepted_at_its_boundaries():
    info = [{"count": 9, "window": 8, "truncated": True, "window_end_char": 0}]
    client, _ = _client({"token_info": info})
    [entry] = await client.count_tokens([TEXT])
    assert entry.truncated is True and entry.window_end_char == 0
