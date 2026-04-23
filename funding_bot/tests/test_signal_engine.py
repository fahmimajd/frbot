"""Tests for signal engine."""

import pytest
from src.strategy.signal_engine import SignalEngine, SignalSide
from src.config_loader import Config


@pytest.fixture
def config():
    """Create test configuration."""
    return Config("config/config.yaml")


@pytest.fixture
def signal_engine(config):
    """Create signal engine instance."""
    return SignalEngine(config)


def test_check_funding_threshold(signal_engine):
    """Test funding rate threshold check."""
    # Above threshold (positive)
    assert signal_engine.check_funding_threshold(0.0004) is True
    
    # Above threshold (negative)
    assert signal_engine.check_funding_threshold(-0.0004) is True
    
    # Below threshold
    assert signal_engine.check_funding_threshold(0.0002) is False
    assert signal_engine.check_funding_threshold(-0.0002) is False


def test_determine_side(signal_engine):
    """Test trade side determination."""
    # Positive funding → SHORT
    assert signal_engine.determine_side(0.0004) == SignalSide.SHORT
    
    # Negative funding → LONG
    assert signal_engine.determine_side(-0.0004) == SignalSide.LONG
    
    # No signal
    assert signal_engine.determine_side(0.0001) == SignalSide.NONE


def test_calculate_tp_sl(signal_engine):
    """Test take profit and stop loss calculation."""
    entry_price = 100.0
    
    # SHORT position
    tp, sl = signal_engine.calculate_tp_sl(entry_price, SignalSide.SHORT)
    assert tp < entry_price  # TP below entry for short
    assert sl > entry_price  # SL above entry for short
    
    # LONG position
    tp, sl = signal_engine.calculate_tp_sl(entry_price, SignalSide.LONG)
    assert tp > entry_price  # TP above entry for long
    assert sl < entry_price  # SL below entry for long


def test_calculate_r_ratio(signal_engine):
    """Test risk-reward ratio calculation."""
    entry_price = 100.0
    
    # LONG with good R:R
    tp_price = 100.30  # +0.30%
    sl_price = 99.80   # -0.20%
    r_ratio = signal_engine.calculate_r_ratio(
        entry_price, tp_price, sl_price, SignalSide.LONG
    )
    assert r_ratio == 1.5  # 0.30 / 0.20 = 1.5
    
    # SHORT with same R:R
    tp_price = 99.70   # -0.30%
    sl_price = 100.20  # +0.20%
    r_ratio = signal_engine.calculate_r_ratio(
        entry_price, tp_price, sl_price, SignalSide.SHORT
    )
    assert r_ratio == 1.5
