# Polykalsh

Personal prediction market trading bot with two modes:

1. **Polymarket Copy-Trader** - Mirrors top-performing wallets in real-time
2. **Kalshi Advisor** - Surfaces best bet opportunities (recommendations only, no auto-trading)

## Features

- Paper trading mode for safe testing
- 10+ configurable safety guards
- Flask + HTMX dashboard
- Discord notifications
- Docker deployment for Mac Mini 24/7 operation

## Quick Start

```bash
# Clone and setup
cd polykalsh
cp .env.example .env
# Edit .env with your credentials

# Install
pip install -e .

# Initialize database
python -c "from polykalsh.database import init_db; init_db()"

# Run
python -m polykalsh.main
```

## Configuration

See `.env.example` for all available configuration options.

## Risk Disclaimer

Trading on prediction markets involves substantial risk of loss. Past performance does not guarantee future results. Never trade with money you cannot afford to lose.
