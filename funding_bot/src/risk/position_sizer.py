"""
Position sizing calculator.
Calculates optimal position size based on risk parameters for Binance Futures.
"""

import logging
import math
from typing import Any, Dict, Optional

from src.config_loader import Config
from src.exchange.binance_rest import BinanceRESTClient
from src.constants import DefaultParams

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Calculates position sizes based on risk parameters for Binance Futures.

    Uses equity-based risk percentage and leverage to determine optimal
    position size. Binance uses quantity (amount of asset) instead of lots.

    Key features:
    - Supports leverage up to 20x
    - Works with small capital ($1 USDT)
    - Respects Binance filters (stepSize, minQty, minNotional)
    - Calculates quantity, not lots
    """

    def __init__(self, config: Config):
        """
        Initialize position sizer.

        Args:
            config: Configuration instance.
        """
        self.config = config
        # Use constants as defaults
        self.risk_per_trade_pct = config.get(
            "risk", "risk_per_trade_pct", default=DefaultParams.RISK_PER_TRADE_PCT
        ) / 100.0
        self.max_leverage = config.get("risk", "max_leverage", default=DefaultParams.MAX_LEVERAGE)
        self.capital = config.get("risk", "capital", default=DefaultParams.CAPITAL_USDT)
        # Cache exchange info
        self._exchange_info: Optional[Dict[str, Any]] = None
        self.rest_client: Optional[BinanceRESTClient] = None

    def set_rest_client(self, rest_client: BinanceRESTClient):
        """Set REST client for getting symbol filters."""
        self.rest_client = rest_client

    async def load_exchange_info(self, rest_client: BinanceRESTClient):
        """Load exchange info from Binance API."""
        if self._exchange_info is None:
            try:
                self._exchange_info = await rest_client.get_exchange_info()
                logger.info("Exchange info loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load exchange info: {e}")
                raise

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        equity: float,
    ) -> Optional[float]:
        """
        Calculate position size (quantity) for Binance Futures.

        Uses modal USDT with leverage to calculate quantity.
        Formula: quantity = (modal * leverage) / entry_price

        Note: Binance uses quantity (amount of asset), NOT lots.

        Args:
            symbol: Trading pair symbol.
            entry_price: Entry price.
            equity: Current account equity in USDT (not used directly, uses config capital).

        Returns:
            Position size in base asset quantity, or None if invalid.
        """
        try:
            # 1. Hitung nilai total posisi dengan leverage
            # Modal $1 dengan leverage 20x = posisi $20
            total_position_value = self.capital * self.max_leverage

            # 2. Hitung quantity kasar: quantity = notional / price
            raw_quantity = total_position_value / entry_price

            # 3. Ambil filter dari exchange info
            if self._exchange_info is None or self.rest_client is None:
                logger.warning("Exchange info not loaded, using default filters")
                filters = {"stepSize": "0.001", "minQty": "0.001", "minNotional": "5.0"}
            else:
                filters = self.rest_client.get_symbol_filters(symbol, self._exchange_info)
                if not filters:
                    logger.warning(f"No filters found for {symbol}, using defaults")
                    filters = {"stepSize": "0.001", "minQty": "0.001", "minNotional": "5.0"}

            step_size = float(filters["stepSize"])
            min_qty = float(filters["minQty"])
            min_notional = float(filters["minNotional"])

            # 4. Sesuaikan quantity dengan stepSize (Binance uses quantity, not lots)
            # Convert to string for precision calculation
            step_size_str = str(filters["stepSize"])
            precision = len(step_size_str.split(".")[-1].rstrip("0")) if "." in step_size_str else 0
            adjusted_quantity = math.floor(raw_quantity / step_size) * step_size

            # Format ke presisi yang benar
            quantity_str = f"{adjusted_quantity:.{precision}f}"
            final_quantity = float(quantity_str)

            # 5. Validasi minimum quantity
            if final_quantity < min_qty:
                logger.warning(
                    f"Quantity {final_quantity} < minQty {min_qty} for {symbol}. Skip."
                )
                return None

            # 6. Validasi minimum notional (nilai posisi minimal)
            estimated_value = final_quantity * entry_price
            if estimated_value < min_notional:
                logger.warning(
                    f"Notional ${estimated_value:.2f} < minNotional ${min_notional} for {symbol}. "
                    f"Modal ${self.capital} dengan {self.max_leverage}x terlalu kecil untuk pair ini."
                )
                return None

            logger.info(
                f"[PositionSizer] {symbol}: Price=${entry_price:.4f}, "
                f"Qty={final_quantity}, Value=${estimated_value:.2f}, "
                f"Leverage={self.max_leverage}x, Modal=${self.capital}"
            )
            return final_quantity

        except Exception as e:
            logger.error(f"Error calculating position size: {e}", exc_info=True)
            return None

    def calculate_quantity_from_notional(
        self, notional_usd: float, price: float
    ) -> float:
        """
        Calculate quantity from notional value.

        Args:
            notional_usd: Desired notional value in USD.
            price: Current price.

        Returns:
            Quantity in base asset.
        """
        if price <= 0:
            return 0.0
        return notional_usd / price
