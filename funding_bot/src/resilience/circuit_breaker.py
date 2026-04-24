"""
Resilience utilities for handling API failures.
Implements retry logic with exponential backoff and circuit breaker pattern.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_retries: int = 3
    initial_delay: float = 1.0  # seconds
    max_delay: float = 60.0  # seconds
    exponential_base: float = 2.0
    jitter: bool = True
    retryable_exceptions: tuple = (Exception,)


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5  # failures before opening circuit
    success_threshold: int = 2  # successes before closing circuit
    timeout: float = 30.0  # seconds before attempting reset
    half_open_max_calls: int = 3  # max calls in half-open state


@dataclass
class CircuitStats:
    """Statistics for circuit breaker."""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    state_changes: List[Dict[str, Any]] = field(default_factory=list)


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open."""
    pass


class CircuitBreaker:
    """
    Circuit breaker implementation to prevent cascading failures.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Circuit tripped, requests are rejected immediately
    - HALF_OPEN: Testing if service has recovered
    
    Transitions:
    - CLOSED -> OPEN: When failure count reaches threshold
    - OPEN -> HALF_OPEN: After timeout period
    - HALF_OPEN -> CLOSED: When success count reaches threshold
    - HALF_OPEN -> OPEN: On any failure
    """
    
    def __init__(self, config: Optional[CircuitBreakerConfig] = None, name: str = "default"):
        """
        Initialize circuit breaker.
        
        Args:
            config: Circuit breaker configuration.
            name: Name for identification in logs.
        """
        self.config = config or CircuitBreakerConfig()
        self.name = name
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_state_change = time.time()
        self._stats = CircuitStats()
        self._half_open_calls = 0
        self._lock = asyncio.Lock()
        
    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state
    
    @property
    def stats(self) -> CircuitStats:
        """Get circuit statistics."""
        return self._stats
    
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self._state == CircuitState.CLOSED
    
    def is_open(self) -> bool:
        """Check if circuit is open (rejecting requests)."""
        return self._state == CircuitState.OPEN
    
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)."""
        return self._state == CircuitState.HALF_OPEN
    
    async def _check_state(self) -> bool:
        """
        Check and potentially update circuit state.
        
        Returns:
            True if request should be allowed, False otherwise.
        """
        async with self._lock:
            current_time = time.time()
            
            if self._state == CircuitState.OPEN:
                # Check if timeout has elapsed
                if current_time - self._last_state_change >= self.config.timeout:
                    logger.info(f"Circuit '{self.name}' transitioning from OPEN to HALF_OPEN")
                    self._transition_to(CircuitState.HALF_OPEN)
                    return True
                return False
            
            if self._state == CircuitState.HALF_OPEN:
                # Allow limited calls in half-open state
                if self._half_open_calls < self.config.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False
            
            # CLOSED state - always allow
            return True
    
    def _transition_to(self, new_state: CircuitState):
        """Transition to a new state."""
        old_state = self._state
        self._state = new_state
        self._last_state_change = time.time()
        current_time = self._last_state_change
        
        # Reset counters based on new state
        if new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            self._success_count = 0
        elif new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0
        elif new_state == CircuitState.OPEN:
            self._success_count = 0
        
        # Record state change
        self._stats.state_changes.append({
            "from": old_state.value,
            "to": new_state.value,
            "timestamp": current_time,
        })
        
        logger.info(f"Circuit '{self.name}' state: {old_state.value} -> {new_state.value}")
    
    async def record_success(self):
        """Record a successful call."""
        async with self._lock:
            self._stats.successful_calls += 1
            self._stats.last_success_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    logger.info(f"Circuit '{self.name}' recovered, transitioning to CLOSED")
                    self._transition_to(CircuitState.CLOSED)
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success in closed state
                self._failure_count = 0
    
    async def record_failure(self):
        """Record a failed call."""
        async with self._lock:
            self._stats.failed_calls += 1
            self._stats.last_failure_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open state opens the circuit
                logger.warning(f"Circuit '{self.name}' failed during recovery, reopening")
                self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.config.failure_threshold:
                    logger.critical(f"Circuit '{self.name}' tripped due to {self._failure_count} failures")
                    self._transition_to(CircuitState.OPEN)
    
    async def execute(self, func: Callable[..., T], *args, **kwargs) -> T:
        """
        Execute a function with circuit breaker protection.
        
        Args:
            func: Async function to execute.
            *args: Positional arguments for func.
            **kwargs: Keyword arguments for func.
            
        Returns:
            Result from func.
            
        Raises:
            CircuitBreakerOpen: If circuit is open.
            Exception: Any exception from func.
        """
        self._stats.total_calls += 1
        
        if not await self._check_state():
            self._stats.rejected_calls += 1
            logger.warning(f"Circuit '{self.name}' is OPEN, rejecting request")
            raise CircuitBreakerOpen(f"Circuit breaker '{self.name}' is open")
        
        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception as e:
            await self.record_failure()
            raise
    
    def get_status(self) -> Dict[str, Any]:
        """Get current circuit status."""
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "total_calls": self._stats.total_calls,
            "successful_calls": self._stats.successful_calls,
            "failed_calls": self._stats.failed_calls,
            "rejected_calls": self._stats.rejected_calls,
            "last_failure_time": self._stats.last_failure_time,
            "last_success_time": self._stats.last_success_time,
        }


def with_retry(config: Optional[RetryConfig] = None):
    """
    Decorator for adding retry logic with exponential backoff.
    
    Args:
        config: Retry configuration.
        
    Returns:
        Decorated function.
    """
    cfg = config or RetryConfig()
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(cfg.max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except cfg.retryable_exceptions as e:
                    last_exception = e
                    
                    if attempt == cfg.max_retries:
                        logger.error(f"{func.__name__} failed after {cfg.max_retries + 1} attempts: {e}")
                        raise
                    
                    # Calculate delay with exponential backoff
                    delay = min(
                        cfg.initial_delay * (cfg.exponential_base ** attempt),
                        cfg.max_delay
                    )
                    
                    # Add jitter if enabled
                    if cfg.jitter:
                        jitter = random.uniform(0, delay * 0.2)
                        delay += jitter
                    
                    logger.warning(
                        f"{func.__name__} failed (attempt {attempt + 1}/{cfg.max_retries + 1}): {e}. "
                        f"Retrying in {delay:.2f}s"
                    )
                    await asyncio.sleep(delay)
            
            # Should never reach here, but just in case
            raise last_exception
        
        return wrapper
    return decorator


class ResilientClient:
    """
    Base class for resilient API clients with built-in retry and circuit breaker.
    """
    
    def __init__(
        self,
        retry_config: Optional[RetryConfig] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
        name: str = "api_client",
    ):
        """
        Initialize resilient client.
        
        Args:
            retry_config: Configuration for retry behavior.
            circuit_breaker_config: Configuration for circuit breaker.
            name: Client name for identification.
        """
        self.retry_config = retry_config or RetryConfig()
        self.circuit_breaker = CircuitBreaker(circuit_breaker_config, name=name)
        self._request_history: List[Dict[str, Any]] = []
        
    async def _execute_with_resilience(
        self,
        func: Callable[..., T],
        *args,
        operation_name: str = "operation",
        **kwargs
    ) -> T:
        """
        Execute an operation with both retry and circuit breaker protection.
        
        Args:
            func: Async function to execute.
            *args: Positional arguments.
            operation_name: Name of operation for logging.
            **kwargs: Keyword arguments.
            
        Returns:
            Result from func.
        """
        start_time = time.time()
        
        async def wrapped_operation():
            return await func(*args, **kwargs)
        
        try:
            result = await self.circuit_breaker.execute(wrapped_operation)
            duration = time.time() - start_time
            
            self._request_history.append({
                "operation": operation_name,
                "success": True,
                "duration": duration,
                "timestamp": start_time,
            })
            
            logger.debug(f"{operation_name} completed successfully in {duration:.3f}s")
            return result
            
        except CircuitBreakerOpen as e:
            logger.warning(f"{operation_name} blocked by circuit breaker: {e}")
            raise
        except Exception as e:
            duration = time.time() - start_time
            
            self._request_history.append({
                "operation": operation_name,
                "success": False,
                "error": str(e),
                "duration": duration,
                "timestamp": start_time,
            })
            
            logger.error(f"{operation_name} failed: {e}")
            raise
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get client health status."""
        recent_requests = self._request_history[-10:] if self._request_history else []
        success_rate = (
            sum(1 for r in recent_requests if r.get("success")) / len(recent_requests) * 100
            if recent_requests else 100.0
        )
        
        return {
            "name": self.circuit_breaker.name,
            "circuit_state": self.circuit_breaker.state.value,
            "circuit_stats": self.circuit_breaker.get_status(),
            "recent_success_rate": success_rate,
            "recent_requests": len(recent_requests),
        }


# Pre-configured retry configs for common scenarios
RETRY_CONSERVATIVE = RetryConfig(
    max_retries=2,
    initial_delay=0.5,
    max_delay=10.0,
)

RETRY_MODERATE = RetryConfig(
    max_retries=3,
    initial_delay=1.0,
    max_delay=30.0,
)

RETRY_AGGRESSIVE = RetryConfig(
    max_retries=5,
    initial_delay=2.0,
    max_delay=120.0,
)

# Pre-configured circuit breaker configs
CIRCUIT_BREAKER_STRICT = CircuitBreakerConfig(
    failure_threshold=3,
    success_threshold=2,
    timeout=30.0,
)

CIRCUIT_BREAKER_MODERATE = CircuitBreakerConfig(
    failure_threshold=5,
    success_threshold=3,
    timeout=60.0,
)

CIRCUIT_BREAKER_LENIENT = CircuitBreakerConfig(
    failure_threshold=10,
    success_threshold=5,
    timeout=120.0,
)
