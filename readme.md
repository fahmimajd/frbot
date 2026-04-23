You are a senior quantitative trading engineer and crypto derivatives specialist.

Your task is to design and implement a PRODUCTION-READY automated trading bot
that exploits funding rate inefficiencies in Binance perpetual futures markets,
based on the following empirically-observed market behavior:

════════════════════════════════════════════════════════════
CORE MARKET MECHANIC (The Edge)
════════════════════════════════════════════════════════════

Binance perpetual futures settle funding fees every 8 hours
at: 00:00, 08:00, and 16:00 UTC.

OBSERVED BEHAVIOR:
  When funding rate is POSITIVE (> +0.03%):
    → Long holders must PAY short holders
    → Long holders are incentivized to CLOSE positions before settlement
    → This creates SELLING PRESSURE in the minutes before funding
    → Price tends to drift DOWN leading into and just after settlement
    → STRATEGY: Open SHORT ~10–15 min before funding settlement
    → EXIT: Close SHORT ~5–15 min after settlement confirmation

  When funding rate is NEGATIVE (< -0.03%):
    → Short holders must PAY long holders
    → Short holders are incentivized to CLOSE positions before settlement
    → This creates BUYING PRESSURE in the minutes before funding
    → Price tends to drift UP leading into and just after settlement
    → STRATEGY: Open LONG ~10–15 min before funding settlement
    → EXIT: Close LONG ~5–15 min after settlement confirmation

IMPORTANT NUANCE:
  - The edge comes from POSITION CLOSING PRESSURE, not speculative momentum
  - Price does NOT always pump before dumping — it can drop directly
  - This is a LOW-EDGE, HIGH-FREQUENCY strategy (not a trend play)
  - Transaction fees (taker: ~0.04%) WILL erode profits on weak signals
  - Strategy fails during strong directional trends or major news events

════════════════════════════════════════════════════════════
SIGNAL CONDITIONS (Entry Filter — ALL must be true)
════════════════════════════════════════════════════════════

  [1] Funding rate threshold:
        SHORT signal: current funding rate > +0.03%
        LONG  signal: current funding rate < -0.03%
        NO TRADE if |funding rate| < 0.03%

  [2] Time window:
        Entry allowed: T-15min to T-10min before settlement only
        NO ENTRY after T-5min (liquidity thins, slippage spikes)
        NO ENTRY before T-20min (edge not yet materialized)

  [3] Volatility filter (MUST PASS):
        ATR(14) must be < 2.0x the 30-day average ATR
        |1-hour price change| must be < 2.0%
        Bid-ask spread must be < 3x the symbol's normal spread
        → SKIP this cycle if any volatility condition is breached

  [4] Basis filter:
        |Mark Price - Index Price| must be < 0.3%
        → Wider basis = abnormal conditions, skip trade

  [5] Order book imbalance (optional confirmation):
        For SHORT: bid/ask volume ratio (top 10 levels) < 0.8
        For LONG:  bid/ask volume ratio (top 10 levels) > 1.2
        → Confirms directional pressure from market participants

════════════════════════════════════════════════════════════
POSITION SIZING & LEVERAGE
════════════════════════════════════════════════════════════

  Risk per trade   : 1.0% – 1.5% of current account equity
  Max leverage     : 5x (HARD LIMIT — never exceed)
  Position size formula:
    size = (equity × risk_pct) / (entry_price × sl_distance_pct)

  Example:
    equity       = $1,000
    risk_pct     = 1% → $10 at risk
    entry_price  = $1.08
    sl_distance  = 0.3%
    size = $10 / ($1.08 × 0.003) = ~3,086 units

  NEVER use fixed lot sizing — always calculate from equity + SL distance.

════════════════════════════════════════════════════════════
TAKE PROFIT / STOP LOSS RULES
════════════════════════════════════════════════════════════

  Take Profit  : 0.25% – 0.40% from entry price
  Stop Loss    : 0.15% – 0.25% from entry price
  Min R:R ratio: 1 : 1.3 (if R:R < 1.3, do not enter)

  Trailing Stop: Activate after +0.15% unrealized profit
                 Trail at 0.10% below peak profit

  EXIT PRIORITY ORDER (first condition met = exit):
    1. Stop Loss hit
    2. Take Profit hit
    3. Trailing stop triggered
    4. T+15 minutes after settlement (time-based hard exit)
    5. Funding rate reverses direction before settlement
    6. Volatility spike detected post-entry

════════════════════════════════════════════════════════════
RISK MANAGEMENT (HARD LIMITS — NON-NEGOTIABLE)
════════════════════════════════════════════════════════════

  Daily max loss          : 3% of starting equity that day → FULL STOP
  Session drawdown limit  : 5% → pause bot for 24 hours
  Max concurrent trades   : 3 (configurable), never same symbol twice
  Liquidation buffer      : Maintain minimum 60% margin at all times
  Consecutive losses      : 3 losses in a row → 8-hour cooldown
  API error threshold     : 5 consecutive errors → halt + send alert

  TRADING HALT CONDITIONS (auto-disable trading):
    - Daily loss limit reached
    - Consecutive loss limit reached
    - API connectivity issues
    - Exchange maintenance window detected
    - Abnormal funding rate (> 0.5% absolute — manipulation suspected)

════════════════════════════════════════════════════════════
MULTI-PAIR SCANNER
════════════════════════════════════════════════════════════

  Scan ALL Binance USDT-margined perpetual pairs every 5 minutes.

  Ranking criteria (score each pair):
    1. |Funding rate| magnitude (higher = stronger signal)
    2. 24h volume > $50M USDT (liquidity requirement)
    3. Spread score (tighter = better)
    4. Historical win rate on this pair (if data available)

  Select TOP 3 pairs by score for each funding cycle.
  Never trade more than 1 position per symbol simultaneously.

════════════════════════════════════════════════════════════
TECHNICAL STACK
════════════════════════════════════════════════════════════

  Language     : Python 3.11+ (preferred) or Node.js TypeScript
  Exchange     : Binance Futures (USDT-margined perpetuals)
  Database     : PostgreSQL
  Config       : YAML or .env file (never hardcode credentials)
  Deployment   : Docker + docker-compose ready
  Alerts       : Telegram Bot API (trade entries, exits, errors, daily summary)

  BINANCE API USAGE:
    REST endpoints:
      GET /fapi/v1/premiumIndex        → funding rate + mark/index price
      GET /fapi/v1/fundingRate         → historical funding rates
      GET /fapi/v2/account             → account equity + margin info
      POST /fapi/v1/order              → place orders
      DELETE /fapi/v1/order            → cancel orders
      GET /fapi/v1/openOrders          → open order status
      GET /fapi/v1/depth               → order book snapshot

    WebSocket streams (maintain persistent connections):
      <symbol>@markPrice@1s            → real-time mark price + funding rate
      <symbol>@depth20@100ms           → order book for imbalance signal
      <symbol>@aggTrade                → trade flow + volume
      listenKey (userData stream)      → order fills + account updates

    Rate limit management:
      Track request weight per minute (limit: 2400 weight/min)
      Implement token bucket algorithm for rate limiting
      Exponential backoff on HTTP 429 (retry after header) and 418
      Never poll REST funding endpoint faster than every 5 seconds

════════════════════════════════════════════════════════════
PROJECT STRUCTURE
════════════════════════════════════════════════════════════

  funding_bot/
  ├── config/
  │   ├── config.yaml              # all strategy parameters
  │   └── .env                     # API keys (never commit)
  ├── src/
  │   ├── main.py                  # entry point + scheduler
  │   ├── exchange/
  │   │   ├── binance_rest.py      # REST API client wrapper
  │   │   ├── binance_ws.py        # WebSocket manager
  │   │   └── rate_limiter.py      # token bucket rate limiter
  │   ├── strategy/
  │   │   ├── funding_scanner.py   # multi-pair funding rate ranker
  │   │   ├── signal_engine.py     # entry signal logic
  │   │   └── exit_manager.py      # TP/SL/trailing/time-based exit
  │   ├── execution/
  │   │   ├── order_manager.py     # order placement + tracking
  │   │   └── position_tracker.py  # open position state management
  │   ├── risk/
  │   │   ├── pre_trade_check.py   # all filters before entry
  │   │   ├── position_sizer.py    # equity-based position sizing
  │   │   └── risk_monitor.py      # daily loss, drawdown, halt logic
  │   ├── data/
  │   │   ├── db_manager.py        # PostgreSQL connection + queries
  │   │   └── models.py            # ORM models / table schemas
  │   ├── notifications/
  │   │   └── telegram_alert.py    # Telegram bot notifications
  │   └── backtest/
  │       ├── data_fetcher.py      # historical OHLCV + funding data
  │       ├── backtest_engine.py   # strategy replay engine
  │       └── report_generator.py  # performance metrics + charts
  ├── tests/
  │   ├── test_signal_engine.py
  │   ├── test_position_sizer.py
  │   └── test_risk_monitor.py
  ├── docker-compose.yml
  ├── Dockerfile
  ├── requirements.txt
  └── README.md

════════════════════════════════════════════════════════════
DATABASE SCHEMA (PostgreSQL)
════════════════════════════════════════════════════════════

  CREATE TABLE trades (
    id                  SERIAL PRIMARY KEY,
    symbol              VARCHAR(20) NOT NULL,
    side                VARCHAR(5) NOT NULL,          -- LONG / SHORT
    entry_price         DECIMAL(18,8) NOT NULL,
    exit_price          DECIMAL(18,8),
    quantity            DECIMAL(18,8) NOT NULL,
    leverage            INTEGER NOT NULL,
    funding_rate        DECIMAL(10,6) NOT NULL,       -- rate at entry
    pnl_usd             DECIMAL(18,4),
    pnl_pct             DECIMAL(10,4),
    fee_paid_usd        DECIMAL(18,4),
    entry_time          TIMESTAMPTZ NOT NULL,
    exit_time           TIMESTAMPTZ,
    exit_reason         VARCHAR(30),                  -- TP/SL/TIME/TRAIL/MANUAL
    status              VARCHAR(10) DEFAULT 'OPEN'    -- OPEN/CLOSED/CANCELLED
  );

  CREATE TABLE funding_log (
    id                  SERIAL PRIMARY KEY,
    symbol              VARCHAR(20) NOT NULL,
    funding_rate        DECIMAL(10,6) NOT NULL,
    next_settlement     TIMESTAMPTZ NOT NULL,
    mark_price          DECIMAL(18,8),
    index_price         DECIMAL(18,8),
    basis_pct           DECIMAL(10,6),
    recorded_at         TIMESTAMPTZ DEFAULT NOW()
  );

  CREATE TABLE system_events (
    id                  SERIAL PRIMARY KEY,
    level               VARCHAR(10) NOT NULL,         -- INFO/WARN/ERROR/CRITICAL
    event_type          VARCHAR(30) NOT NULL,
    message             TEXT,
    context             JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW()
  );

  CREATE TABLE daily_summary (
    id                  SERIAL PRIMARY KEY,
    date                DATE UNIQUE NOT NULL,
    starting_equity     DECIMAL(18,4),
    ending_equity       DECIMAL(18,4),
    total_pnl_usd       DECIMAL(18,4),
    total_fees_usd      DECIMAL(18,4),
    num_trades          INTEGER DEFAULT 0,
    num_wins            INTEGER DEFAULT 0,
    win_rate_pct        DECIMAL(5,2),
    max_drawdown_pct    DECIMAL(5,2),
    halt_triggered      BOOLEAN DEFAULT FALSE,
    notes               TEXT
  );

════════════════════════════════════════════════════════════
CONFIGURATION FILE (config.yaml)
════════════════════════════════════════════════════════════

  strategy:
    funding_threshold_pct: 0.03        # minimum |funding rate| to trade
    entry_window_start_min: 15         # minutes before settlement
    entry_window_end_min: 10           # minutes before settlement
    hard_exit_after_min: 15            # minutes after settlement
    max_concurrent_trades: 3
    top_pairs_to_scan: 10
    min_volume_24h_usdt: 50000000      # $50M minimum liquidity

  risk:
    risk_per_trade_pct: 1.0            # % of equity risked per trade
    max_leverage: 5
    take_profit_pct: 0.30
    stop_loss_pct: 0.20
    min_rr_ratio: 1.3
    trailing_stop_activation_pct: 0.15
    trailing_stop_distance_pct: 0.10
    daily_max_loss_pct: 3.0
    session_drawdown_limit_pct: 5.0
    consecutive_loss_limit: 3
    margin_buffer_pct: 60.0            # minimum margin to maintain

  filters:
    atr_multiplier_limit: 2.0          # skip if ATR > 2x 30d average
    max_1h_price_change_pct: 2.0
    max_spread_multiplier: 3.0
    max_basis_pct: 0.30
    ob_imbalance_enabled: true
    ob_imbalance_short_threshold: 0.8
    ob_imbalance_long_threshold: 1.2

  exchange:
    api_url: "https://fapi.binance.com"
    ws_url: "wss://fstream.binance.com"
    testnet: false                     # SET TRUE FOR TESTING FIRST
    request_weight_limit: 2400
    rate_limit_buffer_pct: 20

  notifications:
    telegram_enabled: true
    alert_on_trade_open: true
    alert_on_trade_close: true
    alert_on_error: true
    daily_summary_time: "00:05"        # UTC

════════════════════════════════════════════════════════════
LOGGING REQUIREMENTS
════════════════════════════════════════════════════════════

  Format: Structured JSON logs to file + stdout
  Rotation: Daily, keep 30 days

  Log on EVERY trade entry:
    - Symbol, side, entry price, quantity, leverage
    - Funding rate that triggered signal
    - ATR value, spread, basis at entry time
    - Order book imbalance ratio
    - Expected TP price, SL price

  Log on EVERY trade exit:
    - Exit price, exit reason
    - PnL in USD and percentage
    - Fees paid
    - Hold duration in seconds
    - Slippage vs expected price

  Log EVERY 1 minute (system heartbeat):
    - Active positions count
    - Current equity
    - Daily PnL so far
    - Next funding settlement times for monitored pairs

════════════════════════════════════════════════════════════
BACKTESTING MODULE REQUIREMENTS
════════════════════════════════════════════════════════════

  Data required:
    - Historical funding rates (from /fapi/v1/fundingRate)
    - OHLCV 1-minute candles (from /fapi/v1/klines)
    - Minimum 90 days of data

  Simulation must include:
    - Realistic taker fee: 0.04% per trade (entry + exit = 0.08%)
    - Slippage model: 0.02%–0.05% per trade
    - Entry at open of candle T-10min before settlement
    - Exit logic matching live strategy (TP/SL/time-based)

  Output metrics:
    - Total PnL, win rate, Sharpe ratio
    - Max drawdown (absolute + percentage)
    - Average hold time
    - Performance breakdown per symbol
    - Best and worst funding cycles
    - Fee drag analysis (show gross vs net PnL)

════════════════════════════════════════════════════════════
IMPLEMENTATION RULES
════════════════════════════════════════════════════════════

  [1] TESTNET FIRST
      Run on Binance Futures Testnet minimum 2 weeks.
      Only go live after positive backtest AND testnet results.

  [2] NO HARDCODED VALUES
      All parameters must come from config.yaml or .env.
      Code must be configurable without touching source files.

  [3] GRACEFUL SHUTDOWN
      On SIGTERM/SIGINT: close all open positions, cancel all orders,
      log final state, then shut down cleanly.

  [4] IDEMPOTENT ORDER MANAGEMENT
      Use clientOrderId with unique prefix + timestamp.
      Always verify order status before assuming fill.
      Never place duplicate orders for same signal.

  [5] RECONNECTION LOGIC
      WebSocket must auto-reconnect with exponential backoff.
      Max reconnect attempts: 10 before sending critical alert.
      Switch to REST polling fallback during WS outage.

  [6] NO OVER-ENGINEERING
      Write clean, readable, well-documented code.
      Each function does ONE thing.
      Add docstrings and type hints to every function.
      Unit test all risk and signal logic.

════════════════════════════════════════════════════════════
HONEST RISK WARNINGS (must be documented in README)
════════════════════════════════════════════════════════════

  1. This edge is NOT guaranteed — it is probabilistic.
  2. Strategy degrades when too many bots exploit the same signal.
  3. Strong market trends OVERRIDE funding rate pressure.
  4. Fees (0.04% taker × 2 = 0.08% round trip) can erase edge
     on signals below threshold.
  5. Exchange API downtime during settlement = missed exit risk.
  6. Black swan events (liquidation cascades) can cause extreme
     slippage invalidating all TP/SL assumptions.
  7. Backtesting does NOT guarantee future performance.
  8. This bot requires ACTIVE monitoring — not a set-and-forget system.
  9. Never fund this bot with money you cannot afford to lose entirely.
  10. Regulatory changes may affect perpetual futures availability.

════════════════════════════════════════════════════════════
DELIVERABLES
════════════════════════════════════════════════════════════

  Provide the following in order:

  [1] System architecture diagram (ASCII or Mermaid)
  [2] Full source code — modular, production-quality, no pseudocode
  [3] config.yaml — complete with all parameters + inline comments
  [4] docker-compose.yml + Dockerfile
  [5] README.md with:
        - Setup instructions (local + Docker)
        - Environment variables reference
        - How to run backtest
        - How to switch testnet ↔ mainnet
        - Risk warnings section
  [6] Backtest results on minimum:
        - BTCUSDT (high liquidity baseline)
        - ETHUSDT (second benchmark)
        - 1 mid-cap altcoin with historically volatile funding rates
  [7] Unit tests for:
        - Signal engine (entry conditions)
        - Position sizer (equity-based calculation)
        - Risk monitor (halt conditions)
