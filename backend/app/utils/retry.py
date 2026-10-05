"""Exponential backoff for transient failures (network blips, 429s)."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

from app.exceptions import ExtractorError

T = TypeVar("T")
logger = logging.getLogger(__name__)


def with_retries(
    func: Callable[[], T],
    *,
    attempts: int,
    base_delay: float,
    description: str,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``func``; retry ``ExtractorError``s marked ``retryable``.

    Delay doubles each attempt with +/-25% jitter: base, 2*base, 4*base ...
    """
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except ExtractorError as exc:
            if not exc.retryable or attempt == attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1)) * random.uniform(0.75, 1.25)
            logger.warning(
                "%s failed (%s), retry %d/%d in %.1fs",
                description, exc.code, attempt, attempts - 1, delay,
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
