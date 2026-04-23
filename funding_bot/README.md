# Funding Rate Arbitrage Bot

Production-ready automated trading bot that exploits funding rate inefficiencies in Binance perpetual futures markets.

## ⚠️ Risk Warning

**This software is for educational purposes only. Trading cryptocurrency derivatives involves substantial risk of loss.**

- This edge is **NOT guaranteed** — it is probabilistic
- Strategy degrades when too many bots exploit the same signal
- Always test on **testnet first** before using real funds
- Never risk more than you can afford to lose

## Features

- **Multi-pair scanning**: Scans all USDT-margined perpetual contracts
- **Signal filtering**: ATR, spread, basis, and order book imbalance filters
- **Risk management**: Daily loss limits, position sizing, trailing stops
- **Real-time monitoring**: WebSocket connections for live price updates
- **Telegram alerts**: Trade notifications and daily summaries
- **PostgreSQL logging**: Complete trade history and analytics

## Project Structure

```
funding_bot/
├── config/
│   ├── config.yaml          # Strategy parameters
│   └── .env.example         # Environment variables template
├── src/
│   ├── main.py              # Entry point
│   ├── config_loader.py     # Configuration management
│   ├── exchange/            # Binance API clients
│   ├── strategy/            # Signal generation
│   ├── execution/           # Order management
│   ├── risk/                # Risk controls
│   ├── data/                # Database operations
│   └── notifications/       # Telegram alerts
├── tests/                   # Unit tests
├── requirements.txt         # Python dependencies
├── Dockerfile              # Container definition
└── docker-compose.yml      # Multi-container setup
```

## Installation

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Docker & Docker Compose (optional)

### Local Setup

1. Clone repository:
```bash
git clone <repository-url>
cd funding_bot
```

2. Create virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment:
```bash
cp config/.env.example config/.env
# Edit config/.env with your credentials
```

5. Update `config/config.yaml` with your preferred settings.

### Docker Setup

```bash
docker-compose up -d
```

## Configuration

### Key Parameters (`config.yaml`)

```yaml
strategy:
  funding_threshold_pct: 0.03    # Min |funding rate| to trade
  entry_window_start_min: 15     # Minutes before settlement
  entry_window_end_min: 10       # Minutes before settlement

risk:
  risk_per_trade_pct: 1.0        # % of equity per trade
  max_leverage: 5                # Maximum leverage
  take_profit_pct: 0.30          # Take profit target
  stop_loss_pct: 0.20            # Stop loss level
  min_rr_ratio: 1.3              # Minimum R:R ratio

filters:
  atr_multiplier_limit: 2.0      # Volatility filter
  max_1h_price_change_pct: 2.0   # Price change limit
  max_basis_pct: 0.30            # Mark/Index price diff
```

### Environment Variables (`.env`)

```env
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_secret
BINANCE_TESTNET=true

TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

DB_USER=funding_bot_user
DB_PASSWORD=secure_password
DB_HOST=localhost
DB_NAME=funding_bot
```

## Usage

### Run the Bot

```bash
python -m src.main
```

### Run Tests

```bash
pytest tests/
```

## Trading Strategy

### Entry Conditions (ALL must be true)

1. **Funding Rate**: |rate| > 0.03%
2. **Time Window**: T-15min to T-10min before settlement
3. **Volatility Filter**: 
   - ATR(14) < 2.0x 30-day average
   - |1h price change| < 2.0%
   - Spread < 3x normal
4. **Basis Filter**: |Mark - Index| < 0.3%
5. **Order Book Imbalance** (optional confirmation)

### Exit Conditions (first met wins)

1. Stop Loss hit (-0.20%)
2. Take Profit hit (+0.30%)
3. Trailing stop triggered
4. Time-based exit (T+15min)
5. Funding rate reversal
6. Volatility spike

### Position Sizing

```
size = (equity × risk_pct) / (entry_price × sl_distance_pct)
```

Example: $1000 equity, 1% risk, 0.20% SL → ~$5000 notional

## Risk Management

- **Daily Max Loss**: 3% → Full stop
- **Session Drawdown**: 5% → 24h pause
- **Consecutive Losses**: 3 → 8h cooldown
- **API Errors**: 5 → Halt + alert
- **Max Concurrent Trades**: 3

## Database Schema

See `src/data/db_manager.py` for table definitions:
- `trades`: Trade records
- `funding_log`: Funding rate history
- `system_events`: System logs
- `daily_summary`: Daily PnL summaries

## Monitoring

### Logs

Structured JSON logs written to:
- Console (stdout)
- `funding_bot.log` file

### Telegram Alerts

Configure in `config.yaml`:
```yaml
notifications:
  telegram_enabled: true
  alert_on_trade_open: true
  alert_on_trade_close: true
  alert_on_error: true
```

## Backtesting

Backtesting module planned (see README.md section 347-368).

## Development

### Code Style

- Type hints on all functions
- Docstrings for all classes/functions
- Single responsibility principle
- Unit tests for critical logic

### Running Tests

```bash
pytest tests/ -v --cov=src
```

## Deployment

### Production Checklist

- [ ] Test on testnet for 2+ weeks
- [ ] Verify all risk limits
- [ ] Set up monitoring/alerting
- [ ] Configure database backups
- [ ] Review API key permissions
- [ ] Start with minimal capital

### Docker Compose

```yaml
version: '3.8'
services:
  bot:
    build: .
    depends_on:
      - db
    environment:
      - BINANCE_TESTNET=false
  
  db:
    image: postgres:14
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

## License

MIT License - See LICENSE file for details.

## Disclaimer

This software is provided "as is" without warranty. The authors are not responsible for any financial losses. Use at your own risk.

**Past performance does not guarantee future results.**
