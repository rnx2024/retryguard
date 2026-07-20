from __future__ import annotations

from ..models import RetryCategory, RetryDecision
from ..parsers import extract_retry_after, extract_status_code

RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404, 405, 409, 410, 422}


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
