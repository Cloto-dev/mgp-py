"""Credential and request-context boundaries shared by independent consumers."""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from mcp_common import identity


def test_env_reference_is_resolved_without_accepting_partial_references(monkeypatch):
    monkeypatch.setenv("TEST_IDENTITY_SECRET", "test-secret-value")
    assert identity.resolve_env_token("${TEST_IDENTITY_SECRET}") == "test-secret-value"
    assert identity.resolve_env_token("literal-value") == "literal-value"
    for bad in ("pre${TEST_IDENTITY_SECRET}", "${TEST_IDENTITY_SECRET}suffix", "", "päss"):
        with pytest.raises(identity.CredentialError) as caught:
            identity.resolve_env_token(bad)
        assert bad not in str(caught.value) or bad == ""
    monkeypatch.delenv("TEST_IDENTITY_SECRET")
    with pytest.raises(identity.CredentialError, match="unset or empty"):
        identity.resolve_env_token("${TEST_IDENTITY_SECRET}")


def test_duplicate_and_reserved_credentials_are_rejected():
    entries = [("first-token", "first"), ("second-token", "second")]
    identity.validate_token_entries(entries)
    with pytest.raises(identity.CredentialError, match="duplicates"):
        identity.validate_token_entries(entries + [("first-token", "third")])
    with pytest.raises(identity.CredentialError, match="duplicates"):
        identity.validate_token_entries(entries, reserved_tokens=["second-token"])


def test_resolver_compares_all_entries_and_rejects_hostile_unicode(monkeypatch):
    entries = [("first-token", "first"), ("second-token", "second")]
    compared = []
    original = identity.hmac.compare_digest

    def compare(left, right):
        compared.append(right)
        return original(left, right)

    monkeypatch.setattr(identity.hmac, "compare_digest", compare)
    assert identity.resolve_token(entries, "first-token") == identity.Principal("first")
    assert compared == [b"first-token", b"second-token"]
    assert identity.resolve_token(entries, "second-token") == identity.Principal("second")
    assert identity.resolve_token(entries, "tökén") is None
    assert identity.resolve_token(entries, "") is None
    assert identity.resolve_token(entries, "unknown-token") is None


@pytest.mark.asyncio
async def test_context_isolated_between_tasks_and_consumers_and_restored_on_error():
    context = identity.PrincipalContext("test-first-app")
    other = identity.PrincipalContext("test-second-app")
    ready = asyncio.Event()
    seen = []

    async def worker(client):
        with context.bind(identity.Principal(client)):
            if client == "first":
                ready.set()
            await ready.wait()
            await asyncio.sleep(0)
            seen.append(context.get().client_id)
            assert other.get() is None
        assert context.get() is None

    await asyncio.gather(worker("first"), worker("second"))
    assert sorted(seen) == ["first", "second"]
    outer = identity.Principal("outer", issuer="issuer", subject="subject")
    with context.bind(outer):
        with pytest.raises(RuntimeError), context.bind(identity.Principal("inner")):
            raise RuntimeError("test error")
        assert context.get() == outer
    assert context.get() is None


@pytest.mark.asyncio
async def test_cancelled_task_resets_its_principal():
    context = identity.PrincipalContext("test-cancellation")
    ready = asyncio.Event()
    restored = []

    async def worker():
        try:
            with context.bind(identity.Principal("cancelled")):
                ready.set()
                await asyncio.Event().wait()
        finally:
            restored.append(context.get())

    task = asyncio.create_task(worker())
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert restored == [None]


def test_sync_checks_exact_source_and_preserves_unrelated_files(tmp_path):
    script = Path(__file__).resolve().parents[3] / "scripts/sync-identity.py"
    package = tmp_path / "consumer"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    unrelated = package / "other.py"
    unrelated.write_text("sentinel", encoding="utf-8")
    command = [sys.executable, str(script), "--target-package", str(package)]
    assert subprocess.run(command + ["--check"], capture_output=True).returncode == 1
    assert subprocess.run(command, capture_output=True).returncode == 0
    assert subprocess.run(command + ["--check"], capture_output=True).returncode == 0
    (package / "identity.py").write_text("drift", encoding="utf-8")
    assert subprocess.run(command + ["--check"], capture_output=True).returncode == 1
    assert unrelated.read_text(encoding="utf-8") == "sentinel"
