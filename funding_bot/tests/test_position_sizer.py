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
    """Test position size calculation with proper exchange info mock."""
    symbol = "BTCUSDT"
    entry_price = 10000.0  # Higher price so notional >> minNotional
    equity = 1.0  # $1 capital

    # Mock exchange info to avoid API calls
    # Must match Binance exchangeInfo format: {"symbols": [{symbol: "...", filters: [...]}]}
    position_sizer._exchange_info = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "NOTIONAL", "notional": "5.0"},
                ],
            }
        ]
    }

    # Calculate position size
    size = position_sizer.calculate_position_size(
        symbol=symbol,
        entry_price=entry_price,
        equity=equity,
    )

    # Verify position size is reasonable
    assert size is not None, "Position size should not be None"
    assert size > 0, "Position size should be positive"

    # With $1 capital and 20x leverage (from config), position value = $20
    # At price $10000, quantity = 20 / 10000 = 0.002
    expected_quantity = (1.0 * 20) / 10000  # capital * leverage / price
    assert abs(size - expected_quantity) < 0.001


def test_calculate_position_size_with_defaults(position_sizer):
    """Test position size calculation with default filters."""
    symbol = "BTCUSDT"  # Use BTC instead of ETH to avoid minNotional issues
    entry_price = 100.0
    equity = 1.0
    
    # Without exchange info, should use defaults
    size = position_sizer.calculate_position_size(
        symbol=symbol,
        entry_price=entry_price,
        equity=equity
    )
    
    # Should return a valid quantity
    assert size is not None
    assert size > 0


def test_calculate_quantity_from_notional(position_sizer):
    """Test quantity calculation from notional value."""
    notional = 5000.0
    price = 100.0
    
    quantity = position_sizer.calculate_quantity_from_notional(notional, price)
    
    assert quantity == 50.0  # 5000 / 100 = 50
    
    # Zero price should return 0
    assert position_sizer.calculate_quantity_from_notional(notional, 0) == 0.0
