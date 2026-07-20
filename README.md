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
- **AWS SDK / botocore** (when installed): `Error.Code`-based classification for `ClientError`
  (mirroring botocore's own internal retry policy), plus type-based dispatch for connection-level
  `BotoCoreError`s.
- **Google Cloud / google-api-core** (when installed): type-based dispatch over
  `google.api_core.exceptions.*`, mirroring google-api-core's own default retry policy.
- **Azure SDK / azure-core** (when installed): type/status-based classification of
  `azure.core.exceptions.*`, mirroring azure-core's own default retry policy.
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

## AWS SDK / botocore

**AWS routinely returns HTTP `400` for throttling, not `429`** — many services (DynamoDB, Kinesis,
STS, and others) return `ProvisionedThroughputExceededException`/`ThrottlingException` as HTTP `400`,
the same status used for genuine permanent validation errors. Because of this, `retryguard` does
**not** classify AWS errors via the generic `classify_http_status` rule — that would misclassify
throttling as a permanent failure. Instead, `botocore.exceptions.ClientError` is classified primarily
by its `Error.Code` string (a stable, documented field — exact-equality reads, not message parsing,
the same category of thing as reading `sqlstate`), falling back to `HTTPStatusCode` only for codes
without a specific mapping.

AWS's service-specific "modeled" exceptions (e.g.
`DynamoDB.Client.exceptions.ProvisionedThroughputExceededException`) are generated dynamically per
boto3 client instance — not stable, importable classes — so this classification can't be type-based
the way the Redis rule is. The retryable/throttling `Error.Code` lists are pulled directly from
botocore's own internal retry policy (`botocore.retries.standard`), not invented.

Retryable examples:

- Connection-level `BotoCoreError`s: `ConnectTimeoutError`, `ReadTimeoutError`,
  `EndpointConnectionError`, `ProxyConnectionError`, `ConnectionClosedError`, `HTTPClientError`
- `Error.Code` in botocore's own throttling list: `ThrottlingException`, `Throttling`,
  `RequestLimitExceeded`, `ProvisionedThroughputExceededException`, `TooManyRequestsException`,
  `SlowDown`, and others — **including when returned as HTTP `400`**
- `Error.Code` in botocore's own transient list: `RequestTimeout`, `RequestTimeoutException`,
  `PriorRequestNotComplete`
- `ConditionalCheckFailedException` (DynamoDB optimistic-lock conflict) — not in botocore's own retry
  lists (identical request retry can't fix it), but treated as retryable at the caller-redo-the-operation
  level, same precedent as Postgres `40001` and Redis `WatchError`
- Unrecognized `Error.Code` with `HTTPStatusCode` `500/502/503/504` or `429`

Non-retryable examples:

- `NoCredentialsError`, `PartialCredentialsError`, `UnauthorizedSSOTokenError` — retrying doesn't fix
  missing/bad credentials
- Unrecognized `Error.Code` with `HTTPStatusCode` `401`/`403`
- Any other unrecognized `Error.Code` (e.g. `ValidationException`, `ResourceNotFoundException`) —
  defaults to non-retryable `CLIENT`, regardless of HTTP status

## Google Cloud / google-api-core

`google.api_core.exceptions.*` happens to expose a `.code` attribute that `retryguard`'s generic
HTTP-status extraction already reads — meaning most GCP exceptions would already be classified
"correctly" by `classify_http_status` alone. `retryguard` still classifies them explicitly via a
dedicated, type-based rule (registered *before* `classify_http_status` in the pipeline, not after)
so that GCP errors get GCP-specific reason codes independent of the generic HTTP status-code
tables, and so the two cases below — where GCP's own semantics genuinely diverge from generic HTTP
status-code conventions — are handled correctly:

- `Aborted` shares HTTP status `409` with ordinary conflict errors, which are non-retryable by
  generic convention. But `ABORTED` in Google Cloud (most commonly Firestore/Spanner/BigTable
  transaction contention) means the caller should retry the operation — same precedent as Postgres
  `40001` and Redis `WatchError`.
- `DeadlineExceeded`/`GatewayTimeout`/`BadGateway` share HTTP statuses `504`/`502`, which are
  retryable by generic convention. But google-api-core's own default retry policy
  (`google.api_core.retry.retry_base.if_transient_error`) deliberately excludes these — a
  timed-out RPC may have partially succeeded server-side, so blind retry isn't safe.

Retryable examples:

- `ResourceExhausted`/`TooManyRequests` (quota/rate-limit errors)
- `ServiceUnavailable`, `InternalServerError` — matches google-api-core's own default retry policy
- `Aborted` — transaction conflict; retry the operation (see above)

Non-retryable examples:

- `DeadlineExceeded`, `GatewayTimeout`, `BadGateway` — deliberately excluded from
  google-api-core's own retry policy (see above)
- `InvalidArgument`, `FailedPrecondition`, `OutOfRange` (400) — genuine validation errors; unlike
  AWS's HTTP `400`, GCP doesn't overload this status with throttling
- `Unauthorized`/`Unauthenticated`/`Forbidden`/`PermissionDenied` (401/403)
- Anything else (e.g. `NotFound`, `AlreadyExists`) — defaults to non-retryable `CLIENT`

## Azure SDK / azure-core

Like GCP, `azure.core.exceptions.HttpResponseError` exposes a `.status_code` attribute that
`retryguard`'s generic HTTP-status extraction already reads (`"status_code"` is in fact the
*first* attribute name it checks) — so `classify_azure` is registered *before*
`classify_http_status` in the pipeline, and classifies every case explicitly with its own
`azure_*` reason code rather than relying on the generic rule.

The one case that needs disambiguating by type, not status code: `ResourceModifiedError`
(ETag conflict on a conditional write — Storage, Cosmos DB, App Configuration) typically
carries HTTP `412`, same as `ResourceNotFoundError` can when raised on an update. `retryguard`
treats `ResourceModifiedError` as retryable (re-read and retry the operation) — same precedent
as Postgres `40001`, Redis `WatchError`, AWS `ConditionalCheckFailedException`, and GCP
`Aborted` — while a `ResourceNotFoundError` carrying the same status code stays non-retryable.

Retryable examples:

- `ServiceRequestError`/`ServiceResponseError` and their timeout variants (connection-level,
  no response received)
- `ResourceModifiedError` — ETag conflict; retry the operation (see above)
- HTTP `408`, `429`, `500`, `502`, `503`, `504` — matches azure-core's own default retry policy

Non-retryable examples:

- `ClientAuthenticationError`, or HTTP `401`/`403`
- `ResourceNotModifiedError` (HTTP `304`) — not an error; retrying achieves nothing
- `TooManyRedirectsError`, `DecodeError` — client-side/protocol issues
- Anything else (e.g. `ResourceNotFoundError`, `ResourceExistsError`) — defaults to
  non-retryable `CLIENT`

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
