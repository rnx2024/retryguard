from __future__ import annotations

import pytest

from retryguard import RetryCategory
from retryguard.rules import classify_postgres_sqlstate


class FakePgError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"sqlstate={sqlstate}")
        self.sqlstate = sqlstate


# ── classify_postgres_sqlstate ─────────────────────────────────────────────────

@pytest.mark.parametrize("sqlstate", ["08000", "08001", "08003", "08006", "08007"])
def test_pg_class_08_connection_exceptions_are_retryable(sqlstate: str) -> None:
    decision = classify_postgres_sqlstate(FakePgError(sqlstate))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK


@pytest.mark.parametrize("sqlstate,expected_delay", [
    ("40001", 1.0),   # serialization_failure — low delay
    ("40P01", 1.0),   # deadlock_detected — low delay
    ("55P03", 1.0),   # lock_not_available — low delay
    ("53300", 2.0),   # too_many_connections — higher delay
    ("57P01", 2.0),   # admin_shutdown
    ("57P02", 2.0),   # crash_shutdown
    ("57P03", 2.0),   # cannot_connect_now
])
def test_pg_transient_sqlstates_are_retryable_with_correct_delay(
    sqlstate: str, expected_delay: float
) -> None:
    decision = classify_postgres_sqlstate(FakePgError(sqlstate))
    assert decision is not None
    assert decision.retryable is True
    assert decision.suggested_delay_seconds == expected_delay


def test_pg_57014_query_canceled_has_timeout_category() -> None:
    decision = classify_postgres_sqlstate(FakePgError("57014"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT


def test_pg_40001_has_database_category() -> None:
    decision = classify_postgres_sqlstate(FakePgError("40001"))
    assert decision is not None
    assert decision.category == RetryCategory.DATABASE


@pytest.mark.parametrize("sqlstate", ["28000", "28P01"])
def test_pg_class_28_auth_failures_are_not_retryable(sqlstate: str) -> None:
    decision = classify_postgres_sqlstate(FakePgError(sqlstate))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH


@pytest.mark.parametrize("sqlstate", ["23502", "23503", "23505", "23514", "23001"])
def test_pg_class_23_constraint_violations_are_not_retryable(sqlstate: str) -> None:
    decision = classify_postgres_sqlstate(FakePgError(sqlstate))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE


@pytest.mark.parametrize("sqlstate", ["22001", "22003", "22007", "22012"])
def test_pg_class_22_data_exceptions_are_not_retryable(sqlstate: str) -> None:
    decision = classify_postgres_sqlstate(FakePgError(sqlstate))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.VALIDATION


@pytest.mark.parametrize("sqlstate", ["42601", "42501", "42703", "42P01"])
def test_pg_class_42_syntax_privilege_errors_are_not_retryable(sqlstate: str) -> None:
    decision = classify_postgres_sqlstate(FakePgError(sqlstate))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.CLIENT


@pytest.mark.parametrize("sqlstate", ["0A000", "0A001"])
def test_pg_class_0a_feature_not_supported_is_not_retryable(sqlstate: str) -> None:
    decision = classify_postgres_sqlstate(FakePgError(sqlstate))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.CLIENT


@pytest.mark.parametrize("sqlstate", ["53100", "53200", "53400"])
def test_pg_class_53_resource_errors_are_retryable(sqlstate: str) -> None:
    decision = classify_postgres_sqlstate(FakePgError(sqlstate))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE


@pytest.mark.parametrize("sqlstate", ["58000", "58030", "58P01"])
def test_pg_class_58_system_errors_are_retryable(sqlstate: str) -> None:
    decision = classify_postgres_sqlstate(FakePgError(sqlstate))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE


def test_pg_sqlstate_unclassified_defaults_to_non_retryable() -> None:
    class E(Exception):
        sqlstate = "ZZ999"

    decision = classify_postgres_sqlstate(E())
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE


def test_pg_sqlstate_not_present_returns_none() -> None:
    assert classify_postgres_sqlstate(Exception("no sqlstate")) is None


def test_pg_sqlstate_reason_code_includes_sqlstate() -> None:
    decision = classify_postgres_sqlstate(FakePgError("40001"))
    assert decision is not None
    assert decision.reason_code == "pg_40001"


def test_pg_sqlstate_extracted_from_chained_orig() -> None:
    class Wrapper(Exception):
        def __init__(self, orig: BaseException) -> None:
            self.orig = orig

    decision = classify_postgres_sqlstate(Wrapper(FakePgError("40P01")))
    assert decision is not None
    assert decision.retryable is True


# ── classify_sqlalchemy / classify_psycopg / classify_asyncpg ─────────────────

def test_classify_sqlalchemy_pool_timeout() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from retryguard.rules import classify_sqlalchemy

    exc = sqlalchemy.exc.TimeoutError("pool timeout")
    decision = classify_sqlalchemy(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "sqlalchemy_pool_timeout"


def test_classify_sqlalchemy_connection_invalidated_is_retryable() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from retryguard.rules import classify_sqlalchemy

    exc = sqlalchemy.exc.DBAPIError(
        statement="select 1",
        params=None,
        orig=Exception("boom"),
        connection_invalidated=True,
    )
    decision = classify_sqlalchemy(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "sqlalchemy_connection_invalidated"


def test_classify_sqlalchemy_dbapi_chain_uses_sqlstate() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from retryguard.rules import classify_sqlalchemy

    class FakeDbapiError(Exception):
        sqlstate = "57014"

    exc = sqlalchemy.exc.DBAPIError(
        statement="select pg_sleep(10)",
        params=None,
        orig=FakeDbapiError("query canceled"),
        connection_invalidated=False,
    )
    decision = classify_sqlalchemy(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT
    assert decision.reason_code == "pg_57014"


def test_classify_sqlalchemy_dbapi_no_sqlstate_defaults_to_non_retryable_database() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from retryguard.rules import classify_sqlalchemy

    exc = sqlalchemy.exc.DBAPIError(
        statement="select 1",
        params=None,
        orig=Exception("some unrecognized db error"),
        connection_invalidated=False,
    )
    decision = classify_sqlalchemy(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "sqlalchemy_unclassified_dbapi_error"


def test_classify_psycopg_sqlstate_path() -> None:
    psycopg = pytest.importorskip("psycopg")
    from retryguard.rules import classify_psycopg

    class FakePsycopgError(psycopg.Error):
        def __init__(self, message: str = "psycopg error") -> None:
            super().__init__(message)

        @property
        def sqlstate(self) -> str:
            return "57P01"

    decision = classify_psycopg(FakePsycopgError("admin shutdown"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "pg_57P01"


def test_classify_psycopg_string_marker_fallback() -> None:
    psycopg = pytest.importorskip("psycopg")
    from retryguard.rules import classify_psycopg

    class FakePsycopgError(psycopg.Error):
        def __init__(self, message: str = "psycopg error") -> None:
            super().__init__(message)

        @property
        def sqlstate(self) -> None:
            return None

        def __str__(self) -> str:
            return "deadlock detected"

    decision = classify_psycopg(FakePsycopgError())
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "psycopg_unclassified"


def test_classify_psycopg_no_sqlstate_or_marker_defaults_non_retryable_database() -> None:
    psycopg = pytest.importorskip("psycopg")
    from retryguard.rules import classify_psycopg

    class FakePsycopgError(psycopg.Error):
        def __init__(self, message: str = "psycopg error") -> None:
            super().__init__(message)

        @property
        def sqlstate(self) -> None:
            return None

        def __str__(self) -> str:
            return "some unrecognized db error"

    decision = classify_psycopg(FakePsycopgError())
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "psycopg_unclassified"


def test_classify_asyncpg_sqlstate_path() -> None:
    asyncpg = pytest.importorskip("asyncpg")
    from retryguard.rules import classify_asyncpg

    class FakeAsyncpgError(asyncpg.PostgresError):
        def __init__(self, message: str = "asyncpg error") -> None:
            super().__init__(message)

        @property
        def sqlstate(self) -> str:
            return "40001"

    decision = classify_asyncpg(FakeAsyncpgError("serialization failure"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.reason_code == "pg_40001"


def test_classify_asyncpg_string_marker_fallback() -> None:
    asyncpg = pytest.importorskip("asyncpg")
    from retryguard.rules import classify_asyncpg

    class FakeAsyncpgError(asyncpg.PostgresError):
        def __init__(self, message: str = "asyncpg error") -> None:
            super().__init__(message)

        @property
        def sqlstate(self) -> None:
            return None

        def __str__(self) -> str:
            return "too many connections"

    decision = classify_asyncpg(FakeAsyncpgError("too many connections"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "asyncpg_unclassified"


def test_classify_asyncpg_no_sqlstate_or_marker_defaults_non_retryable_database() -> None:
    asyncpg = pytest.importorskip("asyncpg")
    from retryguard.rules import classify_asyncpg

    class FakeAsyncpgError(asyncpg.PostgresError):
        def __init__(self, message: str = "asyncpg error") -> None:
            super().__init__(message)

        @property
        def sqlstate(self) -> None:
            return None

        def __str__(self) -> str:
            return "some unrecognized db error"

    decision = classify_asyncpg(FakeAsyncpgError("some unrecognized db error"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "asyncpg_unclassified"
