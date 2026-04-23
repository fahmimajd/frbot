"""
Pre-trade validation checks.
Performs final market data validation before order execution.
"""

import logging
from typing import Any, Dict, Optional

from src.config_loader import Config
from src.exchange.binance_rest import BinanceRESTClient
from src.strategy.signal_engine import EntrySignal

logger = logging.getLogger(__name__)


class PreTradeChecker:
    """
    Performs pre-trade validation checks.

    Validates market conditions immediately before order placement
    to ensure signals are still valid.
    """

    def __init__(self, config: Config, rest_client: BinanceRESTClient):
        """
        Initialize pre-trade checker.

        Args:
            config: Configuration instance.
            rest_client: Binance REST API client.
        """
        self.config = config
        self.rest_client = rest_client

        self.max_basis_pct = config.get(
            "filters", "max_basis_pct", default=0.30
        ) / 100.0
        self.max_1h_price_change = config.get(
            "filters", "max_1h_price_change_pct", default=2.0
        ) / 100.0

    async def get_market_data(self, symbol: str) -> Dict[str, Any]:
        """
        Get current market data for a symbol.

        Args:
            symbol: Trading pair symbol.

        Returns:
            Dictionary with market data (ATR, spread, OB ratio, etc.).
        """
        try:
            # Get funding rate and prices
            premium_index = await self.rest_client.get_funding_rate(symbol)
            mark_price = float(premium_index.get("markPrice", 0))
            index_price = float(premium_index.get("indexPrice", 0))

            # Calculate basis
            basis_pct = ((mark_price - index_price) / index_price * 100) if index_price > 0 else 0

            # Get order book for spread and imbalance
            order_book = await self.rest_client.get_order_book(symbol, limit=20)
            bids = order_book.get("bids", [])
            asks = order_book.get("asks", [])

            # Calculate spread
            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                mid_price = (best_bid + best_ask) / 2
                spread_pct = ((best_ask - best_bid) / mid_price) * 100 if mid_price > 0 else 0
            else:
                spread_pct = 0

            # Calculate order book imbalance (top 10 levels)
            bid_volume = sum(float(bid[1]) for bid in bids[:10])
            ask_volume = sum(float(ask[1]) for ask in asks[:10])
            ob_ratio = bid_volume / ask_volume if ask_volume > 0 else float('inf')

            # Get klines for ATR calculation
            klines = await self.rest_client.get_klines(symbol, "1m", limit=50)
            atr = self._calculate_atr(klines, period=14)
            avg_atr = self._calculate_avg_atr(klines, period=30)

            # Get 1-hour price change
            klines_1h = await self.rest_client.get_klines(symbol, "1h", limit=2)
            if len(klines_1h) >= 2:
                prev_close = float(klines_1h[-2][4])
                curr_price = float(klines_1h[-1][4])
                price_change_1h = ((curr_price - prev_close) / prev_close) if prev_close > 0 else 0
            else:
                price_change_1h = 0

            return {
                "atr": atr,
                "avg_atr": avg_atr,
                "spread_pct": spread_pct / 100,  # Convert to decimal
                "normal_spread": spread_pct / 100,  # Use current as baseline
                "ob_ratio": ob_ratio,
                "basis_pct": basis_pct,
                "price_change_1h": price_change_1h * 100,  # Keep as percentage
                "mark_price": mark_price,
                "index_price": index_price,
            }

        except Exception as e:
            logger.error(f"Error getting market data for {symbol}: {e}")
            return {
                "atr": 0,
                "avg_atr": 0,
                "spread_pct": 0,
                "normal_spread": 0,
                "ob_ratio": 1.0,
                "basis_pct": 0,
                "price_change_1h": 0,
            }

    def _calculate_atr(self, klines: list, period: int = 14) -> float:
        """
        Calculate ATR (Average True Range) from klines.

        Args:
            klines: List of kline data [open_time, open, high, low, close, ...].
            period: ATR period.

        Returns:
            Current ATR value.
        """
        if len(klines) < period + 1:
            return 0.0

        true_ranges = []
        for i in range(1, len(klines)):
            high = float(klines[i][2])
            low = float(klines[i][3])
            prev_close = float(klines[i - 1][4])

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            true_ranges.append(tr)

        # Simple average for recent periods
        recent_tr = true_ranges[-period:]
        return sum(recent_tr) / len(recent_tr) if recent_tr else 0.0

    def _calculate_avg_atr(self, klines: list, period: int = 30) -> float:
        """
        Calculate average ATR over longer period.

        Args:
            klines: List of kline data.
            period: Number of periods for average.

        Returns:
            Average ATR value.
        """
        return self._calculate_atr(klines, period)

    async def final_check(self, signal: EntrySignal) -> bool:
        """
        Perform final pre-trade validation.

        Args:
            signal: Entry signal to validate.

        Returns:
            True if all checks pass, False otherwise.
        """
        try:
            # Get fresh market data
            market_data = await self.get_market_data(signal.symbol)

            # Check basis hasn't widened significantly
            current_basis = market_data["basis_pct"] / 100
            if abs(current_basis) > self.max_basis_pct:
                logger.warning(
                    f"Final check failed for {signal.symbol}: "
                    f"basis {current_basis * 100:.3f}% exceeds limit"
                )
                return False

            # Check price hasn't moved too much
            price_change = market_data["price_change_1h"] / 100
            if abs(price_change) > self.max_1h_price_change:
                logger.warning(
                    f"Final check failed for {signal.symbol}: "
                    f"1h price change {price_change * 100:.2f}% exceeds limit"
                )
                return False

            # Verify entry price is still reasonable
            current_price = market_data["mark_price"]
            price_diff_pct = abs((current_price - signal.entry_price) / signal.entry_price)
            if price_diff_pct > 0.005:  # 0.5% slippage tolerance
                logger.warning(
                    f"Final check failed for {signal.symbol}: "
                    f"price slippage {price_diff_pct * 100:.2f}% too high"
                )
                return False

            logger.info(f"✓ Final pre-trade check passed for {signal.symbol}")
            return True

        except Exception as e:
            logger.error(f"Error in final check: {e}")
            return False
