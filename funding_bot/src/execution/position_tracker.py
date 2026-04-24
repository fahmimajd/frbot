"""
Position tracker for managing open trades.
Tracks entry/exit conditions and calculates PnL.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.constants import SignalSide, ExitReason, TimeConstants

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents an open position."""

    symbol: str
    side: SignalSide
    entry_price: float
    quantity: float
    leverage: int
    stop_loss: float
    take_profit: float
    entry_time: datetime
    trailing_stop_activated: bool = False
    trailing_stop_price: Optional[float] = None
    peak_profit_pct: float = 0.0


class PositionTracker:
    """
    Tracks and manages open positions.

    Monitors:
    - Open positions
    - Stop loss and take profit levels
    - Trailing stops
    - Time-based exits
    - PnL calculations
    """

    def __init__(self):
        """Initialize position tracker."""
        self.positions: Dict[str, Position] = {}

    def add_position(
        self,
        symbol: str,
        side: SignalSide,
        entry_price: float,
        quantity: float,
        leverage: int,
        stop_loss: float,
        take_profit: float,
        entry_time: datetime,
    ):
        """
        Add a new position to tracking.

        Args:
            symbol: Trading pair symbol.
            side: Trade side (LONG or SHORT).
            entry_price: Entry price.
            quantity: Position quantity.
            leverage: Leverage used.
            stop_loss: Stop loss price.
            take_profit: Take profit price.
            entry_time: Entry timestamp.
        """
        position = Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_time=entry_time,
        )

        self.positions[symbol] = position
        logger.info(f"Position added: {symbol} {side.value} @ ${entry_price:.4f}")

    def remove_position(self, symbol: str):
        """
        Remove a position from tracking.

        Args:
            symbol: Symbol to remove.
        """
        if symbol in self.positions:
            del self.positions[symbol]
            logger.info(f"Position removed: {symbol}")

    def get_open_positions(self) -> List[Position]:
        """
        Get all open positions.

        Returns:
            List of open Position objects.
        """
        return list(self.positions.values())

    def is_trading_symbol(self, symbol: str) -> bool:
        """
        Check if already trading a symbol.

        Args:
            symbol: Symbol to check.

        Returns:
            True if symbol has an open position.
        """
        return symbol in self.positions

    def get_open_position_count(self) -> int:
        """
        Get the number of open positions.

        Returns:
            Number of open positions.
        """
        return len(self.positions)

    def calculate_pnl(self, position: Position, current_price: float) -> float:
        """
        Calculate unrealized PnL for a position.

        Args:
            position: Position to calculate PnL for.
            current_price: Current market price.

        Returns:
            PnL in USDT.
        """
        if position.side == SignalSide.SHORT:
            pnl = (position.entry_price - current_price) * position.quantity
        else:  # LONG
            pnl = (current_price - position.entry_price) * position.quantity

        return pnl

    def calculate_pnl_pct(self, position: Position, current_price: float) -> float:
        """
        Calculate PnL percentage.

        Args:
            position: Position to calculate PnL for.
            current_price: Current market price.

        Returns:
            PnL as percentage.
        """
        if position.side == SignalSide.SHORT:
            pnl_pct = (position.entry_price - current_price) / position.entry_price
        else:  # LONG
            pnl_pct = (current_price - position.entry_price) / position.entry_price

        return pnl_pct * 100

    def update_trailing_stop(self, position: Position, current_price: float):
        """
        Update trailing stop if activated.

        Args:
            position: Position to update.
            current_price: Current market price.
        """
        pnl_pct = self.calculate_pnl_pct(position, current_price) / 100

        # Activate trailing stop after +0.15% profit
        if pnl_pct >= 0.0015 and not position.trailing_stop_activated:
            position.trailing_stop_activated = True
            position.peak_profit_pct = pnl_pct
            position.trailing_stop_price = current_price
            logger.info(
                f"Trailing stop activated for {position.symbol} "
                f"at ${position.trailing_stop_price:.4f}"
            )

        # Update peak profit and trailing stop
        if position.trailing_stop_activated:
            if pnl_pct > position.peak_profit_pct:
                position.peak_profit_pct = pnl_pct

                # Trail at 0.10% below peak
                if position.side == SignalSide.SHORT:
                    position.trailing_stop_price = current_price * (1 + 0.0010)
                else:  # LONG
                    position.trailing_stop_price = current_price * (1 - 0.0010)

                logger.debug(
                    f"Trailing stop updated for {position.symbol} "
                    f"to ${position.trailing_stop_price:.4f}"
                )

    def check_exit_conditions(
        self, position: Position, current_price: float
    ) -> Optional[ExitReason]:
        """
        Check if any exit conditions are met.

        Args:
            position: Position to check.
            current_price: Current market price.

        Returns:
            ExitReason if exit condition met, None otherwise.
        """
        # Update trailing stop first
        self.update_trailing_stop(position, current_price)

        # Check stop loss
        if position.side == SignalSide.SHORT:
            if current_price >= position.stop_loss:
                logger.info(f"Stop loss hit for {position.symbol} @ ${current_price:.4f}")
                return ExitReason.STOP_LOSS
        else:  # LONG
            if current_price <= position.stop_loss:
                logger.info(f"Stop loss hit for {position.symbol} @ ${current_price:.4f}")
                return ExitReason.STOP_LOSS

        # Check take profit
        if position.side == SignalSide.SHORT:
            if current_price <= position.take_profit:
                logger.info(f"Take profit hit for {position.symbol} @ ${current_price:.4f}")
                return ExitReason.TAKE_PROFIT
        else:  # LONG
            if current_price >= position.take_profit:
                logger.info(f"Take profit hit for {position.symbol} @ ${current_price:.4f}")
                return ExitReason.TAKE_PROFIT

        # Check trailing stop
        if position.trailing_stop_activated and position.trailing_stop_price:
            if position.side == SignalSide.SHORT:
                if current_price >= position.trailing_stop_price:
                    logger.info(
                        f"Trailing stop hit for {position.symbol} @ ${current_price:.4f}"
                    )
                    return ExitReason.TRAILING_STOP
            else:  # LONG
                if current_price <= position.trailing_stop_price:
                    logger.info(
                        f"Trailing stop hit for {position.symbol} @ ${current_price:.4f}"
                    )
                    return ExitReason.TRAILING_STOP

        # Check time-based exit (15 minutes after settlement)
        elapsed_minutes = (datetime.now(timezone.utc) - position.entry_time).total_seconds() / TimeConstants.SECONDS_PER_MINUTE
        if elapsed_minutes >= TimeConstants.HARD_EXIT_AFTER_MINUTES:  # Hard exit after 30 minutes
            logger.info(f"Time-based exit for {position.symbol} ({elapsed_minutes:.0f} min)")
            return ExitReason.TIME_BASED

        return None

    async def close_all_positions(self, order_manager):
        """
        Close all open positions.

        Args:
            order_manager: OrderManager instance for executing exits.
        """
        positions = self.get_open_positions()

        for position in positions:
            try:
                await order_manager.place_exit_order(
                    position, ExitReason.MANUAL, position.entry_price
                )
                self.remove_position(position.symbol)
            except Exception as e:
                logger.error(f"Error closing position {position.symbol}: {e}")

        logger.info(f"Closed {len(positions)} positions")
