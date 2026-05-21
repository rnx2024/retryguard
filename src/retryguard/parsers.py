from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from email.utils import parsedate_to_datetime


def extract_status_code(exc: BaseException) -> int | None:
    for attr_name in ("status_code", "status", "code"):
        value = getattr(exc, attr_name, None)
        if isinstance(value, int):
            return value

    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value

    return None


def extract_headers(exc: BaseException) -> dict[str, str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)

    if isinstance(headers, Mapping):
        # Normalize keys for case-insensitive lookup (HTTP headers are case-insensitive).
        return {str(k).lower(): str(v) for k, v in headers.items()}

    return {}


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None

    value = value.strip()

    # Delta-seconds form.
    if value.isdigit():
        return float(value)

    # HTTP-date form.
    try:
        retry_at = parsedate_to_datetime(value)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=dt.timezone.utc)
        now = dt.datetime.now(dt.timezone.utc)
        delta = (retry_at - now).total_seconds()
        return max(delta, 0.0)
    except Exception:
        return None


def extract_retry_after(exc: BaseException) -> float | None:
    headers = extract_headers(exc)
    return parse_retry_after(headers.get("retry-after"))


def _looks_like_sqlstate(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    if len(value) != 5:
        return None
    if all(("0" <= c <= "9") or ("A" <= c <= "Z") for c in value):
        return value
    return None


def iter_exception_chain(exc: BaseException, *, max_depth: int = 12) -> tuple[BaseException, ...]:
    seen: set[int] = set()
    chain: list[BaseException] = []

    cur: BaseException | None = exc
    depth = 0
    while cur is not None and depth < max_depth:
        cur_id = id(cur)
        if cur_id in seen:
            break
        seen.add(cur_id)
        chain.append(cur)

        next_exc: BaseException | None = None
        for attr_name in ("orig", "original_exception"):
            candidate = getattr(cur, attr_name, None)
            if isinstance(candidate, BaseException):
                next_exc = candidate
                break
        if next_exc is None:
            for candidate in (cur.__cause__, cur.__context__):
                if isinstance(candidate, BaseException):
                    next_exc = candidate
                    break

        cur = next_exc
        depth += 1

    return tuple(chain)


def extract_sqlstate(exc: BaseException) -> str | None:
    # psycopg/asyncpg typically expose `sqlstate`; psycopg2 uses `pgcode`.
    for candidate in iter_exception_chain(exc):
        for attr_name in ("sqlstate", "pgcode", "sql_state"):
            value = getattr(candidate, attr_name, None)
            sqlstate = _looks_like_sqlstate(value)
            if sqlstate is not None:
                return sqlstate

        # Some drivers expose error code on `.diag.sqlstate`.
        diag = getattr(candidate, "diag", None)
        diag_sqlstate = _looks_like_sqlstate(getattr(diag, "sqlstate", None))
        if diag_sqlstate is not None:
            return diag_sqlstate

    return None
