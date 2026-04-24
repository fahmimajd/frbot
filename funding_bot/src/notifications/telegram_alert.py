"""
Telegram notification bot.
Sends trade alerts and system notifications via Telegram Bot API.
"""

import logging
from typing import Any, Dict, Optional

import aiohttp
from src.config_loader import Config
from src.strategy.signal_engine import EntrySignal
from src.constants import SignalSide, ExitReason

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """
    Sends notifications via Telegram Bot API.

    Supports:
    - Trade open/close alerts
    - Error notifications
    - Daily summaries
    """

    def __init__(self, config: Config):
        """
        Initialize Telegram notifier.

        Args:
            config: Configuration instance.
        """
        self.config = config
        self.enabled = config.get("notifications", "telegram_enabled", default=False)
        self.bot_token = config.get("notifications", "telegram_bot_token")
        self.chat_id = config.get("notifications", "telegram_chat_id")

        self.base_url = "https://api.telegram.org"
        self._session: Optional[aiohttp.ClientSession] = None

        if not self.enabled or not self.bot_token or not self.chat_id:
            logger.info("Telegram notifications disabled or not configured")

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def send_message(self, message: str, parse_mode: str = "HTML"):
        """
        Send a message to Telegram chat.

        Args:
            message: Message text (supports HTML formatting).
            parse_mode: Parse mode (HTML or Markdown).
        """
        if not self.enabled or not self.bot_token or not self.chat_id:
            return

        try:
            session = await self._get_session()
            url = f"{self.base_url}/bot{self.bot_token}/sendMessage"

            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode,
            }

            async with session.post(url, json=payload) as response:
                result = await response.json()
                if not result.get("ok"):
                    logger.error(f"Telegram error: {result}")

        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")

    async def send_trade_open(self, signal: EntrySignal, order_result: Dict[str, Any]):
        """
        Send trade open notification.

        Args:
            signal: Entry signal.
            order_result: Order execution result.
        """
        fill_price = order_result.get("fill_price", signal.entry_price)

        message = (
            f"🟢 <b>TRADE OPENED</b>\n\n"
            f"Symbol: <code>{signal.symbol}</code>\n"
            f"Side: <b>{signal.side.value}</b>\n"
            f"Entry: ${fill_price:.4f}\n"
            f"Quantity: {signal.position_size:.2f}\n"
            f"Leverage: {signal.leverage}x\n"
            f"Funding Rate: {signal.funding_rate * 100:.4f}%\n\n"
            f"Take Profit: ${signal.take_profit_price:.4f}\n"
            f"Stop Loss: ${signal.stop_loss_price:.4f}\n"
            f"R:R Ratio: {signal.r_ratio:.2f}"
        )

        await self.send_message(message)

    async def send_trade_close(
        self, position, reason: str, pnl: float, order_result: Dict[str, Any]
    ):
        """
        Send trade close notification.

        Args:
            position: Closed position.
            reason: Exit reason.
            pnl: Realized PnL.
            order_result: Order execution result.
        """
        fill_price = order_result.get("fill_price", 0)
        pnl_icon = "✅" if pnl > 0 else "❌" if pnl < 0 else "➖"
        reason_str = reason.value if hasattr(reason, 'value') else reason

        message = (
            f"{pnl_icon} <b>TRADE CLOSED</b>\n\n"
            f"Symbol: <code>{position.symbol}</code>\n"
            f"Side: <b>{position.side.value}</b>\n"
            f"Exit Price: ${fill_price:.4f}\n"
            f"PnL: <b>${pnl:+.2f}</b>\n"
            f"Reason: {reason_str}\n\n"
            f"Entry: ${position.entry_price:.4f}\n"
            f"Hold Time: {(position.entry_time.timestamp() - __import__('time').time()) / 60:.0f} min"
        )

        await self.send_message(message)

    async def send_error(self, error_type: str, message: str):
        """
        Send error notification.

        Args:
            error_type: Type of error.
            message: Error message.
        """
        text = f"🚨 <b>ERROR ALERT</b>\n\nType: {error_type}\nMessage: <code>{message}</code>"
        await self.send_message(text)

    async def send_daily_summary(self, summary: Dict[str, Any]):
        """
        Send daily trading summary.

        Args:
            summary: Daily statistics dictionary.
        """
        win_rate = summary.get("win_rate", 0)
        pnl_icon = "✅" if summary.get("daily_pnl", 0) >= 0 else "❌"

        message = (
            f"{pnl_icon} <b>DAILY SUMMARY</b>\n\n"
            f"Date: {summary.get('date', 'N/A')}\n"
            f"PnL: <b>${summary.get('daily_pnl', 0):+.2f}</b>\n"
            f"Trades: {summary.get('trades_today', 0)}\n"
            f"Wins: {summary.get('wins_today', 0)}\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"Consecutive Losses: {summary.get('consecutive_losses', 0)}\n\n"
            f"Status: {'🔴 HALTED' if summary.get('is_halted') else '🟢 ACTIVE'}"
        )

        await self.send_message(message)
