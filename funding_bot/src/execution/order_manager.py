"""
Order manager for trade execution.
Handles order placement, cancellation, and tracking.
"""

import logging
import time
from typing import Any, Dict, Optional

from src.config_loader import Config
from src.exchange.binance_rest import BinanceRESTClient
from src.exchange.rate_limiter import RateLimiter
from src.strategy.signal_engine import EntrySignal, ExitReason

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Manages order execution on Binance Futures.

    Handles:
    - Order placement (entry and exit)
    - Order cancellation
    - Order status tracking
    - Idempotent order management
    """

    def __init__(
        self,
        config: Config,
        rest_client: BinanceRESTClient,
        rate_limiter: RateLimiter,
    ):
        """
        Initialize order manager.

        Args:
            config: Configuration instance.
            rest_client: Binance REST API client.
            rate_limiter: Rate limiter for API calls.
        """
        self.config = config
        self.rest_client = rest_client
        self.rate_limiter = rate_limiter

    def _generate_client_order_id(self, prefix: str = "bot") -> str:
        """
        Generate unique client order ID.

        Args:
            prefix: Order ID prefix.

        Returns:
            Unique client order ID.
        """
        return f"{prefix}_{int(time.time() * 1000)}"

    async def place_entry_order(self, signal: EntrySignal) -> Dict[str, Any]:
        """
        Place an entry order.

        Args:
            signal: Entry signal with order parameters.

        Returns:
            Order result dictionary with success status and details.
        """
        try:
            # Determine order side based on signal side
            if signal.side.value == "SHORT":
                side = "SELL"
                position_side = "SHORT"
            else:  # LONG
                side = "BUY"
                position_side = "LONG"

            # Generate unique client order ID
            client_order_id = self._generate_client_order_id("entry")

            # Place market order for immediate execution
            logger.info(
                f"Placing entry order: {signal.symbol} {side} {position_side} | "
                f"Qty: {signal.position_size}"
            )

            order = await self.rest_client.place_order(
                symbol=signal.symbol,
                side=side,
                order_type="MARKET",
                quantity=signal.position_size,
                position_side=position_side,
                client_order_id=client_order_id,
            )

            # Verify order was filled
            if order.get("status") in ["FILLED", "PARTIALLY_FILLED"]:
                fill_price = float(order.get("avgPrice", signal.entry_price))
                filled_qty = float(order.get("executedQty", 0))

                logger.info(
                    f"✓ Entry order filled: {signal.symbol} | "
                    f"Price: ${fill_price:.4f} | Qty: {filled_qty}"
                )

                return {
                    "success": True,
                    "order_id": order.get("orderId"),
                    "client_order_id": client_order_id,
                    "fill_price": fill_price,
                    "filled_quantity": filled_qty,
                    "status": order.get("status"),
                }
            else:
                logger.warning(f"Entry order not filled: {order}")
                return {
                    "success": False,
                    "error": "Order not filled",
                    "order": order,
                }

        except Exception as e:
            logger.error(f"Error placing entry order: {e}", exc_info=True)
            self.rate_limiter.record_error()
            return {
                "success": False,
                "error": str(e),
            }

    async def place_exit_order(
        self, position, reason: ExitReason, current_price: float
    ) -> Dict[str, Any]:
        """
        Place an exit order to close a position.

        Args:
            position: Position to close.
            reason: Exit reason.
            current_price: Current market price.

        Returns:
            Order result dictionary.
        """
        try:
            # Determine order side (opposite of entry)
            if position.side.value == "SHORT":
                side = "BUY"
                position_side = "SHORT"
            else:  # LONG
                side = "SELL"
                position_side = "LONG"

            # Generate unique client order ID
            client_order_id = self._generate_client_order_id("exit")

            logger.info(
                f"Placing exit order: {position.symbol} {side} {position_side} | "
                f"Qty: {position.quantity} | Reason: {reason}"
            )

            # Place market order for immediate execution
            order = await self.rest_client.place_order(
                symbol=position.symbol,
                side=side,
                order_type="MARKET",
                quantity=position.quantity,
                position_side=position_side,
                client_order_id=client_order_id,
            )

            # Verify order was filled
            if order.get("status") in ["FILLED", "PARTIALLY_FILLED"]:
                fill_price = float(order.get("avgPrice", current_price))
                filled_qty = float(order.get("executedQty", 0))

                logger.info(
                    f"✓ Exit order filled: {position.symbol} | "
                    f"Price: ${fill_price:.4f} | Qty: {filled_qty}"
                )

                return {
                    "success": True,
                    "order_id": order.get("orderId"),
                    "client_order_id": client_order_id,
                    "fill_price": fill_price,
                    "filled_quantity": filled_qty,
                    "exit_reason": reason.value if hasattr(reason, 'value') else reason,
                }
            else:
                logger.warning(f"Exit order not filled: {order}")
                return {
                    "success": False,
                    "error": "Order not filled",
                    "order": order,
                }

        except Exception as e:
            logger.error(f"Error placing exit order: {e}", exc_info=True)
            self.rate_limiter.record_error()
            return {
                "success": False,
                "error": str(e),
            }

    async def cancel_order(
        self, symbol: str, order_id: Optional[int] = None, client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Cancel an open order.

        Args:
            symbol: Trading pair symbol.
            order_id: Original order ID.
            client_order_id: Original client order ID.

        Returns:
            Cancellation result.
        """
        try:
            result = await self.rest_client.cancel_order(
                symbol=symbol,
                order_id=order_id,
                client_order_id=client_order_id,
            )

            logger.info(f"Order cancelled: {symbol} | ID: {order_id or client_order_id}")

            return {
                "success": True,
                "result": result,
            }

        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    async def get_open_orders(self, symbol: Optional[str] = None) -> list:
        """
        Get all open orders.

        Args:
            symbol: Optional symbol filter.

        Returns:
            List of open orders.
        """
        try:
            orders = await self.rest_client.get_open_orders(symbol)
            return orders
        except Exception as e:
            logger.error(f"Error getting open orders: {e}")
            return []
