from __future__ import annotations

from ..models import RetryCategory, RetryDecision
from ..parsers import extract_sqlstate, iter_exception_chain

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
