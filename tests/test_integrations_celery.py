from __future__ import annotations

from retryguard.integrations.celery import countdown_from_decision
from retryguard.models import RetryCategory, RetryDecision


def _decision(
    *,
    retry_after: float | None = None,
    suggested: float | None = None,
) -> RetryDecision:
    return RetryDecision(
        retryable=True,
        category=RetryCategory.SERVER,
        reason_code="test",
        reason="test decision",
        retry_after_seconds=retry_after,
        suggested_delay_seconds=suggested,
    )


# ── Basic delay selection ──────────────────────────────────────────────────────

def test_uses_retry_after_seconds_when_set() -> None:
    assert countdown_from_decision(_decision(retry_after=10.0)) == 10


def test_falls_back_to_suggested_delay_when_no_retry_after() -> None:
    assert countdown_from_decision(_decision(suggested=5.0)) == 5


def test_uses_default_when_neither_delay_is_set() -> None:
    assert countdown_from_decision(_decision(), default_seconds=3) == 3


def test_default_is_two_when_not_specified() -> None:
    assert countdown_from_decision(_decision()) == 2


def test_retry_after_takes_priority_over_suggested_delay() -> None:
    assert countdown_from_decision(_decision(retry_after=7.0, suggested=20.0)) == 7


# ── Clamping behaviour ─────────────────────────────────────────────────────────

def test_respects_min_seconds_lower_bound() -> None:
    assert countdown_from_decision(_decision(suggested=1.0), min_seconds=5) == 5


def test_respects_max_seconds_upper_bound() -> None:
    assert countdown_from_decision(_decision(suggested=100.0), max_seconds=30) == 30


def test_value_between_min_and_max_is_unchanged() -> None:
    assert countdown_from_decision(_decision(suggested=15.0), min_seconds=5, max_seconds=30) == 15


def test_min_and_max_both_applied_when_value_exceeds_max() -> None:
    assert countdown_from_decision(_decision(suggested=50.0), min_seconds=10, max_seconds=30) == 30


def test_min_clamps_when_value_is_below_min() -> None:
    assert countdown_from_decision(_decision(suggested=1.0), min_seconds=10, max_seconds=30) == 10


def test_default_min_is_zero_so_no_clamping_from_below() -> None:
    assert countdown_from_decision(_decision(suggested=0.0)) == 0


def test_float_delay_is_truncated_to_int() -> None:
    assert isinstance(countdown_from_decision(_decision(retry_after=7.9)), int)
    assert countdown_from_decision(_decision(retry_after=7.9)) == 7


def test_non_retryable_decision_also_works() -> None:
    decision = RetryDecision(
        retryable=False,
        category=RetryCategory.CLIENT,
        reason_code="http_422",
        reason="not retryable",
        suggested_delay_seconds=0.0,
    )
    assert countdown_from_decision(decision) == 0
