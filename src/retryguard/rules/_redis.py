from __future__ import annotations

from ..models import RetryCategory, RetryDecision


def classify_redis(exc: BaseException) -> RetryDecision | None:
    try:
        import redis
    except Exception:
        return None

    if not isinstance(exc, redis.exceptions.RedisError):
        return None

    # AuthenticationError/AuthorizationError are ConnectionError subclasses in
    # redis-py; they must be checked before the generic ConnectionError branch
    # or bad credentials would be classified as retryable.
    if isinstance(exc, (redis.exceptions.AuthenticationError, redis.exceptions.AuthorizationError)):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.AUTH,
            reason_code="redis_auth_error",
            reason="Redis authentication/authorization error is not retryable.",
        )

    if isinstance(exc, redis.exceptions.BusyLoadingError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.NETWORK,
            reason_code="redis_busy_loading",
            reason="Redis is still loading data into memory; retryable.",
            suggested_delay_seconds=2.0,
        )

    if isinstance(exc, redis.exceptions.MaxConnectionsError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.NETWORK,
            reason_code="redis_max_connections",
            reason="Redis connection pool exhausted; retryable once a connection frees up.",
            suggested_delay_seconds=2.0,
        )

    if isinstance(exc, redis.exceptions.ConnectionError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.NETWORK,
            reason_code="redis_connection_error",
            reason="Redis connection error is retryable.",
            suggested_delay_seconds=2.0,
        )

    if isinstance(exc, redis.exceptions.TimeoutError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.TIMEOUT,
            reason_code="redis_timeout",
            reason="Redis command/connection timeout is retryable.",
            suggested_delay_seconds=2.0,
        )

    if isinstance(exc, redis.exceptions.WatchError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.DATABASE,
            reason_code="redis_watch_conflict",
            reason="Redis WATCH conflict is an optimistic-lock failure; retry the transaction.",
            suggested_delay_seconds=1.0,
        )

    # ClusterDownError also covers MasterDownError (its subclass): cluster is
    # temporarily unreachable/resharding, not a permanent failure.
    if isinstance(exc, redis.exceptions.ClusterDownError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.DATABASE,
            reason_code="redis_cluster_down",
            reason="Redis cluster is temporarily down/resharding; retryable.",
            suggested_delay_seconds=2.0,
        )

    if isinstance(exc, redis.exceptions.TryAgainError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.DATABASE,
            reason_code="redis_try_again",
            reason="Redis cluster reported a transient TRYAGAIN state; retryable.",
            suggested_delay_seconds=2.0,
        )

    if isinstance(exc, redis.exceptions.ReadOnlyError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.DATABASE,
            reason_code="redis_read_only",
            reason="Redis replica has not finished promotion to primary; retryable.",
            suggested_delay_seconds=1.0,
        )

    # MovedError/AskError (cluster redirects) require reconnecting to a different
    # node, not a blind retry on the same connection.
    if isinstance(exc, (redis.exceptions.MovedError, redis.exceptions.AskError)):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.DATABASE,
            reason_code="redis_cluster_redirect",
            reason="Redis cluster redirect requires reconnecting elsewhere, not a blind retry.",
        )

    if isinstance(exc, redis.exceptions.NoScriptError):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.DATABASE,
            reason_code="redis_no_script",
            reason="Redis script not cached; reload it (SCRIPT LOAD / EVAL) instead of retrying EVALSHA.",
        )

    if isinstance(exc, redis.exceptions.LockError):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.VALIDATION,
            reason_code="redis_lock_error",
            reason="Redis lock could not be acquired/extended/released; not retryable as-is.",
        )

    if isinstance(exc, redis.exceptions.DataError):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.VALIDATION,
            reason_code="redis_data_error",
            reason="Redis command argument error; not retryable.",
        )

    if isinstance(exc, redis.exceptions.InvalidResponse):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.CLIENT,
            reason_code="redis_invalid_response",
            reason="Redis protocol response could not be parsed; not retryable.",
        )

    if isinstance(exc, redis.exceptions.ResponseError):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.DATABASE,
            reason_code="redis_response_error",
            reason="Redis server-side response error is not retryable.",
        )

    return RetryDecision(
        retryable=False,
        category=RetryCategory.DATABASE,
        reason_code="redis_unclassified",
        reason="Redis error is unclassified; defaulting to non-retryable.",
    )
