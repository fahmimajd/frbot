"""
Binance WebSocket manager.
Handles real-time data streams for mark prices, order book, and trades.
"""

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional

import websockets
from src.config_loader import Config

logger = logging.getLogger(__name__)


class BinanceWebSocketManager:
    """Manages WebSocket connections to Binance Futures streams."""

    def __init__(self, config: Config):
        """
        Initialize WebSocket manager.

        Args:
            config: Configuration instance with exchange settings.
        """
        self.config = config
        self.ws_url = config.get("exchange", "ws_url")
        self.testnet = config.get("exchange", "testnet", default=False)

        if self.testnet:
            self.ws_url = "wss://stream.binancefuture.com"

        self._streams: Dict[str, websockets.WebSocketClientProtocol] = {}
        self._callbacks: Dict[str, Callable] = {}
        self._running = False
        self._reconnect_attempts: Dict[str, int] = {}
        self._max_reconnect_attempts = 10

    async def start(self):
        """Start the WebSocket manager."""
        self._running = True
        logger.info("WebSocket manager started")

    async def stop(self):
        """Stop all WebSocket connections."""
        self._running = False
        for stream_name, ws in list(self._streams.items()):
            await ws.close()
            logger.info(f"Closed WebSocket stream: {stream_name}")
        logger.info("WebSocket manager stopped")

    def subscribe_mark_price(
        self, symbol: str, callback: Callable[[Dict[str, Any]], None]
    ):
        """
        Subscribe to mark price updates for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT').
            callback: Function to call with mark price data.
        """
        stream_name = f"{symbol.lower()}@markPrice@1s"
        self._callbacks[stream_name] = callback
        asyncio.create_task(self._connect_stream(stream_name))

    def subscribe_order_book(
        self, symbol: str, callback: Callable[[Dict[str, Any]], None], depth: int = 20
    ):
        """
        Subscribe to order book updates for a symbol.

        Args:
            symbol: Trading pair symbol.
            callback: Function to call with order book data.
            depth: Order book depth levels.
        """
        stream_name = f"{symbol.lower()}@depth{depth}@100ms"
        self._callbacks[stream_name] = callback
        asyncio.create_task(self._connect_stream(stream_name))

    def subscribe_agg_trades(
        self, symbol: str, callback: Callable[[Dict[str, Any]], None]
    ):
        """
        Subscribe to aggregate trade updates for a symbol.

        Args:
            symbol: Trading pair symbol.
            callback: Function to call with trade data.
        """
        stream_name = f"{symbol.lower()}@aggTrade"
        self._callbacks[stream_name] = callback
        asyncio.create_task(self._connect_stream(stream_name))

    async def _connect_stream(self, stream_name: str):
        """
        Connect to a WebSocket stream with reconnection logic.

        Args:
            stream_name: Name of the stream to connect to.
        """
        url = f"{self.ws_url}/ws/{stream_name}"
        self._reconnect_attempts[stream_name] = 0

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=30, ping_timeout=10) as ws:
                    self._streams[stream_name] = ws
                    logger.info(f"Connected to WebSocket stream: {stream_name}")
                    self._reconnect_attempts[stream_name] = 0

                    async for message in ws:
                        if not self._running:
                            break

                        try:
                            data = json.loads(message)
                            callback = self._callbacks.get(stream_name)
                            if callback:
                                await self._safe_callback(callback, data)
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error: {e}")
                        except Exception as e:
                            logger.error(f"Error processing message: {e}")

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed: {stream_name}, code={e.code}")
                await self._handle_reconnect(stream_name)
            except Exception as e:
                logger.error(f"WebSocket error for {stream_name}: {e}")
                await self._handle_reconnect(stream_name)

    async def _handle_reconnect(self, stream_name: str):
        """
        Handle reconnection with exponential backoff.

        Args:
            stream_name: Name of the stream to reconnect.
        """
        attempts = self._reconnect_attempts.get(stream_name, 0)

        if attempts >= self._max_reconnect_attempts:
            logger.critical(
                f"Max reconnection attempts reached for {stream_name}. "
                "Switching to REST polling fallback."
            )
            # Could trigger fallback mechanism here
            return

        delay = min(2 ** attempts * 1, 60)  # Exponential backoff, max 60s
        logger.info(f"Reconnecting to {stream_name} in {delay}s (attempt {attempts + 1})")
        await asyncio.sleep(delay)
        self._reconnect_attempts[stream_name] = attempts + 1

    async def _safe_callback(self, callback: Callable, data: Any):
        """
        Safely execute a callback function.

        Args:
            callback: The callback function to execute.
            data: Data to pass to the callback.
        """
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            logger.error(f"Error in callback: {e}")

    def get_stream(self, stream_name: str) -> Optional[websockets.WebSocketClientProtocol]:
        """
        Get an active WebSocket stream.

        Args:
            stream_name: Name of the stream.

        Returns:
            WebSocket connection or None if not connected.
        """
        return self._streams.get(stream_name)

    def is_connected(self, stream_name: str) -> bool:
        """
        Check if a stream is connected.

        Args:
            stream_name: Name of the stream.

        Returns:
            True if connected, False otherwise.
        """
        ws = self._streams.get(stream_name)
        return ws is not None and not ws.closed
