from __future__ import annotations

import pytest

from retryguard import RetryCategory
from retryguard.rules import classify_http_status


# ── Fixtures / helpers ─────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class FakeHTTPError(Exception):
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.response = FakeResponse(status_code=status_code, headers=headers)


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
