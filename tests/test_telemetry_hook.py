from __future__ import annotations

import logging

import pytest

from retryguard import ErrorClassifier, RetryCategory, RetryDecision


class _UnknownError(Exception):
    pass


# ── hook fires with correct args ────────────────────────────────────────────────


def test_hook_fires_with_matched_rule_decision() -> None:
    calls: list[tuple[BaseException, RetryDecision]] = []
    classifier = ErrorClassifier(on_decision=lambda exc, d: calls.append((exc, d)))

    exc = TimeoutError("t")
    decision = classifier.classify(exc)

    assert len(calls) == 1
    logged_exc, logged_decision = calls[0]
    assert logged_exc is exc
    assert logged_decision == decision
    assert logged_decision.reason_code == "builtin_timeout"


def test_hook_fires_for_unknown_fallback_decision() -> None:
    calls: list[tuple[BaseException, RetryDecision]] = []
    classifier = ErrorClassifier(on_decision=lambda exc, d: calls.append((exc, d)))

    decision = classifier.classify(_UnknownError("x"))

    assert len(calls) == 1
    assert calls[0][1] == decision
    assert decision.reason_code == "unknown"


def test_hook_fires_exactly_once_even_with_earlier_crashing_rule() -> None:
    calls: list[RetryDecision] = []

    def bad_rule(_exc: BaseException) -> RetryDecision | None:
        raise RuntimeError("rule bug")

    def good_rule(exc: BaseException) -> RetryDecision | None:
        if isinstance(exc, TimeoutError):
            return RetryDecision(
                retryable=True,
                category=RetryCategory.TIMEOUT,
                reason_code="good_rule",
                reason="good rule matched",
            )
        return None

    classifier = ErrorClassifier(
        rules=(bad_rule, good_rule), on_decision=lambda exc, d: calls.append(d)
    )
    classifier.classify(TimeoutError("t"))

    assert len(calls) == 1
    assert calls[0].reason_code == "good_rule"


# ── hook failure isolation ──────────────────────────────────────────────────────


def test_hook_raising_does_not_propagate_and_does_not_change_decision(caplog) -> None:
    def broken_hook(_exc: BaseException, _decision: RetryDecision) -> None:
        raise RuntimeError("telemetry sink is down")

    classifier = ErrorClassifier(on_decision=broken_hook)

    with caplog.at_level(logging.ERROR, logger="retryguard.classifier"):
        decision = classifier.classify(TimeoutError("t"))

    assert decision.retryable is True
    assert decision.reason_code == "builtin_timeout"

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert "on_decision" in record.message
    assert record.exc_info is not None, "log record should include the hook's traceback"


def test_hook_raising_on_unknown_fallback_still_returns_unknown_decision(caplog) -> None:
    def broken_hook(_exc: BaseException, _decision: RetryDecision) -> None:
        raise ValueError("boom")

    classifier = ErrorClassifier(on_decision=broken_hook)

    with caplog.at_level(logging.ERROR, logger="retryguard.classifier"):
        decision = classifier.classify(_UnknownError("x"))

    assert decision.retryable is False
    assert decision.category == RetryCategory.UNKNOWN
    assert len(caplog.records) == 1


# ── no hook (default) is a true no-op ───────────────────────────────────────────


def test_no_hook_by_default_behaves_identically(caplog) -> None:
    with caplog.at_level(logging.ERROR, logger="retryguard.classifier"):
        decision = ErrorClassifier().classify(TimeoutError("t"))

    assert decision.retryable is True
    assert decision.reason_code == "builtin_timeout"
    assert caplog.records == []


def test_rules_still_works_positionally_with_on_decision_keyword_only() -> None:
    calls: list[RetryDecision] = []
    classifier = ErrorClassifier(
        ErrorClassifier.DEFAULT_RULES, on_decision=lambda exc, d: calls.append(d)
    )
    decision = classifier.classify(TimeoutError("t"))

    assert decision.reason_code == "builtin_timeout"
    assert len(calls) == 1


# ── hook fires through the tenacity integration (single chokepoint) ────────────


def test_hook_fires_through_retry_if_retryguard() -> None:
    pytest.importorskip("tenacity")
    from tenacity import retry, stop_after_attempt, wait_fixed

    from retryguard.integrations.tenacity import retry_if_retryguard

    calls: list[RetryDecision] = []
    classifier = ErrorClassifier(on_decision=lambda exc, d: calls.append(d))

    call_count = 0

    @retry(retry=retry_if_retryguard(classifier), wait=wait_fixed(0), stop=stop_after_attempt(3))
    def flaky() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise TimeoutError("transient")
        return "done"

    result = flaky()

    assert result == "done"
    # classify() only runs when the wrapped function raises; the 3rd (successful)
    # attempt never gets classified, so 2 calls for 2 failures, not 3.
    assert len(calls) == 2
    assert all(d.reason_code == "builtin_timeout" for d in calls)
