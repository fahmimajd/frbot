"""Tests for position sizer."""

import pytest
from src.risk.position_sizer import PositionSizer
from src.config_loader import Config


@pytest.fixture
def config():
    """Create test configuration."""
    return Config("config/config.yaml")


@pytest.fixture
def position_sizer(config):
    """Create position sizer instance."""
    return PositionSizer(config)


def test_calculate_position_size(position_sizer):
    """Test position size calculation."""
    equity = 1000.0
    entry_price = 100.0
    stop_loss_price = 99.80  # 0.2% SL
    
    # LONG position
    size, leverage = position_sizer.calculate_position_size(
        equity, entry_price, stop_loss_price, "LONG"
    )
    
    # Verify position size is reasonable
    assert size > 0
    assert leverage >= 1
    assert leverage <= 5  # Max leverage limit
    
    # SHORT position
    stop_loss_price_short = 100.20  # 0.2% SL
    size_short, leverage_short = position_sizer.calculate_position_size(
        equity, entry_price, stop_loss_price_short, "SHORT"
    )
    
    assert size_short > 0
    assert leverage_short >= 1


def test_leverage_limit(position_sizer):
    """Test that leverage never exceeds maximum."""
    equity = 1000.0
    entry_price = 100.0
    stop_loss_price = 99.99  # Very tight SL (0.01%)
    
    size, leverage = position_sizer.calculate_position_size(
        equity, entry_price, stop_loss_price, "LONG"
    )
    
    # Leverage should be capped at max (5x)
    assert leverage <= 5


def test_calculate_quantity_from_notional(position_sizer):
    """Test quantity calculation from notional value."""
    notional = 5000.0
    price = 100.0
    
    quantity = position_sizer.calculate_quantity_from_notional(notional, price)
    
    assert quantity == 50.0  # 5000 / 100 = 50
    
    # Zero price should return 0
    assert position_sizer.calculate_quantity_from_notional(notional, 0) == 0.0
