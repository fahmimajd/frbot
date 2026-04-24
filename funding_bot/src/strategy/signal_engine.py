"""
Signal engine for entry logic.
Evaluates all signal conditions and generates trade signals.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.config_loader import Config
from src.strategy.funding_scanner import FundingSignal
from src.constants import SignalSide, ExitReason, ScoringWeights, DefaultParams, FilterDefaults

# Import PositionSizer with TYPE_CHECKING to avoid circular import
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.risk.position_sizer import PositionSizer

logger = logging.getLogger(__name__)


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
    score: float = 0.0  # Quality score for ranking signals (0-100)


class SignalEngine:
    """
    Evaluates entry conditions and generates trade signals.

    Implements all signal filters from the strategy specification:
    - Funding rate threshold
    - Time window
    - Volatility filter
    - Basis filter
    - Order book imbalance

    Features:
    - Multi-pair scanning with signal scoring
    - Selects best signal based on weighted scoring system
    - Supports leverage up to 20x with $1 capital
    - Only 1 active trade at a time
    """

    def __init__(self, config: Config):
        """
        Initialize the signal engine.

        Args:
            config: Configuration instance.
        """
        self.config = config

        # Strategy parameters - use constants as defaults
        self.funding_threshold = config.get(
            "strategy", "funding_threshold_pct", default=DefaultParams.FUNDING_THRESHOLD_PCT
        ) / 100.0
        self.entry_window_start = config.get(
            "strategy", "entry_window_start_min", default=DefaultParams.ENTRY_WINDOW_START_MIN
        )
        self.entry_window_end = config.get(
            "strategy", "entry_window_end_min", default=DefaultParams.ENTRY_WINDOW_END_MIN
        )

        # Filter parameters - use constants as defaults
        self.atr_multiplier_limit = config.get(
            "filters", "atr_multiplier_limit", default=FilterDefaults.ATR_MULTIPLIER_LIMIT
        )
        self.max_1h_price_change = config.get(
            "filters", "max_1h_price_change_pct", default=FilterDefaults.MAX_1H_PRICE_CHANGE_PCT
        ) / 100.0
        self.max_spread_multiplier = config.get(
            "filters", "max_spread_multiplier", default=FilterDefaults.MAX_SPREAD_MULTIPLIER
        )
        self.max_basis_pct = config.get(
            "filters", "max_basis_pct", default=FilterDefaults.MAX_BASIS_PCT
        ) / 100.0
        self.ob_imbalance_enabled = config.get(
            "filters", "ob_imbalance_enabled", default=FilterDefaults.OB_IMBALANCE_ENABLED
        )
        self.ob_short_threshold = config.get(
            "filters", "ob_imbalance_short_threshold", default=FilterDefaults.OB_SHORT_THRESHOLD
        )
        self.ob_long_threshold = config.get(
            "filters", "ob_imbalance_long_threshold", default=FilterDefaults.OB_LONG_THRESHOLD
        )

        # Risk parameters - use constants as defaults
        self.risk_per_trade = config.get(
            "risk", "risk_per_trade_pct", default=DefaultParams.RISK_PER_TRADE_PCT
        ) / 100.0
        self.max_leverage = config.get("risk", "max_leverage", default=DefaultParams.MAX_LEVERAGE)
        self.take_profit_pct = config.get(
            "risk", "take_profit_pct", default=DefaultParams.TAKE_PROFIT_PCT
        ) / 100.0
        self.stop_loss_pct = config.get(
            "risk", "stop_loss_pct", default=DefaultParams.STOP_LOSS_PCT
        ) / 100.0
        self.min_rr_ratio = config.get("risk", "min_rr_ratio", default=DefaultParams.MIN_RR_RATIO)

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

    def calculate_signal_score(
        self,
        funding_rate: float,
        r_ratio: float,
        minutes_to_funding: int,
        spread_pct: float,
        ob_ratio: float,
        side: SignalSide,
    ) -> float:
        """
        Calculate a quality score for ranking signals.

        Higher score = better signal quality.

        Scoring factors (using ScoringWeights constants):
        - Funding rate magnitude (higher = better) - max {ScoringWeights.FUNDING_RATE} points
        - Risk-reward ratio (higher = better) - max {ScoringWeights.R_RATIO} points
        - Time to funding (closer to optimal window = better) - max {ScoringWeights.TIMING} points
        - Spread (lower = better) - max {ScoringWeights.SPREAD} points
        - Order book imbalance confirmation (stronger = better) - max {ScoringWeights.OB_IMBALANCE} points

        Args:
            funding_rate: Current funding rate.
            r_ratio: Risk-reward ratio.
            minutes_to_funding: Minutes until funding settlement.
            spread_pct: Current spread percentage.
            ob_ratio: Order book imbalance ratio.
            side: Trade side.

        Returns:
            Quality score (0-100 scale).
        """
        # 1. Funding rate score (0-FUNDING_RATE points)
        # Higher absolute funding rate = more profit potential
        funding_score = min(abs(funding_rate) * 1000, ScoringWeights.FUNDING_RATE)

        # 2. R:R ratio score (0-R_RATIO points)
        # Higher R:R = better risk management
        rr_score = min(max((r_ratio - 1.0) * 10, 0), ScoringWeights.R_RATIO)

        # 3. Timing score (0-TIMING points)
        # Optimal: 12-13 minutes before funding (middle of 10-15 window)
        optimal_time = 12.5
        time_diff = abs(minutes_to_funding - optimal_time)
        timing_score = max(ScoringWeights.TIMING - (time_diff * 2), 0)

        # 4. Spread score (0-SPREAD points)
        # Lower spread = better execution
        spread_score = max(ScoringWeights.SPREAD - (spread_pct * 100), 0)

        # 5. OB imbalance score (0-OB_IMBALANCE points)
        # Stronger imbalance = better confirmation
        if side == SignalSide.SHORT:
            # For SHORT: lower ob_ratio is better (more asks)
            ob_score = min((self.ob_short_threshold - ob_ratio) * 20, ScoringWeights.OB_IMBALANCE)
        else:  # LONG
            # For LONG: higher ob_ratio is better (more bids)
            ob_score = min((ob_ratio - self.ob_long_threshold) * 20, ScoringWeights.OB_IMBALANCE)
        ob_score = max(ob_score, 0)

        total_score = funding_score + rr_score + timing_score + spread_score + ob_score

        logger.debug(
            f"Signal score breakdown: funding={funding_score:.1f}, "
            f"rr={rr_score:.1f}, timing={timing_score:.1f}, "
            f"spread={spread_score:.1f}, ob={ob_score:.1f} => {total_score:.1f}"
        )

        return total_score

    def evaluate_signal(
        self,
        signal_data: FundingSignal,
        market_data: Dict[str, Any],
        equity: float,
        position_sizer: "PositionSizer",
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

        # Calculate signal quality score for ranking
        signal_score = self.calculate_signal_score(
            funding_rate=funding_rate,
            r_ratio=r_ratio,
            minutes_to_funding=minutes_to_funding,
            spread_pct=spread_pct,
            ob_ratio=ob_ratio,
            side=side,
        )

        # Calculate position size using PositionSizer
        position_size = position_sizer.calculate_position_size(
            symbol=symbol,
            entry_price=entry_price,
            equity=equity,
        )

        if position_size is None or position_size <= 0:
            logger.warning(f"Invalid position size for {symbol}")
            return None

        # Apply leverage limit (use config max_leverage, not hardcoded)
        leverage = self.max_leverage

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
            score=signal_score,
        )

        logger.info(
            f"✓ Valid entry signal: {symbol} {side.value} | "
            f"Entry: ${entry_price:.4f} | TP: ${tp_price:.4f} | "
            f"SL: ${sl_price:.4f} | R:R: {r_ratio:.2f} | Score: {signal_score:.1f}"
        )

        return entry_signal
