"""
Database manager for PostgreSQL operations.
Handles connections and CRUD operations for trades, events, and summaries.
"""

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg
from src.config_loader import Config
from src.data.models import Trade, FundingLog, SystemEvent, DailySummary
from src.strategy.signal_engine import EntrySignal

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Manages PostgreSQL database operations.

    Handles:
    - Connection management
    - Table creation
    - Trade logging
    - Event logging
    - Daily summaries
    """

    def __init__(self, config: Config):
        """
        Initialize database manager.

        Args:
            config: Configuration instance.
        """
        self.config = config
        self.db_config = config.get("database", default={})
        self._pool: Optional[asyncpg.Pool] = None

    async def initialize(self):
        """Initialize database connection pool and create tables."""
        try:
            host = self.db_config.get("host", "localhost")
            port = self.db_config.get("port", 5432)
            database = self.db_config.get("name", "funding_bot")
            user = self.db_config.get("user", "postgres")
            password = self.db_config.get("password", "")

            self._pool = await asyncpg.create_pool(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                min_size=2,
                max_size=10,
            )

            logger.info(f"Connected to PostgreSQL: {host}:{port}/{database}")

            # Create tables
            await self._create_tables()

        except Exception as e:
            logger.warning(f"Database initialization failed: {e}. Running without DB.")
            self._pool = None

    async def _create_tables(self):
        """Create database tables if they don't exist."""
        if not self._pool:
            return

        async with self._pool.acquire() as conn:
            # Trades table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    side VARCHAR(5) NOT NULL,
                    entry_price DECIMAL(18,8) NOT NULL,
                    exit_price DECIMAL(18,8),
                    quantity DECIMAL(18,8) NOT NULL,
                    leverage INTEGER NOT NULL,
                    funding_rate DECIMAL(10,6) NOT NULL,
                    pnl_usd DECIMAL(18,4),
                    pnl_pct DECIMAL(10,4),
                    fee_paid_usd DECIMAL(18,4),
                    entry_time TIMESTAMPTZ NOT NULL,
                    exit_time TIMESTAMPTZ,
                    exit_reason VARCHAR(30),
                    status VARCHAR(10) DEFAULT 'OPEN'
                )
            """)

            # Funding log table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS funding_log (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    funding_rate DECIMAL(10,6) NOT NULL,
                    next_settlement TIMESTAMPTZ NOT NULL,
                    mark_price DECIMAL(18,8),
                    index_price DECIMAL(18,8),
                    basis_pct DECIMAL(10,6),
                    recorded_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # System events table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS system_events (
                    id SERIAL PRIMARY KEY,
                    level VARCHAR(10) NOT NULL,
                    event_type VARCHAR(30) NOT NULL,
                    message TEXT,
                    context JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # Daily summary table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_summary (
                    id SERIAL PRIMARY KEY,
                    date DATE UNIQUE NOT NULL,
                    starting_equity DECIMAL(18,4),
                    ending_equity DECIMAL(18,4),
                    total_pnl_usd DECIMAL(18,4),
                    total_fees_usd DECIMAL(18,4),
                    num_trades INTEGER DEFAULT 0,
                    num_wins INTEGER DEFAULT 0,
                    win_rate_pct DECIMAL(5,2),
                    max_drawdown_pct DECIMAL(5,2),
                    halt_triggered BOOLEAN DEFAULT FALSE,
                    notes TEXT
                )
            """)

            logger.info("Database tables created/verified")

    async def log_trade_entry(self, signal: EntrySignal, order_result: Dict[str, Any]):
        """
        Log a trade entry to the database.

        Args:
            signal: Entry signal.
            order_result: Order execution result.
        """
        if not self._pool:
            return

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO trades
                    (symbol, side, entry_price, quantity, leverage, funding_rate, entry_time, status)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    signal.symbol,
                    signal.side.value,
                    signal.entry_price,
                    signal.position_size,
                    signal.leverage,
                    signal.funding_rate,
                    signal.timestamp,
                    "OPEN",
                )
        except Exception as e:
            logger.error(f"Error logging trade entry: {e}")

    async def log_trade_exit(
        self, position, reason: str, pnl: float, order_result: Dict[str, Any]
    ):
        """
        Log a trade exit to the database.

        Args:
            position: Position being closed.
            reason: Exit reason.
            pnl: Realized PnL.
            order_result: Order execution result.
        """
        if not self._pool:
            return

        try:
            async with self._pool.acquire() as conn:
                fill_price = order_result.get("fill_price", 0)
                fee = abs(pnl) * 0.0008  # Estimate fee (0.04% per side)
                pnl_pct = (pnl / (position.entry_price * position.quantity)) * 100

                await conn.execute(
                    """
                    UPDATE trades
                    SET exit_price = $1, pnl_usd = $2, pnl_pct = $3,
                        fee_paid_usd = $4, exit_time = $5, exit_reason = $6, status = 'CLOSED'
                    WHERE symbol = $7 AND status = 'OPEN'
                    """,
                    fill_price,
                    pnl,
                    pnl_pct,
                    fee,
                    datetime.now(timezone.utc),
                    reason if isinstance(reason, str) else reason.value,
                    position.symbol,
                )
        except Exception as e:
            logger.error(f"Error logging trade exit: {e}")

    async def log_event(
        self, level: str, event_type: str, message: str,
        context: Optional[Dict] = None
    ):
        """
        Log a system event.

        Args:
            level: Event level (INFO, WARN, ERROR, CRITICAL).
            event_type: Type of event.
            message: Event message.
            context: Optional context dictionary.
        """
        if not self._pool:
            return

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO system_events (level, event_type, message, context)
                    VALUES ($1, $2, $3, $4)
                    """,
                    level,
                    event_type,
                    message,
                    context,
                )
        except Exception as e:
            logger.error(f"Error logging event: {e}")

    async def log_funding_rate(
        self,
        symbol: str,
        funding_rate: float,
        next_settlement: datetime,
        mark_price: float,
        index_price: float,
        basis_pct: float,
    ):
        """
        Log a funding rate reading.

        Args:
            symbol: Trading pair symbol.
            funding_rate: Funding rate.
            next_settlement: Next settlement time.
            mark_price: Mark price.
            index_price: Index price.
            basis_pct: Basis percentage.
        """
        if not self._pool:
            return

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO funding_log
                    (symbol, funding_rate, next_settlement, mark_price, index_price, basis_pct)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    symbol,
                    funding_rate,
                    next_settlement,
                    mark_price,
                    index_price,
                    basis_pct,
                )
        except Exception as e:
            logger.error(f"Error logging funding rate: {e}")

    async def get_daily_summary(self, trade_date: date) -> Optional[DailySummary]:
        """
        Get daily summary for a specific date.

        Args:
            trade_date: Date to get summary for.

        Returns:
            DailySummary or None.
        """
        if not self._pool:
            return None

        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM daily_summary WHERE date = $1", trade_date
                )
                if row:
                    return DailySummary(**dict(row))
        except Exception as e:
            logger.error(f"Error getting daily summary: {e}")

        return None

    async def close(self):
        """Close database connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("Database connection closed")
