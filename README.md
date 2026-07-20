# retryguard

Small, dependency-light library that classifies exceptions into **retryable** vs **non-retryable** decisions.

Goal: make every backend service call *one* classifier before retrying anything, so you stop blindly retrying
things like `400`, `401`, validation failures, or malformed payloads.

## What it returns

For any exception, `retryguard` returns a `RetryDecision`:

- `retryable: bool`
- `reason: str`
- `reason_code: str` (stable, machine-usable)
- `category: RetryCategory`
- `retry_after_seconds: float | None`
- `suggested_delay_seconds: float | None`

## Capabilities

`retryguard` is a **policy classifier**: it inspects exceptions (types and attributes) and returns a
`RetryDecision`. It does **not** perform any I/O and it does **not** implement retry loops/backoff
itself.

It classifies:

- **HTTP status codes**: common retryable/non-retryable codes, including `Retry-After` support for `429`.
- **httpx / requests** (when installed): timeout vs network exception types.
- **Postgres via SQLSTATE** (when available): extracts `sqlstate`/`pgcode` (including wrapped exceptions)
  and maps transient/non-transient codes to decisions.
- **SQLAlchemy** (when installed): pool timeouts and `DBAPIError.connection_invalidated`, plus SQLSTATE
  extraction from the wrapped DBAPI exception chain.
- **Redis / redis-py** (when installed): type-based dispatch over `redis.exceptions.*` — no message
  parsing, since redis-py already parses the wire-protocol error into distinct exception classes.
- **Builtins**: `TimeoutError` (retryable), `ConnectionError`/`OSError` (retryable), `ValueError`
  (non-retryable).

Unknowns default to **non-retryable**.

## Default policy (Phase 1)

Retryable by default:

- timeouts, connection resets, DNS/network blips
- rate limits
- HTTP `408, 425, 429, 500, 502, 503, 504`
- Postgres transient SQLSTATEs (works via `sqlstate/pgcode` extraction; supports wrappers like SQLAlchemy)

Non-retryable by default:

- validation/parsing errors
- bad credentials / auth failures
- HTTP `400, 401, 403, 404, 405, 409, 410, 422`

Unknowns default to **non-retryable**

## Postgres / SQLAlchemy / asyncpg / psycopg3

`retryguard` classifies Postgres errors primarily via SQLSTATE, including when wrapped by
SQLAlchemy (`.orig`, `__cause__`, `__context__` are unwrapped).

If a SQLAlchemy `DBAPIError` has no SQLSTATE anywhere in its exception chain and doesn't match the
SQLSTATE-based rules, `retryguard` returns a non-retryable `DATABASE` decision with
`reason_code="sqlalchemy_unclassified_dbapi_error"` (instead of falling through and potentially ending up
as `UNKNOWN`).

Retryable examples:

- `08xxx` connection exceptions
- `40001` serialization failure
- `40P01` deadlock detected
- `55P03` lock not available
- `53xxx` insufficient resources (too many connections, disk full, out of memory)
- `57014` query canceled (often statement timeout)
- `57P01/57P02/57P03` shutdown / cannot connect now
- `58xxx` system errors (I/O error, undefined file)

Non-retryable examples:

- `23xxx` constraint violations (e.g. `23505` unique violation)
- `28xxx` invalid authorization
- `22xxx` data exceptions (invalid input, etc.)

## Redis / redis-py

`retryguard` classifies `redis.exceptions.*` by type — never by parsing the exception message.
redis-py already converts the server's wire-protocol error prefix (`-LOADING`, `-READONLY`,
`-NOSCRIPT`, `-MOVED`, etc.) into a distinct Python exception class before your code sees it, so
`classify_redis` is a pure `isinstance` dispatch, the same shape as `classify_httpx`.

Two subtleties drove the branch order in `classify_redis` (checked narrow-subclass-first,
broad-parent-last):

- `AuthenticationError`/`AuthorizationError` are `ConnectionError` subclasses in redis-py. They're
  checked *before* the generic `ConnectionError` branch and marked non-retryable — otherwise bad
  credentials would get retried, which is exactly what this library exists to prevent.
- `ClusterDownError`/`TryAgainError` are `ResponseError` subclasses. They're checked *before* the
  generic `ResponseError` catch-all so transient cluster-resharding states aren't misclassified as
  permanent failures.

Retryable examples:

- `ConnectionError`, `TimeoutError`, `BusyLoadingError` (still loading), `MaxConnectionsError`
  (pool exhausted)
- `WatchError` — optimistic-lock conflict on a watched key; same concept as Postgres `40001`
  (serialization_failure), which is also retryable
- `ClusterDownError` (and its subclass `MasterDownError`), `TryAgainError` — transient cluster state
- `ReadOnlyError` — replica hasn't finished promotion to primary during failover

Non-retryable examples:

- `AuthenticationError`, `AuthorizationError` — bad credentials/permissions
- `MovedError`, `AskError` — cluster redirects; the fix is reconnecting to a different node, not
  retrying the same connection
- `NoScriptError` — retrying `EVALSHA` fails identically without reloading the script first
  (`SCRIPT LOAD` / `EVAL`)
- `LockError` / `LockNotOwnedError`, `DataError`, `InvalidResponse`, and any other `ResponseError`
  (syntax/argument errors, e.g. `WRONGTYPE`)

## Usage

```python
from retryguard import ErrorClassifier

classifier = ErrorClassifier()

try:
    ...
except Exception as exc:
    decision = classifier.classify(exc)
    if decision.retryable:
        delay = decision.retry_after_seconds or decision.suggested_delay_seconds or 2.0
        print("retry", delay, decision.reason_code, decision.reason)
    else:
        print("fail", decision.reason_code, decision.reason)
```

## Celery example (don’t retry blindly)

```python
from celery import shared_task
from retryguard import ErrorClassifier
from retryguard.integrations.celery import countdown_from_decision

classifier = ErrorClassifier()


@shared_task(bind=True, max_retries=5)
def run_job(self, payload: dict) -> str:
    try:
        return do_work(payload)
    except Exception as exc:
        decision = classifier.classify(exc)
        if not decision.retryable:
            raise

        delay = countdown_from_decision(decision, default_seconds=2)
        raise self.retry(exc=exc, countdown=delay)
```

## Tenacity (build on top, don’t reimplement)

Tenacity handles *how* to retry (stop/backoff/jitter). `retryguard` decides *whether* to retry.

```python
import logging
from tenacity import retry, stop_after_attempt
from retryguard import ErrorClassifier
from retryguard.integrations.tenacity import (
    before_sleep_log_retryguard,
    retry_if_retryguard,
    wait_retryguard,
)

logger = logging.getLogger(__name__)
classifier = ErrorClassifier()


@retry(
    retry=retry_if_retryguard(classifier),
    wait=wait_retryguard(classifier, fallback_seconds=1.0),
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log_retryguard(logger, classifier=classifier),
)
def call_something():
    ...
```

## Thread and async safety

`ErrorClassifier` is stateless — it holds only an immutable tuple of rule functions and
creates no shared mutable state during `classify()`. It is safe to share a single
instance across threads and async tasks.

The module-level singleton from `default_classifier()` is cached via `@lru_cache`, which
is thread-safe in CPython. For async code (asyncio, trio), the classifier itself is safe
to call from any coroutine; no I/O is performed.

## Overrides

Put provider-specific logic in a custom rule and pass it before the defaults:

```python
from retryguard import ErrorClassifier, RetryDecision, RetryCategory


def classify_my_service(exc: BaseException) -> RetryDecision | None:
    ...


classifier = ErrorClassifier(rules=(classify_my_service, *ErrorClassifier.DEFAULT_RULES))
```

## Stable API (1.0+)

The stable surface is the public package API:

- `retryguard.ErrorClassifier`
- `retryguard.RetryDecision` and `retryguard.RetryCategory`
- `retryguard.classify_error()`, `retryguard.should_retry()`, `retryguard.default_classifier()`
- `retryguard.integrations.celery.countdown_from_decision()`
- `retryguard.integrations.tenacity.retry_if_retryguard()`, `wait_retryguard()`, `before_sleep_log_retryguard()`

Everything else (including `retryguard.rules.*` and `retryguard.parsers.*`) is considered internal and may
change without notice, even in minor versions.
