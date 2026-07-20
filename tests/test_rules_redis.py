from __future__ import annotations

import pytest

from retryguard import ErrorClassifier, RetryCategory


def _classify_redis():
    pytest.importorskip("redis")
    from retryguard.rules import classify_redis

    return classify_redis


# ── retryable ────────────────────────────────────────────────────────────────


def test_redis_connection_error_is_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.ConnectionError("connection reset"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "redis_connection_error"
    assert decision.suggested_delay_seconds == 2.0


def test_redis_timeout_error_is_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.TimeoutError("command timed out"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT
    assert decision.reason_code == "redis_timeout"


def test_redis_busy_loading_error_is_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.BusyLoadingError("Redis is loading"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "redis_busy_loading"


def test_redis_max_connections_error_is_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.MaxConnectionsError("pool exhausted"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "redis_max_connections"


def test_redis_watch_error_is_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.WatchError("watched key changed"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "redis_watch_conflict"
    assert decision.suggested_delay_seconds == 1.0


def test_redis_cluster_down_error_is_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.ClusterDownError("CLUSTERDOWN The cluster is down"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "redis_cluster_down"


def test_redis_master_down_error_is_retryable_via_cluster_down_check() -> None:
    """MasterDownError is a ClusterDownError subclass; must not fall through to
    the generic ResponseError catch-all."""
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(
        redis.exceptions.MasterDownError("MASTERDOWN Link with MASTER is down")
    )
    assert decision is not None
    assert decision.retryable is True
    assert decision.reason_code == "redis_cluster_down"


def test_redis_try_again_error_is_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.TryAgainError("TRYAGAIN"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "redis_try_again"


def test_redis_read_only_error_is_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.ReadOnlyError("READONLY replica"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "redis_read_only"
    assert decision.suggested_delay_seconds == 1.0


# ── non-retryable ────────────────────────────────────────────────────────────


def test_redis_authentication_error_is_not_retryable() -> None:
    """Regression: AuthenticationError is a ConnectionError subclass in redis-py;
    it must NOT be swept up by the generic ConnectionError -> retryable branch."""
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.AuthenticationError("invalid password"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH
    assert decision.reason_code == "redis_auth_error"


def test_redis_authorization_error_is_not_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(
        redis.exceptions.AuthorizationError("NOPERM this user has no permissions")
    )
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH
    assert decision.reason_code == "redis_auth_error"


def test_redis_moved_error_is_not_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.MovedError("1 127.0.0.1:7001"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "redis_cluster_redirect"


def test_redis_ask_error_is_not_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.AskError("1 127.0.0.1:7001"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "redis_cluster_redirect"


def test_redis_no_script_error_is_not_retryable() -> None:
    """Regression: retrying EVALSHA after NOSCRIPT fails identically without a
    script reload; must not be classified as blindly retryable."""
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.NoScriptError("NOSCRIPT No matching script"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.reason_code == "redis_no_script"


def test_redis_lock_error_is_not_retryable_and_not_swallowed_by_builtin() -> None:
    """Regression: LockError subclasses (RedisError, ValueError). If classify_redis
    weren't registered before classify_builtin, this would be caught generically
    as builtin_value_error instead of getting a redis-specific decision."""
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.LockError("could not acquire lock"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.VALIDATION
    assert decision.reason_code == "redis_lock_error"


def test_redis_lock_not_owned_error_is_not_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.LockNotOwnedError("lock no longer owned"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.reason_code == "redis_lock_error"


def test_redis_data_error_is_not_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.DataError("bad command arguments"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.VALIDATION
    assert decision.reason_code == "redis_data_error"


def test_redis_invalid_response_is_not_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.InvalidResponse("protocol error"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.CLIENT
    assert decision.reason_code == "redis_invalid_response"


def test_redis_generic_response_error_is_not_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(
        redis.exceptions.ResponseError("WRONGTYPE Operation against a wrong kind of value")
    )
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "redis_response_error"


def test_redis_no_permission_error_falls_back_to_response_error_default() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    decision = classify_redis(redis.exceptions.NoPermissionError("NOPERM"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.reason_code == "redis_response_error"


def test_redis_unclassified_redis_error_defaults_non_retryable() -> None:
    redis = pytest.importorskip("redis")
    classify_redis = _classify_redis()

    class SomeOtherRedisError(redis.exceptions.RedisError):
        pass

    decision = classify_redis(SomeOtherRedisError("something new"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "redis_unclassified"


# ── not a redis error ────────────────────────────────────────────────────────


def test_redis_non_redis_exception_returns_none() -> None:
    pytest.importorskip("redis")
    classify_redis = _classify_redis()

    assert classify_redis(ValueError("not redis")) is None


# ── end-to-end through the full DEFAULT_RULES pipeline ─────────────────────────
# These specifically prove classify_redis is registered *before* classify_builtin
# in DEFAULT_RULES; the unit tests above only prove classify_redis is correct in
# isolation, not that ordering actually routes exceptions to it first.


@pytest.mark.parametrize(
    "exc_name,retryable,expected_reason_code",
    [
        ("ConnectionError", True, "redis_connection_error"),
        ("TimeoutError", True, "redis_timeout"),
        ("AuthenticationError", False, "redis_auth_error"),
        ("WatchError", True, "redis_watch_conflict"),
    ],
)
def test_redis_errors_classified_end_to_end(
    exc_name: str, retryable: bool, expected_reason_code: str
) -> None:
    redis = pytest.importorskip("redis")
    exc_cls = getattr(redis.exceptions, exc_name)
    decision = ErrorClassifier().classify(exc_cls("boom"))

    assert decision.retryable is retryable
    assert decision.reason_code == expected_reason_code


def test_redis_lock_error_not_swallowed_by_builtin_value_error_end_to_end() -> None:
    """LockError subclasses (RedisError, ValueError). If DEFAULT_RULES ordering
    regressed (classify_builtin running before classify_redis), this would come
    back as reason_code='builtin_value_error' instead of the redis-specific one."""
    redis = pytest.importorskip("redis")
    decision = ErrorClassifier().classify(redis.exceptions.LockError("could not acquire lock"))

    assert decision.retryable is False
    assert decision.reason_code == "redis_lock_error"
    assert decision.category == RetryCategory.VALIDATION


def test_redis_cluster_down_error_not_swallowed_by_generic_response_error() -> None:
    """ClusterDownError subclasses ResponseError; must hit its own branch before
    the generic ResponseError -> non-retryable catch-all."""
    redis = pytest.importorskip("redis")
    decision = ErrorClassifier().classify(
        redis.exceptions.ClusterDownError("CLUSTERDOWN The cluster is down")
    )

    assert decision.retryable is True
    assert decision.reason_code == "redis_cluster_down"
