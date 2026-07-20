from __future__ import annotations

from ..models import RetryCategory, RetryDecision

# botocore's own internal retry policy (botocore.retries.standard), used by its
# adaptive/standard retry mode. Sourced from the real error-code lists rather than
# invented, so this mirrors AWS's own classification instead of guessing.
_AWS_THROTTLED_ERROR_CODES = {
    "Throttling",
    "ThrottlingException",
    "ThrottledException",
    "RequestThrottledException",
    "TooManyRequestsException",
    "ProvisionedThroughputExceededException",
    "TransactionInProgressException",
    "RequestLimitExceeded",
    "BandwidthLimitExceeded",
    "LimitExceededException",
    "RequestThrottled",
    "SlowDown",
    "PriorRequestNotComplete",
    "EC2ThrottledException",
}

_AWS_TRANSIENT_ERROR_CODES = {
    "RequestTimeout",
    "RequestTimeoutException",
    "PriorRequestNotComplete",
}

# Notably does NOT include 400: AWS overloads HTTP 400 for both throttling (see
# _AWS_THROTTLED_ERROR_CODES above) and genuine permanent validation errors, so
# blind status-code classification would misclassify throttling as non-retryable.
# 429 is included separately below since it is unambiguous (unlike 400).
_AWS_TRANSIENT_STATUS_CODES = {500, 502, 503, 504}


def classify_aws(exc: BaseException) -> RetryDecision | None:
    try:
        import botocore.exceptions
    except Exception:
        return None

    # Client/SDK-side failures: no server response was ever received.
    if isinstance(exc, botocore.exceptions.BotoCoreError):
        if isinstance(
            exc,
            (
                botocore.exceptions.ConnectTimeoutError,
                botocore.exceptions.ReadTimeoutError,
            ),
        ):
            return RetryDecision(
                retryable=True,
                category=RetryCategory.TIMEOUT,
                reason_code="aws_connection_timeout",
                reason="AWS SDK connection/read timeout is retryable.",
                suggested_delay_seconds=2.0,
            )

        if isinstance(
            exc,
            (
                botocore.exceptions.ConnectionError,
                botocore.exceptions.HTTPClientError,
            ),
        ):
            return RetryDecision(
                retryable=True,
                category=RetryCategory.NETWORK,
                reason_code="aws_connection_error",
                reason="AWS SDK connection error is retryable.",
                suggested_delay_seconds=2.0,
            )

        if isinstance(
            exc,
            (
                botocore.exceptions.NoCredentialsError,
                botocore.exceptions.PartialCredentialsError,
                botocore.exceptions.UnauthorizedSSOTokenError,
            ),
        ):
            return RetryDecision(
                retryable=False,
                category=RetryCategory.AUTH,
                reason_code="aws_credentials_error",
                reason="AWS credentials are missing/invalid; not retryable.",
            )

        return RetryDecision(
            retryable=False,
            category=RetryCategory.CLIENT,
            reason_code="aws_botocore_unclassified",
            reason="AWS SDK client-side error is unclassified; defaulting to non-retryable.",
        )

    # Server responded with an error.
    if isinstance(exc, botocore.exceptions.ClientError):
        response = exc.response if isinstance(exc.response, dict) else {}
        error_code = response.get("Error", {}).get("Code")
        status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")

        if error_code in _AWS_THROTTLED_ERROR_CODES:
            return RetryDecision(
                retryable=True,
                category=RetryCategory.RATE_LIMIT,
                reason_code="aws_throttled",
                reason=f"AWS error code {error_code!r} is a known throttling error; retryable.",
                suggested_delay_seconds=5.0,
            )

        if error_code in _AWS_TRANSIENT_ERROR_CODES:
            return RetryDecision(
                retryable=True,
                category=RetryCategory.SERVER,
                reason_code="aws_transient",
                reason=f"AWS error code {error_code!r} is a known transient error; retryable.",
                suggested_delay_seconds=2.0,
            )

        # DynamoDB optimistic-lock conflict. botocore itself does not auto-retry
        # this (identical request retry can't fix it), but retryguard's policy is
        # that the caller is expected to redo the operation with fresh reads —
        # same precedent as Postgres 40001 and Redis WatchError.
        if error_code == "ConditionalCheckFailedException":
            return RetryDecision(
                retryable=True,
                category=RetryCategory.DATABASE,
                reason_code="aws_conditional_check_failed",
                reason="DynamoDB conditional check failed; redo the operation with a fresh read.",
                suggested_delay_seconds=1.0,
            )

        if status_code in _AWS_TRANSIENT_STATUS_CODES:
            return RetryDecision(
                retryable=True,
                category=RetryCategory.SERVER,
                reason_code="aws_server_error",
                reason=f"AWS HTTP {status_code} is a transient server error; retryable.",
                suggested_delay_seconds=2.0,
            )

        if status_code == 429:
            return RetryDecision(
                retryable=True,
                category=RetryCategory.RATE_LIMIT,
                reason_code="aws_rate_limited",
                reason="AWS HTTP 429 is retryable.",
                suggested_delay_seconds=5.0,
            )

        if status_code in (401, 403):
            return RetryDecision(
                retryable=False,
                category=RetryCategory.AUTH,
                reason_code="aws_auth_error",
                reason=f"AWS HTTP {status_code} is not retryable.",
            )

        return RetryDecision(
            retryable=False,
            category=RetryCategory.CLIENT,
            reason_code="aws_client_error",
            reason=f"AWS error code {error_code!r} (HTTP {status_code}) is not retryable.",
        )

    return None
