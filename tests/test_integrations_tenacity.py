import logging

import pytest

tenacity = pytest.importorskip("tenacity")

from retryguard import ErrorClassifier  # noqa: E402
from retryguard.integrations.tenacity import (  # noqa: E402
    before_sleep_log_retryguard,
    retry_if_retryguard,
    wait_retryguard,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

class _FailedOutcome:
    failed = True

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def exception(self) -> BaseException:
        return self._exc


class _SuccessOutcome:
    failed = False

    def exception(self) -> None:
        return None


class _RetryState:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome


# ── retry_if_retryguard ────────────────────────────────────────────────────────

def test_tenacity_retry_predicate_uses_classifier() -> None:
    classifier = ErrorClassifier()
    # retry_if_exception wraps a predicate; call .predicate directly to test it.
    retry_predicate = retry_if_retryguard(classifier)

    assert retry_predicate.predicate(TimeoutError("boom")) is True
    assert retry_predicate.predicate(ValueError("bad input")) is False


def test_retry_if_retryguard_uses_default_classifier_when_none_given() -> None:
    predicate = retry_if_retryguard()
    assert predicate.predicate(TimeoutError("t")) is True
    assert predicate.predicate(ValueError("v")) is False


# ── wait_retryguard ────────────────────────────────────────────────────────────

def test_tenacity_wait_strategy_uses_classifier_suggestion() -> None:
    classifier = ErrorClassifier()
    wait = wait_retryguard(classifier, fallback_seconds=3.0)

    class Outcome:
        failed = True

        def exception(self):
            return TimeoutError("boom")

    class RetryState:
        outcome = Outcome()

    assert wait(RetryState()) == 2.0


def test_wait_retryguard_returns_fallback_when_outcome_is_none() -> None:
    wait = wait_retryguard(fallback_seconds=4.0)
    state = _RetryState(outcome=None)
    assert wait(state) == 4.0


def test_wait_retryguard_returns_fallback_when_outcome_not_failed() -> None:
    wait = wait_retryguard(fallback_seconds=4.0)
    state = _RetryState(outcome=_SuccessOutcome())
    assert wait(state) == 4.0


def test_wait_retryguard_uses_retry_after_from_http_response() -> None:
    class FakeResponse:
        status_code = 429
        headers = {"Retry-After": "12"}

    class FakeHTTPError(Exception):
        response = FakeResponse()

    wait = wait_retryguard(fallback_seconds=1.0)
    state = _RetryState(outcome=_FailedOutcome(FakeHTTPError()))
    assert wait(state) == 12.0


def test_wait_retryguard_uses_default_classifier_when_none_given() -> None:
    wait = wait_retryguard(fallback_seconds=3.0)
    state = _RetryState(outcome=_FailedOutcome(TimeoutError("t")))
    assert wait(state) == 2.0


# ── before_sleep_log_retryguard ────────────────────────────────────────────────

def test_before_sleep_log_retryguard_logs_on_retryable_exception() -> None:
    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("retryguard.test.sleep_log")
    logger.addHandler(CapturingHandler())
    logger.setLevel(logging.DEBUG)

    hook = before_sleep_log_retryguard(logger, level=logging.WARNING)
    state = _RetryState(outcome=_FailedOutcome(TimeoutError("t")))
    hook(state)

    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert "retryguard" in records[0].__dict__


def test_before_sleep_log_retryguard_does_not_log_when_outcome_is_none() -> None:
    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("retryguard.test.sleep_log_none")
    logger.addHandler(CapturingHandler())
    logger.setLevel(logging.DEBUG)

    hook = before_sleep_log_retryguard(logger)
    hook(_RetryState(outcome=None))

    assert len(records) == 0


def test_before_sleep_log_retryguard_uses_default_classifier_when_none_given() -> None:
    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("retryguard.test.sleep_log_default")
    logger.addHandler(CapturingHandler())
    logger.setLevel(logging.DEBUG)

    hook = before_sleep_log_retryguard(logger)
    hook(_RetryState(outcome=_FailedOutcome(TimeoutError("t"))))

    assert len(records) == 1
