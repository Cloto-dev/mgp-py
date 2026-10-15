"""Endpoint credentials stay out of error text and logs; a nested body is a failure.

A credential configured in the endpoint URL (userinfo, or a key in the query
string) used to be removed from exception text only where the configured
spelling appeared verbatim. httpx quotes its own normalised spelling of the URL
-- host lower-cased, default port dropped, userinfo and query percent-encoded --
so any other spelling passed through to the failure evidence and the WARNING
log, and httpx's own INFO request line carried the URL unredacted on every
request. Separately, a backend body nested deeper than the JSON decoder can
follow raised RecursionError, which no failure boundary caught.
"""

import logging

import httpx
import pytest
from mcp_common import embedding_client
from mcp_common.embedding_client import EmbeddingClient


def _failing(status: int, **kw) -> EmbeddingClient:
    client = EmbeddingClient(**kw)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(status))
    )
    return client


# (configured URL, secrets that must not appear in any spelling, host that must)
_SPELLINGS = [
    ("http://Embed.Local:8401/embed?token=SECRET1", ["SECRET1"], "embed.local"),
    ("http://embed.local:80/embed?token=SECRET2", ["SECRET2"], "embed.local"),
    (
        "http://svc:PASSWORD3@LOCALHOST:8401/embed?key=SECRET3",
        ["PASSWORD3", "SECRET3"],
        "localhost",
    ),
    ("http://svc:Pw@SECRET4@embed.local/embed?k=v", ["SECRET4"], "embed.local"),
    ("http://embed.local:8401/embed?token=SE CRET5", ["CRET5"], "embed.local"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,secrets,host", _SPELLINGS, ids=[f"s{i}" for i in range(len(_SPELLINGS))]
)
@pytest.mark.parametrize("call", ["embed", "capabilities", "count_tokens"])
async def test_failure_evidence_carries_no_credential_in_any_spelling(
    caplog, url, secrets, host, call
):
    client = _failing(503, mode="http", http_url=url)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="mcp_common.embedding_client"):
        if call == "embed":
            _, outcome = await client.embed_with_outcome(["x"])
        elif call == "capabilities":
            _, outcome = await client.capabilities_with_outcome()
        else:
            _, outcome = await client.count_tokens_with_outcome(["x"])
    assert outcome.attempted and not outcome.ok
    # Control: the evidence still says where and how it failed, so a sanitiser that
    # passed by dropping the message would not look like this.
    assert host in outcome.error.lower() and "503" in outcome.error, outcome.error
    for secret in secrets:
        assert secret not in outcome.error, outcome.error
        assert secret not in caplog.text, caplog.text


@pytest.mark.asyncio
async def test_api_mode_evidence_carries_no_credential_with_uppercase_host_and_default_port(
    caplog,
):
    client = _failing(
        401,
        mode="api",
        api_url="https://user:PASSWORD6@Api.Example.com:443/v1/embeddings?key=SECRET6",
        api_key="k",
        model="m",
    )
    with caplog.at_level(logging.WARNING):
        _, outcome = await client.embed_with_outcome(["x"])
    assert "api.example.com" in outcome.error.lower() and "401" in outcome.error, outcome.error
    for secret in ("PASSWORD6", "SECRET6"):
        assert secret not in outcome.error, outcome.error
        assert secret not in caplog.text, caplog.text


@pytest.mark.asyncio
async def test_httpx_request_log_line_carries_no_credential(caplog):
    client = EmbeddingClient(
        mode="http", http_url="http://svc:PASSWORD7@embed.local:8401/embed?token=SECRET7"
    )
    await client.initialize()
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
        )
    )
    caplog.clear()
    with caplog.at_level(logging.INFO):
        vectors = await client.embed(["hello"])
    await client._client.aclose()
    assert vectors == [[0.1, 0.2]]
    request_lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    # Control: httpx did log the request, so the assertion below is about the
    # line's content, not about its absence.
    assert any("HTTP Request: POST" in line and "embed.local" in line for line in request_lines), (
        request_lines
    )
    assert "PASSWORD7" not in caplog.text and "SECRET7" not in caplog.text, request_lines


def test_redact_urls_keeps_a_url_without_credentials_and_cleans_one_with_them():
    text = (
        "GET http://plain.example/path failed; "
        "retry at 'https://u:SECRET8@Other.Example:443/x?k=SECRET9' later"
    )
    cleaned = embedding_client.redact_urls(text)
    assert "http://plain.example/path" in cleaned
    assert "other.example" in cleaned.lower()
    assert "SECRET8" not in cleaned and "SECRET9" not in cleaned, cleaned


# Far below the default response budget, far above the decoder's nesting limit.
_DEPTH = 100_000
_NESTED_EMBED = ('{"embeddings":' + "[" * _DEPTH + "]" * _DEPTH + "}").encode()
_NESTED_PLAIN = ("[" * _DEPTH + "]" * _DEPTH).encode()


def _nested_client(*, capabilities="404", max_response_bytes=None):
    def handler(request):
        path = request.url.path
        json_type = {"content-type": "application/json"}
        if path.endswith("/capabilities"):
            if capabilities == "nested":
                return httpx.Response(200, content=_NESTED_PLAIN, headers=json_type)
            return httpx.Response(404)
        if path.endswith("/count_tokens"):
            return httpx.Response(200, content=_NESTED_PLAIN, headers=json_type)
        return httpx.Response(200, content=_NESTED_EMBED, headers=json_type)

    kwargs = {} if max_response_bytes is None else {"max_response_bytes": max_response_bytes}
    client = EmbeddingClient(mode="http", http_url="http://embedding.invalid/embed", **kwargs)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("max_response_bytes", [None, 0], ids=["budgeted", "unbudgeted"])
async def test_a_deeply_nested_embed_body_is_a_failed_outcome_not_an_escape(max_response_bytes):
    client = _nested_client(max_response_bytes=max_response_bytes)
    vectors, outcome = await client.embed_with_outcome(["x"])
    assert vectors is None
    assert outcome.attempted is True and outcome.ok is False, outcome
    assert "nest" in (outcome.error or ""), outcome


@pytest.mark.asyncio
async def test_a_deeply_nested_capability_report_is_a_failed_outcome():
    report, outcome = await _nested_client(capabilities="nested").capabilities_with_outcome()
    assert report is None
    assert outcome.attempted is True and outcome.ok is False, outcome


@pytest.mark.asyncio
async def test_a_deeply_nested_token_report_is_a_failed_outcome():
    report, outcome = await _nested_client().count_tokens_with_outcome(["x"])
    assert report is None
    assert outcome.attempted is True and outcome.ok is False, outcome
