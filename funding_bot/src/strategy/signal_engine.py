"""
Signal engine for entry logic.
Evaluates all signal conditions and generates trade signals.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from src.config_loader import Config
from src.strategy.funding_scanner import FundingSignal

logger = logging.getLogger(__name__)


class SignalSide(Enum):
    """Trade side enumeration."""
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


class ExitReason(Enum):
    """Exit reason enumeration."""
    TAKE_PROFIT = "TP"
    STOP_LOSS = "SL"
    TRAILING_STOP = "TRAIL"
    TIME_BASED = "TIME"
    MANUAL = "MANUAL"
    VOLATILITY_SPIKE = "VOLATILITY"
    FUNDING_REVERSAL = "FUNDING_REV"


@dataclass
class EntrySignal:
    """Represents a validated entry signal."""

    symbol: str
    side: SignalSide
    funding_rate: float
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    position_size: float
    leverage: int
    timestamp: datetime
    atr_value: float
    spread_pct: float
    basis_pct: float
    ob_imbalance: float
    minutes_to_funding: int
    r_ratio: float


class SignalEngine:
    """
    Evaluates entry conditions and generates trade signals.

    Implements all signal filters from the strategy specification:
    - Funding rate threshold
    - Time window
    - Volatility filter
    - Basis filter
    - Order book imbalance
    """

    def __init__(self, config: Config):
        """
        Initialize the signal engine.

        Args:
            config: Configuration instance.
        """
        self.config = config

        # Strategy parameters
        self.funding_threshold = config.get(
            "strategy", "funding_threshold_pct", default=0.03
        ) / 100.0
        self.entry_window_start = config.get(
            "strategy", "entry_window_start_min", default=15
        )
        self.entry_window_end = config.get(
            "strategy", "entry_window_end_min", default=10
        )

        # Filter parameters
        self.atr_multiplier_limit = config.get(
            "filters", "atr_multiplier_limit", default=2.0
        )
        self.max_1h_price_change = config.get(
            "filters", "max_1h_price_change_pct", default=2.0
        ) / 100.0
        self.max_spread_multiplier = config.get(
            "filters", "max_spread_multiplier", default=3.0
        )
        self.max_basis_pct = config.get(
            "filters", "max_basis_pct", default=0.30
        ) / 100.0
        self.ob_imbalance_enabled = config.get(
            "filters", "ob_imbalance_enabled", default=True
        )
        self.ob_short_threshold = config.get(
            "filters", "ob_imbalance_short_threshold", default=0.8
        )
        self.ob_long_threshold = config.get(
            "filters", "ob_imbalance_long_threshold", default=1.2
        )

        # Risk parameters
        self.risk_per_trade = config.get(
            "risk", "risk_per_trade_pct", default=1.0
        ) / 100.0
        self.max_leverage = config.get("risk", "max_leverage", default=5)
        self.take_profit_pct = config.get(
            "risk", "take_profit_pct", default=0.30
        ) / 100.0
        self.stop_loss_pct = config.get(
            "risk", "stop_loss_pct", default=0.20
        ) / 100.0
        self.min_rr_ratio = config.get("risk", "min_rr_ratio", default=1.3)

    def check_time_window(self, minutes_to_funding: int) -> bool:
        """
        Check if current time is within entry window.

        Args:
            minutes_to_funding: Minutes until next funding settlement.

        Returns:
            True if within entry window, False otherwise.
        """
        in_window = (
            self.entry_window_end <= minutes_to_funding <= self.entry_window_start
        )
        if not in_window:
            logger.debug(
                f"Time window check failed: {minutes_to_funding} min "
                f"(valid: {self.entry_window_end}-{self.entry_window_start})"
            )
        return in_window

    def check_funding_threshold(self, funding_rate: float) -> bool:
        """
        Check if funding rate meets threshold.

        Args:
            funding_rate: Current funding rate.

        Returns:
            True if threshold met, False otherwise.
        """
        meets_threshold = abs(funding_rate) >= self.funding_threshold
        if not meets_threshold:
            logger.debug(
                f"Funding threshold check failed: {funding_rate * 100:.4f}% "
                f"(required: {self.funding_threshold * 100:.2f}%)"
            )
        return meets_threshold

    def check_volatility_filter(
        self,
        current_atr: float,
        avg_atr: float,
        price_change_1h: float,
        spread_pct: float,
        normal_spread_pct: float,
    ) -> bool:
        """
        Check volatility filter conditions.

        Args:
            current_atr: Current ATR(14) value.
            avg_atr: 30-day average ATR.
            price_change_1h: 1-hour price change percentage.
            spread_pct: Current bid-ask spread percentage.
            normal_spread_pct: Normal spread percentage for the symbol.

        Returns:
            True if all volatility conditions pass, False otherwise.
        """
        # ATR check
        if avg_atr > 0:
            atr_ratio = current_atr / avg_atr
            if atr_ratio > self.atr_multiplier_limit:
                logger.debug(
                    f"ATR check failed: {atr_ratio:.2f}x "
                    f"(limit: {self.atr_multiplier_limit}x)"
                )
                return False

        # 1-hour price change check
        if abs(price_change_1h) > self.max_1h_price_change:
            logger.debug(
                f"Price change check failed: {price_change_1h * 100:.2f}% "
                f"(limit: {self.max_1h_price_change * 100:.2f}%)"
            )
            return False

        # Spread check
        if normal_spread_pct > 0:
            spread_ratio = spread_pct / normal_spread_pct
            if spread_ratio > self.max_spread_multiplier:
                logger.debug(
                    f"Spread check failed: {spread_ratio:.2f}x "
                    f"(limit: {self.max_spread_multiplier}x)"
                )
                return False

        return True

    def check_basis_filter(self, basis_pct: float) -> bool:
        """
        Check basis filter.

        Args:
            basis_pct: Mark price vs index price difference percentage.

        Returns:
            True if basis is within acceptable range, False otherwise.
        """
        if abs(basis_pct) > self.max_basis_pct:
            logger.debug(
                f"Basis check failed: {basis_pct * 100:.3f}% "
                f"(limit: {self.max_basis_pct * 100:.2f}%)"
            )
            return False
        return True

    def check_order_book_imbalance(
        self, ob_ratio: float, side: SignalSide
    ) -> bool:
        """
        Check order book imbalance confirmation.

        Args:
            ob_ratio: Bid/ask volume ratio (top 10 levels).
            side: Trade side (LONG or SHORT).

        Returns:
            True if OB imbalance confirms the signal, False otherwise.
        """
        if not self.ob_imbalance_enabled:
            return True

        if side == SignalSide.SHORT:
            # For SHORT: want bid/ask < 0.8 (more asks than bids)
            if ob_ratio >= self.ob_short_threshold:
                logger.debug(
                    f"OB imbalance check failed for SHORT: {ob_ratio:.2f} "
                    f"(need < {self.ob_short_threshold})"
                )
                return False
        elif side == SignalSide.LONG:
            # For LONG: want bid/ask > 1.2 (more bids than asks)
            if ob_ratio <= self.ob_long_threshold:
                logger.debug(
                    f"OB imbalance check failed for LONG: {ob_ratio:.2f} "
                    f"(need > {self.ob_long_threshold})"
                )
                return False

        return True

    def determine_side(self, funding_rate: float) -> SignalSide:
        """
        Determine trade side based on funding rate.

        Args:
            funding_rate: Current funding rate.

        Returns:
            SignalSide (LONG, SHORT, or NONE).
        """
        if funding_rate > self.funding_threshold:
            return SignalSide.SHORT  # Positive funding → short to collect fees
        elif funding_rate < -self.funding_threshold:
            return SignalSide.LONG  # Negative funding → long to collect fees
        else:
            return SignalSide.NONE

    def calculate_tp_sl(
        self, entry_price: float, side: SignalSide
    ) -> tuple[float, float]:
        """
        Calculate take profit and stop loss prices.

        Args:
            entry_price: Entry price.
            side: Trade side.

        Returns:
            Tuple of (take_profit_price, stop_loss_price).
        """
        if side == SignalSide.SHORT:
            tp_price = entry_price * (1 - self.take_profit_pct)
            sl_price = entry_price * (1 + self.stop_loss_pct)
        else:  # LONG
            tp_price = entry_price * (1 + self.take_profit_pct)
            sl_price = entry_price * (1 - self.stop_loss_pct)

        return tp_price, sl_price

    def calculate_r_ratio(
        self, entry_price: float, tp_price: float, sl_price: float, side: SignalSide
    ) -> float:
        """
        Calculate risk-reward ratio.

        Args:
            entry_price: Entry price.
            tp_price: Take profit price.
            sl_price: Stop loss price.
            side: Trade side.

        Returns:
            Risk-reward ratio (reward/risk).
        """
        if side == SignalSide.SHORT:
            reward = entry_price - tp_price
            risk = sl_price - entry_price
        else:  # LONG
            reward = tp_price - entry_price
            risk = entry_price - sl_price

        if risk == 0:
            return 0.0

        return reward / risk

    def evaluate_signal(
        self,
        signal_data: FundingSignal,
        market_data: Dict[str, Any],
        equity: float,
    ) -> Optional[EntrySignal]:
        """
        Evaluate all conditions and generate entry signal if valid.

        Args:
            signal_data: Funding signal from scanner.
            market_data: Additional market data (ATR, spread, OB, etc.).
            equity: Current account equity.

        Returns:
            EntrySignal if all conditions met, None otherwise.
        """
        symbol = signal_data.symbol
        funding_rate = signal_data.funding_rate
        entry_price = signal_data.mark_price
        basis_pct = signal_data.basis_pct / 100.0  # Convert from percentage

        # Extract market data
        current_atr = market_data.get("atr", 0)
        avg_atr = market_data.get("avg_atr", current_atr)
        price_change_1h = market_data.get("price_change_1h", 0) / 100.0
        spread_pct = market_data.get("spread_pct", 0)
        normal_spread = market_data.get("normal_spread", spread_pct)
        ob_ratio = market_data.get("ob_ratio", 1.0)
        minutes_to_funding = market_data.get("minutes_to_funding", 0)

        # Check all filters
        if not self.check_funding_threshold(funding_rate):
            return None

        if not self.check_time_window(minutes_to_funding):
            return None

        if not self.check_volatility_filter(
            current_atr, avg_atr, price_change_1h, spread_pct, normal_spread
        ):
            return None

        if not self.check_basis_filter(basis_pct):
            return None

        # Determine side
        side = self.determine_side(funding_rate)
        if side == SignalSide.NONE:
            return None

        if not self.check_order_book_imbalance(ob_ratio, side):
            return None

        # Calculate TP/SL
        tp_price, sl_price = self.calculate_tp_sl(entry_price, side)

        # Calculate R:R ratio
        r_ratio = self.calculate_r_ratio(entry_price, tp_price, sl_price, side)
        if r_ratio < self.min_rr_ratio:
            logger.debug(
                f"R:R ratio check failed: {r_ratio:.2f} "
                f"(minimum: {self.min_rr_ratio:.1f})"
            )
            return None

        # Calculate position size
        sl_distance_pct = abs((sl_price - entry_price) / entry_price)
        if sl_distance_pct > 0:
            risk_amount = equity * self.risk_per_trade
            position_size = risk_amount / (entry_price * sl_distance_pct)
        else:
            position_size = 0

        # Apply leverage limit
        leverage = min(self.max_leverage, 5)

        entry_signal = EntrySignal(
            symbol=symbol,
            side=side,
            funding_rate=funding_rate,
            entry_price=entry_price,
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            position_size=position_size,
            leverage=leverage,
            timestamp=datetime.now(timezone.utc),
            atr_value=current_atr,
            spread_pct=spread_pct,
            basis_pct=basis_pct,
            ob_imbalance=ob_ratio,
            minutes_to_funding=minutes_to_funding,
            r_ratio=r_ratio,
        )

        logger.info(
            f"✓ Valid entry signal: {symbol} {side.value} | "
            f"Entry: ${entry_price:.4f} | TP: ${tp_price:.4f} | "
            f"SL: ${sl_price:.4f} | R:R: {r_ratio:.2f}"
        )

        return entry_signal
