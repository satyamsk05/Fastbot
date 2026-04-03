# 💎 FASTBOT: Polymarket Streak-Reversal Suite v2.5

[![Status](https://img.shields.io/badge/Status-Production--Ready-brightgreen)](https://github.com/satyamsk05/Fastbot)
[![Version](https://img.shields.io/badge/Version-2.5.0-blue)](https://github.com/satyamsk05/Fastbot)
[![Strategy](https://img.shields.io/badge/Strategy-15m--Martingale--Reversal-orange)](https://github.com/satyamsk05/Fastbot)

A premium, high-performance trading suite for Polymarket. **Fastbot** combines ultra-low latency infrastructure with a battle-tested **15-minute streak-reversal strategy** and an optimized **Martingale execution engine**.

---

## ⚡ A2Z Technical Strategy

### 📊 1. The 15-Minute Reversal Engine
Fastbot monitors 15-minute price boundaries on **BTC, ETH, SOL, and XRP**.
- **Signal Logic**: Detects a "streak" of **3+ consecutive same-direction closes** (e.g., three 🟢 candles or three 🔴 candles).
- **Execution**: At the 15m boundary (plus a 5s settlement buffer), the bot places a **Reversal Bet** (e.g., if 3 UP candles, it bets DOWN).
- **Why 15m?**: This interval provides significantly more stability and reliable trend reversals compared to 1m or 5m charts.

### 🪜 2. Martingale Recovery Ladder
If a signal results in a loss, Fastbot automatically scales the position to recover:
- **Ladder**: `$3 → $6 → $13 → $28 → $60 USDC`
- **Logic**: Resets to `$3` immediately after a WIN or reaching the Max Level cap to protect capital.
- **Persistence**: Martingale state is saved to `data/martingale_state.json` to survive bot restarts.

### 🎯 3. Optimized Pricing Brackets
Fastbot uses dual-layer pricing to ensure the best possible entries:
- **L1 (Step 0 - Entry)**: Market-like (FOK) order within a **0.45 - 0.55 USDC** bracket. This ensures we don't enter "low-probability" extremes.
- **L2-L5 (Martingale Recovery)**: Limit (GTC) orders locked within a **0.49 - 0.53 USDC** bracket.
- **Targeted Buying**: By capping recovery orders at `0.53`, the bot waits for the market to move to an optimal price, effectively **buying at the lowest possible rates** during recovery phases.

---

## 🚀 Key Features

### 🏁 Zero-Latency Terminal Dashboard
- **4Hz Refresh Rate**: UI updates every **0.25 seconds (400ms)** for a smooth, "instant-action" feel.
- **Compact Layout**: Optimized to **120-character width** for standard terminal windows.
- **Background Balance Sync**: Wallet balances (Native USDC/USDC.e) are synced in a separate thread, ensuring the UI **never freezes** during network requests.

### 📱 Interactive Telegram Hub (3x3 Menu)
- **Live State**: Quick view of active prices and market timers.
- **Manual Trade Flow**: Interactive multi-step guide to place custom $N bets on the fly.
- **PnL Analytics**: Daily and session-based profit/loss reporting directly to your phone.

### 🛡️ Production Monitoring
- **Watchdog Supervisor**: The `run.py` launcher monitors the bot and performs auto-restarts upon crash or network failures.
- **Websocket Watchdog**: Automatic re-connection and staleness detection for 24/7 uptime.

---

## 🏗️ Architecture
```
Fastbot/
├── src/
│   ├── main.py                # Core execution loop (Parallel)
│   ├── data_feed.py           # Multi-market WebSocket client
│   ├── strategy.py            # Martingale reversal logic
│   ├── dashboard.py           # Premium Terminal UI (Rich)
│   ├── telegram_bot.py        # Interactive 3x3 menu & UI
│   ├── history_manager.py     # Persistence & PnL tracking
│   └── utils/
│       ├── gsd_logger.py      # Thread-safe logging
│       └── metrics_manager.py # JSON health exporter
├── run.py                     # Watchdog Supervisor (Main Entry)
├── .env                       # Secret keys & Config
└── README.md                  # Comprehensive Documentation
```

---

## ⚙️ Quick Setup
1. **Clone & Setup**:
   ```bash
   git clone https://github.com/satyamsk05/Fastbot.git
   cd Fastbot
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Configure**:
   ```bash
   cp .env.example .env
   # Edit .env with your Polymarket API keys and Wallet PK
   ```
3. **Run**:
   ```bash
   python3 run.py
   ```

---

© 2026 Polymarket Pro Bot Team. Managed via Fastbot Professional Infrastructure.
