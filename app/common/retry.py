"""
Retry policy and exponential backoff utilities.

Used by both the notification worker (provider call retries) and the
inter-service HTTP client (gateway call retries).
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration for retry behaviour.

    Attributes:
        max_attempts: Maximum number of total attempts (first attempt + retries).
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Upper cap on the computed delay, in seconds.
        jitter: When ``True``, add a random jitter to each delay to prevent
            thundering-herd problems.
        backoff_factor: Multiplier applied to the base delay for each retry.
    """

    max_attempts: int = 3
    base_delay: float = 2.0
    max_delay: float = 60.0
    jitter: bool = True
    backoff_factor: float = 2.0

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the sleep duration (seconds) before *attempt* (1-indexed).

        Args:
            attempt: The attempt number that just failed (1 = first attempt).

        Returns:
            Seconds to wait before the next attempt.
        """
        delay = min(
            self.base_delay * (self.backoff_factor ** (attempt - 1)),
            self.max_delay,
        )
        if self.jitter:
            delay = delay * (0.5 + random.random() * 0.5)  # 50-100 % of computed delay
        return delay

    @property
    def max_retries(self) -> int:
        """Number of retries (max_attempts minus the first attempt)."""
        return max(0, self.max_attempts - 1)


# Pre-defined retry policies for common use-cases.

#: Conservative policy for provider API calls (SMS, WhatsApp, Email).
PROVIDER_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=2.0,
    max_delay=60.0,
    jitter=True,
    backoff_factor=2.0,
)

#: Aggressive policy for inter-service gateway calls.
GATEWAY_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay=1.0,
    max_delay=15.0,
    jitter=True,
    backoff_factor=2.0,
)


async def exponential_backoff_with_jitter(
    func: "Callable[..., object]",
    *args: object,
    policy: RetryPolicy = PROVIDER_RETRY_POLICY,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    **kwargs: object,
) -> object:
    """Call *func* with retry logic based on *policy*.

    Args:
        func: Async callable to invoke.
        *args: Positional arguments forwarded to *func*.
        policy: :class:`RetryPolicy` controlling retry behaviour.
        retryable_exceptions: Only retry on these exception types.
        **kwargs: Keyword arguments forwarded to *func*.

    Returns:
        The return value of *func* on success.

    Raises:
        The last exception raised by *func* after all attempts are exhausted.
    """
    last_exc: Exception | None = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await func(*args, **kwargs)  # type: ignore[operator]
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt == policy.max_attempts:
                break
            delay = policy.delay_for_attempt(attempt)
            await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]
