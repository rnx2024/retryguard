from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RetryCategory(str, Enum):
    NETWORK = "network"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    CLIENT = "client"
    AUTH = "auth"
    VALIDATION = "validation"
    DATABASE = "database"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retryable: bool
    category: RetryCategory
    reason_code: str
    reason: str
    retry_after_seconds: float | None = None
    suggested_delay_seconds: float | None = None
