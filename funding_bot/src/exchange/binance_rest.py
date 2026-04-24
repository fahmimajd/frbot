"""
Binance REST API client wrapper.
Handles all REST API interactions with Binance Futures.
Includes retry logic with exponential backoff and circuit breaker for resilience.
"""

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp
from src.config_loader import Config
from src.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitState,
    RetryConfig,
    RETRY_MODERATE,
    CIRCUIT_BREAKER_MODERATE,
)

logger = logging.getLogger(__name__)


# Custom exceptions for better error handling
class BinanceAPIError(Exception):
    """Base exception for Binance API errors."""
    def __init__(self, message: str, status_code: int = None, response_data: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}


class BinanceRateLimitError(BinanceAPIError):
    """Raised when rate limit is exceeded."""
    pass


class BinanceServerError(BinanceAPIError):
    """Raised for 5xx server errors (retryable)."""
    pass


class BinanceClientError(BinanceAPIError):
    """Raised for 4xx client errors (not retryable)."""
    pass


class BinanceRESTClient:
    """Async REST API client for Binance Futures with resilience features."""

    def __init__(
        self,
        config: Config,
        retry_config: Optional[RetryConfig] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ):
        """
        Initialize the Binance REST client.

        Args:
            config: Configuration instance with exchange settings.
            retry_config: Optional retry configuration (uses RETRY_MODERATE by default).
            circuit_breaker_config: Optional circuit breaker config (uses CIRCUIT_BREAKER_MODERATE by default).
        """
        self.config = config
        self.api_key = config.get("exchange", "api_key")
        self.api_secret = config.get("exchange", "api_secret")
        self.base_url = config.get("exchange", "api_url")
        self.testnet = config.get("exchange", "testnet", default=False)

        if self.testnet:
            self.base_url = "https://testnet.binancefuture.com"

        self._session: Optional[aiohttp.ClientSession] = None
        
        # Initialize resilience components
        self.retry_config = retry_config or RETRY_MODERATE
        self.circuit_breaker = CircuitBreaker(
            circuit_breaker_config or CIRCUIT_BREAKER_MODERATE,
            name="binance_rest"
        )
        
        # Track request metrics
        self._request_count = 0
        self._error_count = 0
        self._last_error_time: Optional[float] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def _generate_signature(self, query_string: str) -> str:
        """
        Generate HMAC SHA256 signature for request.

        Args:
            query_string: URL-encoded query string to sign.

        Returns:
            Hex-encoded signature.
        """
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with API key."""
        return {"X-MBX-APIKEY": self.api_key}

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        retry_count: int = 0,
    ) -> Dict[str, Any]:
        """
        Make HTTP request to Binance API with retry and circuit breaker.

        Args:
            method: HTTP method (GET, POST, DELETE).
            endpoint: API endpoint path.
            params: Query parameters.
            signed: Whether to sign the request.
            retry_count: Current retry attempt (internal use).

        Returns:
            JSON response from API.

        Raises:
            BinanceAPIError: If request fails.
            CircuitBreakerOpen: If circuit breaker is open.
        """
        # Check circuit breaker state
        if not await self._check_circuit_breaker():
            raise CircuitBreakerOpen("Circuit breaker is OPEN for Binance API")
        
        self._request_count += 1
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"

        if params is None:
            params = {}

        # Add timestamp for signed requests
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            query_string = urlencode(params)
            signature = self._generate_signature(query_string)
            params["signature"] = signature

        headers = self._get_headers() if signed else {}

        try:
            async with session.request(
                method, url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                data = await response.json()

                if response.status != 200:
                    error_msg = f"Binance API error: {data}"
                    logger.error(error_msg)
                    
                    # Classify error type
                    if response.status >= 500:
                        # Server error - retryable
                        self._error_count += 1
                        self._last_error_time = time.time()
                        await self._record_failure()
                        
                        # Retry with exponential backoff for server errors
                        if retry_count < self.retry_config.max_retries:
                            delay = self._calculate_backoff(retry_count)
                            logger.warning(f"Server error, retrying in {delay:.2f}s (attempt {retry_count + 1})")
                            await asyncio.sleep(delay)
                            return await self._request(method, endpoint, params, signed, retry_count + 1)
                        
                        raise BinanceServerError(error_msg, response.status, data)
                    
                    elif response.status == 429:
                        # Rate limit exceeded
                        self._error_count += 1
                        self._last_error_time = time.time()
                        await self._record_failure()
                        raise BinanceRateLimitError(error_msg, response.status, data)
                    
                    else:
                        # Client error - not retryable
                        self._error_count += 1
                        self._last_error_time = time.time()
                        await self._record_failure()
                        raise BinanceClientError(error_msg, response.status, data)

                # Success - record it
                await self._record_success()
                return data

        except aiohttp.ClientError as e:
            # Network error - retryable
            self._error_count += 1
            self._last_error_time = time.time()
            await self._record_failure()
            
            if retry_count < self.retry_config.max_retries:
                delay = self._calculate_backoff(retry_count)
                logger.warning(f"Network error: {e}, retrying in {delay:.2f}s (attempt {retry_count + 1})")
                await asyncio.sleep(delay)
                return await self._request(method, endpoint, params, signed, retry_count + 1)
            
            raise BinanceServerError(f"Network error: {e}", response_data={"error": str(e)})
        
        except asyncio.TimeoutError:
            # Timeout - retryable
            self._error_count += 1
            self._last_error_time = time.time()
            await self._record_failure()
            
            if retry_count < self.retry_config.max_retries:
                delay = self._calculate_backoff(retry_count)
                logger.warning(f"Request timeout, retrying in {delay:.2f}s (attempt {retry_count + 1})")
                await asyncio.sleep(delay)
                return await self._request(method, endpoint, params, signed, retry_count + 1)
            
            raise BinanceServerError("Request timeout", response_data={"error": "timeout"})

    def _calculate_backoff(self, retry_count: int) -> float:
        """
        Calculate exponential backoff delay with jitter.
        
        Args:
            retry_count: Current retry attempt number.
            
        Returns:
            Delay in seconds.
        """
        import random
        delay = min(
            self.retry_config.initial_delay * (self.retry_config.exponential_base ** retry_count),
            self.retry_config.max_delay
        )
        if self.retry_config.jitter:
            delay += random.uniform(0, delay * 0.2)
        return delay

    async def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker allows requests."""
        cb_state = self.circuit_breaker.state
        
        if cb_state == CircuitState.CLOSED:
            return True
        elif cb_state == CircuitState.OPEN:
            # Check if timeout has elapsed
            if self.circuit_breaker._stats.last_failure_time:
                elapsed = time.time() - self.circuit_breaker._stats.last_failure_time
                if elapsed >= self.circuit_breaker.config.timeout:
                    logger.info("Circuit breaker transitioning to HALF_OPEN")
                    self.circuit_breaker._state = CircuitState.HALF_OPEN
                    return True
            return False
        else:  # HALF_OPEN
            # Allow limited requests in half-open state
            return True

    async def _record_success(self):
        """Record successful request for circuit breaker."""
        await self.circuit_breaker.record_success()
        # Reset error count on success
        self._error_count = 0

    async def _record_failure(self):
        """Record failed request for circuit breaker."""
        await self.circuit_breaker.record_failure()
        
        # Check if we should trip the circuit breaker
        if self._error_count >= self.circuit_breaker.config.failure_threshold:
            logger.critical(f"Circuit breaker OPENED after {self._error_count} failures")

    async def get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """
        Get current funding rate and mark price for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT').

        Returns:
            Funding rate and price data.
        """
        endpoint = "/fapi/v1/premiumIndex"
        params = {"symbol": symbol}
        return await self._request("GET", endpoint, params)

    async def get_historical_funding_rates(
        self, symbol: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get historical funding rates for a symbol.

        Args:
            symbol: Trading pair symbol.
            limit: Number of records to retrieve (max 1000).

        Returns:
            List of historical funding rate records.
        """
        endpoint = "/fapi/v1/fundingRate"
        params = {"symbol": symbol, "limit": limit}
        return await self._request("GET", endpoint, params)

    async def get_account_info(self) -> Dict[str, Any]:
        """
        Get account information including balance and positions.

        Returns:
            Account data including equity, margin, and positions.
        """
        endpoint = "/fapi/v2/account"
        return await self._request("GET", endpoint, signed=True)

    async def get_order_book(
        self, symbol: str, limit: int = 20
    ) -> Dict[str, Any]:
        """
        Get order book depth for a symbol.

        Args:
            symbol: Trading pair symbol.
            limit: Depth levels (5, 10, 20, 50, 100, 500, 1000).

        Returns:
            Order book with bids and asks.
        """
        endpoint = "/fapi/v1/depth"
        params = {"symbol": symbol, "limit": limit}
        return await self._request("GET", endpoint, params)

    async def get_klines(
        self, symbol: str, interval: str, limit: int = 500
    ) -> List[List[Any]]:
        """
        Get candlestick/kline data for a symbol.

        Args:
            symbol: Trading pair symbol.
            interval: Kline interval (1m, 3m, 5m, 15m, 30m, 1h, etc.).
            limit: Number of candles to retrieve (max 1500).

        Returns:
            List of kline data [open_time, open, high, low, close, volume, ...].
        """
        endpoint = "/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        return await self._request("GET", endpoint, params)

    async def get_exchange_info(self) -> Dict[str, Any]:
        """
        Get exchange info including symbol filters.

        Returns:
            Exchange info with symbols, filters (stepSize, minQty, minNotional).
        """
        endpoint = "/fapi/v1/exchangeInfo"
        return await self._request("GET", endpoint)

    def get_symbol_filters(self, symbol: str, exchange_info: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        Get trading filters for a specific symbol.

        Args:
            symbol: Trading pair symbol.
            exchange_info: Exchange info from get_exchange_info().

        Returns:
            Dictionary with stepSize, minQty, minNotional or None if not found.
        """
        for sym_info in exchange_info.get("symbols", []):
            if sym_info.get("symbol") == symbol:
                filters = sym_info.get("filters", [])
                result = {}
                for f in filters:
                    if f.get("filterType") == "LOT_SIZE":
                        result["stepSize"] = f.get("stepSize", "0.001")
                        result["minQty"] = f.get("minQty", "0.001")
                    elif f.get("filterType") == "NOTIONAL":
                        result["minNotional"] = f.get("notional", "5.0")

                # Default values if not found
                if "stepSize" not in result:
                    result["stepSize"] = "0.001"
                if "minQty" not in result:
                    result["minQty"] = "0.001"
                if "minNotional" not in result:
                    result["minNotional"] = "5.0"

                return result
        return None

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        position_side: str = "BOTH",
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Place a new order.

        Args:
            symbol: Trading pair symbol.
            side: BUY or SELL.
            order_type: LIMIT, MARKET, STOP_MARKET, etc.
            quantity: Order quantity in base asset.
            price: Limit price (required for LIMIT orders).
            position_side: BOTH, LONG, or SHORT.
            client_order_id: Custom order ID for idempotency.

        Returns:
            Order confirmation data.
        """
        endpoint = "/fapi/v1/order"
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
            "positionSide": position_side,
        }

        if price is not None:
            params["price"] = price

        if client_order_id:
            params["newClientOrderId"] = client_order_id
        else:
            import time
            params["newClientOrderId"] = f"bot_{int(time.time() * 1000)}"

        return await self._request("POST", endpoint, params, signed=True)

    async def cancel_order(
        self, symbol: str, order_id: Optional[int] = None, client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Cancel an existing order.

        Args:
            symbol: Trading pair symbol.
            order_id: Original order ID.
            client_order_id: Original client order ID.

        Returns:
            Cancellation confirmation.
        """
        endpoint = "/fapi/v1/order"
        params = {"symbol": symbol}

        if order_id:
            params["orderId"] = order_id
        elif client_order_id:
            params["origClientOrderId"] = client_order_id
        else:
            raise ValueError("Either order_id or client_order_id must be provided")

        return await self._request("DELETE", endpoint, params, signed=True)

    async def get_open_orders(
        self, symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all open orders.

        Args:
            symbol: Optional symbol filter.

        Returns:
            List of open orders.
        """
        endpoint = "/fapi/v1/openOrders"
        params = {}
        if symbol:
            params["symbol"] = symbol
        return await self._request("GET", endpoint, params, signed=True)

    async def get_position_risk(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get current position risk information.

        Args:
            symbol: Optional symbol filter.

        Returns:
            List of position risk data.
        """
        endpoint = "/fapi/v2/positionRisk"
        params = {}
        if symbol:
            params["symbol"] = symbol
        return await self._request("GET", endpoint, params, signed=True)

    def get_health_status(self) -> Dict[str, Any]:
        """
        Get client health status for monitoring.
        
        Returns:
            Dictionary with health metrics.
        """
        return {
            "circuit_breaker_state": self.circuit_breaker.state.value,
            "circuit_breaker_stats": self.circuit_breaker.get_status(),
            "request_count": self._request_count,
            "error_count": self._error_count,
            "last_error_time": self._last_error_time,
            "is_healthy": self.circuit_breaker.is_closed() and self._error_count < 3,
        }
