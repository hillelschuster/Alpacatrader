"""Tests for _call_with_timeout retry-on-transient-failure behavior.

Root cause being fixed: a single transient network timeout killed the bot
because _call_with_timeout had no retry.  These tests prove the retry logic
without hitting the real Alpaca API.
"""
from __future__ import annotations

import concurrent.futures

import pytest

import src.paper_execution as pe


# ──────────────────────────────────────────────────────────────────
#  Fakes — replace the shared ThreadPoolExecutor with a controllable one
# ──────────────────────────────────────────────────────────────────


class _FakeFuture:
    def __init__(self, result_fn):
        self._result_fn = result_fn

    def result(self, timeout=None):
        return self._result_fn()


class _FakeExecutor:
    """Records submit calls and drives a scripted result sequence."""

    def __init__(self, result_fn):
        self._result_fn = result_fn
        self.submit_calls = 0

    def submit(self, fn, *args, **kwargs):
        self.submit_calls += 1
        return _FakeFuture(self._result_fn)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Skip real backoff sleeps so tests are instant."""
    monkeypatch.setattr(pe._time, "sleep", lambda _s: None)


# ──────────────────────────────────────────────────────────────────
#  Tests
# ──────────────────────────────────────────────────────────────────


def test_retry_succeeds_after_transient_timeout(monkeypatch):
    """First attempt times out, second succeeds → returns value, 2 calls."""
    attempts = {"n": 0}

    def result_fn():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise concurrent.futures.TimeoutError()
        return "ok"

    fake = _FakeExecutor(result_fn)
    monkeypatch.setattr(pe, "_EXECUTOR", fake)

    result = pe._call_with_timeout(lambda: None, timeout=1.0, retries=3)
    assert result == "ok"
    assert fake.submit_calls == 2


def test_non_transient_error_propagates_immediately(monkeypatch):
    """ValueError (auth/validation-like) must NOT be retried."""
    def result_fn():
        raise ValueError("bad request")

    fake = _FakeExecutor(result_fn)
    monkeypatch.setattr(pe, "_EXECUTOR", fake)

    with pytest.raises(ValueError, match="bad request"):
        pe._call_with_timeout(lambda: None, timeout=1.0, retries=3)
    assert fake.submit_calls == 1  # no retry


def test_all_retries_exhausted_on_timeout(monkeypatch):
    """Persistent timeout → RuntimeError after `retries` attempts."""
    def result_fn():
        raise concurrent.futures.TimeoutError()

    fake = _FakeExecutor(result_fn)
    monkeypatch.setattr(pe, "_EXECUTOR", fake)

    with pytest.raises(RuntimeError, match="timed out"):
        pe._call_with_timeout(lambda: None, timeout=1.0, retries=3)
    assert fake.submit_calls == 3


def test_retry_on_httpx_transport_error(monkeypatch):
    """httpx network errors are transient → retried until success."""
    import httpx

    attempts = {"n": 0}

    def result_fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("connection refused")
        return "ok"

    fake = _FakeExecutor(result_fn)
    monkeypatch.setattr(pe, "_EXECUTOR", fake)

    result = pe._call_with_timeout(lambda: None, timeout=1.0, retries=3)
    assert result == "ok"
    assert fake.submit_calls == 3


def test_non_retryable_api_status_propagates(monkeypatch):
    """A 401 APIError is non-transient → no retry, propagates."""
    from alpaca.common.exceptions import APIError

    err = APIError({"code": 401, "message": "unauthorized"})

    def result_fn():
        raise err

    fake = _FakeExecutor(result_fn)
    monkeypatch.setattr(pe, "_EXECUTOR", fake)

    with pytest.raises(APIError):
        pe._call_with_timeout(lambda: None, timeout=1.0, retries=3)
    assert fake.submit_calls == 1
