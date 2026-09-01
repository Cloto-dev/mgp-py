"""`embed()` says nothing about why it returned nothing; `embed_with_outcome()` does.

An unconfigured client and a dead endpoint both make `embed()` return `None`. That is
enough to fall back on and not enough to tell anyone what to fix, so a caller that wants
to report the difference has had to send a second request of its own to find out — one
that can succeed while the real call fails, and that hits the endpoint again while it is
already struggling.

These tests pin the three things a caller needs from the real call: whether a request went
out, whether it produced embeddings, and safe evidence when it did not. They also pin what
must NOT change: `embed()` returns exactly what it always returned.
"""

import httpx
import pytest
from mcp_common.embedding_client import EmbeddingClient, EmbedOutcome

DIMENSION = 4
URL = "http://127.0.0.1:8401/embed"

# A URL carrying both of the places a credential can hide in one. Nothing here may reach
# the evidence string.
SECRET_USER = "hunter2"
SECRET_QUERY = "s3cr3t-token"
CREDENTIALED_URL = f"http://admin:{SECRET_USER}@127.0.0.1:8401/embed?token={SECRET_QUERY}"


class Response:
    def __init__(self, data: dict, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Server error '{self.status_code}' for url '{URL}'",
                request=httpx.Request("POST", URL),
                response=httpx.Response(self.status_code),
            )


class Transport:
    """Injected in place of httpx.AsyncClient. `behaviour` decides what one POST does."""

    def __init__(self, behaviour="ok", url: str = URL):
        self.behaviour = behaviour
        self.url = url
        self.post_count = 0

    async def post(self, url: str, json: dict = None, **kwargs):
        self.post_count += 1
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        if self.behaviour == "empty":
            return Response({"embeddings": []})
        if self.behaviour == "no-key":
            return Response({"dimensions": DIMENSION})
        if self.behaviour == "500":
            return Response({}, status_code=500)
        texts = (json or {}).get("texts", [])
        return Response({"embeddings": [[1.0] + [0.0] * (DIMENSION - 1) for _ in texts]})

    async def aclose(self):
        pass


def _client(behaviour="ok", *, mode: str = "http", url: str = URL) -> EmbeddingClient:
    client = EmbeddingClient(mode=mode, http_url=url, api_url=url, api_key="never-in-evidence")
    client._client = Transport(behaviour, url)
    return client


# --- what the caller gets to know ------------------------------------------------------


@pytest.mark.asyncio
async def test_no_backend_is_not_a_failure():
    """`mode=none` must be distinguishable from a backend that broke.

    This is the whole point of the outcome: the two cases return the same `None`, and a
    reader told "the embedding backend failed" when nothing was ever configured is being
    sent to debug a server that does not exist.
    """
    client = EmbeddingClient(mode="none")

    result, outcome = await client.embed_with_outcome(["hello"])

    assert result is None
    assert outcome == EmbedOutcome(attempted=False, ok=False, error=None)


@pytest.mark.asyncio
async def test_a_dead_endpoint_reports_attempted_and_why():
    client = _client(httpx.ConnectError("All connection attempts failed"))

    result, outcome = await client.embed_with_outcome(["hello"])

    assert result is None
    assert outcome.attempted is True
    assert outcome.ok is False
    # The three things the evidence exists to carry: which mode, which endpoint, what went
    # wrong. Asserted by content rather than by exact string so the message can be reworded.
    assert "mode=http" in outcome.error
    assert URL in outcome.error
    assert "ConnectError" in outcome.error


@pytest.mark.asyncio
async def test_an_http_error_status_reports_attempted_and_why():
    client = _client("500")

    result, outcome = await client.embed_with_outcome(["hello"])

    assert result is None
    assert outcome.attempted is True
    assert outcome.ok is False
    assert "HTTPStatusError" in outcome.error
    assert "500" in outcome.error


@pytest.mark.asyncio
@pytest.mark.parametrize("behaviour", ["empty", "no-key"])
async def test_a_2xx_that_carries_no_embeddings_is_a_failure(behaviour):
    """A success code is not a success.

    This is the case a second health probe gets wrong: the probe would receive the same
    2xx and pronounce the endpoint healthy while every real call comes back empty. Reading
    the real call instead makes the disagreement impossible.
    """
    client = _client(behaviour)

    result, outcome = await client.embed_with_outcome(["hello"])

    assert not result  # `[]` or `None` depending on the shape the backend sent
    assert outcome.attempted is True
    assert outcome.ok is False
    assert "no embeddings" in outcome.error


@pytest.mark.asyncio
async def test_success_reports_a_round_trip():
    client = _client()

    result, outcome = await client.embed_with_outcome(["hello"])

    assert result is not None and len(result) == 1
    assert outcome == EmbedOutcome(attempted=True, ok=True, error=None)


@pytest.mark.asyncio
async def test_a_cache_hit_is_ok_without_being_attempted():
    """`attempted` means a request went out, so a served-from-cache success says so."""
    client = _client()
    await client.embed_with_outcome(["hello"])

    result, outcome = await client.embed_with_outcome(["hello"])

    assert result is not None
    assert outcome == EmbedOutcome(attempted=False, ok=True, error=None)
    assert client._client.post_count == 1


@pytest.mark.asyncio
async def test_an_unsupported_mode_names_itself():
    client = _client(mode="telepathy")

    result, outcome = await client.embed_with_outcome(["hello"])

    assert result is None
    assert outcome.attempted is False
    assert outcome.ok is False
    assert "telepathy" in outcome.error


# --- what the evidence must never carry ------------------------------------------------


@pytest.mark.asyncio
async def test_evidence_drops_credentials_from_the_configured_url():
    """A credential in the endpoint must not reach a string built for a user to read.

    Both halves are checked because they leak by different routes: the userinfo and query
    are stripped from the endpoint this code prints, and the same substitution is applied
    to the exception text, which quotes the URL it was given verbatim.
    """
    client = _client(
        httpx.ConnectError(f"All connection attempts failed for {CREDENTIALED_URL}"),
        url=CREDENTIALED_URL,
    )

    _, outcome = await client.embed_with_outcome(["hello"])

    assert SECRET_USER not in outcome.error
    assert SECRET_QUERY not in outcome.error
    assert "admin" not in outcome.error
    # Still useful: the host and path survive, which is what identifies the endpoint.
    assert "127.0.0.1:8401/embed" in outcome.error


@pytest.mark.asyncio
async def test_evidence_never_carries_the_api_key():
    """`mode=api` is the mode that holds a secret, so its evidence is checked too.

    The failure is injected at `_embed_via_api` rather than at the transport because that
    method imports numpy first, and numpy is deliberately not a dependency of this package
    (only `mode="api"` consumers need it, and they install it themselves). Patching the
    method keeps this test about the evidence and not about the test environment.
    """
    client = _client(mode="api")

    async def refuse(_texts):
        raise httpx.ConnectError("All connection attempts failed")

    client._embed_via_api = refuse

    _, outcome = await client.embed_with_outcome(["hello"])

    assert outcome.attempted is True
    assert "never-in-evidence" not in outcome.error
    assert "mode=api" in outcome.error


# --- what must not have changed --------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "behaviour,expected_len",
    [("ok", 1), ("empty", 0), ("500", None), ("no-key", None)],
)
async def test_embed_returns_what_it_always_returned(behaviour, expected_len):
    """The call sites that read `embed()` must not be able to tell this changed.

    `expected_len` is the length of the returned list, or `None` when the old code
    returned `None`. The `empty` row is the one that matters and the one this test was
    written wrong for first: an `embeddings: []` response reached callers as `[]`, not
    as `None`. Both are falsy and no caller can tell them apart, which is exactly why
    substituting one for the other would have shipped unnoticed. The expectations here
    were taken by running the pre-change implementation, not by reading it.
    """
    client = _client(behaviour)

    result = await client.embed(["hello"])

    if expected_len is None:
        assert result is None
    else:
        assert result is not None and len(result) == expected_len


@pytest.mark.asyncio
async def test_embed_still_returns_none_when_unconfigured():
    assert await EmbeddingClient(mode="none").embed(["hello"]) is None


@pytest.mark.asyncio
async def test_embed_still_caches():
    """Delegation must not have moved the cache out from under the plain entry point."""
    client = _client()

    await client.embed(["hello"])
    await client.embed(["hello"])

    assert client._client.post_count == 1
    assert client.cache_hits == 1
    assert client.cache_misses == 1
