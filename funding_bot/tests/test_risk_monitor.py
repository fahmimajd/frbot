"""Tests for risk monitor."""

import pytest
from src.risk.risk_monitor import RiskMonitor
from src.config_loader import Config


@pytest.fixture
def config():
    """Create test configuration."""
    return Config("config/config.yaml")


@pytest.fixture
def risk_monitor(config):
    """Create risk monitor instance."""
    return RiskMonitor(config)


def test_initial_state(risk_monitor):
    """Test initial risk monitor state."""
    assert risk_monitor.is_halted is False
    assert risk_monitor.daily_pnl == 0.0
    assert risk_monitor.consecutive_losses == 0
    assert risk_monitor.can_enter_trade() is True


def test_update_equity(risk_monitor):
    """Test equity update."""
    risk_monitor.update_equity(1000.0)
    
    assert risk_monitor.starting_equity == 1000.0
    assert risk_monitor.current_equity == 1000.0
    
    # Update again
    risk_monitor.update_equity(1050.0)
    assert risk_monitor.current_equity == 1050.0
    # Starting equity should remain the same
    assert risk_monitor.starting_equity == 1000.0


def test_record_trade_exit_win(risk_monitor):
    """Test recording a winning trade."""
    risk_monitor.record_trade_exit(50.0)
    
    assert risk_monitor.trades_today == 1
    assert risk_monitor.wins_today == 1
    assert risk_monitor.consecutive_losses == 0
    assert risk_monitor.get_win_rate() == 100.0


def test_record_trade_exit_loss(risk_monitor):
    """Test recording a losing trade."""
    risk_monitor.record_trade_exit(-30.0)
    
    assert risk_monitor.trades_today == 1
    assert risk_monitor.wins_today == 0
    assert risk_monitor.consecutive_losses == 1
    assert risk_monitor.get_win_rate() == 0.0


def test_consecutive_loss_halt(risk_monitor):
    """Test trading halt after consecutive losses."""
    # Record 3 consecutive losses (default limit)
    risk_monitor.record_trade_exit(-10.0)
    risk_monitor.record_trade_exit(-10.0)
    risk_monitor.record_trade_exit(-10.0)
    
    assert risk_monitor.is_halted is True
    assert risk_monitor.can_enter_trade() is False


def test_daily_loss_halt(risk_monitor):
    """Test trading halt after daily loss limit."""
    risk_monitor.update_equity(1000.0)
    
    # Record losses totaling > 3% (default daily max loss)
    risk_monitor.record_trade_exit(-35.0)  # 3.5% loss
    
    assert risk_monitor.is_halted is True
    assert risk_monitor.can_enter_trade() is False


def test_get_status(risk_monitor):
    """Test status retrieval."""
    risk_monitor.update_equity(1000.0)
    risk_monitor.record_trade_exit(50.0)
    risk_monitor.record_trade_exit(-20.0)
    
    status = risk_monitor.get_status()
    
    assert "is_halted" in status
    assert "daily_pnl" in status
    assert "win_rate" in status
    assert status["trades_today"] == 2
    assert status["daily_pnl"] == 30.0
