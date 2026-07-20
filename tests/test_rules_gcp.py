from __future__ import annotations

import pytest

from retryguard import ErrorClassifier, RetryCategory


def _classify_gcp():
    pytest.importorskip("google.api_core")
    from retryguard.rules import classify_gcp

    return classify_gcp


# ── retryable ────────────────────────────────────────────────────────────────


def test_gcp_resource_exhausted_is_retryable() -> None:
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    decision = classify_gcp(gcp.ResourceExhausted("quota exceeded"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.RATE_LIMIT
    assert decision.reason_code == "gcp_resource_exhausted"


def test_gcp_too_many_requests_is_retryable() -> None:
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    decision = classify_gcp(gcp.TooManyRequests("rate limited"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.RATE_LIMIT
    assert decision.reason_code == "gcp_too_many_requests"


def test_gcp_service_unavailable_is_retryable() -> None:
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    decision = classify_gcp(gcp.ServiceUnavailable("backend down"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.SERVER
    assert decision.reason_code == "gcp_service_unavailable"


def test_gcp_internal_server_error_is_retryable() -> None:
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    decision = classify_gcp(gcp.InternalServerError("internal error"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.SERVER
    assert decision.reason_code == "gcp_internal_server_error"


def test_gcp_aborted_is_retryable() -> None:
    """Regression: Aborted.code == 409, which classify_http_status's
    NON_RETRYABLE_STATUS_CODES treats as non-retryable. Must be overridden —
    ABORTED signals a transaction conflict the caller should retry."""
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    decision = classify_gcp(gcp.Aborted("transaction aborted due to conflict"))
    assert decision is not None
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE
    assert decision.reason_code == "gcp_aborted"


# ── non-retryable ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("exc_name", ["DeadlineExceeded", "GatewayTimeout", "BadGateway"])
def test_gcp_deadline_exceeded_family_is_not_retryable(exc_name: str) -> None:
    """Regression: these have HTTPStatus codes 504/502, which
    classify_http_status's RETRYABLE_STATUS_CODES treats as retryable. Must be
    overridden — google-api-core's own if_transient_error deliberately excludes
    these (a timed-out RPC may have partially succeeded server-side)."""
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    exc_cls = getattr(gcp, exc_name)
    decision = classify_gcp(exc_cls("boom"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.TIMEOUT
    assert decision.reason_code == "gcp_deadline_exceeded"


def test_gcp_invalid_argument_is_not_retryable() -> None:
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    decision = classify_gcp(gcp.InvalidArgument("bad field"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.VALIDATION
    assert decision.reason_code == "gcp_invalid_argument"


@pytest.mark.parametrize("exc_name", ["Unauthorized", "Unauthenticated"])
def test_gcp_401_family_is_not_retryable(exc_name: str) -> None:
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    exc_cls = getattr(gcp, exc_name)
    decision = classify_gcp(exc_cls("no token"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH
    assert decision.reason_code == "gcp_auth_error"


@pytest.mark.parametrize("exc_name", ["Forbidden", "PermissionDenied"])
def test_gcp_403_family_is_not_retryable(exc_name: str) -> None:
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    exc_cls = getattr(gcp, exc_name)
    decision = classify_gcp(exc_cls("no permission"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH
    assert decision.reason_code == "gcp_auth_error"


def test_gcp_not_found_is_not_retryable() -> None:
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    decision = classify_gcp(gcp.NotFound("document not found"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.CLIENT
    assert decision.reason_code == "gcp_client_error"


def test_gcp_already_exists_is_not_retryable() -> None:
    """AlreadyExists shares HTTPStatus 409 with Aborted but is a sibling Conflict
    subclass, not an Aborted subclass — must not be swept into the Aborted
    carve-out."""
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    decision = classify_gcp(gcp.AlreadyExists("resource already exists"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.CLIENT
    assert decision.reason_code == "gcp_client_error"


@pytest.mark.parametrize("exc_name", ["DataLoss", "Unknown"])
def test_gcp_code_none_defaults_non_retryable_unknown(exc_name: str) -> None:
    gcp = pytest.importorskip("google.api_core.exceptions")
    classify_gcp = _classify_gcp()

    exc_cls = getattr(gcp, exc_name)
    decision = classify_gcp(exc_cls("boom"))
    assert decision is not None
    assert decision.retryable is False
    assert decision.category == RetryCategory.UNKNOWN
    assert decision.reason_code == "gcp_unclassified"


# ── not a GCP error ──────────────────────────────────────────────────────────


def test_gcp_non_gcp_exception_returns_none() -> None:
    pytest.importorskip("google.api_core")
    classify_gcp = _classify_gcp()

    assert classify_gcp(ValueError("not gcp")) is None


# ── end-to-end through the full DEFAULT_RULES pipeline ─────────────────────────
# These specifically prove classify_gcp is registered *before* classify_http_status
# in DEFAULT_RULES; the unit tests above only prove classify_gcp is correct in
# isolation, not that ordering actually routes exceptions to it first.


def test_gcp_aborted_not_swallowed_by_classify_http_status_end_to_end() -> None:
    gcp = pytest.importorskip("google.api_core.exceptions")
    decision = ErrorClassifier().classify(gcp.Aborted("transaction aborted"))

    assert decision.retryable is True
    assert decision.reason_code == "gcp_aborted"


def test_gcp_deadline_exceeded_not_swallowed_by_classify_http_status_end_to_end() -> None:
    gcp = pytest.importorskip("google.api_core.exceptions")
    decision = ErrorClassifier().classify(gcp.DeadlineExceeded("deadline exceeded"))

    assert decision.retryable is False
    assert decision.reason_code == "gcp_deadline_exceeded"


def test_gcp_resource_exhausted_gets_gcp_specific_label_end_to_end() -> None:
    """Same retryable verdict as the generic HTTP 429 path would give, but proves
    classify_gcp runs first and produces the GCP-specific reason_code rather than
    classify_http_status's generic 'http_429'."""
    gcp = pytest.importorskip("google.api_core.exceptions")
    decision = ErrorClassifier().classify(gcp.ResourceExhausted("quota exceeded"))

    assert decision.retryable is True
    assert decision.reason_code == "gcp_resource_exhausted"
