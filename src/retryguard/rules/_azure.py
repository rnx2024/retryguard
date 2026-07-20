from __future__ import annotations

from ..models import RetryCategory, RetryDecision
from ..parsers import extract_retry_after


def classify_azure(exc: BaseException) -> RetryDecision | None:
    try:
        from azure.core import exceptions
    except Exception:
        return None

    if not isinstance(exc, exceptions.AzureError):
        return None

    # Transport-level: no response was ever received. Timeout variants checked
    # first since they subclass the broader request/response error classes.
    if isinstance(
        exc, (exceptions.ServiceRequestTimeoutError, exceptions.ServiceResponseTimeoutError)
    ):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.TIMEOUT,
            reason_code="azure_connection_timeout",
            reason="Azure SDK connection/response timeout is retryable.",
            suggested_delay_seconds=2.0,
        )

    if isinstance(exc, (exceptions.ServiceRequestError, exceptions.ServiceResponseError)):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.NETWORK,
            reason_code="azure_connection_error",
            reason="Azure SDK connection error is retryable.",
            suggested_delay_seconds=2.0,
        )

    if not isinstance(exc, exceptions.HttpResponseError):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.CLIENT,
            reason_code="azure_unclassified",
            reason="Azure error is unclassified; defaulting to non-retryable.",
        )

    # Deliberate retryguard-level policy choice, beyond azure-core's own
    # conservative default: ResourceModifiedError signals an ETag conflict on a
    # conditional write (Storage, Cosmos DB, App Configuration). Same precedent
    # as Postgres 40001, Redis WatchError, AWS ConditionalCheckFailedException,
    # and GCP Aborted — re-read and retry the operation. Checked before any
    # status-code logic since ResourceNotFoundError can also carry 412.
    if isinstance(exc, exceptions.ResourceModifiedError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.DATABASE,
            reason_code="azure_resource_modified",
            reason="Azure resource was modified (ETag conflict); re-read and retry the operation.",
            suggested_delay_seconds=1.0,
        )

    status_code = exc.status_code

    if isinstance(exc, exceptions.ClientAuthenticationError) or status_code in (401, 403):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.AUTH,
            reason_code="azure_auth_error",
            reason=f"Azure HTTP {status_code} is not retryable.",
        )

    if isinstance(exc, exceptions.ResourceNotModifiedError):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.CLIENT,
            reason_code="azure_not_modified",
            reason="Azure resource not modified (304); not an error, retrying achieves nothing.",
        )

    if isinstance(exc, (exceptions.TooManyRedirectsError, exceptions.DecodeError)):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.CLIENT,
            reason_code="azure_client_error",
            reason="Azure client-side/protocol error is not retryable.",
        )

    # Matches azure-core's own RetryPolicy default retryable status codes
    # ({408, 429, 500, 502, 503, 504}).
    if status_code == 429:
        retry_after = extract_retry_after(exc)
        return RetryDecision(
            retryable=True,
            category=RetryCategory.RATE_LIMIT,
            reason_code="azure_too_many_requests",
            reason="Azure HTTP 429 is retryable.",
            retry_after_seconds=retry_after,
            suggested_delay_seconds=retry_after or 5.0,
        )

    if status_code == 408:
        return RetryDecision(
            retryable=True,
            category=RetryCategory.TIMEOUT,
            reason_code="azure_request_timeout",
            reason="Azure HTTP 408 is retryable.",
            suggested_delay_seconds=2.0,
        )

    if status_code in (500, 502, 503, 504):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.SERVER,
            reason_code="azure_server_error",
            reason=f"Azure HTTP {status_code} is a transient server error; retryable.",
            suggested_delay_seconds=2.0,
        )

    return RetryDecision(
        retryable=False,
        category=RetryCategory.CLIENT,
        reason_code="azure_client_error",
        reason=f"Azure error (HTTP {status_code}) is not retryable.",
    )
