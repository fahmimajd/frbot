"""
Position sizing calculator.
Calculates optimal position size based on risk parameters.
"""

import logging
from typing import Tuple

from src.config_loader import Config

logger = logging.getLogger(__name__)


class PositionSizer:
    """
    Calculates position sizes based on risk parameters.

    Uses equity-based risk percentage and stop-loss distance
    to determine optimal position size.
    """

    def __init__(self, config: Config):
        """
        Initialize position sizer.

        Args:
            config: Configuration instance.
        """
        self.config = config
        self.risk_per_trade_pct = config.get(
            "risk", "risk_per_trade_pct", default=1.0
        ) / 100.0
        self.max_leverage = config.get("risk", "max_leverage", default=5)

    def calculate_position_size(
        self,
        equity: float,
        entry_price: float,
        stop_loss_price: float,
        side: str,
    ) -> Tuple[float, int]:
        """
        Calculate position size and leverage.

        Args:
            equity: Current account equity in USDT.
            entry_price: Entry price.
            stop_loss_price: Stop loss price.
            side: Trade side (LONG or SHORT).

        Returns:
            Tuple of (position_size, leverage).
        """
        # Calculate stop loss distance
        if side == "SHORT":
            sl_distance = stop_loss_price - entry_price
        else:  # LONG
            sl_distance = entry_price - stop_loss_price

        sl_distance_pct = sl_distance / entry_price if entry_price > 0 else 0

        if sl_distance_pct <= 0:
            logger.warning("Invalid stop loss distance, using minimum")
            sl_distance_pct = 0.002  # 0.2% minimum

        # Calculate risk amount
        risk_amount = equity * self.risk_per_trade_pct

        # Calculate position size: size = risk / (entry_price * sl_distance_pct)
        position_size = risk_amount / (entry_price * sl_distance_pct)

        # Calculate required leverage
        notional_value = position_size * entry_price
        required_leverage = notional_value / equity if equity > 0 else 1

        # Apply max leverage limit
        leverage = min(int(required_leverage) + 1, self.max_leverage)
        leverage = max(leverage, 1)

        # Adjust position size to match leverage
        max_notional = equity * leverage
        if notional_value > max_notional:
            position_size = max_notional / entry_price

        logger.info(
            f"Position size calculated: {position_size:.4f} units | "
            f"Leverage: {leverage}x | "
            f"Notional: ${notional_value:.2f}"
        )

        return position_size, leverage

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
