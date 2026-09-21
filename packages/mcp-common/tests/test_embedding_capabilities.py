"""`capabilities()` asks the embedding server what produced its vectors.

Over HTTP the request carries texts and the server picks the model, so the name never
crosses the wire; a caller that falls back to its own configuration reads two different
models as one and keeps comparing vectors it should have rebuilt. These tests pin that a
fingerprint comes back only when the server established its whole identity, that every
other case is `None` (unknown, never "unchanged"), that a report contradicting itself is
refused rather than trusted halfway, and that the outcome says which case it was without
carrying a credential from the configured URL.
"""

import httpx
import pytest
from mcp_common.embedding_client import BackendIdentity, EmbeddingClient

URL = "http://127.0.0.1:8401/embed"
CAP_URL = "http://127.0.0.1:8401/capabilities"
SECRET_USER = "hunter2"
SECRET_QUERY = "s3cr3t-token"
CREDENTIALED_URL = f"http://admin:{SECRET_USER}@127.0.0.1:8401/embed?token={SECRET_QUERY}"

FIELDS = {
    "provider": "onnx_bge_m3",
    "model": "bge-m3",
    "dimensions": 1024,
    "window": 512,
    "pooling": "cls",
    "normalized": True,
    "digests": {"graph": "ac93a741", "tokenizer": "98d4a1d3"},
}
COMPLETE = {"identity": FIELDS, "incomplete": [], "fingerprint": "1:53c2228b"}


class Response:
    def __init__(self, data, status_code: int = 200, url: str = CAP_URL):
        self._data = data
        self.status_code = status_code
        self._url = url

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Client error '{self.status_code}' for url '{self._url}'",
                request=httpx.Request("GET", self._url),
                response=httpx.Response(self.status_code),
            )


class Transport:
    def __init__(self, reply):
        self.reply = reply
        self.gets: list[str] = []

    async def get(self, url: str, **kwargs):
        self.gets.append(url)
        if isinstance(self.reply, Exception):
            raise self.reply
        if isinstance(self.reply, Response):
            return self.reply
        return Response(self.reply, url=url)

    async def aclose(self):
        pass


def _client(reply, *, mode: str = "http", url: str = URL) -> tuple[EmbeddingClient, Transport]:
    client = EmbeddingClient(mode=mode, http_url=url, api_url=url, api_key="never-in-evidence")
    transport = Transport(reply)
    client._client = transport
    return client, transport


# --- an identity the server could establish -------------------------------------------


@pytest.mark.asyncio
async def test_the_report_is_read_from_the_route_beside_embed():
    client, transport = _client(COMPLETE)
    identity, outcome = await client.capabilities_with_outcome()

    assert transport.gets == [CAP_URL]
    assert outcome.attempted and outcome.ok
    assert identity == BackendIdentity(fingerprint="1:53c2228b", fields=FIELDS, incomplete=())


@pytest.mark.asyncio
async def test_the_query_string_travels_to_the_capability_route():
    """A configured token lives in the query, and the sibling route needs it too."""
    client, transport = _client(COMPLETE, url=CREDENTIALED_URL)
    await client.capabilities()

    assert transport.gets == [
        f"http://admin:{SECRET_USER}@127.0.0.1:8401/capabilities?token={SECRET_QUERY}"
    ]


@pytest.mark.asyncio
async def test_two_backends_that_differ_are_told_apart():
    """The whole point: same configured URL, different graph, different fingerprint."""
    first, _ = _client(COMPLETE)
    other = {**COMPLETE, "fingerprint": "1:b56bb130"}
    second, _ = _client(other)

    assert (await first.capabilities()).fingerprint != (await second.capabilities()).fingerprint


# --- what is not known ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_backend_issues_no_request_and_is_not_a_failure():
    client, transport = _client(COMPLETE, mode="none")
    identity, outcome = await client.capabilities_with_outcome()

    assert identity is None
    assert not outcome.attempted and not outcome.ok and outcome.error is None
    assert transport.gets == []


@pytest.mark.asyncio
async def test_api_mode_has_no_report_and_says_so_without_calling():
    """There is nothing to ask: the model this client sends is the one that answered."""
    client, transport = _client(COMPLETE, mode="api")
    identity, outcome = await client.capabilities_with_outcome()

    assert identity is None
    assert not outcome.attempted
    assert "mode=api has no capability report" == outcome.error
    assert transport.gets == []


@pytest.mark.asyncio
async def test_a_url_that_is_not_an_embed_route_is_not_guessed_at():
    client, transport = _client(COMPLETE, url="http://127.0.0.1:8401/v1/vectors")
    identity, outcome = await client.capabilities_with_outcome()

    assert identity is None
    assert not outcome.attempted
    assert "does not end in /embed" in outcome.error
    assert transport.gets == []


@pytest.mark.asyncio
async def test_a_server_without_the_route_is_named_as_older():
    client, _ = _client(Response({}, status_code=404))
    identity, outcome = await client.capabilities_with_outcome()

    assert identity is None
    assert outcome.attempted and not outcome.ok
    assert "predates the capability report" in outcome.error


@pytest.mark.parametrize("mentioned", [SECRET_USER, SECRET_QUERY])
@pytest.mark.asyncio
async def test_a_dead_endpoint_is_reported_without_credentials(mentioned):
    failure = httpx.ConnectError(f"All connection attempts failed for {CREDENTIALED_URL}")
    client, _ = _client(failure, url=CREDENTIALED_URL)
    identity, outcome = await client.capabilities_with_outcome()

    assert identity is None
    assert outcome.attempted and not outcome.ok
    assert mentioned not in outcome.error


# --- a report that contradicts itself -------------------------------------------------


@pytest.mark.asyncio
async def test_a_fingerprint_over_an_incomplete_identity_is_refused():
    """The contradiction that matters. A backend that cannot name its own graph must
    not be compared as though it could, however confident its fingerprint looks."""
    client, _ = _client({**COMPLETE, "incomplete": ["digests.graph"]})
    identity, outcome = await client.capabilities_with_outcome()

    assert identity is None
    assert outcome.attempted and not outcome.ok
    assert "carries a fingerprint while naming 1 component" in outcome.error


@pytest.mark.asyncio
async def test_an_identity_that_is_neither_complete_nor_incomplete_is_refused():
    """Nothing missing and nothing named leaves a caller unable to tell
    "complete" from "unknown"."""
    client, _ = _client({"identity": FIELDS, "incomplete": [], "fingerprint": None})
    identity, outcome = await client.capabilities_with_outcome()

    assert identity is None
    assert "says neither what it is nor what it is missing" in outcome.error


@pytest.mark.parametrize(
    "report",
    [
        [],
        "onnx_bge_m3",
        {"incomplete": [], "fingerprint": "1:a"},
        {"identity": ["model"], "incomplete": [], "fingerprint": "1:a"},
        {"identity": FIELDS, "incomplete": "digests.graph", "fingerprint": None},
        {"identity": FIELDS, "incomplete": [7], "fingerprint": None},
        {"identity": FIELDS, "incomplete": [], "fingerprint": ""},
        {"identity": FIELDS, "incomplete": [], "fingerprint": 12345},
        {"identity": FIELDS, "incomplete": [], "fingerprint": True},
    ],
)
@pytest.mark.asyncio
async def test_a_malformed_report_is_refused(report):
    client, _ = _client(report)
    identity, outcome = await client.capabilities_with_outcome()

    assert identity is None
    assert outcome.attempted and not outcome.ok


@pytest.mark.asyncio
async def test_an_incomplete_identity_is_returned_without_a_fingerprint():
    """A remote backend still says what it knows — it just may not be compared."""
    partial = {
        "identity": {"provider": "api_openai", "model": "text-embedding-3-small"},
        "incomplete": ["digests.graph", "digests.tokenizer", "window", "pooling"],
        "fingerprint": None,
    }
    client, _ = _client(partial)
    identity, outcome = await client.capabilities_with_outcome()

    assert outcome.attempted and outcome.ok
    assert identity.fingerprint is None
    assert identity.fields["model"] == "text-embedding-3-small"
    assert "digests.graph" in identity.incomplete


@pytest.mark.asyncio
async def test_two_unknown_backends_do_not_look_equal_through_the_fingerprint():
    """Both answer None, which is why None is not a value a caller may compare.
    What tells them apart is the fields, not the fingerprint."""
    one = {"identity": {"model": "one"}, "incomplete": ["digests.graph"], "fingerprint": None}
    other = {"identity": {"model": "other"}, "incomplete": ["digests.graph"], "fingerprint": None}
    first, _ = _client(one)
    second, _ = _client(other)

    a = await first.capabilities()
    b = await second.capabilities()
    assert a.fingerprint is b.fingerprint is None
    assert a.fields != b.fields


# --- the shared route derivation ------------------------------------------------------


@pytest.mark.asyncio
async def test_the_two_sibling_routes_are_derived_the_same_way():
    """`count_tokens` and `capabilities` share one derivation, so a URL either has
    both siblings or neither."""
    from mcp_common.embedding_client import _capabilities_url, _count_tokens_url

    for url in ["http://h/embed", "http://h/api/embed/", "http://h/embed?t=1", "http://h/vectors"]:
        assert (_count_tokens_url(url) is None) == (_capabilities_url(url) is None)
    assert _capabilities_url("http://h/api/embed/") == "http://h/api/capabilities"
