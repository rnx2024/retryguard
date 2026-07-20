"""
End-to-end tests that verify:
- All 9 RetryCategory values are assigned by at least one rule.
- retryable vs non-retryable decisions are correct for each category.
- RetryDecision always carries a non-empty reason and reason_code.
- Tenacity retries retryable exceptions and lets non-retryable ones propagate.
- The library works without tenacity (direct and Celery-style usage).
- before_sleep_log_retryguard produces structured log output during retry loops.
"""
from __future__ import annotations

import logging

import pytest

from retryguard import ErrorClassifier, RetryCategory


# ── Shared fixtures ─────────────────────────���──────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class _FakeHTTPError(Exception):
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.response = _FakeResponse(status_code, headers)


class _FakePgError(Exception):
    def __init__(self, sqlstate: str, msg: str = "") -> None:
        super().__init__(msg or f"pg error {sqlstate}")
        self.sqlstate = sqlstate


# ── Category coverage — all 9 categories ────────────────────────��─────────────

@pytest.mark.parametrize("exc,expected_category,expected_retryable", [
    # NETWORK
    (ConnectionError("connection reset"),          RetryCategory.NETWORK,     True),
    (OSError("broken pipe"),                       RetryCategory.NETWORK,     True),
    # TIMEOUT
    (TimeoutError("deadline exceeded"),            RetryCategory.TIMEOUT,     True),
    (_FakeHTTPError(408),                          RetryCategory.TIMEOUT,     True),
    (_FakeHTTPError(425),                          RetryCategory.TIMEOUT,     True),
    (_FakePgError("57014"),                        RetryCategory.TIMEOUT,     True),
    # RATE_LIMIT
    (_FakeHTTPError(429),                          RetryCategory.RATE_LIMIT,  True),
    (_FakeHTTPError(429, {"Retry-After": "10"}),   RetryCategory.RATE_LIMIT,  True),
    # SERVER
    (_FakeHTTPError(500),                          RetryCategory.SERVER,      True),
    (_FakeHTTPError(502),                          RetryCategory.SERVER,      True),
    (_FakeHTTPError(503),                          RetryCategory.SERVER,      True),
    (_FakeHTTPError(504),                          RetryCategory.SERVER,      True),
    # CLIENT
    (_FakeHTTPError(400),                          RetryCategory.CLIENT,      False),
    (_FakeHTTPError(404),                          RetryCategory.CLIENT,      False),
    (_FakeHTTPError(405),                          RetryCategory.CLIENT,      False),
    (_FakeHTTPError(409),                          RetryCategory.CLIENT,      False),
    (_FakeHTTPError(410),                          RetryCategory.CLIENT,      False),
    (_FakeHTTPError(422),                          RetryCategory.CLIENT,      False),
    # AUTH
    (_FakeHTTPError(401),                          RetryCategory.AUTH,        False),
    (_FakeHTTPError(403),                          RetryCategory.AUTH,        False),
    (_FakePgError("28P01"),                        RetryCategory.AUTH,        False),
    (_FakePgError("28000"),                        RetryCategory.AUTH,        False),
    # VALIDATION
    (ValueError("invalid payload"),                RetryCategory.VALIDATION,  False),
    (_FakePgError("22001"),                        RetryCategory.VALIDATION,  False),
    # DATABASE (retryable)
    (_FakePgError("40001"),                        RetryCategory.DATABASE,    True),
    (_FakePgError("40P01"),                        RetryCategory.DATABASE,    True),
    (_FakePgError("55P03"),                        RetryCategory.DATABASE,    True),
    (_FakePgError("53300"),                        RetryCategory.DATABASE,    True),
    (_FakePgError("57P01"),                        RetryCategory.DATABASE,    True),
    # DATABASE (non-retryable)
    (_FakePgError("23505"),                        RetryCategory.DATABASE,    False),
    (_FakePgError("23502"),                        RetryCategory.DATABASE,    False),
    # UNKNOWN
    (_FakeHTTPError(418),                          RetryCategory.UNKNOWN,     False),
    # Extra SQLSTATE classes added in Medium fixes
    (_FakePgError("53100"),                        RetryCategory.DATABASE,    True),   # disk_full
    (_FakePgError("53200"),                        RetryCategory.DATABASE,    True),   # out_of_memory
    (_FakePgError("58030"),                        RetryCategory.DATABASE,    True),   # io_error
])
def test_category_retryable_and_reason_are_correct(
    exc: BaseException,
    expected_category: RetryCategory,
    expected_retryable: bool,
) -> None:
    decision = ErrorClassifier().classify(exc)

    assert decision.category == expected_category, (
        f"Expected category {expected_category!r}, got {decision.category!r} "
        f"for {exc!r}"
    )
    assert decision.retryable is expected_retryable, (
        f"Expected retryable={expected_retryable} for {exc!r}, "
        f"reason: {decision.reason}"
    )
    assert decision.reason, "reason must be a non-empty string"
    assert decision.reason_code, "reason_code must be a non-empty string"


# ── Redis — end-to-end through the full DEFAULT_RULES pipeline ────────────────
# These specifically prove classify_redis is registered *before* classify_builtin
# in DEFAULT_RULES; classify_redis's own unit tests only prove the function is
# correct in isolation, not that ordering routes exceptions to it first.

@pytest.mark.parametrize("exc_name,retryable,expected_reason_code", [
    ("ConnectionError", True, "redis_connection_error"),
    ("TimeoutError", True, "redis_timeout"),
    ("AuthenticationError", False, "redis_auth_error"),
    ("WatchError", True, "redis_watch_conflict"),
])
def test_redis_errors_classified_end_to_end(
    exc_name: str, retryable: bool, expected_reason_code: str
) -> None:
    redis = pytest.importorskip("redis")
    exc_cls = getattr(redis.exceptions, exc_name)
    decision = ErrorClassifier().classify(exc_cls("boom"))

    assert decision.retryable is retryable
    assert decision.reason_code == expected_reason_code


def test_redis_lock_error_not_swallowed_by_builtin_value_error_end_to_end() -> None:
    """LockError subclasses (RedisError, ValueError). If DEFAULT_RULES ordering
    regressed (classify_builtin running before classify_redis), this would come
    back as reason_code='builtin_value_error' instead of the redis-specific one."""
    redis = pytest.importorskip("redis")
    decision = ErrorClassifier().classify(redis.exceptions.LockError("could not acquire lock"))

    assert decision.retryable is False
    assert decision.reason_code == "redis_lock_error"
    assert decision.category == RetryCategory.VALIDATION


def test_redis_cluster_down_error_not_swallowed_by_generic_response_error() -> None:
    """ClusterDownError subclasses ResponseError; must hit its own branch before
    the generic ResponseError -> non-retryable catch-all."""
    redis = pytest.importorskip("redis")
    decision = ErrorClassifier().classify(
        redis.exceptions.ClusterDownError("CLUSTERDOWN The cluster is down")
    )

    assert decision.retryable is True
    assert decision.reason_code == "redis_cluster_down"


def test_unknown_category_assigned_when_no_rule_matches() -> None:
    class _WeirdError(Exception):
        pass

    decision = ErrorClassifier().classify(_WeirdError("totally unknown"))
    assert decision.category == RetryCategory.UNKNOWN
    assert decision.retryable is False
    assert decision.reason


def test_retry_after_seconds_populated_for_429_with_header() -> None:
    decision = ErrorClassifier().classify(_FakeHTTPError(429, {"Retry-After": "30"}))
    assert decision.retry_after_seconds == 30.0
    assert decision.suggested_delay_seconds == 30.0


def test_retry_after_seconds_none_for_429_without_header() -> None:
    decision = ErrorClassifier().classify(_FakeHTTPError(429))
    assert decision.retry_after_seconds is None
    assert decision.suggested_delay_seconds == 5.0


def test_suggested_delay_present_for_all_retryable_decisions() -> None:
    retryable_examples = [
        ConnectionError("reset"),
        TimeoutError("timeout"),
        _FakeHTTPError(503),
        _FakePgError("40P01"),
    ]
    for exc in retryable_examples:
        decision = ErrorClassifier().classify(exc)
        assert decision.suggested_delay_seconds is not None, (
            f"Expected suggested_delay_seconds for retryable {exc!r}"
        )
        assert decision.suggested_delay_seconds > 0


def test_no_delay_fields_for_non_retryable_decisions() -> None:
    non_retryable_examples = [
        ValueError("bad"),
        _FakeHTTPError(422),
        _FakePgError("23505"),
    ]
    for exc in non_retryable_examples:
        decision = ErrorClassifier().classify(exc)
        assert decision.retry_after_seconds is None
        assert decision.suggested_delay_seconds is None


# ── Direct usage without tenacity ─────────────────────────────────────────────

def test_direct_usage_retryable_produces_actionable_decision() -> None:
    """Library works standalone; caller decides how to retry."""
    classifier = ErrorClassifier()
    exc = TimeoutError("upstream timed out")

    decision = classifier.classify(exc)

    assert decision.retryable is True
    delay = decision.retry_after_seconds or decision.suggested_delay_seconds or 2.0
    assert delay > 0
    assert "retryable" in decision.reason.lower()


def test_direct_usage_non_retryable_produces_clear_message() -> None:
    classifier = ErrorClassifier()
    exc = ValueError("schema validation failed")

    decision = classifier.classify(exc)

    assert decision.retryable is False
    assert decision.reason
    assert decision.reason_code == "builtin_value_error"


def test_celery_style_usage_without_tenacity() -> None:
    """Celery integration uses countdown_from_decision; no tenacity involved."""
    from retryguard.integrations.celery import countdown_from_decision

    classifier = ErrorClassifier()
    exc = _FakeHTTPError(503)
    decision = classifier.classify(exc)

    assert decision.retryable is True
    countdown = countdown_from_decision(decision, default_seconds=5)
    assert isinstance(countdown, int)
    assert countdown >= 0


# ── Tenacity end-to-end ────────────────────────────────────────────────────────

def test_tenacity_retries_retryable_exception_until_success() -> None:
    pytest.importorskip("tenacity")
    from tenacity import retry, stop_after_attempt, wait_fixed

    from retryguard.integrations.tenacity import retry_if_retryguard

    call_count = 0

    @retry(
        retry=retry_if_retryguard(),
        wait=wait_fixed(0),
        stop=stop_after_attempt(3),
    )
    def flaky() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TimeoutError("transient upstream failure")
        return "recovered"

    result = flaky()
    assert result == "recovered"
    assert call_count == 3


def test_tenacity_does_not_retry_non_retryable_exception() -> None:
    pytest.importorskip("tenacity")
    from tenacity import retry, stop_after_attempt, wait_fixed

    from retryguard.integrations.tenacity import retry_if_retryguard

    call_count = 0

    @retry(
        retry=retry_if_retryguard(),
        wait=wait_fixed(0),
        stop=stop_after_attempt(5),
    )
    def strict() -> str:
        nonlocal call_count
        call_count += 1
        raise ValueError("bad payload — permanent failure")

    with pytest.raises(ValueError, match="bad payload"):
        strict()

    assert call_count == 1, "non-retryable exception must not trigger any retry"


def test_tenacity_exhausts_all_attempts_for_persistent_retryable_error() -> None:
    pytest.importorskip("tenacity")
    from tenacity import RetryError, retry, stop_after_attempt, wait_fixed

    from retryguard.integrations.tenacity import retry_if_retryguard

    call_count = 0

    @retry(
        retry=retry_if_retryguard(),
        wait=wait_fixed(0),
        stop=stop_after_attempt(3),
    )
    def always_down() -> str:
        nonlocal call_count
        call_count += 1
        raise ConnectionError("service permanently down")

    with pytest.raises(RetryError):
        always_down()

    assert call_count == 3, "should have attempted all 3 times before giving up"


def test_tenacity_auth_error_propagates_immediately() -> None:
    pytest.importorskip("tenacity")
    from tenacity import retry, stop_after_attempt, wait_fixed

    from retryguard.integrations.tenacity import retry_if_retryguard

    call_count = 0

    @retry(
        retry=retry_if_retryguard(),
        wait=wait_fixed(0),
        stop=stop_after_attempt(5),
    )
    def needs_auth() -> str:
        nonlocal call_count
        call_count += 1
        raise _FakeHTTPError(401)

    with pytest.raises(_FakeHTTPError):
        needs_auth()

    assert call_count == 1, "AUTH error must not be retried"


def test_tenacity_rate_limit_is_retried() -> None:
    pytest.importorskip("tenacity")
    from tenacity import retry, stop_after_attempt, wait_fixed

    from retryguard.integrations.tenacity import retry_if_retryguard

    call_count = 0

    @retry(
        retry=retry_if_retryguard(),
        wait=wait_fixed(0),
        stop=stop_after_attempt(4),
    )
    def rate_limited() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise _FakeHTTPError(429)
        return "ok"

    assert rate_limited() == "ok"
    assert call_count == 3


def test_tenacity_server_error_is_retried() -> None:
    pytest.importorskip("tenacity")
    from tenacity import retry, stop_after_attempt, wait_fixed

    from retryguard.integrations.tenacity import retry_if_retryguard

    call_count = 0

    @retry(
        retry=retry_if_retryguard(),
        wait=wait_fixed(0),
        stop=stop_after_attempt(4),
    )
    def flaky_service() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise _FakeHTTPError(503)
        return "ok"

    assert flaky_service() == "ok"
    assert call_count == 3


def test_tenacity_db_deadlock_is_retried() -> None:
    pytest.importorskip("tenacity")
    from tenacity import retry, stop_after_attempt, wait_fixed

    from retryguard.integrations.tenacity import retry_if_retryguard

    call_count = 0

    @retry(
        retry=retry_if_retryguard(),
        wait=wait_fixed(0),
        stop=stop_after_attempt(4),
    )
    def write_with_contention() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise _FakePgError("40P01", "deadlock detected")
        return "committed"

    assert write_with_contention() == "committed"
    assert call_count == 3


def test_tenacity_db_constraint_violation_not_retried() -> None:
    pytest.importorskip("tenacity")
    from tenacity import retry, stop_after_attempt, wait_fixed

    from retryguard.integrations.tenacity import retry_if_retryguard

    call_count = 0

    @retry(
        retry=retry_if_retryguard(),
        wait=wait_fixed(0),
        stop=stop_after_attempt(5),
    )
    def duplicate_insert() -> str:
        nonlocal call_count
        call_count += 1
        raise _FakePgError("23505", "unique violation")

    with pytest.raises(_FakePgError):
        duplicate_insert()

    assert call_count == 1, "constraint violation (23505) must not be retried"


# ── Logging in a live retry loop ──────────────────────────────────────���────────

def test_before_sleep_hook_logs_structured_decision_on_each_retry() -> None:
    pytest.importorskip("tenacity")
    from tenacity import retry, stop_after_attempt, wait_fixed

    from retryguard.integrations.tenacity import before_sleep_log_retryguard, retry_if_retryguard

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("retryguard.e2e.loop")
    logger.addHandler(_Capture())
    logger.setLevel(logging.DEBUG)

    call_count = 0

    @retry(
        retry=retry_if_retryguard(),
        wait=wait_fixed(0),
        stop=stop_after_attempt(3),
        before_sleep=before_sleep_log_retryguard(logger, level=logging.WARNING),
    )
    def flaky() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TimeoutError("transient")
        return "done"

    flaky()

    # before_sleep fires between attempt 1→2 and 2→3, so exactly 2 records.
    assert len(records) == 2
    for r in records:
        assert r.levelno == logging.WARNING
        # structured metadata is merged into the LogRecord dict by Python logging
        assert "retryguard" in r.__dict__, "structured retryguard dict missing from log record"
        meta = r.__dict__["retryguard"]
        assert meta["retryable"] is True
        assert meta["reason_code"] == "builtin_timeout"
        assert meta["category"] == RetryCategory.TIMEOUT.value
        assert meta["reason"]


def test_non_retryable_produces_no_before_sleep_log() -> None:
    """before_sleep is never called for non-retryable exceptions since tenacity
    raises immediately without sleeping."""
    pytest.importorskip("tenacity")
    from tenacity import retry, stop_after_attempt, wait_fixed

    from retryguard.integrations.tenacity import before_sleep_log_retryguard, retry_if_retryguard

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("retryguard.e2e.noretry")
    logger.addHandler(_Capture())
    logger.setLevel(logging.DEBUG)

    @retry(
        retry=retry_if_retryguard(),
        wait=wait_fixed(0),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log_retryguard(logger),
    )
    def strict() -> str:
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        strict()

    assert len(records) == 0, (
        "before_sleep must not fire for non-retryable exceptions"
    )
