from __future__ import annotations

import pytest

from retryguard import ErrorClassifier, RetryCategory


def _classify_aws():
    pytest.importorskip("botocore.exceptions")
    from retryguard.rules import classify_aws

    return classify_aws


def _client_error(botocore, code: str, status: int, operation: str = "GetItem"):
    return botocore.ClientError(
        error_response={
            "Error": {"Code": code, "Message": "boom"},
            "ResponseMetadata": {"HTTPStatusCode": status, "RequestId": "req-1"},
        },
        operation_name=operation,
    )


# ── BotoCoreError branch — connection-level, retryable ──────────────────────────


def test_aws_connect_timeout_is_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = botocore.ConnectTimeoutError(endpoint_url="https://example.amazonaws.com")
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT
    assert decision.reason_code == "aws_connection_timeout"


def test_aws_read_timeout_is_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = botocore.ReadTimeoutError(endpoint_url="https://example.amazonaws.com")
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT
    assert decision.reason_code == "aws_connection_timeout"


def test_aws_endpoint_connection_error_is_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = botocore.EndpointConnectionError(endpoint_url="https://example.amazonaws.com")
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "aws_connection_error"


def test_aws_proxy_connection_error_is_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = botocore.ProxyConnectionError(proxy_url="https://proxy.example.com")
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "aws_connection_error"


def test_aws_connection_closed_error_is_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = botocore.ConnectionClosedError(endpoint_url="https://example.amazonaws.com")
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "aws_connection_error"


def test_aws_http_client_error_is_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = botocore.HTTPClientError(error="unexpected transport failure")
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "aws_connection_error"


# ── BotoCoreError branch — credentials, not retryable ───────────────────────────


def test_aws_no_credentials_error_is_not_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    decision = classify_aws(botocore.NoCredentialsError())
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH
    assert decision.reason_code == "aws_credentials_error"


def test_aws_partial_credentials_error_is_not_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = botocore.PartialCredentialsError(provider="env", cred_var="AWS_SECRET_ACCESS_KEY")
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH
    assert decision.reason_code == "aws_credentials_error"


def test_aws_unauthorized_sso_token_error_is_not_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    decision = classify_aws(botocore.UnauthorizedSSOTokenError())
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH
    assert decision.reason_code == "aws_credentials_error"


def test_aws_unclassified_botocore_error_defaults_non_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    class SomeOtherBotoCoreError(botocore.BotoCoreError):
        fmt = "something new"

    decision = classify_aws(SomeOtherBotoCoreError())
    assert decision is not None
    assert decision.retryable is False
    assert decision.reason_code == "aws_botocore_unclassified"


# ── ClientError branch — the throttling-as-400 regression this design exists for ─


def test_aws_provisioned_throughput_exceeded_as_http_400_is_retryable() -> None:
    """The whole reason this design doesn't reuse classify_http_status: AWS
    returns HTTP 400 for throttling, which classify_http_status would treat as
    non-retryable CLIENT. Error.Code must be checked first."""
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = _client_error(botocore, "ProvisionedThroughputExceededException", 400)
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.RATE_LIMIT
    assert decision.reason_code == "aws_throttled"


def test_aws_throttling_exception_as_http_400_is_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = _client_error(botocore, "ThrottlingException", 400)
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.RATE_LIMIT


def test_aws_slow_down_s3_error_is_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = _client_error(botocore, "SlowDown", 503, operation="PutObject")
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.RATE_LIMIT


def test_aws_request_timeout_error_code_is_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = _client_error(botocore, "RequestTimeout", 400)
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.SERVER
    assert decision.reason_code == "aws_transient"


def test_aws_conditional_check_failed_is_retryable() -> None:
    """DynamoDB optimistic-lock conflict; not in botocore's own retry lists (it
    doesn't auto-retry identical requests for this), but retryguard treats it as
    retryable at the caller-redo-the-operation level, same as Postgres 40001 and
    Redis WatchError."""
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = _client_error(botocore, "ConditionalCheckFailedException", 400)
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "aws_conditional_check_failed"


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_aws_transient_5xx_status_fallback_is_retryable(status_code: int) -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = _client_error(botocore, "InternalError", status_code)
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.SERVER
    assert decision.reason_code == "aws_server_error"


def test_aws_429_status_fallback_is_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = _client_error(botocore, "SomeUnrecognizedCode", 429)
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.RATE_LIMIT
    assert decision.reason_code == "aws_rate_limited"


@pytest.mark.parametrize("status_code", [401, 403])
def test_aws_auth_status_fallback_is_not_retryable(status_code: int) -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = _client_error(botocore, "AccessDeniedException", status_code)
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH
    assert decision.reason_code == "aws_auth_error"


def test_aws_validation_exception_as_http_400_is_not_retryable() -> None:
    """A genuine permanent validation error, also HTTP 400 like throttling above —
    confirms Error.Code (not status code) is what actually distinguishes them."""
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = _client_error(botocore, "ValidationException", 400)
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.CLIENT
    assert decision.reason_code == "aws_client_error"


def test_aws_resource_not_found_is_not_retryable() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    exc = _client_error(botocore, "ResourceNotFoundException", 404)
    decision = classify_aws(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.CLIENT


# ── not an AWS error ─────────────────────────────────────────────────────────────


def test_aws_non_aws_exception_returns_none() -> None:
    pytest.importorskip("botocore.exceptions")
    classify_aws = _classify_aws()

    assert classify_aws(ValueError("not aws")) is None


# ── end-to-end through the full DEFAULT_RULES pipeline ─────────────────────────
# These specifically prove classify_aws is registered *before* classify_builtin in
# DEFAULT_RULES; the unit tests above only prove classify_aws is correct in
# isolation, not that ordering actually routes exceptions to it first.


def test_aws_connect_timeout_not_swallowed_by_builtin_oserror_end_to_end() -> None:
    """ConnectTimeoutError subclasses builtin OSError (botocore reuses
    requests/urllib3 exception mixins). If DEFAULT_RULES ordering regressed
    (classify_builtin running before classify_aws), this would come back as
    reason_code='builtin_network_error' instead of the AWS-specific one."""
    botocore = pytest.importorskip("botocore.exceptions")
    exc = botocore.ConnectTimeoutError(endpoint_url="https://example.amazonaws.com")
    decision = ErrorClassifier().classify(exc)

    assert decision.retryable is True
    assert decision.reason_code == "aws_connection_timeout"
    assert decision.category == RetryCategory.TIMEOUT


def test_aws_read_timeout_not_swallowed_by_builtin_oserror_end_to_end() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    exc = botocore.ReadTimeoutError(endpoint_url="https://example.amazonaws.com")
    decision = ErrorClassifier().classify(exc)

    assert decision.retryable is True
    assert decision.reason_code == "aws_connection_timeout"


def test_aws_proxy_connection_error_not_swallowed_by_builtin_oserror_end_to_end() -> None:
    botocore = pytest.importorskip("botocore.exceptions")
    exc = botocore.ProxyConnectionError(proxy_url="https://proxy.example.com")
    decision = ErrorClassifier().classify(exc)

    assert decision.retryable is True
    assert decision.reason_code == "aws_connection_error"


def test_aws_throttling_as_400_classified_end_to_end() -> None:
    """The core regression this whole design exists to prevent, exercised through
    the full ErrorClassifier() pipeline, not just classify_aws in isolation."""
    botocore = pytest.importorskip("botocore.exceptions")
    exc = _client_error(botocore, "ProvisionedThroughputExceededException", 400)
    decision = ErrorClassifier().classify(exc)

    assert decision.retryable is True
    assert decision.category == RetryCategory.RATE_LIMIT
    assert decision.reason_code == "aws_throttled"
