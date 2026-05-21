from __future__ import annotations

import pytest

from retryguard import RetryCategory


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
