from __future__ import annotations

from ..models import RetryCategory, RetryDecision


def classify_gcp(exc: BaseException) -> RetryDecision | None:
    try:
        from google.api_core import exceptions
    except Exception:
        return None

    if not isinstance(exc, exceptions.GoogleAPICallError):
        return None

    # ResourceExhausted is a TooManyRequests subclass; check it first for the
    # more specific reason_code (Google Cloud quota errors are the common case).
    if isinstance(exc, exceptions.ResourceExhausted):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.RATE_LIMIT,
            reason_code="gcp_resource_exhausted",
            reason="GCP quota/resource exhausted (429); retryable.",
            suggested_delay_seconds=5.0,
        )

    if isinstance(exc, exceptions.TooManyRequests):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.RATE_LIMIT,
            reason_code="gcp_too_many_requests",
            reason="GCP HTTP 429 is retryable.",
            suggested_delay_seconds=5.0,
        )

    if isinstance(exc, exceptions.ServiceUnavailable):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.SERVER,
            reason_code="gcp_service_unavailable",
            reason="GCP service unavailable (503); retryable, matches google-api-core's own default retry policy.",
            suggested_delay_seconds=2.0,
        )

    if isinstance(exc, exceptions.InternalServerError):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.SERVER,
            reason_code="gcp_internal_server_error",
            reason="GCP internal server error (500); retryable, matches google-api-core's own default retry policy.",
            suggested_delay_seconds=2.0,
        )

    # Deliberate retryguard-level policy choice, beyond google-api-core's own
    # conservative default: ABORTED most commonly signals a transaction conflict
    # (Firestore/Spanner/BigTable). Same precedent as Postgres 40001 and Redis
    # WatchError — the caller is expected to retry the operation.
    if isinstance(exc, exceptions.Aborted):
        return RetryDecision(
            retryable=True,
            category=RetryCategory.DATABASE,
            reason_code="gcp_aborted",
            reason="GCP transaction aborted due to a conflict; retry the operation.",
            suggested_delay_seconds=1.0,
        )

    # Explicit override: the generic HTTP 504/502 status codes would otherwise
    # suggest retryable, but google-api-core's own default retry predicate
    # (if_transient_error) deliberately excludes these — a timed-out RPC may have
    # partially succeeded server-side, making blind retry unsafe.
    if isinstance(
        exc, (exceptions.DeadlineExceeded, exceptions.GatewayTimeout, exceptions.BadGateway)
    ):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.TIMEOUT,
            reason_code="gcp_deadline_exceeded",
            reason="GCP deadline exceeded; not retried by default (may have partially succeeded server-side).",
        )

    # Fallback: use .code (already present, no extraction helper needed) to pick
    # an accurate non-retryable category. No invented non-retryable code list.
    code = getattr(exc, "code", None)

    if code == 400:
        return RetryDecision(
            retryable=False,
            category=RetryCategory.VALIDATION,
            reason_code="gcp_invalid_argument",
            reason="GCP invalid argument/precondition (400); not retryable.",
        )

    if code in (401, 403):
        return RetryDecision(
            retryable=False,
            category=RetryCategory.AUTH,
            reason_code="gcp_auth_error",
            reason=f"GCP HTTP {code} is not retryable.",
        )

    if code is None:
        return RetryDecision(
            retryable=False,
            category=RetryCategory.UNKNOWN,
            reason_code="gcp_unclassified",
            reason="GCP error has no resolvable status code; defaulting to non-retryable.",
        )

    return RetryDecision(
        retryable=False,
        category=RetryCategory.CLIENT,
        reason_code="gcp_client_error",
        reason=f"GCP error (HTTP {code}) is not retryable.",
    )
