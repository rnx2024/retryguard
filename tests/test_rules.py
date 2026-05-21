from __future__ import annotations

import pytest

from retryguard import RetryCategory
from retryguard.rules import (
    classify_builtin,
    classify_http_status,
    classify_postgres_sqlstate,
)


# ── Fixtures / helpers ─────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class FakeHTTPError(Exception):
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.response = FakeResponse(status_code=status_code, headers=headers)


class FakePgError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"sqlstate={sqlstate}")
        self.sqlstate = sqlstate


# ── classify_http_status — retryable codes ─────────────────────────────────────

@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_5xx_server_codes_are_retryable_with_server_category(status_code: int) -> None:
    decision = classify_http_status(FakeHTTPError(status_code))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.SERVER
    assert decision.reason_code == f"http_{status_code}"


@pytest.mark.parametrize("status_code", [408, 425])
def test_4xx_timeout_codes_are_retryable_with_timeout_category(status_code: int) -> None:
    decision = classify_http_status(FakeHTTPError(status_code))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT


def test_http_429_is_retryable_with_rate_limit_category() -> None:
    decision = classify_http_status(FakeHTTPError(429))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.RATE_LIMIT
    assert decision.reason_code == "http_429"


def test_http_429_uses_retry_after_header() -> None:
    decision = classify_http_status(FakeHTTPError(429, {"Retry-After": "15"}))
    assert decision is not None
    assert decision.retry_after_seconds == 15.0
    assert decision.suggested_delay_seconds == 15.0


def test_http_429_without_retry_after_defaults_suggested_to_five() -> None:
    decision = classify_http_status(FakeHTTPError(429))
    assert decision is not None
    assert decision.retry_after_seconds is None
    assert decision.suggested_delay_seconds == 5.0


def test_http_5xx_with_retry_after_header() -> None:
    decision = classify_http_status(FakeHTTPError(503, {"Retry-After": "30"}))
    assert decision is not None
    assert decision.retry_after_seconds == 30.0


# ── classify_http_status — non-retryable codes ────────────────────────────────

@pytest.mark.parametrize("status_code", [400, 404, 405, 409, 410, 422])
def test_client_error_codes_are_not_retryable(status_code: int) -> None:
    decision = classify_http_status(FakeHTTPError(status_code))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.CLIENT
    assert decision.reason_code == f"http_{status_code}"


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_error_codes_are_not_retryable_with_auth_category(status_code: int) -> None:
    decision = classify_http_status(FakeHTTPError(status_code))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH


def test_http_unclassified_code_defaults_to_non_retryable() -> None:
    decision = classify_http_status(FakeHTTPError(418))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.UNKNOWN
    assert decision.reason_code == "http_418"


def test_http_no_status_code_returns_none() -> None:
    assert classify_http_status(Exception("no status code here")) is None


# ── classify_builtin ───────────────────────────────────────────────────────────

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


# ── classify_httpx ─────────────────────────────────────────────────────────────

def test_httpx_timeout_exception_is_retryable() -> None:
    httpx = pytest.importorskip("httpx")
    from retryguard.rules import classify_httpx

    exc = httpx.ConnectTimeout("timed out", request=None)
    decision = classify_httpx(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT
    assert decision.reason_code == "httpx_timeout"


def test_httpx_network_error_is_retryable() -> None:
    httpx = pytest.importorskip("httpx")
    from retryguard.rules import classify_httpx

    exc = httpx.NetworkError("connection failed", request=None)
    decision = classify_httpx(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "httpx_network_error"


def test_httpx_non_network_exception_returns_none() -> None:
    pytest.importorskip("httpx")
    from retryguard.rules import classify_httpx

    assert classify_httpx(ValueError("not httpx")) is None


# ── classify_requests ──────────────────────────────────────────────────────────

def test_requests_timeout_is_retryable_with_timeout_category() -> None:
    requests = pytest.importorskip("requests")
    from retryguard.rules import classify_requests

    exc = requests.Timeout("timed out")
    decision = classify_requests(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT
    assert decision.reason_code == "requests_timeout"


def test_requests_connect_timeout_is_retryable_with_timeout_category() -> None:
    requests = pytest.importorskip("requests")
    from retryguard.rules import classify_requests

    exc = requests.ConnectTimeout("connect timed out")
    decision = classify_requests(exc)
    assert decision is not None
    assert decision.retryable is True
    # ConnectTimeout is a subclass of both Timeout and ConnectionError;
    # Timeout is checked first so it gets TIMEOUT category.
    assert decision.category == RetryCategory.TIMEOUT


def test_requests_connection_error_is_retryable() -> None:
    requests = pytest.importorskip("requests")
    from retryguard.rules import classify_requests

    exc = requests.ConnectionError("connection refused")
    decision = classify_requests(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK


def test_requests_non_network_exception_returns_none() -> None:
    pytest.importorskip("requests")
    from retryguard.rules import classify_requests

    assert classify_requests(ValueError("not requests")) is None
