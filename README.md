# Polymarket Streak-Reversal Martingale Bot

4coinsbot ka **data infrastructure** + tredebot ki **streak-reversal strategy** — ek clean combined bot.

Official Repository: [github.com/satyamsk05/2in](https://github.com/satyamsk05/2in.git)

---

## Architecture

```
DataFeed (4coinsbot WebSocket)
  └─ BTC / ETH / SOL / XRP — Parallel Threaded Processing ⚡
  └─ Real-time: up_ask, down_ask, seconds_till_end
  └─ Zero Sequential Delay: All 4 coins update simultaneously.

StrategyEngine (tredebot logic)
  └─ Har 15-min boundary pe last-trade-price fetch
  └─ 3+ same-dir closes → reverse bet (streak reversal)
  └─ Martingale sizing: $3 → $6 → $13 → $28 → $60 USDC

OrderExecutor
  └─ L1 (step 0): FOK @ 0.99  (market fill)
  └─ L2+ (step>0): GTC @ 0.49 (limit order)

Dashboard (terminal)
  └─ 4coinsbot-style UI + martingale ladder overlay
  └─ Per-coin: orderbook prices, timer, MG step, pending bet

TelegramNotifier
  └─ Signal alert, trade placed, win/loss result
  └─ OG-style "━━━━━━━━━━━━━━━━━━━━" separator format (Premium UI)
  └─ Interactive 3x3 Reply Keyboard Menu
```

---

## Setup

```bash
git clone https://github.com/satyamsk05/2in.git
cd 2in

python3 -m venv venv
source venv/bin/activate            # Windows: .\venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
nano .env                           # fill in your keys
```

### .env minimum required

```
PRIVATE_KEY=0x...
POLYMARKET_API_KEY=...
POLYMARKET_API_SECRET=...
POLYMARKET_API_PASSPHRASE=...
WALLET_ADDRESS=0x...
RPC_URL=https://polygon-rpc.com     # For real balance fetching
```

---

## Run

```bash
# Entry point (handles auto-restart & logging)
python run.py

# Manually running the core
python src/main.py
```

---

## Strategy Logic

```
Every 15-min candle boundary:
  1. Fetch last-trade-price of just-closed market
  2. Push close to candle history
  3. Check last 3 closes:
       All > 0.5  →  BET DOWN (streak was UP → reverse)
       All < 0.5  →  BET UP   (streak was DOWN → reverse)
       Mixed      →  No trade
  4. If signal:
       Get Martingale bet size for this coin
       Place bet on NEW active market
  5. On next boundary:
       Resolve previous bet → win/loss → update ladder
```

### Martingale Ladder

| Level | Bet (USDC) | Trigger       |
|-------|-----------|---------------|
| L1    | $3        | Fresh start / after WIN |
| L2    | $6        | After 1 loss  |
| L3    | $13       | After 2 losses|
| L4    | $28       | After 3 losses|
| L5    | $60       | After 4 losses|
| Reset | $3        | After L5 loss |

---

## Telegram Hub (3x3 Menu)

| Command | Description |
|-----------|---------------------------|
| `📊 Live` | Real-time prices & Refresh button |
| `💰 Balance` | Virtual, Locked, and Real (On-chain) USDC |
| `📌 Position` | Current open bets on Polymarket |
| `📈 History` | **Recent Trades**: Wins/Losses for current session |
| `📉 Trend` | **Market Data**: 10-candle streak history (🟢/🔴) |
| `📅 PnL` | Daily profit/loss summary |
| `⏸ Stop/Res` | Emergency Pause or Resume trading |
| `🎯 Manual` | Interactive flow to place manual $N bets |
| `🔄 Refresh` | Re-send the command menu |

---

## Files

```
2in/
├── src/
│   ├── main.py                ← core loop (Parallel Threaded)
│   ├── strategy.py            ← Martingale reversal logic
│   ├── telegram_bot.py        ← Interactive 3x3 menu & commands
│   ├── history_manager.py     ← Persistence (resets on startup)
│   └── ...
├── run.py                     ← Entry point script
├── .env                       ← Secret keys
└── README.md
```

---

## Troubleshooting

**Real Balance shows $0.00:**
- Check if your funds are in **USDC.e** (Polygon) or **Native USDC**. The bot checks both.
- Ensure `RPC_URL` is working.
- If using Magic/Google auth, put your **Proxy/Safe Address** in `WALLET_ADDRESS`.

**Trend/History is empty:**
- Candle history is **reset on startup** for a fresh session. Wait for 1-2 candles (15-30 mins) to see data.

**Sequential Lag:**
- Fixed in v2.1 via Parallel Processing. Updates are now near-instant.
