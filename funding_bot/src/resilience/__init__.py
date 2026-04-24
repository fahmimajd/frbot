"""
Resilience module for handling API failures.
"""

from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitState,
    CircuitStats,
    ResilientClient,
    RetryConfig,
    with_retry,
    # Pre-configured configs
    RETRY_CONSERVATIVE,
    RETRY_MODERATE,
    RETRY_AGGRESSIVE,
    CIRCUIT_BREAKER_STRICT,
    CIRCUIT_BREAKER_MODERATE,
    CIRCUIT_BREAKER_LENIENT,
)

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerOpen",
    "CircuitState",
    "CircuitStats",
    "ResilientClient",
    "RetryConfig",
    "with_retry",
    "RETRY_CONSERVATIVE",
    "RETRY_MODERATE",
    "RETRY_AGGRESSIVE",
    "CIRCUIT_BREAKER_STRICT",
    "CIRCUIT_BREAKER_MODERATE",
    "CIRCUIT_BREAKER_LENIENT",
]
