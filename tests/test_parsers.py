from __future__ import annotations

import datetime as dt
import email.utils

import pytest

from retryguard.parsers import (
    _looks_like_sqlstate,
    extract_headers,
    extract_retry_after,
    extract_sqlstate,
    extract_status_code,
    iter_exception_chain,
    parse_retry_after,
)


# ── parse_retry_after ──────────────────────────────────────────────────────────

def test_parse_retry_after_none() -> None:
    assert parse_retry_after(None) is None


def test_parse_retry_after_empty_string() -> None:
    assert parse_retry_after("") is None


def test_parse_retry_after_whitespace_only() -> None:
    assert parse_retry_after("   ") is None


def test_parse_retry_after_delta_seconds() -> None:
    assert parse_retry_after("30") == 30.0


def test_parse_retry_after_zero_seconds() -> None:
    assert parse_retry_after("0") == 0.0


def test_parse_retry_after_large_value() -> None:
    assert parse_retry_after("3600") == 3600.0


def test_parse_retry_after_invalid_string() -> None:
    assert parse_retry_after("not-a-date") is None


def test_parse_retry_after_http_date_in_past_clamps_to_zero() -> None:
    past_date = "Wed, 01 Jan 2020 00:00:00 GMT"
    result = parse_retry_after(past_date)
    assert result == 0.0


def test_parse_retry_after_http_date_in_future_returns_positive() -> None:
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)
    future_str = email.utils.format_datetime(future)
    result = parse_retry_after(future_str)
    assert result is not None
    assert 0 < result <= 60


def test_parse_retry_after_delta_seconds_with_whitespace() -> None:
    assert parse_retry_after("  15  ") == 15.0


# ── extract_status_code ────────────────────────────────────────────────────────

def test_extract_status_code_from_status_code_attr() -> None:
    class E(Exception):
        status_code = 404

    assert extract_status_code(E()) == 404


def test_extract_status_code_from_status_attr() -> None:
    class E(Exception):
        status = 500

    assert extract_status_code(E()) == 500


def test_extract_status_code_from_code_attr() -> None:
    class E(Exception):
        code = 403

    assert extract_status_code(E()) == 403


def test_extract_status_code_from_response_status_code() -> None:
    class Response:
        status_code = 429

    class E(Exception):
        response = Response()

    assert extract_status_code(E()) == 429


def test_extract_status_code_returns_none_when_no_match() -> None:
    assert extract_status_code(Exception("no code")) is None


def test_extract_status_code_ignores_string_status_code() -> None:
    class E(Exception):
        status_code = "404"

    assert extract_status_code(E()) is None


def test_extract_status_code_ignores_none_status_code() -> None:
    class E(Exception):
        status_code = None

    assert extract_status_code(E()) is None


# ── extract_headers ────────────────────────────────────────────────────────────

def test_extract_headers_normalizes_keys_to_lowercase() -> None:
    class Response:
        headers = {"Retry-After": "5", "X-Custom": "value"}

    class E(Exception):
        response = Response()

    headers = extract_headers(E())
    assert "retry-after" in headers
    assert "x-custom" in headers
    assert headers["retry-after"] == "5"


def test_extract_headers_returns_empty_dict_with_no_response() -> None:
    assert extract_headers(Exception("bare")) == {}


def test_extract_headers_returns_empty_dict_with_no_headers() -> None:
    class Response:
        headers = None

    class E(Exception):
        response = Response()

    assert extract_headers(E()) == {}


# ── extract_retry_after ────────────────────────────────────────────────────────

def test_extract_retry_after_reads_header_from_response() -> None:
    class Response:
        headers = {"Retry-After": "10"}

    class E(Exception):
        response = Response()

    assert extract_retry_after(E()) == 10.0


def test_extract_retry_after_returns_none_when_header_absent() -> None:
    class Response:
        headers = {}

    class E(Exception):
        response = Response()

    assert extract_retry_after(E()) is None


def test_extract_retry_after_header_key_is_case_insensitive() -> None:
    class Response:
        headers = {"retry-after": "20"}

    class E(Exception):
        response = Response()

    assert extract_retry_after(E()) == 20.0


# ── iter_exception_chain ───────────────────────────────────────────────────────

def test_iter_exception_chain_single_exception() -> None:
    exc = ValueError("x")
    assert iter_exception_chain(exc) == (exc,)


def test_iter_exception_chain_follows_orig_attr() -> None:
    inner = ValueError("inner")

    class Wrapper(Exception):
        def __init__(self, orig: BaseException) -> None:
            self.orig = orig

    outer = Wrapper(inner)
    chain = iter_exception_chain(outer)
    assert chain == (outer, inner)


def test_iter_exception_chain_follows_original_exception_attr() -> None:
    inner = ValueError("inner")

    class Wrapper(Exception):
        def __init__(self, orig: BaseException) -> None:
            self.original_exception = orig

    outer = Wrapper(inner)
    chain = iter_exception_chain(outer)
    assert chain == (outer, inner)


def test_iter_exception_chain_follows_cause() -> None:
    inner = ValueError("inner")
    outer = RuntimeError("outer")
    outer.__cause__ = inner
    chain = iter_exception_chain(outer)
    assert inner in chain


def test_iter_exception_chain_follows_context() -> None:
    inner = ValueError("inner")
    outer = RuntimeError("outer")
    outer.__context__ = inner
    chain = iter_exception_chain(outer)
    assert inner in chain


def test_iter_exception_chain_orig_takes_priority_over_cause() -> None:
    via_orig = ValueError("via orig")
    via_cause = RuntimeError("via cause")

    class Wrapper(Exception):
        def __init__(self) -> None:
            self.orig = via_orig
            self.__cause__ = via_cause

    outer = Wrapper()
    chain = iter_exception_chain(outer)
    assert via_orig in chain
    assert via_cause not in chain


def test_iter_exception_chain_stops_on_cycle() -> None:
    exc = ValueError("x")
    exc.__context__ = exc
    chain = iter_exception_chain(exc)
    assert chain.count(exc) == 1


def test_iter_exception_chain_respects_max_depth() -> None:
    excs = [ValueError(str(i)) for i in range(6)]
    for i in range(5):
        excs[i].__cause__ = excs[i + 1]
    chain = iter_exception_chain(excs[0], max_depth=3)
    assert len(chain) == 3


def test_iter_exception_chain_three_level_orig() -> None:
    level2 = ValueError("level2")
    level1 = RuntimeError("level1")
    level1.orig = level2

    class Top(Exception):
        def __init__(self) -> None:
            self.orig = level1

    chain = iter_exception_chain(Top())
    assert len(chain) == 3


# ── extract_sqlstate ───────────────────────────────────────────────────────────

def test_extract_sqlstate_via_sqlstate_attr() -> None:
    class E(Exception):
        sqlstate = "40001"

    assert extract_sqlstate(E()) == "40001"


def test_extract_sqlstate_via_pgcode_attr() -> None:
    class E(Exception):
        pgcode = "23505"

    assert extract_sqlstate(E()) == "23505"


def test_extract_sqlstate_via_sql_state_attr() -> None:
    class E(Exception):
        sql_state = "08006"

    assert extract_sqlstate(E()) == "08006"


def test_extract_sqlstate_via_diag_sqlstate() -> None:
    class Diag:
        sqlstate = "40P01"

    class E(Exception):
        diag = Diag()

    assert extract_sqlstate(E()) == "40P01"


def test_extract_sqlstate_from_chained_orig() -> None:
    class PgError(Exception):
        sqlstate = "55P03"

    class Wrapper(Exception):
        def __init__(self, orig: BaseException) -> None:
            self.orig = orig

    assert extract_sqlstate(Wrapper(PgError())) == "55P03"


def test_extract_sqlstate_from_cause_chain() -> None:
    class PgError(Exception):
        sqlstate = "57014"

    outer = RuntimeError("outer")
    outer.__cause__ = PgError()
    assert extract_sqlstate(outer) == "57014"


def test_extract_sqlstate_returns_none_when_missing() -> None:
    assert extract_sqlstate(Exception("no sqlstate")) is None


def test_extract_sqlstate_normalizes_to_uppercase() -> None:
    class E(Exception):
        sqlstate = "40p01"

    assert extract_sqlstate(E()) == "40P01"


def test_extract_sqlstate_rejects_too_short() -> None:
    class E(Exception):
        sqlstate = "4000"

    assert extract_sqlstate(E()) is None


def test_extract_sqlstate_rejects_too_long() -> None:
    class E(Exception):
        sqlstate = "400010"

    assert extract_sqlstate(E()) is None


# ── _looks_like_sqlstate ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("40001", "40001"),
    ("40P01", "40P01"),
    ("08006", "08006"),
    ("0A000", "0A000"),
    ("23505", "23505"),
])
def test_looks_like_sqlstate_valid_values(value: str, expected: str) -> None:
    assert _looks_like_sqlstate(value) == expected


@pytest.mark.parametrize("value", [
    "",
    "1234",
    "123456",
    None,
    12345,
    "4000!",
    "4000 ",
])
def test_looks_like_sqlstate_invalid_values(value: object) -> None:
    assert _looks_like_sqlstate(value) is None
