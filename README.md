# 💎 Polymarket Streak-Reversal Martingale Bot v2.1

[![Status](https://img.shields.io/badge/Status-Production--Ready-brightgreen)](https://github.com/satyamsk05/5in)
[![Version](https://img.shields.io/badge/Version-2.1.0-blue)](https://github.com/satyamsk05/5in)
[![Strategy](https://img.shields.io/badge/Strategy-Martingale--Reversal-orange)](https://github.com/satyamsk05/5in)

A high-performance, multi-market trading suite for Polymarket. This bot combines **ultra-low latency data infrastructure** with a refined **streak-reversal strategy** for **5-minute price markets**.

---

## 🚀 Key Features

### ⚡ Parallel Data Processing
Processes **BTC, ETH, SOL, and XRP** markets simultaneously using a threaded WebSocket architecture. 
- **Zero Sequential Delay**: Signals are evaluated and orders placed across all markets in parallel.
- **WebSocket Health Watchdog**: Automatic re-connection and staleness detection for 24/7 uptime.

### 🛡️ Production Monitoring & Resilience
Hardened for 24/7 unattended operation:
- **Redundant RPCs**: Intelligent failover system with parallel nodes to guarantee blockchain read/write integrity even during Polygon network congestion.
- **Watchdog Supervisor**: The `run.py` launcher monitors the bot process and performs auto-restarts upon crash (including OOM or network failures).
- **Telegram Watchdog Alerts**: Critical stall detections instantly notify the user to take action.
- **Discrete PnL Tracking**: Accurate separation of Live (Real) vs Dry Run (Virtual) performance metrics.

### 🍱 High-Fidelity Terminal Dashboard
A premium, scroll-free terminal interface using `Rich`:
- **Real-time Orderbook**: Live `up_ask` and `down_ask` prices.
- **MG Ladder Overlay**: Visual tracking of Martingale steps per coin.
- **System Logs**: Fixed internal panel for the last 8 system events.

### 📱 Interactive Telegram Hub
A full-featured 3x3 menu for mobile management:
- **Live State**: Quick view of active prices and market timers.
- **Manual Trade Flow**: Interactive multi-step guide to place custom $N bets on the fly.
- **PnL Analytics**: Daily and session-based profit/loss reporting with formatting for Telegram.

---

## 🏗️ Architecture

```
5in/
├── src/
│   ├── main.py                # Core execution loop (Parallel)
│   ├── data_feed.py           # Multi-market WebSocket client
│   ├── strategy.py            # Martingale reversal logic
│   ├── telegram_bot.py        # Interactive 3x3 menu & UI
│   ├── history_manager.py     # Persistence & PnL tracking
│   └── utils/
│       ├── gsd_logger.py      # Centralized thread-safe logging
│       └── metrics_manager.py # JSON health & stats exporter
├── run.py                     # Watchdog Supervisor (Production ENTRY POINT)
├── data/
│   ├── history.json           # Persistent trade data
│   └── metrics.json           # Live health metrics
└── .env                       # Secret keys & Configuration
```

---

## ⚙️ Setup & Installation

### 1. Requirements
- Python 3.9+
- USDC.e or Native USDC on Polygon network.
- Polymarket CLOB API Keys.

### 2. Quick Start
```bash
git clone https://github.com/satyamsk05/5in.git
cd 5in

# Setup Environment
python3 -m venv venv
source venv/bin/activate  # Windows: .\venv\Scripts\activate
pip install -r requirements.txt

# Configure Secrets
cp .env.example .env
nano .env # Fill in keys
```

### 3. Running the Bot (Recommended)
Always use `run.py` in production to enable the auto-restart supervisor and centralized logging.
```bash
python3 run.py
```

---

## 📊 Strategy: Martingale Streak-Reversal

The bot monitors 5-minute price boundaries:
1. **Streak Detection**: If the last 3 closes are in the same direction (e.g., all 🟢 or all 🔴), the bot signals a **Reversal**.
2. **Order Execution**:
   - **Level 1**: Market-fill (FOK) at the start of a streak.
   - **Level 2+**: Protective Limit orders (GTC) to manage Martingale risk.
3. **Martingale Ladder**:
   - `$3 → $6 → $13 → $28 → $60` (Customizable in `strategy.py`).
   - Resets to `$3` after a Win or reaching the Max Level cap.

---

## 💡 Troubleshooting & FAQs

- **Balance Discrepancy**: The bot automatically aggregates both Native USDC and USDC.e (bridged) for real balance reporting using redundant nodes.
- **Data Stall Alerts**: If the bot hasn't received a WebSocket push from Polymarket in >30s, the connection is instantly refreshed and a Telegram alert is sent.
- **No Trend Data**: Data resets on startup for session purity. After 5 minutes, the first candle will appear.

---

© 2026 Polymarket Pro Bot Team. Managed via GSD Spec-Driven Development.
