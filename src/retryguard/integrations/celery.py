from __future__ import annotations

from ..models import RetryDecision


def countdown_from_decision(
    decision: RetryDecision,
    *,
    default_seconds: int = 2,
    min_seconds: int = 0,
    max_seconds: int | None = None,
) -> int:
    value = decision.retry_after_seconds or decision.suggested_delay_seconds
    seconds = int(value) if value is not None else int(default_seconds)

    if seconds < min_seconds:
        seconds = int(min_seconds)
    if max_seconds is not None and seconds > max_seconds:
        seconds = int(max_seconds)

    return seconds
