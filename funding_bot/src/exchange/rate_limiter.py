"""
Token bucket rate limiter for Binance API.
Implements rate limiting to stay within Binance API limits.
"""

import asyncio
import logging
import time
from typing import Optional

from src.config_loader import Config

logger = logging.getLogger(__name__)


class TokenBucket:
    """
    Token bucket algorithm for rate limiting.

    Tokens are added at a fixed rate up to a maximum capacity.
    Each request consumes one or more tokens.
    """

    def __init__(self, capacity: int, refill_rate: float):
        """
        Initialize token bucket.

        Args:
            capacity: Maximum number of tokens in the bucket.
            refill_rate: Number of tokens added per second.
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        tokens_to_add = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)
        self.last_refill = now

    async def acquire(self, tokens: int = 1) -> bool:
        """
        Try to acquire tokens from the bucket.

        Args:
            tokens: Number of tokens to acquire.

        Returns:
            True if tokens were acquired, False otherwise.
        """
        async with self._lock:
            await self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    async def wait_for_token(self, tokens: int = 1, timeout: Optional[float] = None):
        """
        Wait until tokens are available.

        Args:
            tokens: Number of tokens needed.
            timeout: Maximum time to wait (None for no timeout).

        Raises:
            asyncio.TimeoutError: If timeout is reached.
        """
        start_time = time.monotonic()

        while True:
            async with self._lock:
                await self._refill()
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return

            # Calculate wait time
            async with self._lock:
                tokens_needed = tokens - self.tokens
                wait_time = tokens_needed / self.refill_rate

            # Check timeout
            if timeout is not None:
                elapsed = time.monotonic() - start_time
                if elapsed + wait_time > timeout:
                    raise asyncio.TimeoutError(
                        f"Timeout waiting for {tokens} tokens"
                    )

            # Wait for tokens to refill
            await asyncio.sleep(min(wait_time, 0.1))

    def __repr__(self) -> str:
        """Return string representation."""
        return f"TokenBucket(capacity={self.capacity}, tokens={self.tokens:.2f})"


class RateLimiter:
    """
    Rate limiter for Binance API requests.

    Binance Futures has a request weight limit of 2400 per minute.
    Different endpoints have different weights.
    """

    # Default weights for common endpoints
    ENDPOINT_WEIGHTS = {
        "/fapi/v1/premiumIndex": 2,
        "/fapi/v1/fundingRate": 20,
        "/fapi/v2/account": 10,
        "/fapi/v1/order": 1,
        "/fapi/v1/depth": 20,
        "/fapi/v1/klines": 20,
        "/fapi/v1/openOrders": 40,
        "/fapi/v2/positionRisk": 5,
    }

    def __init__(self, config: Config):
        """
        Initialize rate limiter.

        Args:
            config: Configuration instance with exchange settings.
        """
        self.config = config
        self.weight_limit = config.get("exchange", "request_weight_limit", default=2400)
        self.buffer_pct = config.get("exchange", "rate_limit_buffer_pct", default=20)

        # Apply buffer to stay safely under limit
        effective_limit = int(self.weight_limit * (100 - self.buffer_pct) / 100)

        # Create token bucket with weight-based capacity
        # Refill rate: weights per second (2400 per minute = 40 per second)
        self.bucket = TokenBucket(
            capacity=effective_limit,
            refill_rate=self.weight_limit / 60.0,
        )

        self._error_count = 0
        self._max_errors = 5
        self._halted = False

    async def acquire(self, endpoint: str, weight: Optional[int] = None):
        """
        Acquire rate limit tokens for an endpoint.

        Args:
            endpoint: API endpoint path.
            weight: Request weight (auto-detected if not provided).

        Raises:
            Exception: If rate limiter is halted due to errors.
        """
        if self._halted:
            raise Exception("Rate limiter is halted due to consecutive errors")

        if weight is None:
            weight = self.ENDPOINT_WEIGHTS.get(endpoint, 1)

        # Wait for tokens with timeout
        try:
            await self.bucket.wait_for_token(weight, timeout=30.0)
            logger.debug(f"Acquired {weight} weight for {endpoint}")
        except asyncio.TimeoutError:
            logger.warning(f"Rate limit timeout for {endpoint}")
            raise

    def record_error(self):
        """Record an API error for error threshold tracking."""
        self._error_count += 1
        if self._error_count >= self._max_errors:
            logger.critical(
                f"API error threshold reached ({self._error_count} errors). "
                "Halting rate limiter."
            )
            self._halted = True

    def record_success(self):
        """Record a successful API call."""
        self._error_count = max(0, self._error_count - 1)

    def reset(self):
        """Reset error count and unhalt the rate limiter."""
        self._error_count = 0
        self._halted = False
        logger.info("Rate limiter reset")

    def get_status(self) -> dict:
        """
        Get current rate limiter status.

        Returns:
            Dictionary with status information.
        """
        return {
            "tokens_available": self.bucket.tokens,
            "capacity": self.bucket.capacity,
            "error_count": self._error_count,
            "halted": self._halted,
        }

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"RateLimiter(limit={self.weight_limit}, "
            f"available={self.bucket.tokens:.2f}, halted={self._halted})"
        )
