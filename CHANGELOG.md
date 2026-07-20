# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- `classify_redis` rule: type-based classification of `redis.exceptions.*` (no message
  parsing). Registered in `DEFAULT_RULES` before `classify_builtin`, since
  `redis.exceptions.LockError` subclasses `ValueError` and would otherwise be
  intercepted there. Handles the `AuthenticationError`/`AuthorizationError`
  (`ConnectionError` subclasses) and `ClusterDownError`/`TryAgainError`
  (`ResponseError` subclasses) edge cases explicitly so credential failures and
  transient cluster-resharding states aren't misclassified.
- New `redis` optional dependency group (`redis>=4.2.0,<6.0`).
- `classify_aws` rule: `Error.Code`-based classification of `botocore.exceptions.ClientError`
  (mirroring botocore's own internal retry policy from `botocore.retries.standard`, not an
  invented list), plus type-based dispatch for connection-level `BotoCoreError`s. Deliberately
  does not reuse `classify_http_status`, because AWS returns HTTP `400` for both throttling and
  genuine permanent validation errors — status-code-only classification would misclassify
  throttling as non-retryable. Registered in `DEFAULT_RULES` before `classify_builtin`, since
  `botocore.exceptions.ConnectTimeoutError`/`ReadTimeoutError`/`ProxyConnectionError` subclass
  builtin `OSError` and would otherwise be intercepted there.
- New `aws` optional dependency group (`botocore>=1.34.0,<2.0`).
- `classify_gcp` rule: type-based classification of `google.api_core.exceptions.*`.
  Registered in `DEFAULT_RULES` *before* `classify_http_status` — google-api-core
  exceptions expose a `.code` attribute that the generic HTTP-status extraction
  already reads, so `classify_http_status` would otherwise intercept every GCP
  exception first. Fixes two cases where GCP's own semantics diverge from generic
  HTTP status-code conventions: `Aborted` (HTTP 409, but retryable — transaction
  conflict, same precedent as Postgres `40001`/Redis `WatchError`) and
  `DeadlineExceeded`/`GatewayTimeout`/`BadGateway` (HTTP 504/502, but not
  retryable by google-api-core's own default retry policy). Every other case is
  classified explicitly with its own `gcp_*` reason code rather than delegating to
  the generic HTTP rule, to avoid GCP's correctness silently depending on
  `classify_http_status`'s status-code tables never changing.
- New `gcp` optional dependency group (`google-api-core>=2.0.0,<3.0`).
- `classify_azure` rule: type/status-based classification of `azure.core.exceptions.*`.
  Registered in `DEFAULT_RULES` *before* `classify_http_status` — `HttpResponseError`
  exposes a `.status_code` attribute (the first name the generic HTTP-status
  extraction checks), so `classify_http_status` would otherwise intercept every
  Azure exception first. `ResourceModifiedError` (ETag conflict, typically HTTP
  412) is treated as retryable — same precedent as Postgres `40001`, Redis
  `WatchError`, AWS `ConditionalCheckFailedException`, and GCP `Aborted` — and is
  disambiguated by type from `ResourceNotFoundError`, which can carry the same
  412 status on update operations but stays non-retryable.
- New `azure` optional dependency group (`azure-core>=1.28.0,<2.0`).

---

## [1.0.0] — 2026-04-29

### Fixed
- `classify_requests`: `requests.Timeout` now correctly returns `TIMEOUT` category
  instead of `NETWORK`. `requests.ConnectTimeout` (subclass of both `Timeout` and
  `ConnectionError`) also gets `TIMEOUT` because `Timeout` is checked first.

### Added
- Postgres SQLSTATE class `53` (insufficient resources: disk_full, out_of_memory,
  configuration_limit_exceeded) and class `58` (system errors: io_error,
  undefined_file) are now retryable with `DATABASE` category.
- `ErrorClassifier.classify()` now wraps each rule call in `try/except`; a crashing
  rule is skipped and the next rule continues. Previously a faulty third-party rule
  would propagate an unhandled exception to the caller.
- `retry_if_retryguard()`, `wait_retryguard()`, and `before_sleep_log_retryguard()`
  now call `default_classifier()` (the module-level singleton) when no explicit
  classifier is passed, instead of each creating a separate `ErrorClassifier()`.
- SQLAlchemy rule: `DBAPIError` without SQLSTATE now returns a non-retryable
  `DATABASE` decision (`reason_code="sqlalchemy_unclassified_dbapi_error"`) instead
  of falling through to `None`.
- GitHub Actions: CI now installs the `db` extra; release workflow added for PyPI.

### Changed
- Optional dependency version specifiers now include upper bounds:
  `httpx<2.0`, `requests<3.0`, `SQLAlchemy<3.0`, `asyncpg<1.0`,
  `psycopg<4.0`, `tenacity<10.0`.
- Documented the stable API surface for 1.0+.
- Removed the Postgres string-marker fallback; classification is SQLSTATE/type-based only.

## [0.1.0] — 2026-04-29

Initial release.

### Added
- `ErrorClassifier` with configurable rule pipeline.
- `RetryDecision` frozen dataclass: `retryable`, `category`, `reason_code`,
  `reason`, `retry_after_seconds`, `suggested_delay_seconds`.
- `RetryCategory` enum: `NETWORK`, `TIMEOUT`, `RATE_LIMIT`, `SERVER`, `CLIENT`,
  `AUTH`, `VALIDATION`, `DATABASE`, `UNKNOWN`.
- Built-in rules covering Python builtins, HTTP status codes, httpx, requests,
  SQLAlchemy, psycopg3, asyncpg, and Postgres SQLSTATE / string fallback.
- `Retry-After` header parsing (delta-seconds and HTTP-date forms).
- Exception chain traversal for wrapped DBAPI errors (`.orig`, `__cause__`,
  `__context__`, `.diag.sqlstate`).
- Celery integration: `countdown_from_decision()`.
- Tenacity integration: `retry_if_retryguard()`, `wait_retryguard()`,
  `before_sleep_log_retryguard()`.
- `classify_error()` and `should_retry()` convenience helpers.
- `py.typed` marker (PEP 561).
