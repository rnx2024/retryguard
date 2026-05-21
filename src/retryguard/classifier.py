from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from .models import RetryCategory, RetryDecision
from .rules import (
    classify_builtin,
    classify_http_status,
    classify_httpx,
    classify_postgres_sqlstate,
    classify_psycopg,
    classify_asyncpg,
    classify_requests,
    classify_sqlalchemy,
)

ClassifierRule = Callable[[BaseException], RetryDecision | None]


DEFAULT_RULES: tuple[ClassifierRule, ...] = (
    classify_http_status,
    classify_httpx,
    classify_requests,
    classify_sqlalchemy,
    classify_builtin,
    classify_postgres_sqlstate,
    classify_psycopg,
    classify_asyncpg,
)


class ErrorClassifier:
    DEFAULT_RULES: tuple[ClassifierRule, ...] = DEFAULT_RULES

    def __init__(self, rules: tuple[ClassifierRule, ...] = DEFAULT_RULES) -> None:
        self._rules = rules

    def classify(self, exc: BaseException) -> RetryDecision:
        for rule in self._rules:
            try:
                decision = rule(exc)
            except Exception:
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


def classify_error(exc: BaseException, *, classifier: ErrorClassifier | None = None) -> RetryDecision:
    return (classifier or default_classifier()).classify(exc)


def should_retry(exc: BaseException, *, classifier: ErrorClassifier | None = None) -> bool:
    return classify_error(exc, classifier=classifier).retryable
