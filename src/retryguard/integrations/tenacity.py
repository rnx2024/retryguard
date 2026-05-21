from __future__ import annotations

import logging

from ..classifier import ErrorClassifier, default_classifier

try:
    from tenacity import retry_if_exception
    from tenacity.wait import wait_base
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "retryguard.integrations.tenacity requires tenacity. Install with `retryguard[retry]`."
    ) from exc


def retry_if_retryguard(classifier: ErrorClassifier | None = None):
    classifier = classifier or default_classifier()
    return retry_if_exception(lambda e: classifier.classify(e).retryable)


class wait_retryguard(wait_base):
    def __init__(self, classifier: ErrorClassifier | None = None, *, fallback_seconds: float = 1.0) -> None:
        self._classifier = classifier or default_classifier()
        self._fallback_seconds = float(fallback_seconds)

    def __call__(self, retry_state) -> float:
        outcome = getattr(retry_state, "outcome", None)
        if outcome is None or not getattr(outcome, "failed", False):
            return self._fallback_seconds

        exc = outcome.exception()
        decision = self._classifier.classify(exc)
        return float(decision.retry_after_seconds or decision.suggested_delay_seconds or self._fallback_seconds)


def before_sleep_log_retryguard(
    logger: logging.Logger,
    *,
    level: int = logging.WARNING,
    classifier: ErrorClassifier | None = None,
):
    classifier = classifier or default_classifier()

    def _hook(retry_state) -> None:
        outcome = getattr(retry_state, "outcome", None)
        if outcome is None or not getattr(outcome, "failed", False):
            return

        exc = outcome.exception()
        decision = classifier.classify(exc)
        extra = {
            "retryguard": {
                "retryable": decision.retryable,
                "category": decision.category.value,
                "reason_code": decision.reason_code,
                "reason": decision.reason,
                "retry_after_seconds": decision.retry_after_seconds,
                "suggested_delay_seconds": decision.suggested_delay_seconds,
            }
        }

        # Keep message stable and log structured fields separately.
        logger.log(
            level,
            "RetryGuard retry=%s code=%s category=%s reason=%s",
            decision.retryable,
            decision.reason_code,
            decision.category.value,
            decision.reason,
            extra=extra,
        )

    return _hook
