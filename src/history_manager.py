"""
History Manager
══════════════════════════════════════════════════════
Manages all persistent state files:
  history/bet_history.json   → every bet placed (result, PnL)
  history/candle_history.json→ 7-day 15-min candle closes per coin
  history/open_positions.json→ currently open bets
  history/daily_pnl.json     → date-wise P&L summary
══════════════════════════════════════════════════════
"""
import json
import os
import time
import threading
from datetime import datetime, date
from typing import Dict, List, Optional
from utils.gsd_logger import get_gsd_logger
logger = get_gsd_logger("HIST")

HISTORY_DIR  = os.path.join(os.path.dirname(__file__), "..", "history")
BET_FILE     = os.path.join(HISTORY_DIR, "bet_history.json")
CANDLE_FILE  = os.path.join(HISTORY_DIR, "candle_history.json")
POSITION_FILE= os.path.join(HISTORY_DIR, "open_positions.json")
DAILY_PNL_FILE = os.path.join(HISTORY_DIR, "daily_pnl.json")

MAX_CANDLE_DAYS = 7          # 7-day candle retention
CANDLES_PER_DAY = 96         # 15-min candles per day
MAX_CANDLES = MAX_CANDLE_DAYS * CANDLES_PER_DAY  # 672

_lock = threading.Lock()

os.makedirs(HISTORY_DIR, exist_ok=True)


# ── helpers ────────────────────────────────────────────────────────────────────
def _read(path: str) -> dict | list:
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {} if path.endswith("history.json") or "pnl" in path else []


def _write(path: str, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _today_str() -> str:
    return date.today().strftime("%d %b")   # "26 Mar"


# ══════════════════════════════════════════════════════════════════════════════
# RESET — called on every bot restart
# ══════════════════════════════════════════════════════════════════════════════
def reset_on_startup():
    """
    Reset open_positions and candle_history on every bot start.
    bet_history and daily_pnl are KEPT across restarts.
    """
    with _lock:
        _write(POSITION_FILE, {})
        _write(CANDLE_FILE,   {})
    logger.info("[HISTORY] Position and Candle history reset on startup (Bets preserved).")


# ══════════════════════════════════════════════════════════════════════════════
# BET HISTORY
# ══════════════════════════════════════════════════════════════════════════════
def log_bet_placed(coin: str, direction: str, amount: float,
                   price: float, order_type: str, step: int,
                   market_ts: int, token_id: str):
    """Record a bet placement."""
    with _lock:
        data = _read(BET_FILE)
        if not isinstance(data, list):
            data = []
        data.append({
            "id":         len(data) + 1,
            "coin":       coin,
            "direction":  direction,          # YES / NO
            "amount":     amount,
            "price":      price,
            "order_type": order_type,
            "step":       step,
            "market_ts":  market_ts,
            "token_id":   token_id,
            "placed_at":  int(time.time()),
            "result":     None,               # WIN / LOSS — filled on resolution
            "pnl":        None,
            "fee":        0.0,                # Added: 0.24% estimation
        })
        _write(BET_FILE, data)


def log_bet_result(coin: str, market_ts: int, won: bool, pnl: float, fee: float = 0.0):
    """Update result of a bet after resolution."""
    with _lock:
        data = _read(BET_FILE)
        if not isinstance(data, list):
            return
        for bet in reversed(data):
            if bet["coin"] == coin and bet["market_ts"] == market_ts and bet["result"] is None:
                bet["result"] = "WIN" if won else "LOSS"
                bet["pnl"]    = round(pnl, 4)
                bet["fee"]    = round(fee, 4)
                bet["resolved_at"] = int(time.time())
                break
        _write(BET_FILE, data)


def get_bet_history() -> List[Dict]:
    with _lock:
        data = _read(BET_FILE)
        return data if isinstance(data, list) else []


# ══════════════════════════════════════════════════════════════════════════════
# CANDLE HISTORY  (7-day rolling)
# ══════════════════════════════════════════════════════════════════════════════
def push_candle(coin: str, ts: int, close_price: float):
    """Store a candle close. Keeps last MAX_CANDLES per coin."""
    with _lock:
        data = _read(CANDLE_FILE)
        if not isinstance(data, dict):
            data = {}
        key = coin.upper()
        if key not in data:
            data[key] = []

        # Deduplicate by ts
        if data[key] and data[key][-1]["ts"] == ts:
            return

        data[key].append({
            "ts":    ts,
            "close": close_price,
            "dir":   "UP" if close_price > 0.5 else "DOWN",
        })
        # Keep only 7 days
        data[key] = data[key][-MAX_CANDLES:]
        _write(CANDLE_FILE, data)


def get_candle_history(coin: str, n: int = MAX_CANDLES) -> List[Dict]:
    with _lock:
        data = _read(CANDLE_FILE)
        if not isinstance(data, dict):
            return []
        return data.get(coin.upper(), [])[-n:]

def get_bet_history(n: int = 10) -> List[Dict]:
    """Returns the last n bets."""
    with _lock:
        data = _read(BET_FILE)
        if not isinstance(data, list):
            return []
        return data[-n:]


def get_candle_closes(coin: str, n: int = 5) -> List[float]:
    return [c["close"] for c in get_candle_history(coin, n)]


def get_7day_trend_bar(coin: str) -> str:
    """
    Returns a visual trend bar for /history command.
    Each candle = 🟩 (UP) or 🟥 (DOWN). Last 48 candles (12h) shown.
    """
    candles = get_candle_history(coin, n=48)
    if not candles:
        return "(no data)"
    bar = "".join("🟢" if c["dir"] == "UP" else "🔴" for c in candles)
    # Add summary stats
    ups   = sum(1 for c in candles if c["dir"] == "UP")
    downs = len(candles) - ups
    return f"{bar}\n  🟢 UP: {ups} | 🔴 DOWN: {downs} | Last 48 candles"


# ══════════════════════════════════════════════════════════════════════════════
# OPEN POSITIONS
# ══════════════════════════════════════════════════════════════════════════════
def open_position(coin: str, direction: str, amount: float,
                  price: float, market_ts: int, token_id: str):
    """Add an open position."""
    with _lock:
        data = _read(POSITION_FILE)
        if not isinstance(data, dict):
            data = {}
        data[coin.upper()] = {
            "direction":  direction,
            "amount":     amount,
            "price":      price,
            "market_ts":  market_ts,
            "token_id":   token_id,
            "opened_at":  int(time.time()),
        }
        _write(POSITION_FILE, data)


def close_position(coin: str):
    """Remove a position after resolution."""
    with _lock:
        data = _read(POSITION_FILE)
        if isinstance(data, dict) and coin.upper() in data:
            del data[coin.upper()]
            _write(POSITION_FILE, data)


def get_open_positions() -> Dict:
    with _lock:
        data = _read(POSITION_FILE)
        return data if isinstance(data, dict) else {}


# ══════════════════════════════════════════════════════════════════════════════
# DAILY PNL
# ══════════════════════════════════════════════════════════════════════════════
def record_pnl(pnl: float):
    """Add pnl to today's total."""
    with _lock:
        data = _read(DAILY_PNL_FILE)
        if not isinstance(data, dict):
            data = {}
        today = _today_str()
        
        # Support new dictionary format for fee tracking
        day_data = data.get(today, {"pnl": 0.0, "fee": 0.0})
        if isinstance(day_data, (int, float)):
            day_data = {"pnl": float(day_data), "fee": 0.0}
            
        day_data["pnl"] = round(day_data["pnl"] + pnl, 4)
        data[today] = day_data
        _write(DAILY_PNL_FILE, data)

def record_fee(fee: float):
    """Add fee to today's total."""
    with _lock:
        data = _read(DAILY_PNL_FILE)
        if not isinstance(data, dict):
            data = {}
        today = _today_str()
        
        day_data = data.get(today, {"pnl": 0.0, "fee": 0.0})
        if isinstance(day_data, (int, float)):
            day_data = {"pnl": float(day_data), "fee": 0.0}
            
        day_data["fee"] = round(day_data["fee"] + fee, 4)
        data[today] = day_data
        _write(DAILY_PNL_FILE, data)


def get_daily_pnl() -> Dict[str, float]:
    with _lock:
        data = _read(DAILY_PNL_FILE)
        return data if isinstance(data, dict) else {}

def get_total_pnl() -> float:
    data = get_daily_pnl()
    total = 0.0
    for val in data.values():
        total += val.get("pnl", 0.0) if isinstance(val, dict) else float(val)
    return round(total, 4)

def get_total_fees() -> float:
    data = get_daily_pnl()
    total = 0.0
    for val in data.values():
        total += val.get("fee", 0.0) if isinstance(val, dict) else 0.0
    return round(total, 4)


def get_pnl_summary(days: int = 7) -> str:
    """Returns formatted PNL for Telegram /daily_pnl command."""
    data = get_daily_pnl()
    if not data:
        return "(no data yet)"
    
    recent = list(data.items())[-days:]
    lines = []
    total = 0.0
    for day_label, val in reversed(recent):
        pnl = val.get("pnl", 0.0) if isinstance(val, dict) else float(val)
        fee = val.get("fee", 0.0) if isinstance(val, dict) else 0.0
        sign = "+" if pnl >= 0 else ""
        emoji = "🟢" if pnl >= 0 else "🔴"
        lines.append(f"{emoji} {day_label}: {sign}${pnl:.2f} (Fee: ${fee:.2f})")
        total += pnl
    
    sign = "+" if total >= 0 else ""
    lines.append(f"————————──────")
    lines.append(f"Net Total: {sign}${total:.2f}")
    return "\n".join(lines)
