from __future__ import annotations

from retryguard import RetryCategory
from retryguard.rules import classify_builtin


def test_builtin_timeout_error_is_retryable() -> None:
    decision = classify_builtin(TimeoutError("timed out"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT
    assert decision.reason_code == "builtin_timeout"
    assert decision.suggested_delay_seconds == 2.0


def test_builtin_value_error_is_not_retryable() -> None:
    decision = classify_builtin(ValueError("bad input"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.VALIDATION
    assert decision.reason_code == "builtin_value_error"


def test_builtin_connection_error_is_retryable() -> None:
    decision = classify_builtin(ConnectionError("connection reset"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "builtin_network_error"


def test_builtin_oserror_is_retryable() -> None:
    decision = classify_builtin(OSError("io error"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK


def test_builtin_unknown_exception_returns_none() -> None:
    assert classify_builtin(RuntimeError("unknown")) is None


def test_builtin_key_error_returns_none() -> None:
    assert classify_builtin(KeyError("key")) is None
