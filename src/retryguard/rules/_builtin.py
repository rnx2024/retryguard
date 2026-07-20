from __future__ import annotations

from ..models import RetryCategory, RetryDecision


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
