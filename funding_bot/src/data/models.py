"""
Database models for PostgreSQL.
Defines ORM-like models for trades, funding logs, and system events.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Trade:
    """Represents a trade record."""

    symbol: str
    side: str  # LONG or SHORT
    entry_price: float
    quantity: float
    leverage: int
    funding_rate: float
    entry_time: datetime
    exit_price: Optional[float] = None
    pnl_usd: Optional[float] = None
    pnl_pct: Optional[float] = None
    fee_paid_usd: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    status: str = "OPEN"  # OPEN, CLOSED, CANCELLED
    id: Optional[int] = None


@dataclass
class FundingLog:
    """Represents a funding rate log entry."""

    symbol: str
    funding_rate: float
    next_settlement: datetime
    mark_price: Optional[float] = None
    index_price: Optional[float] = None
    basis_pct: Optional[float] = None
    recorded_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class SystemEvent:
    """Represents a system event log entry."""

    level: str  # INFO, WARN, ERROR, CRITICAL
    event_type: str
    message: str
    context: Optional[dict] = None
    created_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class DailySummary:
    """Represents a daily trading summary."""

    date: datetime
    starting_equity: float
    ending_equity: float
    total_pnl_usd: float
    total_fees_usd: float
    num_trades: int = 0
    num_wins: int = 0
    win_rate_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    halt_triggered: bool = False
    notes: Optional[str] = None
    id: Optional[int] = None
