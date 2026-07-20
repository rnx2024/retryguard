from __future__ import annotations

import logging

from retryguard import ErrorClassifier, RetryCategory, RetryDecision, classify_error, should_retry
from retryguard.classifier import default_classifier


class _UnknownError(Exception):
    pass


# ── Unknown exception fallback ─────────────────────────────────────────────────


def test_unknown_exception_falls_through_to_unknown_category() -> None:
    classifier = ErrorClassifier()
    decision = classifier.classify(_UnknownError("what is this"))
    assert decision.retryable is False
    assert decision.category == RetryCategory.UNKNOWN
    assert decision.reason_code == "unknown"


def test_unknown_exception_reason_mentions_no_rule_matched() -> None:
    decision = ErrorClassifier().classify(_UnknownError("x"))
    assert "no retry rule matched" in decision.reason.lower()


def test_empty_rule_list_always_returns_unknown() -> None:
    classifier = ErrorClassifier(rules=())
    decision = classifier.classify(TimeoutError("no rules to match"))
    assert decision.retryable is False
    assert decision.category == RetryCategory.UNKNOWN


# ── Custom rules ───────────────────────────────────────────────────────────────


def test_custom_rule_prepended_takes_priority() -> None:
    def my_rule(exc: BaseException) -> RetryDecision | None:
        if isinstance(exc, _UnknownError):
            return RetryDecision(
                retryable=True,
                category=RetryCategory.SERVER,
                reason_code="my_custom",
                reason="Custom rule matched.",
            )
        return None

    classifier = ErrorClassifier(rules=(my_rule, *ErrorClassifier.DEFAULT_RULES))
    decision = classifier.classify(_UnknownError("now handled"))
    assert decision.retryable is True
    assert decision.reason_code == "my_custom"


def test_custom_rule_returning_none_falls_through_to_defaults() -> None:
    def pass_through(_exc: BaseException) -> RetryDecision | None:
        return None

    classifier = ErrorClassifier(rules=(pass_through, *ErrorClassifier.DEFAULT_RULES))
    decision = classifier.classify(TimeoutError("still handled by builtin"))
    assert decision.retryable is True
    assert decision.reason_code == "builtin_timeout"


def test_rules_are_evaluated_in_order() -> None:
    calls: list[str] = []

    def rule_a(exc: BaseException) -> RetryDecision | None:
        calls.append("a")
        return None

    def rule_b(exc: BaseException) -> RetryDecision | None:
        calls.append("b")
        return RetryDecision(
            retryable=False,
            category=RetryCategory.UNKNOWN,
            reason_code="rule_b",
            reason="rule b fired",
        )

    def rule_c(exc: BaseException) -> RetryDecision | None:
        calls.append("c")
        return None

    classifier = ErrorClassifier(rules=(rule_a, rule_b, rule_c))
    classifier.classify(_UnknownError("x"))
    assert calls == ["a", "b"]


# ── classify_error / should_retry helpers ─────────────────────────────────────


def test_classify_error_with_explicit_classifier() -> None:
    classifier = ErrorClassifier()
    decision = classify_error(TimeoutError("t"), classifier=classifier)
    assert decision.retryable is True


def test_classify_error_with_none_uses_default_classifier() -> None:
    decision = classify_error(ValueError("v"))
    assert decision.retryable is False


def test_should_retry_returns_true_for_retryable() -> None:
    assert should_retry(TimeoutError("t")) is True


def test_should_retry_returns_false_for_non_retryable() -> None:
    assert should_retry(ValueError("v")) is False


def test_should_retry_with_explicit_classifier() -> None:
    classifier = ErrorClassifier()
    assert should_retry(ConnectionError("c"), classifier=classifier) is True


def test_should_retry_unknown_exception_returns_false() -> None:
    assert should_retry(_UnknownError("unknown")) is False


# ── default_classifier singleton ───────────────────────────────────────────────


def test_default_classifier_is_singleton() -> None:
    a = default_classifier()
    b = default_classifier()
    assert a is b


def test_default_classifier_uses_default_rules() -> None:
    classifier = default_classifier()
    assert classifier._rules is ErrorClassifier.DEFAULT_RULES


# ── RetryDecision immutability ─────────────────────────────────────────────────


def test_retry_decision_is_immutable() -> None:
    import dataclasses

    decision = ErrorClassifier().classify(TimeoutError("t"))
    assert dataclasses.is_dataclass(decision)

    try:
        decision.retryable = False  # type: ignore[misc]
        raised = False
    except (AttributeError, TypeError):
        raised = True

    assert raised, "RetryDecision should be frozen/immutable"


# ── Rule exception safety ──────────────────────────────────────────────────────


def test_crashing_rule_is_skipped_and_next_rule_runs() -> None:
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

    classifier = ErrorClassifier(rules=(bad_rule, good_rule))
    decision = classifier.classify(TimeoutError("t"))
    assert decision.reason_code == "good_rule"


def test_all_rules_crash_falls_through_to_unknown() -> None:
    def always_raises(_exc: BaseException) -> RetryDecision | None:
        raise ValueError("rule broken")

    classifier = ErrorClassifier(rules=(always_raises,))
    decision = classifier.classify(TimeoutError("t"))
    assert decision.retryable is False
    assert decision.category == RetryCategory.UNKNOWN


def test_crashing_rule_is_logged_and_next_rule_still_runs(caplog) -> None:
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

    classifier = ErrorClassifier(rules=(bad_rule, good_rule))
    with caplog.at_level(logging.ERROR, logger="retryguard.classifier"):
        decision = classifier.classify(TimeoutError("t"))

    assert decision.reason_code == "good_rule"
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert "bad_rule" in record.message
    assert "TimeoutError" in record.message
    assert record.exc_info is not None, "log record should include the rule's traceback"


def test_crashing_rule_with_no_match_still_logs_and_falls_through(caplog) -> None:
    def always_raises(_exc: BaseException) -> RetryDecision | None:
        raise ValueError("rule broken")

    classifier = ErrorClassifier(rules=(always_raises,))
    with caplog.at_level(logging.ERROR, logger="retryguard.classifier"):
        decision = classifier.classify(TimeoutError("t"))

    assert decision.retryable is False
    assert decision.category == RetryCategory.UNKNOWN
    assert len(caplog.records) == 1
    assert "always_raises" in caplog.records[0].message


def test_no_crashing_rule_produces_no_error_logs(caplog) -> None:
    with caplog.at_level(logging.ERROR, logger="retryguard.classifier"):
        ErrorClassifier().classify(TimeoutError("t"))

    assert caplog.records == []


def test_crashing_non_function_rule_logs_via_repr_fallback(caplog) -> None:
    class _BadCallableRule:
        def __call__(self, _exc: BaseException) -> RetryDecision | None:
            raise RuntimeError("callable object rule bug")

    def good_rule(exc: BaseException) -> RetryDecision | None:
        if isinstance(exc, TimeoutError):
            return RetryDecision(
                retryable=True,
                category=RetryCategory.TIMEOUT,
                reason_code="good_rule",
                reason="good rule matched",
            )
        return None

    classifier = ErrorClassifier(rules=(_BadCallableRule(), good_rule))
    with caplog.at_level(logging.ERROR, logger="retryguard.classifier"):
        decision = classifier.classify(TimeoutError("t"))

    assert decision.reason_code == "good_rule"
    assert len(caplog.records) == 1
    assert "_BadCallableRule" in caplog.records[0].message


# ── RetryDecision optional fields ─────────────────────────────────────────────


def test_retry_decision_optional_fields_default_to_none() -> None:
    decision = ErrorClassifier().classify(ValueError("v"))
    assert decision.retry_after_seconds is None
    assert decision.suggested_delay_seconds is None


def test_retry_decision_retryable_has_suggested_delay() -> None:
    decision = ErrorClassifier().classify(TimeoutError("t"))
    assert decision.suggested_delay_seconds is not None
    assert decision.suggested_delay_seconds > 0
