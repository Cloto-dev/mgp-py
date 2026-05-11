"""Unit tests for mcp_common.no_persist."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from mcp_common import no_persist


@pytest.fixture(autouse=True)
def _reset_state():
    # Module state must not leak across tests.
    no_persist._no_persist_until = None
    yield
    no_persist._no_persist_until = None


# ----- pause / resume / is_paused / status shape -----


def test_fresh_module_state_is_unpaused():
    assert no_persist.is_paused() is False
    assert no_persist.status() == {
        "paused": False,
        "expires_at": None,
        "ttl_remaining_seconds": None,
    }


def test_pause_arms_flag_and_returns_expected_shape():
    out = no_persist.pause(ttl_seconds=120)
    assert out["paused"] is True
    assert out["ttl_seconds"] == 120
    assert isinstance(out["expires_at"], str)
    # Parse expires_at and verify it's roughly now + 120s.
    expires = datetime.fromisoformat(out["expires_at"])
    delta = (expires - datetime.now(UTC)).total_seconds()
    assert 110 < delta <= 120
    assert no_persist.is_paused() is True


def test_resume_clears_flag_and_reports_was_active_true():
    no_persist.pause(ttl_seconds=60)
    out = no_persist.resume()
    assert out == {"paused": False, "was_active": True}
    assert no_persist.is_paused() is False


def test_resume_when_unpaused_reports_was_active_false():
    out = no_persist.resume()
    assert out == {"paused": False, "was_active": False}


def test_status_shape_when_paused_includes_remaining_seconds():
    no_persist.pause(ttl_seconds=300)
    s = no_persist.status()
    assert s["paused"] is True
    assert isinstance(s["expires_at"], str)
    assert 290 <= s["ttl_remaining_seconds"] <= 300


# ----- TTL lazy decay -----


def test_lazy_decay_after_ttl_elapses():
    # Arm with 1s TTL then sleep past it.
    no_persist.pause(ttl_seconds=1)
    time.sleep(1.2)
    assert no_persist.is_paused() is False
    # Decay clears the underlying state.
    assert no_persist._no_persist_until is None
    # Status reflects unpaused after decay.
    assert no_persist.status()["paused"] is False


def test_status_after_decay_returns_unpaused_shape():
    # Arm but manually expire the flag in the past.
    no_persist._no_persist_until = datetime.now(UTC) - timedelta(seconds=5)
    s = no_persist.status()
    assert s == {
        "paused": False,
        "expires_at": None,
        "ttl_remaining_seconds": None,
    }


def test_consecutive_pause_overwrites_ttl():
    no_persist.pause(ttl_seconds=30)
    first = no_persist._no_persist_until
    time.sleep(0.05)
    no_persist.pause(ttl_seconds=600)
    second = no_persist._no_persist_until
    assert second is not None
    assert first is not None
    assert second > first
    assert (second - datetime.now(UTC)).total_seconds() > 500


# ----- snapshot pattern (review point E) -----


def test_snapshot_pattern_holds_through_decay():
    # Caller snapshots at handler top, then TTL expires mid-loop. The
    # snapshot must NOT change underneath the caller; only fresh
    # is_paused() calls reflect the new state.
    no_persist.pause(ttl_seconds=1)
    paused_snapshot = no_persist.is_paused()
    assert paused_snapshot is True
    time.sleep(1.2)
    # Fresh call sees the decay.
    assert no_persist.is_paused() is False
    # But the boolean we captured doesn't mutate (Python value semantics).
    assert paused_snapshot is True


# ----- input validation -----


def test_pause_rejects_non_int_ttl():
    with pytest.raises(ValueError):
        no_persist.pause(ttl_seconds="60")  # type: ignore[arg-type]


def test_pause_rejects_bool_ttl():
    # True/False are int subclasses in Python — guard against silent acceptance.
    with pytest.raises(ValueError):
        no_persist.pause(ttl_seconds=True)  # type: ignore[arg-type]


def test_pause_rejects_zero_or_negative_ttl():
    with pytest.raises(ValueError):
        no_persist.pause(ttl_seconds=0)
    with pytest.raises(ValueError):
        no_persist.pause(ttl_seconds=-10)


def test_pause_clamps_excessive_ttl_to_max():
    out = no_persist.pause(ttl_seconds=999_999)
    assert out["ttl_seconds"] == no_persist.MAX_TTL_SECONDS


# ----- make_skipped_response -----


def test_make_skipped_response_replaces_id_with_sentinel():
    no_persist.pause(ttl_seconds=300)
    body = no_persist.make_skipped_response({"id": 42, "title": "x"}, "create_scope")
    assert body["id"] == "no-persist"
    assert body["title"] == "x"
    assert body["persisted"] is False
    assert body["dry_run"] is True
    assert "create_scope" in body["reason"]
    assert "TTL" in body["reason"] or "left" in body["reason"]


def test_make_skipped_response_preserves_non_id_fields():
    no_persist.pause(ttl_seconds=120)
    default = {
        "ok": True,
        "id": 99,
        "title": "benchmark",
        "extra": {"nested": [1, 2, 3]},
    }
    body = no_persist.make_skipped_response(default, "store")
    # Original keys preserved (except id which is sentinelled).
    assert body["ok"] is True
    assert body["title"] == "benchmark"
    assert body["extra"] == {"nested": [1, 2, 3]}
    # Caller's dict is not mutated.
    assert default["id"] == 99


def test_make_skipped_response_works_when_default_has_no_id_field():
    # Some write tools return {"ok": true} without an id (e.g. update/delete).
    no_persist.pause(ttl_seconds=120)
    body = no_persist.make_skipped_response({"ok": True}, "update_scope")
    assert "id" not in body  # not added if not present
    assert body["ok"] is True
    assert body["persisted"] is False
    assert body["dry_run"] is True


def test_make_skipped_response_does_not_check_is_paused():
    # Caller is responsible; helper trusts the snapshot. Even after
    # resume(), the helper still produces a skipped-shape response.
    no_persist.resume()
    body = no_persist.make_skipped_response({"id": 1}, "store")
    assert body["id"] == "no-persist"
    assert body["persisted"] is False
    # Reason will say "TTL unknown" since _no_persist_until is None.
    assert "TTL unknown" in body["reason"]
