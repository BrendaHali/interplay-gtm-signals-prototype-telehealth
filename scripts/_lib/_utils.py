"""
Shared utilities: structured logging and HTTP retry decorator.

Replaces print statements in library code with stdlib logging at INFO level by
default, with a single console handler and ISO timestamps. Replaces ad-hoc
try/except blocks around external HTTP calls with a uniform retry decorator
that handles 429, 502, 503, 504, and transient httpx errors with exponential
backoff.

Pipeline orchestrator configures the root logger; library modules call
get_logger(__name__) and emit through that.
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, TypeVar

import httpx


_LOG_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logger once. Idempotent."""
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    _LOG_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module. Calls configure_logging if not yet configured."""
    if not _LOG_CONFIGURED:
        configure_logging()
    return logging.getLogger(name)


F = TypeVar("F", bound=Callable[..., Any])

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}


def retry_on_transient(
    max_attempts: int = 3,
    initial_backoff: float = 2.0,
    backoff_multiplier: float = 2.0,
    transient_statuses: set[int] = TRANSIENT_STATUS_CODES,
) -> Callable[[F], F]:
    """
    Decorator that retries a function on transient HTTP failures.

    Retries on:
      - httpx.HTTPStatusError where the response status is in `transient_statuses`
      - httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError

    Backoff is exponential starting at `initial_backoff` seconds, doubling
    each attempt. Raises the last exception if all attempts fail.
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            log = get_logger(f"retry.{func.__name__}")
            backoff = initial_backoff
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    if exc.response.status_code not in transient_statuses:
                        raise
                    if attempt == max_attempts:
                        log.error(f"giving up after {attempt} attempts: HTTP {exc.response.status_code}")
                        raise
                    log.warning(f"attempt {attempt} returned HTTP {exc.response.status_code}, retrying in {backoff:.1f}s")
                except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        log.error(f"giving up after {attempt} attempts: {type(exc).__name__}: {exc}")
                        raise
                    log.warning(f"attempt {attempt} raised {type(exc).__name__}, retrying in {backoff:.1f}s")
                time.sleep(backoff)
                backoff *= backoff_multiplier
            if last_exc:
                raise last_exc
            raise RuntimeError("retry loop exited without return or raise")
        return wrapped  # type: ignore[return-value]
    return decorator
