from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache

from .models import RetryCategory, RetryDecision
from .rules import (
    classify_aws,
    classify_azure,
    classify_builtin,
    classify_gcp,
    classify_http_status,
    classify_httpx,
    classify_postgres_sqlstate,
    classify_psycopg,
    classify_asyncpg,
    classify_redis,
    classify_requests,
    classify_sqlalchemy,
)

_logger = logging.getLogger(__name__)

ClassifierRule = Callable[[BaseException], RetryDecision | None]
DecisionHook = Callable[[BaseException, RetryDecision], None]


DEFAULT_RULES: tuple[ClassifierRule, ...] = (
    # classify_gcp/classify_azure must precede classify_http_status: their
    # exceptions expose a `.code`/`.status_code` attribute that
    # extract_status_code already reads, so classify_http_status would otherwise
    # intercept every GCP/Azure exception first.
    classify_gcp,
    classify_azure,
    classify_http_status,
    classify_httpx,
    classify_requests,
    classify_redis,
    classify_aws,
    classify_sqlalchemy,
    classify_builtin,
    classify_postgres_sqlstate,
    classify_psycopg,
    classify_asyncpg,
)


class ErrorClassifier:
    DEFAULT_RULES: tuple[ClassifierRule, ...] = DEFAULT_RULES

    def __init__(
        self,
        rules: tuple[ClassifierRule, ...] = DEFAULT_RULES,
        *,
        on_decision: DecisionHook | None = None,
    ) -> None:
        self._rules = rules
        self._on_decision = on_decision

    def classify(self, exc: BaseException) -> RetryDecision:
        decision = self._classify(exc)
        if self._on_decision is not None:
            try:
                self._on_decision(exc, decision)
            except Exception:
                _logger.exception(
                    "retryguard on_decision hook raised; classification unaffected "
                    "(reason_code=%s)",
                    decision.reason_code,
                )
        return decision

    def _classify(self, exc: BaseException) -> RetryDecision:
        for rule in self._rules:
            try:
                decision = rule(exc)
            except Exception:
                _logger.exception(
                    "retryguard rule %s raised while classifying %s; skipping to next rule",
                    getattr(rule, "__name__", repr(rule)),
                    type(exc).__name__,
                )
                continue
            if decision is not None:
                return decision

        return RetryDecision(
            retryable=False,
            category=RetryCategory.UNKNOWN,
            reason_code="unknown",
            reason="No retry rule matched; defaulting to non-retryable.",
        )


@lru_cache(maxsize=1)
def default_classifier() -> ErrorClassifier:
    # Lazy singleton to avoid import-time side effects and keep "defaults" overridable
    # by explicitly passing a classifier to helpers.
    return ErrorClassifier()


def classify_error(
    exc: BaseException, *, classifier: ErrorClassifier | None = None
) -> RetryDecision:
    return (classifier or default_classifier()).classify(exc)


def should_retry(exc: BaseException, *, classifier: ErrorClassifier | None = None) -> bool:
    return classify_error(exc, classifier=classifier).retryable
