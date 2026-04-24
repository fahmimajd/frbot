"""
Centralized constants for the funding bot.
"""

from enum import Enum


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


# Time constants
class TimeConstants:
    """Time-related constants."""
    SECONDS_PER_MINUTE = 60
    MINUTES_PER_HOUR = 60
    HOURS_PER_DAY = 24
    FUNDING_INTERVAL_HOURS = 8
    DEFAULT_SCAN_INTERVAL_SECONDS = 60
    HARD_EXIT_AFTER_MINUTES = 30


# Scoring weights
class ScoringWeights:
    """Weights for signal scoring system."""
    FUNDING_RATE = 30.0      # Max points for funding rate
    R_RATIO = 25.0           # Max points for risk-reward ratio
    TIMING = 20.0            # Max points for timing
    SPREAD = 15.0            # Max points for spread
    OB_IMBALANCE = 10.0      # Max points for order book imbalance
    TOTAL = 100.0


# Default trading parameters
class DefaultParams:
    """Default trading parameters."""
    MAX_LEVERAGE = 20
    CAPITAL_USDT = 1.0
    RISK_PER_TRADE_PCT = 100.0
    TAKE_PROFIT_PCT = 0.30
    STOP_LOSS_PCT = 0.20
    MIN_RR_RATIO = 1.3
    FUNDING_THRESHOLD_PCT = 0.03
    ENTRY_WINDOW_START_MIN = 15
    ENTRY_WINDOW_END_MIN = 10
    MAX_CONCURRENT_TRADES = 1
    TOP_PAIRS_TO_SCAN = 50
    MIN_VOLUME_24H_USDT = 50_000_000


# Filter defaults
class FilterDefaults:
    """Default filter parameters."""
    ATR_MULTIPLIER_LIMIT = 2.0
    MAX_1H_PRICE_CHANGE_PCT = 2.0
    MAX_SPREAD_MULTIPLIER = 3.0
    MAX_BASIS_PCT = 0.30
    OB_IMBALANCE_ENABLED = True
    OB_SHORT_THRESHOLD = 0.8
    OB_LONG_THRESHOLD = 1.2


# Risk management
class RiskLimits:
    """Risk management limits."""
    DAILY_MAX_LOSS_PCT = 50.0
    SESSION_DRAWDOWN_LIMIT_PCT = 50.0
    CONSECUTIVE_LOSS_LIMIT = 5
    MARGIN_BUFFER_PCT = 10.0
    TRAILING_STOP_ACTIVATION_PCT = 0.15
    TRAILING_STOP_DISTANCE_PCT = 0.10


# API limits
class APILimits:
    """Binance API limits."""
    REQUEST_WEIGHT_LIMIT = 2400
    RATE_LIMIT_BUFFER_PCT = 20
    DEFAULT_TIMEOUT_SECONDS = 10


# Database defaults
class DatabaseDefaults:
    """Database configuration defaults."""
    HOST = "localhost"
    PORT = 5432
    NAME = "funding_bot"
    RETENTION_DAYS = 30


# Logging
class LogDefaults:
    """Logging configuration defaults."""
    LEVEL = "INFO"
    FORMAT = "json"
    ROTATION = "daily"
