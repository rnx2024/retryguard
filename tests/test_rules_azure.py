from __future__ import annotations

import pytest

from retryguard import ErrorClassifier, RetryCategory


def _classify_azure():
    pytest.importorskip("azure.core")
    from retryguard.rules import classify_azure

    return classify_azure


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.reason = "reason"
        self.headers = headers or {}


# ── transport-level (AzureError, no response) ──────────────────────────────────


def test_azure_service_request_timeout_is_retryable() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    decision = classify_azure(az.ServiceRequestTimeoutError("timed out"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT
    assert decision.reason_code == "azure_connection_timeout"


def test_azure_service_response_timeout_is_retryable() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    decision = classify_azure(az.ServiceResponseTimeoutError("timed out"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT
    assert decision.reason_code == "azure_connection_timeout"


def test_azure_service_request_error_is_retryable() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    decision = classify_azure(az.ServiceRequestError("could not connect"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "azure_connection_error"


def test_azure_service_response_error_is_retryable() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    decision = classify_azure(az.ServiceResponseError("could not understand response"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK
    assert decision.reason_code == "azure_connection_error"


# ── HttpResponseError — retryable ───────────────────────────────────────────────


def test_azure_resource_modified_is_retryable() -> None:
    """Regression: ResourceModifiedError typically carries status_code=412, which
    is not in any generic retryable set. Must be overridden — ETag conflict on a
    conditional write; caller should re-read and retry, same precedent as Postgres
    40001 / Redis WatchError / AWS ConditionalCheckFailedException / GCP Aborted."""
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.ResourceModifiedError("etag mismatch", response=_FakeResponse(412))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "azure_resource_modified"


def test_azure_resource_not_found_with_412_is_not_retryable() -> None:
    """Regression: ResourceNotFoundError's own docstring says it can also carry
    status_code=412 ('typically triggered by a 412 response for update'). Must
    NOT be swept into the ResourceModifiedError retryable carve-out just because
    it shares the same status code — disambiguation must be type-based."""
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.ResourceNotFoundError("not found on update", response=_FakeResponse(412))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.reason_code == "azure_client_error"


def test_azure_429_is_retryable_with_retry_after() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.HttpResponseError("rate limited", response=_FakeResponse(429, {"Retry-After": "12"}))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.RATE_LIMIT
    assert decision.reason_code == "azure_too_many_requests"
    assert decision.retry_after_seconds == 12.0
    assert decision.suggested_delay_seconds == 12.0


def test_azure_429_without_retry_after_defaults_suggested_to_five() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.HttpResponseError("rate limited", response=_FakeResponse(429))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retry_after_seconds is None
    assert decision.suggested_delay_seconds == 5.0


def test_azure_408_is_retryable() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.HttpResponseError("request timeout", response=_FakeResponse(408))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.TIMEOUT
    assert decision.reason_code == "azure_request_timeout"


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_azure_5xx_is_retryable(status_code: int) -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.HttpResponseError("server error", response=_FakeResponse(status_code))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.SERVER
    assert decision.reason_code == "azure_server_error"


# ── HttpResponseError — non-retryable ───────────────────────────────────────────


def test_azure_client_authentication_error_is_not_retryable() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.ClientAuthenticationError("bad credentials", response=_FakeResponse(401))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH
    assert decision.reason_code == "azure_auth_error"


@pytest.mark.parametrize("status_code", [401, 403])
def test_azure_401_403_status_fallback_is_not_retryable(status_code: int) -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.HttpResponseError("forbidden", response=_FakeResponse(status_code))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH
    assert decision.reason_code == "azure_auth_error"


def test_azure_resource_not_modified_is_not_retryable() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.ResourceNotModifiedError("not modified", response=_FakeResponse(304))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.CLIENT
    assert decision.reason_code == "azure_not_modified"


def test_azure_too_many_redirects_is_not_retryable() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.TooManyRedirectsError(history=[])
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.reason_code == "azure_client_error"


def test_azure_decode_error_is_not_retryable() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.DecodeError("could not decode response", response=_FakeResponse(200))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.reason_code == "azure_client_error"


def test_azure_resource_not_found_is_not_retryable() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.ResourceNotFoundError("not found", response=_FakeResponse(404))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.CLIENT
    assert decision.reason_code == "azure_client_error"


def test_azure_resource_exists_is_not_retryable() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    exc = az.ResourceExistsError("already exists", response=_FakeResponse(409))
    decision = classify_azure(exc)
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.CLIENT
    assert decision.reason_code == "azure_client_error"


def test_azure_generic_azure_error_defaults_non_retryable_unclassified() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    classify_azure = _classify_azure()

    class SomeOtherAzureError(az.AzureError):
        pass

    decision = classify_azure(SomeOtherAzureError("something new"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.reason_code == "azure_unclassified"


# ── not an Azure error ───────────────────────────────────────────────────────


def test_azure_non_azure_exception_returns_none() -> None:
    pytest.importorskip("azure.core")
    classify_azure = _classify_azure()

    assert classify_azure(ValueError("not azure")) is None


# ── end-to-end through the full DEFAULT_RULES pipeline ─────────────────────────
# These specifically prove classify_azure is registered *before* classify_http_status
# in DEFAULT_RULES; the unit tests above only prove classify_azure is correct in
# isolation, not that ordering actually routes exceptions to it first.


def test_azure_429_gets_azure_specific_label_end_to_end() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    exc = az.HttpResponseError("rate limited", response=_FakeResponse(429))
    decision = ErrorClassifier().classify(exc)

    assert decision.retryable is True
    assert decision.reason_code == "azure_too_many_requests"


def test_azure_resource_modified_not_swallowed_by_classify_http_status_end_to_end() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    exc = az.ResourceModifiedError("etag mismatch", response=_FakeResponse(412))
    decision = ErrorClassifier().classify(exc)

    assert decision.retryable is True
    assert decision.reason_code == "azure_resource_modified"


def test_azure_resource_not_found_412_still_non_retryable_end_to_end() -> None:
    az = pytest.importorskip("azure.core.exceptions")
    exc = az.ResourceNotFoundError("not found on update", response=_FakeResponse(412))
    decision = ErrorClassifier().classify(exc)

    assert decision.retryable is False
