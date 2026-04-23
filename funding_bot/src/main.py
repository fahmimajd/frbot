"""
Main entry point for the funding bot.
Orchestrates all components and manages the trading lifecycle.
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import List, Optional

from src.config_loader import get_config, Config
from src.exchange.binance_rest import BinanceRESTClient
from src.exchange.binance_ws import BinanceWebSocketManager
from src.exchange.rate_limiter import RateLimiter
from src.strategy.funding_scanner import FundingScanner, FundingSignal
from src.strategy.signal_engine import SignalEngine, EntrySignal, SignalSide
from src.risk.pre_trade_check import PreTradeChecker
from src.risk.position_sizer import PositionSizer
from src.risk.risk_monitor import RiskMonitor
from src.execution.order_manager import OrderManager
from src.execution.position_tracker import PositionTracker
from src.data.db_manager import DatabaseManager
from src.notifications.telegram_alert import TelegramNotifier

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", '
           '"logger": "%(name)s", "message": "%(message)s"}',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("funding_bot.log"),
    ],
)

logger = logging.getLogger(__name__)


class FundingBot:
    """
    Main funding rate arbitrage trading bot.

    Orchestrates all components:
    - Scanning for funding rate opportunities
    - Evaluating entry signals
    - Managing risk and position sizing
    - Executing trades
    - Monitoring exits
    """

    def __init__(self, config: Config):
        """
        Initialize the funding bot.

        Args:
            config: Configuration instance.
        """
        self.config = config
        self._shutdown_requested = False

        # Initialize components
        self.rest_client = BinanceRESTClient(config)
        self.ws_manager = BinanceWebSocketManager(config)
        self.rate_limiter = RateLimiter(config)
        self.scanner = FundingScanner(config, self.rest_client)
        self.signal_engine = SignalEngine(config)
        self.pre_trade_checker = PreTradeChecker(config, self.rest_client)
        self.position_sizer = PositionSizer(config)
        self.risk_monitor = RiskMonitor(config)
        self.order_manager = OrderManager(config, self.rest_client, self.rate_limiter)
        self.position_tracker = PositionTracker()
        self.db_manager = DatabaseManager(config)
        self.telegram_notifier = TelegramNotifier(config)

        # State
        self.active_signals: List[EntrySignal] = []
        self.equity = 0.0

    async def initialize(self):
        """Initialize all components."""
        logger.info("Initializing funding bot...")

        # Initialize database
        await self.db_manager.initialize()

        # Start WebSocket manager
        await self.ws_manager.start()

        # Get initial account equity
        try:
            account_info = await self.rest_client.get_account_info()
            self.equity = float(account_info.get("availableBalance", 0))
            logger.info(f"Account equity: ${self.equity:,.2f}")
        except Exception as e:
            logger.error(f"Failed to get account info: {e}")
            self.equity = 1000.0  # Default for paper trading

        # Initialize risk monitor with current equity
        self.risk_monitor.update_equity(self.equity)

        logger.info("Funding bot initialized successfully")

    async def shutdown(self):
        """Gracefully shutdown the bot."""
        logger.info("Shutting down funding bot...")

        self._shutdown_requested = True

        # Close all positions
        await self.position_tracker.close_all_positions(self.order_manager)

        # Stop WebSocket connections
        await self.ws_manager.stop()

        # Close REST session
        await self.rest_client.close()

        # Close database connection
        await self.db_manager.close()

        logger.info("Funding bot shutdown complete")

    async def run_scan_cycle(self):
        """Run a single scan and trade evaluation cycle."""
        if self._shutdown_requested:
            return

        try:
            # Check if within entry window
            minutes_to_funding = self.scanner.get_minutes_to_funding()
            logger.info(f"Minutes to next funding: {minutes_to_funding}")

            # Get top signals
            top_signals = await self.scanner.get_top_signals(
                top_n=self.config.get("strategy", "top_pairs_to_scan", default=10)
            )

            # Filter by funding threshold
            qualified_signals = self.scanner.filter_by_threshold(top_signals)

            # Evaluate each signal
            for signal_data in qualified_signals[:3]:  # Max 3 concurrent trades
                # Check if already trading this symbol
                if self.position_tracker.is_trading_symbol(signal_data.symbol):
                    logger.debug(f"Already trading {signal_data.symbol}, skipping")
                    continue

                # Get additional market data
                market_data = await self.pre_trade_checker.get_market_data(
                    signal_data.symbol
                )
                market_data["minutes_to_funding"] = minutes_to_funding

                # Evaluate signal
                entry_signal = self.signal_engine.evaluate_signal(
                    signal_data, market_data, self.equity
                )

                if entry_signal:
                    # Final pre-trade check
                    if await self.pre_trade_checker.final_check(entry_signal):
                        self.active_signals.append(entry_signal)
                        await self.execute_entry(entry_signal)

        except Exception as e:
            logger.error(f"Error in scan cycle: {e}", exc_info=True)

    async def execute_entry(self, signal: EntrySignal):
        """
        Execute an entry trade.

        Args:
            signal: Validated entry signal.
        """
        try:
            # Check risk limits
            if not self.risk_monitor.can_enter_trade():
                logger.warning(f"Risk monitor blocked entry for {signal.symbol}")
                return

            # Place order
            order_result = await self.order_manager.place_entry_order(signal)

            if order_result.get("success"):
                # Track position
                self.position_tracker.add_position(
                    symbol=signal.symbol,
                    side=signal.side,
                    entry_price=signal.entry_price,
                    quantity=signal.position_size,
                    leverage=signal.leverage,
                    stop_loss=signal.stop_loss_price,
                    take_profit=signal.take_profit_price,
                    entry_time=datetime.now(timezone.utc),
                )

                # Update risk monitor
                self.risk_monitor.record_trade_entry(signal)

                # Send notification
                await self.telegram_notifier.send_trade_open(signal, order_result)

                # Log to database
                await self.db_manager.log_trade_entry(signal, order_result)

                logger.info(f"✓ Entry executed: {signal.symbol} {signal.side.value}")
            else:
                logger.error(f"Entry failed for {signal.symbol}: {order_result}")

        except Exception as e:
            logger.error(f"Error executing entry: {e}", exc_info=True)

    async def monitor_exits(self):
        """Monitor open positions for exit conditions."""
        positions = self.position_tracker.get_open_positions()

        for position in positions:
            try:
                # Get current price
                premium_index = await self.rest_client.get_funding_rate(
                    position.symbol
                )
                current_price = float(premium_index.get("markPrice", 0))

                # Check exit conditions
                exit_reason = self.position_tracker.check_exit_conditions(
                    position, current_price
                )

                if exit_reason:
                    await self.execute_exit(position, exit_reason, current_price)

            except Exception as e:
                logger.error(
                    f"Error monitoring position {position.symbol}: {e}", exc_info=True
                )

    async def execute_exit(self, position, reason: str, current_price: float):
        """
        Execute an exit trade.

        Args:
            position: Position to close.
            reason: Exit reason.
            current_price: Current market price.
        """
        try:
            # Place exit order
            order_result = await self.order_manager.place_exit_order(
                position, reason, current_price
            )

            if order_result.get("success"):
                # Calculate PnL
                pnl = self.position_tracker.calculate_pnl(position, current_price)

                # Update risk monitor
                self.risk_monitor.record_trade_exit(pnl)

                # Send notification
                await self.telegram_notifier.send_trade_close(
                    position, reason, pnl, order_result
                )

                # Log to database
                await self.db_manager.log_trade_exit(position, reason, pnl, order_result)

                # Remove from tracker
                self.position_tracker.remove_position(position.symbol)

                logger.info(
                    f"✓ Exit executed: {position.symbol} | "
                    f"Reason: {reason} | PnL: ${pnl:.2f}"
                )

        except Exception as e:
            logger.error(f"Error executing exit: {e}", exc_info=True)

    async def run(self):
        """Main bot loop."""
        await self.initialize()

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig, lambda: asyncio.create_task(self.shutdown())
            )

        logger.info("Starting main trading loop...")

        # Main loop
        while not self._shutdown_requested:
            try:
                # Run scan cycle
                await self.run_scan_cycle()

                # Monitor exits
                await self.monitor_exits()

                # Update equity periodically
                try:
                    account_info = await self.rest_client.get_account_info()
                    self.equity = float(account_info.get("availableBalance", 0))
                    self.risk_monitor.update_equity(self.equity)
                except Exception:
                    pass

                # Wait before next cycle
                await asyncio.sleep(60)  # Scan every minute

            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(5)

        await self.shutdown()


async def main():
    """Main entry point."""
    try:
        config = get_config()
        bot = FundingBot(config)
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Bot interrupted by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
