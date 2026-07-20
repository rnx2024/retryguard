# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

---

## [1.1.0] — 2026-07-20

Four new provider integrations (Redis, AWS, GCP, Azure), a telemetry hook, and two
reliability fixes to the classifier core itself. No breaking changes — every
addition is opt-in (new optional dependency groups, a new keyword-only parameter
defaulting to `None`) and all existing rule behavior is unchanged.

### Added — provider classification rules

Each new rule is self-contained (own file under `retryguard/rules/`, own `reason_code`
namespace) and independently verified against the real installed package, not
written from memory. Install via the matching extra, e.g. `pip install "retryguard[redis]"`.

- **Redis** (`classify_redis`, extra: `redis`, `redis>=4.2.0,<6.0`) — type-based
  classification of `redis.exceptions.*`. Registered before `classify_builtin`
  because `redis.exceptions.LockError` subclasses `ValueError` and would
  otherwise be intercepted there. `AuthenticationError`/`AuthorizationError`
  (which subclass `ConnectionError`) and `ClusterDownError`/`TryAgainError`
  (which subclass `ResponseError`) are handled explicitly so credential failures
  and transient cluster-resharding states aren't misclassified by their parent
  class's default.
- **AWS** (`classify_aws`, extra: `aws`, `botocore>=1.34.0,<2.0`) — `Error.Code`-based
  classification of `botocore.exceptions.ClientError`, sourced from botocore's own
  internal retry policy (`botocore.retries.standard`) rather than an invented
  list, plus type-based dispatch for connection-level `BotoCoreError`s.
  Deliberately does not reuse `classify_http_status`: AWS returns HTTP `400` for
  both throttling and genuine permanent validation errors, so status-code-only
  classification would misclassify throttling as non-retryable.
- **GCP** (`classify_gcp`, extra: `gcp`, `google-api-core>=2.0.0,<3.0`) — type-based
  classification of `google.api_core.exceptions.*`. Registered *before*
  `classify_http_status`, since these exceptions expose a `.code` attribute the
  generic HTTP-status extraction already reads. Overrides two cases where GCP's
  semantics diverge from generic HTTP conventions: `Aborted` (HTTP 409, but
  retryable — transaction conflict) and `DeadlineExceeded`/`GatewayTimeout`/
  `BadGateway` (HTTP 504/502, but not retryable per google-api-core's own
  default retry policy).
- **Azure** (`classify_azure`, extra: `azure`, `azure-core>=1.28.0,<2.0`) —
  type/status-based classification of `azure.core.exceptions.*`. Also registered
  before `classify_http_status` (`HttpResponseError.status_code` is the first
  attribute name the generic extraction checks). `ResourceModifiedError` (ETag
  conflict, typically HTTP 412) is retryable and disambiguated by type from
  `ResourceNotFoundError`, which can carry the same 412 status on update
  operations but stays non-retryable.

All four providers share one precedent for optimistic-concurrency conflicts —
Postgres `40001`, Redis `WatchError`, AWS `ConditionalCheckFailedException`, GCP
`Aborted`, and Azure `ResourceModifiedError` are all treated as retryable: not
because the underlying transport auto-retries them (none do), but because the
correct response is "the caller redoes the operation with fresh data," which is
what `retryable=True` means at retryguard's level.

### Added — telemetry hook

- `ErrorClassifier` accepts an optional keyword-only `on_decision` callback,
  invoked with `(exc, decision)` every time `classify()` produces a
  `RetryDecision`. Fires for every call path (direct usage, `classify_error()`/
  `should_retry()`, the tenacity/Celery integrations) since they all funnel
  through `classify()`. If the hook raises, the exception is logged (not
  propagated) and the returned decision is unaffected — the decision is
  computed and finalized *before* the hook runs, so it can never alter the
  outcome. Defaults to `None`; no behavior change for existing callers.

### Fixed

- `ErrorClassifier.classify()`'s rule loop previously swallowed a crashing rule
  silently (`except Exception: continue`, no logging) — a bug in a `classify_*`
  rule (built-in or custom) could produce a silently-wrong fallback decision
  with no trace of why. Now logged via `logging.getLogger("retryguard.classifier")`
  at `ERROR` level with the full traceback before continuing to the next rule;
  control flow (skip-and-continue, same `UNKNOWN` fallback) is unchanged.
- GitHub Actions CI was only installing the `http`/`db`/`retry`/`dev` extras, so
  every Redis/AWS/GCP/Azure test was silently skipped in CI and coverage on
  those rule files was never actually measured. CI now installs all provider
  extras (`redis,aws,gcp,azure`) so the full suite and real coverage run on
  every push.

### Internal

- `rules.py` (657 lines, 10 classification functions in one file) split into a
  `rules/` package, one file per provider/concern (`_http.py`, `_builtin.py`,
  `_db.py`, `_redis.py`, `_aws.py`, `_gcp.py`, `_azure.py`). Pure reorganization
  — `rules/__init__.py` re-exports every name, so nothing importing from
  `retryguard.rules` needs to change. Tests split to match
  (`test_rules_http.py`, `test_rules_builtin.py`, etc.), keeping every source
  and test file under 500 lines.

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
