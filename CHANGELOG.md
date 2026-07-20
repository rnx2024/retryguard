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
