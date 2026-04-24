"""
Risk monitor for trade limits and drawdown control.
Tracks daily PnL, consecutive losses, and enforces trading halts.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from src.config_loader import Config
from src.strategy.signal_engine import EntrySignal

logger = logging.getLogger(__name__)


class RiskMonitor:
    """
    Monitors risk metrics and enforces trading limits.

    Tracks:
    - Daily PnL and loss limits
    - Session drawdown
    - Consecutive losses
    - API errors
    - Position count limits
    """

    def __init__(self, config: Config):
        """
        Initialize risk monitor.

        Args:
            config: Configuration instance.
        """
        self.config = config

        # Risk limits
        self.daily_max_loss_pct = config.get(
            "risk", "daily_max_loss_pct", default=3.0
        ) / 100.0
        self.session_drawdown_limit_pct = config.get(
            "risk", "session_drawdown_limit_pct", default=5.0
        ) / 100.0
        self.consecutive_loss_limit = config.get(
            "risk", "consecutive_loss_limit", default=3
        )
        self.max_concurrent_trades = config.get(
            "strategy", "max_concurrent_trades", default=3
        )
        self.margin_buffer_pct = config.get(
            "risk", "margin_buffer_pct", default=60.0
        ) / 100.0

        # State tracking
        self.starting_equity = 0.0
        self.current_equity = 0.0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.trades_today = 0
        self.wins_today = 0
        self.api_errors = 0
        self.is_halted = False
        self.halt_reason: Optional[str] = None
        self.halt_until: Optional[datetime] = None

        # Trade history for session tracking
        self.trade_history: List[Dict] = []

    def update_equity(self, equity: float):
        """
        Update current equity.

        Args:
            equity: Current account equity.
        """
        if self.starting_equity == 0:
            self.starting_equity = equity
        self.current_equity = equity

        # Check session drawdown
        if self.starting_equity > 0:
            session_drawdown = (self.starting_equity - equity) / self.starting_equity
            if session_drawdown >= self.session_drawdown_limit_pct:
                self.trigger_halt(
                    f"Session drawdown limit reached ({session_drawdown * 100:.2f}%)"
                )

    def record_trade_entry(self, signal: EntrySignal):
        """
        Record a new trade entry.

        Args:
            signal: Entry signal.
        """
        self.trade_history.append({
            "symbol": signal.symbol,
            "entry_time": signal.timestamp,
            "open": True,
        })
        logger.debug(f"Trade entry recorded for {signal.symbol}")

    def record_trade_exit(self, pnl: float):
        """
        Record a trade exit and update risk metrics.

        Args:
            pnl: Realized PnL for the trade.
        """
        self.daily_pnl += pnl
        self.trades_today += 1

        if pnl > 0:
            self.wins_today += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

            # Check consecutive loss limit
            if self.consecutive_losses >= self.consecutive_loss_limit:
                self.trigger_halt(
                    f"Consecutive loss limit reached ({self.consecutive_losses})"
                )

        # Check daily loss limit
        if self.starting_equity > 0:
            daily_loss_pct = abs(self.daily_pnl) / self.starting_equity
            if self.daily_pnl < 0 and daily_loss_pct >= self.daily_max_loss_pct:
                self.trigger_halt(f"Daily loss limit reached ({daily_loss_pct * 100:.2f}%)")

        # Record in history
        self.trade_history.append({
            "pnl": pnl,
            "timestamp": datetime.now(timezone.utc),
        })

        logger.info(
            f"Trade exit recorded: PnL ${pnl:.2f} | "
            f"Daily PnL: ${self.daily_pnl:.2f} | "
            f"Consecutive losses: {self.consecutive_losses}"
        )

    def can_enter_trade(self) -> bool:
        """
        Check if a new trade can be entered.

        Returns:
            True if trade is allowed, False otherwise.
        """
        # Check if halted
        if self.is_halted:
            if self.halt_until and datetime.now(timezone.utc) >= self.halt_until:
                logger.info("Trading halt expired, resuming trading")
                self.is_halted = False
                self.halt_reason = None
                self.halt_until = None
            else:
                logger.warning(f"Trading halted: {self.halt_reason}")
                return False

        # Check daily loss limit
        if self.starting_equity > 0:
            daily_loss_pct = abs(self.daily_pnl) / self.starting_equity
            if self.daily_pnl < 0 and daily_loss_pct >= self.daily_max_loss_pct:
                logger.warning("Daily loss limit reached")
                return False

        # Check consecutive losses
        if self.consecutive_losses >= self.consecutive_loss_limit:
            logger.warning("Consecutive loss limit reached")
            return False

        # Check concurrent trades
        open_positions = len([t for t in self.trade_history if t.get("open", True)])
        if open_positions >= self.max_concurrent_trades:
            logger.warning(f"Max concurrent trades reached ({self.max_concurrent_trades})")
            return False

        # Check API errors
        if self.api_errors >= 5:
            logger.warning("API error threshold reached")
            return False

        return True

    def trigger_halt(self, reason: str, duration_hours: int = 8):
        """
        Trigger a trading halt.

        Args:
            reason: Reason for the halt.
            duration_hours: Duration of halt in hours.
        """
        self.is_halted = True
        self.halt_reason = reason
        self.halt_until = datetime.now(timezone.utc) + timedelta(hours=duration_hours)

        logger.critical(f"TRADING HALT TRIGGERED: {reason}")

    def record_api_error(self):
        """Record an API error."""
        self.api_errors += 1

        if self.api_errors >= 5:
            self.trigger_halt(f"API error threshold reached ({self.api_errors} errors)")

    def reset_daily_stats(self):
        """Reset daily statistics (call at start of new day)."""
        today = date.today()
        last_reset = getattr(self, "_last_reset", None)

        if last_reset != today:
            logger.info("Resetting daily statistics")
            self.starting_equity = self.current_equity
            self.daily_pnl = 0.0
            self.trades_today = 0
            self.wins_today = 0
            self.api_errors = 0
            self._last_reset = today

            # Clear old trade history (keep last 7 days)
            cutoff = datetime.now(timezone.utc).replace(day=datetime.now(timezone.utc).day - 7)
            self.trade_history = [
                t for t in self.trade_history
                if t.get("timestamp", datetime.now(timezone.utc)) > cutoff
            ]

    def get_win_rate(self) -> float:
        """
        Get current win rate.

        Returns:
            Win rate as percentage (0-100).
        """
        if self.trades_today == 0:
            return 0.0
        return (self.wins_today / self.trades_today) * 100

    def get_status(self) -> Dict:
        """
        Get current risk status.

        Returns:
            Dictionary with risk metrics.
        """
        return {
            "is_halted": self.is_halted,
            "halt_reason": self.halt_reason,
            "starting_equity": self.starting_equity,
            "current_equity": self.current_equity,
            "daily_pnl": self.daily_pnl,
            "daily_pnl_pct": (self.daily_pnl / self.starting_equity * 100) if self.starting_equity > 0 else 0,
            "consecutive_losses": self.consecutive_losses,
            "trades_today": self.trades_today,
            "win_rate": self.get_win_rate(),
            "api_errors": self.api_errors,
        }
