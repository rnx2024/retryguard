from retryguard import ErrorClassifier, RetryCategory, should_retry


class FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


class FakeHTTPError(Exception):
    def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
        self.response = FakeResponse(status_code=status_code, headers=headers)


def test_429_is_retryable() -> None:
    classifier = ErrorClassifier()
    exc = FakeHTTPError(429, {"Retry-After": "7"})

    decision = classifier.classify(exc)

    assert decision.retryable is True
    assert decision.retry_after_seconds == 7.0
    assert decision.reason_code == "http_429"


def test_422_is_not_retryable() -> None:
    classifier = ErrorClassifier()
    exc = FakeHTTPError(422)

    decision = classifier.classify(exc)

    assert decision.retryable is False
    assert decision.reason_code == "http_422"


def test_value_error_is_not_retryable() -> None:
    classifier = ErrorClassifier()

    decision = classifier.classify(ValueError("bad payload"))

    assert decision.retryable is False
    assert decision.reason_code == "builtin_value_error"


def test_timeout_error_is_retryable() -> None:
    classifier = ErrorClassifier()

    decision = classifier.classify(TimeoutError("timed out"))

    assert decision.retryable is True
    assert decision.reason_code == "builtin_timeout"


class FakePgError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"sqlstate={sqlstate}")
        self.sqlstate = sqlstate


class FakeWrapperError(Exception):
    def __init__(self, orig: BaseException) -> None:
        super().__init__("wrapped")
        self.orig = orig


def test_pg_serialization_failure_is_retryable() -> None:
    classifier = ErrorClassifier()
    decision = classifier.classify(FakePgError("40001"))
    assert decision.retryable is True
    assert decision.category in {RetryCategory.DATABASE, RetryCategory.TIMEOUT}


def test_pg_deadlock_is_retryable() -> None:
    classifier = ErrorClassifier()
    decision = classifier.classify(FakePgError("40P01"))
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE


def test_pg_unique_violation_is_not_retryable() -> None:
    classifier = ErrorClassifier()
    decision = classifier.classify(FakePgError("23505"))
    assert decision.retryable is False
    assert decision.category == RetryCategory.DATABASE


def test_pg_invalid_authorization_is_not_retryable() -> None:
    classifier = ErrorClassifier()
    decision = classifier.classify(FakePgError("28P01"))
    assert decision.retryable is False
    assert decision.category == RetryCategory.AUTH


def test_pg_connection_exception_is_retryable() -> None:
    classifier = ErrorClassifier()
    decision = classifier.classify(FakePgError("08006"))
    assert decision.retryable is True
    assert decision.category == RetryCategory.NETWORK


def test_pg_sqlstate_extracted_from_orig_chain() -> None:
    classifier = ErrorClassifier()
    decision = classifier.classify(FakeWrapperError(FakePgError("40P01")))
    assert decision.retryable is True
    assert decision.category == RetryCategory.DATABASE


def test_should_retry_wrapper() -> None:
    assert should_retry(TimeoutError("timed out")) is True
