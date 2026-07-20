from __future__ import annotations

from .models import RetryCategory, RetryDecision
from .parsers import extract_retry_after, extract_sqlstate, extract_status_code, iter_exception_chain

RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404, 405, 409, 410, 422}

# Postgres SQLSTATE references:
# - Class 08: Connection Exception
# - Class 40: Transaction Rollback (includes 40001 serialization_failure, 40P01 deadlock)
# - 55P03: lock_not_available
# - 53300: too_many_connections
# - 57014: query_canceled (commonly statement_timeout)
# - 57P01/02/03: shutdown / cannot_connect_now
_PG_TRANSIENT_SQLSTATE = {
    "40001",  # serialization_failure
    "40P01",  # deadlock_detected
    "55P03",  # lock_not_available
    "53300",  # too_many_connections
    "57014",  # query_canceled (often statement_timeout)
    "57P01",  # admin_shutdown
    "57P02",  # crash_shutdown
    "57P03",  # cannot_connect_now
}

_PG_NON_RETRYABLE_SQLSTATE = {
    # Integrity constraint violation (23xxx)
    "23502",  # not_null_violation
    "23503",  # foreign_key_violation
    "23505",  # unique_violation
    "23514",  # check_violation
}


def classify_builtin(exc: BaseException) -> RetryDecision | None:
    if isinstance(exc, TimeoutError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.TIMEOUT,
            reason_code="builtin_timeout",
            reason="TimeoutError is retryable by default.",
            suggested_delay_seconds=2.0,
        )

    if isinstance(exc, ValueError):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.VALIDATION,
            reason_code="builtin_value_error",
            reason="ValueError usually indicates invalid input or parsing failure.",
        )

    if isinstance(exc, (ConnectionError, OSError)):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.NETWORK,
            reason_code="builtin_network_error",
            reason="Connection/OSError is treated as transient by default.",
            suggested_delay_seconds=2.0,
        )

    return None


def classify_http_status(exc: BaseException) -> RetryDecision | None:
    status_code = extract_status_code(exc)
    if status_code is None:
        return None

    if status_code == 429:
        retry_after = extract_retry_after(exc)
        return RetryDecision(
            retryable=True,
            category=RetryCategory.RATE_LIMIT,
            reason_code="http_429",
            reason="HTTP 429 Too Many Requests is retryable.",
            retry_after_seconds=retry_after,
            suggested_delay_seconds=retry_after or 5.0,
        )

    if status_code in RETRYABLE_STATUS_CODES:
        retry_after = extract_retry_after(exc)
        category = RetryCategory.SERVER if status_code >= 500 else RetryCategory.TIMEOUT
        return RetryDecision(
            retryable=True,
            category=category,
            reason_code=f"http_{status_code}",
            reason=f"HTTP {status_code} is retryable by policy.",
            retry_after_seconds=retry_after,
            suggested_delay_seconds=retry_after or 2.0,
        )

    if status_code in {401, 403}:
        return RetryDecision(
            retryable=False,
            category=RetryCategory.AUTH,
            reason_code=f"http_{status_code}",
            reason=f"HTTP {status_code} is not retryable by default.",
        )

    if status_code in NON_RETRYABLE_STATUS_CODES:
        return RetryDecision(
            retryable=False,
            category=RetryCategory.CLIENT,
            reason_code=f"http_{status_code}",
            reason=f"HTTP {status_code} is not retryable by policy.",
        )

    return RetryDecision(
        retryable=False,
        category=RetryCategory.UNKNOWN,
        reason_code=f"http_{status_code}",
        reason=f"HTTP {status_code} is unclassified; defaulting to non-retryable.",
    )


def classify_postgres_sqlstate(exc: BaseException) -> RetryDecision | None:
    sqlstate = extract_sqlstate(exc)
    if sqlstate is None:
        return None

    if sqlstate.startswith("08"):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.NETWORK,
            reason_code=f"pg_{sqlstate}",
            reason=f"Postgres SQLSTATE {sqlstate} (connection exception) is retryable.",
            suggested_delay_seconds=1.0,
        )

    if sqlstate in _PG_TRANSIENT_SQLSTATE:
        category = RetryCategory.TIMEOUT if sqlstate == "57014" else RetryCategory.DATABASE
        delay = 1.0 if sqlstate in {"40001", "40P01", "55P03"} else 2.0
        return RetryDecision(
            retryable=True,
            category=category,
            reason_code=f"pg_{sqlstate}",
            reason=f"Postgres SQLSTATE {sqlstate} is retryable by policy.",
            suggested_delay_seconds=delay,
        )

    # Class 53: insufficient resources (disk_full, out_of_memory, config limit, etc.)
    # Class 58: system error (io_error, undefined_file, etc.) — transient OS-level failures.
    if sqlstate.startswith(("53", "58")):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.DATABASE,
            reason_code=f"pg_{sqlstate}",
            reason=f"Postgres SQLSTATE {sqlstate} (resource/system error) is retryable.",
            suggested_delay_seconds=2.0,
        )

    if sqlstate.startswith("28"):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.AUTH,
            reason_code=f"pg_{sqlstate}",
            reason=f"Postgres SQLSTATE {sqlstate} (invalid authorization) is not retryable.",
        )

    if sqlstate in _PG_NON_RETRYABLE_SQLSTATE or sqlstate.startswith("23"):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.DATABASE,
            reason_code=f"pg_{sqlstate}",
            reason=f"Postgres SQLSTATE {sqlstate} (constraint violation) is not retryable.",
        )

    if sqlstate.startswith("22"):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.VALIDATION,
            reason_code=f"pg_{sqlstate}",
            reason=f"Postgres SQLSTATE {sqlstate} (data exception) is not retryable.",
        )

    if sqlstate.startswith("42"):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.CLIENT,
            reason_code=f"pg_{sqlstate}",
            reason=f"Postgres SQLSTATE {sqlstate} (SQL syntax/privilege) is not retryable.",
        )

    if sqlstate.startswith("0A"):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.CLIENT,
            reason_code=f"pg_{sqlstate}",
            reason=f"Postgres SQLSTATE {sqlstate} (feature not supported) is not retryable.",
        )

    return RetryDecision(
        retryable=False,
        category=RetryCategory.DATABASE,
        reason_code=f"pg_{sqlstate}",
        reason=f"Postgres SQLSTATE {sqlstate} is unclassified; defaulting to non-retryable.",
    )


def classify_sqlalchemy(exc: BaseException) -> RetryDecision | None:
    try:
        from sqlalchemy import exc as sa_exc
    except Exception:
        return None

    # Pool timeout: usually transient contention.
    if isinstance(exc, sa_exc.TimeoutError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.DATABASE,
            reason_code="sqlalchemy_pool_timeout",
            reason="SQLAlchemy pool TimeoutError is typically transient contention.",
            suggested_delay_seconds=1.0,
        )

    if isinstance(exc, sa_exc.DBAPIError):
        if getattr(exc, "connection_invalidated", False) is True:
            return RetryDecision(
                retryable=True,
                category=RetryCategory.NETWORK,
                reason_code="sqlalchemy_connection_invalidated",
                reason="SQLAlchemy DBAPIError with connection_invalidated=True is retryable.",
                suggested_delay_seconds=1.0,
            )

        # Prefer SQLSTATE-based classification from the wrapped DBAPI exception or cause/context.
        for candidate in iter_exception_chain(exc):
            if candidate is exc:
                continue
            decision = classify_postgres_sqlstate(candidate)
            if decision is not None:
                return decision

        return RetryDecision(
            retryable=False,
            category=RetryCategory.DATABASE,
            reason_code="sqlalchemy_unclassified_dbapi_error",
            reason="SQLAlchemy DBAPIError without SQLSTATE is unclassified; defaulting to non-retryable.",
        )

    return None


def classify_httpx(exc: BaseException) -> RetryDecision | None:
    try:
        import httpx
    except Exception:
        return None

    if isinstance(exc, httpx.TimeoutException):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.TIMEOUT,
            reason_code="httpx_timeout",
            reason="httpx timeout is retryable.",
            suggested_delay_seconds=2.0,
        )

    if isinstance(exc, httpx.NetworkError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.NETWORK,
            reason_code="httpx_network_error",
            reason="httpx network error is retryable.",
            suggested_delay_seconds=2.0,
        )

    return None


def classify_requests(exc: BaseException) -> RetryDecision | None:
    try:
        import requests
    except Exception:
        return None

    if isinstance(exc, requests.Timeout):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.TIMEOUT,
            reason_code="requests_timeout",
            reason="requests timeout is retryable.",
            suggested_delay_seconds=2.0,
        )

    if isinstance(exc, requests.ConnectionError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.NETWORK,
            reason_code="requests_network_error",
            reason="requests connection error is retryable.",
            suggested_delay_seconds=2.0,
        )

    return None


def classify_psycopg(exc: BaseException) -> RetryDecision | None:
    try:
        import psycopg
    except Exception:
        return None

    if isinstance(exc, psycopg.Error):
        decision = classify_postgres_sqlstate(exc)
        if decision is not None:
            return decision

        return RetryDecision(
            retryable=False,
            category=RetryCategory.DATABASE,
            reason_code="psycopg_unclassified",
            reason="psycopg error is unclassified; defaulting to non-retryable.",
        )

    return None


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


def classify_asyncpg(exc: BaseException) -> RetryDecision | None:
    try:
        import asyncpg
    except Exception:
        return None

    if isinstance(exc, asyncpg.PostgresError):
        decision = classify_postgres_sqlstate(exc)
        if decision is not None:
            return decision

        return RetryDecision(
            retryable=False,
            category=RetryCategory.DATABASE,
            reason_code="asyncpg_unclassified",
            reason="asyncpg PostgresError is unclassified; defaulting to non-retryable.",
        )

    return None
