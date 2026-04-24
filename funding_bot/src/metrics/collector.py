"""
Metrics collection for performance tracking.
Provides comprehensive monitoring of trading bot performance and health.
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MetricPoint:
    """A single metric data point."""
    timestamp: float
    value: float
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass
class TradeMetrics:
    """Metrics for a single trade."""
    symbol: str
    entry_time: datetime
    exit_time: Optional[datetime] = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    quantity: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    side: str = ""
    exit_reason: str = ""
    max_drawdown: float = 0.0
    max_profit: float = 0.0
    duration_seconds: float = 0.0


class MetricsCollector:
    """
    Collects and aggregates metrics for bot performance tracking.
    
    Tracks:
    - Trade statistics (win rate, PnL, etc.)
    - API performance (latency, error rates)
    - System health (memory, CPU usage)
    - Risk metrics (exposure, drawdown)
    """
    
    def __init__(self):
        """Initialize metrics collector."""
        self._trade_history: List[TradeMetrics] = []
        self._active_trades: Dict[str, TradeMetrics] = {}
        
        # API metrics
        self._api_latency: Dict[str, List[float]] = defaultdict(list)
        self._api_errors: Dict[str, int] = defaultdict(int)
        self._api_calls: Dict[str, int] = defaultdict(int)
        
        # Performance counters
        self._total_pnl = 0.0
        self._total_trades = 0
        self._winning_trades = 0
        self._losing_trades = 0
        
        # Time series data
        self._equity_curve: List[MetricPoint] = []
        self._drawdown_series: List[MetricPoint] = []
        self._exposure_series: List[MetricPoint] = []
        
        # Peak equity for drawdown calculation
        self._peak_equity = 0.0
        self._starting_equity = 0.0
        
        # Lock for thread safety
        self._lock = asyncio.Lock()
        
        # Last update time
        self._last_update = time.time()
    
    async def record_trade_entry(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        entry_time: Optional[datetime] = None,
    ):
        """
        Record a new trade entry.
        
        Args:
            symbol: Trading pair symbol.
            side: LONG or SHORT.
            entry_price: Entry price.
            quantity: Position size.
            entry_time: Entry timestamp (defaults to now).
        """
        async with self._lock:
            trade = TradeMetrics(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                quantity=quantity,
                entry_time=entry_time or datetime.now(timezone.utc),
            )
            self._active_trades[symbol] = trade
            logger.debug(f"Recorded trade entry: {symbol} {side} @ {entry_price}")
    
    async def record_trade_exit(
        self,
        symbol: str,
        exit_price: float,
        pnl: float,
        exit_reason: str = "",
        exit_time: Optional[datetime] = None,
    ):
        """
        Record a trade exit.
        
        Args:
            symbol: Trading pair symbol.
            exit_price: Exit price.
            pnl: Realized PnL.
            exit_reason: Reason for exit.
            exit_time: Exit timestamp (defaults to now).
        """
        async with self._lock:
            if symbol not in self._active_trades:
                logger.warning(f"No active trade found for {symbol}")
                return
            
            trade = self._active_trades[symbol]
            trade.exit_price = exit_price
            trade.exit_time = exit_time or datetime.now(timezone.utc)
            trade.pnl = pnl
            trade.exit_reason = exit_reason
            
            # Calculate duration
            if trade.entry_time and trade.exit_time:
                trade.duration_seconds = (trade.exit_time - trade.entry_time).total_seconds()
            
            # Calculate PnL percentage
            if trade.entry_price > 0:
                if trade.side == "LONG":
                    trade.pnl_pct = (exit_price - trade.entry_price) / trade.entry_price * 100
                else:  # SHORT
                    trade.pnl_pct = (trade.entry_price - exit_price) / trade.entry_price * 100
            
            # Update counters
            self._total_trades += 1
            self._total_pnl += pnl
            
            if pnl > 0:
                self._winning_trades += 1
            elif pnl < 0:
                self._losing_trades += 1
            
            # Move to history
            self._trade_history.append(trade)
            del self._active_trades[symbol]
            
            logger.info(
                f"Recorded trade exit: {symbol} | PnL: ${pnl:.2f} ({trade.pnl_pct:.2f}%) | "
                f"Duration: {trade.duration_seconds:.0f}s"
            )
    
    async def update_trade_mark(self, symbol: str, mark_price: float):
        """
        Update mark price for an active trade (for tracking drawdown/profit).
        
        Args:
            symbol: Trading pair symbol.
            mark_price: Current mark price.
        """
        async with self._lock:
            if symbol not in self._active_trades:
                return
            
            trade = self._active_trades[symbol]
            
            # Calculate unrealized PnL
            if trade.side == "LONG":
                unrealized_pnl = (mark_price - trade.entry_price) * trade.quantity
            else:
                unrealized_pnl = (trade.entry_price - mark_price) * trade.quantity
            
            # Track max profit and drawdown
            if unrealized_pnl > trade.max_profit:
                trade.max_profit = unrealized_pnl
            if unrealized_pnl < 0 and abs(unrealized_pnl) > trade.max_drawdown:
                trade.max_drawdown = abs(unrealized_pnl)
    
    async def record_api_call(
        self,
        endpoint: str,
        latency_ms: float,
        success: bool = True,
    ):
        """
        Record an API call for performance tracking.
        
        Args:
            endpoint: API endpoint name.
            latency_ms: Request latency in milliseconds.
            success: Whether the call was successful.
        """
        async with self._lock:
            self._api_calls[endpoint] += 1
            self._api_latency[endpoint].append(latency_ms)
            
            # Keep only last 1000 measurements per endpoint
            if len(self._api_latency[endpoint]) > 1000:
                self._api_latency[endpoint] = self._api_latency[endpoint][-1000:]
            
            if not success:
                self._api_errors[endpoint] += 1
    
    async def record_equity(self, equity: float):
        """
        Record current equity for equity curve tracking.
        
        Args:
            equity: Current account equity.
        """
        async with self._lock:
            timestamp = time.time()
            
            if self._starting_equity == 0:
                self._starting_equity = equity
            
            # Update peak equity
            if equity > self._peak_equity:
                self._peak_equity = equity
            
            # Calculate drawdown
            if self._peak_equity > 0:
                drawdown = (self._peak_equity - equity) / self._peak_equity * 100
            else:
                drawdown = 0.0
            
            # Record data points
            self._equity_curve.append(MetricPoint(
                timestamp=timestamp,
                value=equity,
            ))
            self._drawdown_series.append(MetricPoint(
                timestamp=timestamp,
                value=drawdown,
            ))
            
            # Keep only last 10000 points
            if len(self._equity_curve) > 10000:
                self._equity_curve = self._equity_curve[-10000:]
                self._drawdown_series = self._drawdown_series[-10000:]
    
    async def record_exposure(self, exposure_usd: float):
        """
        Record current exposure.
        
        Args:
            exposure_usd: Total position exposure in USD.
        """
        async with self._lock:
            self._exposure_series.append(MetricPoint(
                timestamp=time.time(),
                value=exposure_usd,
            ))
            
            # Keep only last 10000 points
            if len(self._exposure_series) > 10000:
                self._exposure_series = self._exposure_series[-10000:]
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """
        Get comprehensive performance summary.
        
        Returns:
            Dictionary with all performance metrics.
        """
        win_rate = (
            self._winning_trades / self._total_trades * 100
            if self._total_trades > 0 else 0.0
        )
        
        avg_win = 0.0
        avg_loss = 0.0
        profit_factor = 0.0
        
        if self._winning_trades > 0:
            winning_pnl = sum(t.pnl for t in self._trade_history if t.pnl > 0)
            avg_win = winning_pnl / self._winning_trades
        
        if self._losing_trades > 0:
            losing_pnl = abs(sum(t.pnl for t in self._trade_history if t.pnl < 0))
            avg_loss = losing_pnl / self._losing_trades
        
        if avg_loss > 0:
            profit_factor = (
                sum(t.pnl for t in self._trade_history if t.pnl > 0) /
                abs(sum(t.pnl for t in self._trade_history if t.pnl < 0))
            )
        
        # Calculate average trade duration
        completed_trades = [t for t in self._trade_history if t.exit_time]
        avg_duration = (
            sum(t.duration_seconds for t in completed_trades) / len(completed_trades)
            if completed_trades else 0.0
        )
        
        # Current drawdown
        current_drawdown = 0.0
        if self._peak_equity > 0 and self._equity_curve:
            current_equity = self._equity_curve[-1].value
            current_drawdown = (self._peak_equity - current_equity) / self._peak_equity * 100
        
        # Max drawdown
        max_drawdown = max((p.value for p in self._drawdown_series), default=0.0)
        
        return {
            "total_trades": self._total_trades,
            "winning_trades": self._winning_trades,
            "losing_trades": self._losing_trades,
            "win_rate_pct": win_rate,
            "total_pnl": self._total_pnl,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "avg_trade_duration_sec": avg_duration,
            "current_drawdown_pct": current_drawdown,
            "max_drawdown_pct": max_drawdown,
            "peak_equity": self._peak_equity,
            "starting_equity": self._starting_equity,
        }
    
    def get_api_stats(self) -> Dict[str, Any]:
        """
        Get API performance statistics.
        
        Returns:
            Dictionary with API metrics.
        """
        stats = {}
        
        for endpoint in self._api_calls:
            latencies = self._api_latency.get(endpoint, [])
            errors = self._api_errors.get(endpoint, 0)
            calls = self._api_calls.get(endpoint, 0)
            
            if latencies:
                avg_latency = sum(latencies) / len(latencies)
                p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else latencies[0]
                p99_latency = sorted(latencies)[int(len(latencies) * 0.99)] if len(latencies) > 1 else latencies[0]
            else:
                avg_latency = p95_latency = p99_latency = 0.0
            
            stats[endpoint] = {
                "total_calls": calls,
                "errors": errors,
                "error_rate_pct": errors / calls * 100 if calls > 0 else 0.0,
                "avg_latency_ms": avg_latency,
                "p95_latency_ms": p95_latency,
                "p99_latency_ms": p99_latency,
            }
        
        return stats
    
    def get_active_trades(self) -> List[Dict[str, Any]]:
        """Get list of active trades."""
        return [
            {
                "symbol": t.symbol,
                "side": t.side,
                "entry_price": t.entry_price,
                "quantity": t.quantity,
                "entry_time": t.entry_time.isoformat(),
                "max_profit": t.max_profit,
                "max_drawdown": t.max_drawdown,
            }
            for t in self._active_trades.values()
        ]
    
    def get_health_status(self) -> Dict[str, Any]:
        """
        Get overall system health status.
        
        Returns:
            Health status dictionary.
        """
        api_stats = self.get_api_stats()
        
        # Calculate overall error rate
        total_calls = sum(self._api_calls.values())
        total_errors = sum(self._api_errors.values())
        overall_error_rate = total_errors / total_calls * 100 if total_calls > 0 else 0.0
        
        # Determine health status
        is_healthy = overall_error_rate < 5.0  # Less than 5% error rate
        
        return {
            "is_healthy": is_healthy,
            "overall_error_rate_pct": overall_error_rate,
            "total_api_calls": total_calls,
            "total_api_errors": total_errors,
            "active_trades_count": len(self._active_trades),
            "performance_summary": self.get_performance_summary(),
            "api_stats": api_stats,
            "last_update": datetime.fromtimestamp(self._last_update, tz=timezone.utc).isoformat(),
        }


# Global metrics collector instance
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get or create global metrics collector instance."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector
