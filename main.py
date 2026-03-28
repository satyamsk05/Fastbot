#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║   Polymarket Streak-Reversal Martingale Bot  v2.0        ║
╠══════════════════════════════════════════════════════════╣
║  Coins    : BTC · ETH · SOL · XRP (configurable)        ║
║  Interval : 15-minute markets                            ║
║  Signal   : 3+ same-dir closes → reverse bet            ║
║  Ladder   : $3 → $6 → $13 → $28 → $60 USDC             ║
╠══════════════════════════════════════════════════════════╣
║  Data     : 4coinsbot WebSocket (live orderbook)         ║
║  History  : bet_history / candle_history / positions     ║
║  Telegram : /history /live /stop /balance /position      ║
║             /daily_pnl  +  manual $N bets                ║
╚══════════════════════════════════════════════════════════╝
"""

import os, sys, json, time, random, signal, logging, threading, requests
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

SRC_DIR = Path(__file__).parent
sys.path.insert(0, str(SRC_DIR))

from strategy      import StreakReversalStrategy, Martingale, BET_SEQUENCE
from data_feed     import DataFeed
from dashboard     import Dashboard
from telegram_bot  import get_bot, get_notifier
import history_manager as hm

# ═══════════════════════════════════════════════════════════════════════════════
# ENV CONFIG
# ═══════════════════════════════════════════════════════════════════════════════
DRY_RUN        = os.getenv("DRY_RUN", "true").lower() in ("1","true","yes")
PRIVATE_KEY    = os.getenv("PRIVATE_KEY", "")
RPC_URL        = os.getenv("RPC_URL", "https://polygon-rpc.com")
CLOB_HOST      = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
API_KEY        = os.getenv("POLYMARKET_API_KEY", "")
API_SECRET     = os.getenv("POLYMARKET_API_SECRET", "")
API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE", "")
FUNDER_ADDRESS = os.getenv("FUNDER_ADDRESS", "")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")

COINS_ENABLED = {
    "BTC": os.getenv("ENABLE_BTC", "true").lower()  in ("1","true","yes"),
    "ETH": os.getenv("ENABLE_ETH", "true").lower()  in ("1","true","yes"),
    "SOL": os.getenv("ENABLE_SOL", "true").lower()  in ("1","true","yes"),
    "XRP": os.getenv("ENABLE_XRP", "false").lower() in ("1","true","yes"),
}
ACTIVE_COINS = [c for c, v in COINS_ENABLED.items() if v]

INTERVAL_SEC      = 15 * 60
CANDLE_SETTLE     = 5          # wait N sec after boundary before fetching price
DASHBOARD_REFRESH = 1.0
VBAL_START        = 500.0
BET_MIN_FUNDS     = 3.0

# ── dirs + logging ────────────────────────────────────────────────────────────
for d in ("logs","data","history"):
    os.makedirs(d, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("logs/bot.log")],
)
for lib in ("urllib3","requests","httpx","telegram","apscheduler"):
    logging.getLogger(lib).setLevel(logging.WARNING)

def get_coin_logger(coin: str):
    """Returns a thread-safe logger for a specific coin."""
    logger = logging.getLogger(coin.upper())
    if not logger.handlers:
        path = f"logs/{coin.upper()}.log"
        handler = logging.FileHandler(path)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False # don't send to root bot.log
    return logger

# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════════════════
_stop    = threading.Event()
_paused  = threading.Event()    # set = bot is paused

_strat   = StreakReversalStrategy()
_mg      = _strat.martingale

_pending: Dict[str, Optional[Dict]] = {c: None for c in ACTIVE_COINS}
_plock   = threading.Lock()

_tradelog: List[Dict] = []
_tlock    = threading.Lock()
_start_time = time.time()
_redeem_status = "Idle"

# ═══════════════════════════════════════════════════════════════════════════════
# WALLET
# ═══════════════════════════════════════════════════════════════════════════════
_VBAL = "data/virtual_balance.json"

def _vbal_read() -> float:
    try:
        if os.path.exists(_VBAL):
            with open(_VBAL) as f:
                return float(json.load(f).get("balance", VBAL_START))
    except Exception:
        pass
    return VBAL_START

def _vbal_write(b: float):
    with open(_VBAL,"w") as f:
        json.dump({"balance": round(b,2)}, f)

def get_wallet_balance() -> float:
    if DRY_RUN:
        return _vbal_read()
    return get_real_balance()

def get_real_balance() -> float:
    try:
        from web3 import Web3
        from eth_account import Account
        from web3.middleware import ExtraDataToPOAMiddleware
        
        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        if not w3.is_connected():
            logging.warning("[BAL] Web3 not connected")
            return 0.0
            
        wallet = FUNDER_ADDRESS or WALLET_ADDRESS
        if not wallet and PRIVATE_KEY:
            try:
                wallet = Account.from_key(PRIVATE_KEY).address
            except: pass
        
        if not wallet:
            return 0.0
            
        addr = w3.to_checksum_address(wallet)
        logging.info(f"[BAL] Checking balance for: {addr}")
        
        # USDC contracts
        USDCE_ADDR = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174" # USDC.e
        USDCN_ADDR = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359" # Native USDC
        
        abi  = [{"constant":True,"inputs":[{"name":"_owner","type":"address"}],
                 "name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],
                 "type":"function"}]
        
        total_raw = 0
        try:
            val_e = w3.eth.contract(address=w3.to_checksum_address(USDCE_ADDR), abi=abi).functions.balanceOf(addr).call()
            if val_e > 0: logging.info(f"[BAL] Found {val_e/1e6} USDC.e")
            total_raw += val_e
        except Exception as e: logging.debug(f"[BAL] USDC.e Error: {e}")
        
        try:
            val_n = w3.eth.contract(address=w3.to_checksum_address(USDCN_ADDR), abi=abi).functions.balanceOf(addr).call()
            if val_n > 0: logging.info(f"[BAL] Found {val_n/1e6} Native USDC")
            total_raw += val_n
        except Exception as e: logging.debug(f"[BAL] USDC.n Error: {e}")
        
        final = round(total_raw / 1_000_000, 2)
        return final
    except Exception as e:
        logging.error(f"[BAL] Critical Error: {e}")
        return 0.0

def get_in_bets() -> float:
    return sum(p.get("amount",0) for p in hm.get_open_positions().values())

# ═══════════════════════════════════════════════════════════════════════════════
# MARKET DATA
# ═══════════════════════════════════════════════════════════════════════════════
def _tokens(coin: str, ts: int) -> Optional[Dict]:
    slug = f"{coin.lower()}-updown-15m-{ts}"
    url  = f"https://gamma-api.polymarket.com/events?slug={slug}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        events = r.json()
        if not events:
            return None
        mkt  = events[0]["markets"][0]
        tids = mkt.get("clobTokenIds",[])
        outs = mkt.get("outcomes",[])
        cond = mkt.get("conditionId","")
        if isinstance(tids,str): tids = json.loads(tids)
        if isinstance(outs,str): outs = json.loads(outs)
        ui = outs.index("Up")   if "Up"   in outs else 0
        di = outs.index("Down") if "Down" in outs else 1
        return {"yes_token":tids[ui],"no_token":tids[di],
                "condition_id":cond,"slug":slug}
    except Exception as e:
        logging.warning(f"[{coin}] tokens err: {e}")
        return None

def _price(token_id: str) -> Optional[float]:
    try:
        r = requests.get(f"https://clob.polymarket.com/last-trade-price?token_id={token_id}", timeout=8)
        if r.status_code == 200:
            return float(r.json().get("price",0))
    except Exception:
        pass
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# ORDER PLACEMENT
# ═══════════════════════════════════════════════════════════════════════════════
def _place(token_id: str, amount: float, coin: str,
           step: int, direction: str, price: Optional[float] = None):
    """Returns (success, price, order_type_label)"""
    # ── FIXED: Use passed price or fallback only if still None ──
    if price is None:
        price = 0.99 if step == 0 else 0.49
    
    ot_lbl = "FOK" if step == 0 else "GTC"

    if DRY_RUN:
        logging.info(f"[{coin}] DRY {direction} ${amount} @ {price:.3f} ({ot_lbl})")
        return True, price, ot_lbl

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
        from py_clob_client.constants import POLYGON

        creds  = ApiCreds(api_key=API_KEY, api_secret=API_SECRET, api_passphrase=API_PASSPHRASE)
        pk     = PRIVATE_KEY.lstrip("0x") if PRIVATE_KEY else ""
        client = ClobClient(CLOB_HOST, chain_id=POLYGON, key=pk, creds=creds,
                            signature_type=2 if FUNDER_ADDRESS else 1,
                            funder=FUNDER_ADDRESS or None)
        size   = round(amount / price, 2)
        if size < 0.1:
            return False, price, ot_lbl
        signed = client.create_order(OrderArgs(token_id=token_id, price=price, size=size, side="BUY"))
        resp   = client.post_order(signed, OrderType.FOK if step==0 else OrderType.GTC)
        return bool(resp and resp.get("status") not in ("unmatched",None)), price, ot_lbl
    except Exception as e:
        logging.error(f"[{coin}] place: {e}")
        return False, price, ot_lbl

# ═══════════════════════════════════════════════════════════════════════════════
# SETTLEMENT (Auto-Redeem)
# ═══════════════════════════════════════════════════════════════════════════════
def _redeem_all():
    """Periodically check and redeem all winning positions on-chain."""
    global _redeem_status
    if DRY_RUN:
        _redeem_status = "Dry Run"
        return

    _redeem_status = "Checking..."
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
        from py_clob_client.constants import POLYGON

        creds  = ApiCreds(api_key=API_KEY, api_secret=API_SECRET, api_passphrase=API_PASSPHRASE)
        pk     = PRIVATE_KEY.lstrip("0x") if PRIVATE_KEY else ""
        client = ClobClient(CLOB_HOST, chain_id=POLYGON, key=pk, creds=creds,
                            signature_type=2 if FUNDER_ADDRESS else 1,
                            funder=FUNDER_ADDRESS or None)

        # 1. Fetch unredeemed positions from Polymarket Data API
        url = f"https://data-api.polymarket.com/positions?user={WALLET_ADDRESS}&redeemable=true"
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            _redeem_status = "API Error"
            return

        positions = r.json()
        if not positions:
            _redeem_status = "Nothing to redeem"
            return

        logging.info(f"[SETTLE] Found {len(positions)} unredeemed positions")
        _redeem_status = f"Redeeming {len(positions)}..."

        for pos in positions:
            slug = pos.get("slug")
            cond = pos.get("conditionId")
            if not slug or not cond: continue

            logging.info(f"[SETTLE] Redeeming {slug}...")
            try:
                resp = client.redeem_positions(condition_id=cond)
                logging.info(f"[SETTLE] ✅ Redeemed {slug}: {resp}")
                time.sleep(2) # rate limit safety
            except Exception as re:
                logging.warning(f"[SETTLE] Failed redeem {slug}: {re}")
                
        _redeem_status = "Last check success"

    except Exception as e:
        logging.error(f"[SETTLE] Error in redeem loop: {e}")
        _redeem_status = "Error"

# ═══════════════════════════════════════════════════════════════════════════════
# PER-COIN PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════════
class CoinProc:
    def __init__(self, coin: str):
        self.coin = coin.upper()
        self.processed_ts = 0
        self.warmup = 0
        self.log = get_coin_logger(coin)

    def tick(self, now: int) -> Optional[Dict]:
        """Run every second. Returns signal dict or None."""
        boundary   = (now // INTERVAL_SEC) * INTERVAL_SEC
        since      = now - boundary
        closed_ts  = boundary - INTERVAL_SEC
        coin       = self.coin

        if boundary <= self.processed_ts or since < CANDLE_SETTLE:
            return None

        self.processed_ts = boundary
        self.warmup += 1
        self.log.info(f"Boundary {boundary}")

        # 1. Resolve pending
        with _plock:
            pend = _pending.get(coin)
        if pend and pend.get("ts") == closed_ts:
            self._resolve(pend, closed_ts)

        # 2. Fetch close & store candle (ALWAYS, even during warmup)
        tkns = _tokens(coin, closed_ts)
        cp   = None
        if tkns:
            cp = _price(tkns["yes_token"])
        if cp is not None:
            self.log.info(f"Close: {cp:.4f}")
            hm.push_candle(coin, closed_ts, cp)
        else:
            self.log.warning("Could not fetch close price")

        # 3. Warmup — feed candle to strategy but don't trade
        if self.warmup < 3:
            if cp is not None:
                _strat.on_candle_close(coin, closed_ts, cp)  # build history, ignore signal
            self.log.info(f"Warmup {self.warmup}/3 (candle stored)")
            return None

        # 4. Detect signal (only after warmup)
        if cp is None:
            return None
        sig = _strat.on_candle_close(coin, closed_ts, cp)
        if sig is None:
            return None

        # 5. Fetch active market tokens
        active_tkns = _tokens(coin, boundary)
        if not active_tkns:
            self.log.warning("No active market")
            return None

        sig["active_ts"]    = boundary
        sig["yes_token"]    = active_tkns["yes_token"]
        sig["no_token"]     = active_tkns["no_token"]
        sig["condition_id"] = active_tkns["condition_id"]
        return sig

    def _resolve(self, pend: Dict, closed_ts: int):
        coin      = self.coin
        direction = pend["direction"]
        amount    = pend["amount"]

        tkns = _tokens(coin, closed_ts)
        cp   = _price(tkns["yes_token"]) if tkns else None

        if cp is None:
            self.log.warning("Resolve failed — no price")
            return

        won    = (cp > 0.5) if direction == "YES" else (cp < 0.5)
        # Binary option payout is 1 USDC per share if won.
        payout = (amount / pend["price"]) if won else 0.0
        
        # ── NET PnL Calculation (deducting 0.24% estimated fee) ──
        fee    = (payout * 0.0024) if won else (amount * 0.0024)
        pnl    = (payout - amount - fee) if won else -(amount + fee)

        _strat.on_result(coin, won)
        hm.log_bet_result(coin, closed_ts, won, pnl, fee=fee)
        hm.close_position(coin)
        hm.record_pnl(pnl)
        hm.record_fee(fee)

        if DRY_RUN and won:
            _vbal_write(_vbal_read() + payout)

        get_notifier().notify_result(coin, direction, amount, won, payout, _mg.get_step(coin))

        with _tlock:
            _tradelog.append({"coin":coin,"direction":direction,
                               "amount":amount,"won":won,"pnl":round(pnl,2)})
            if len(_tradelog) > 50:
                _tradelog.pop(0)

        with _plock:
            _pending[coin] = None

# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL PICKER  (random, recovery priority)
# ═══════════════════════════════════════════════════════════════════════════════
def _pick_and_place(signals: List[Dict], notifier, data_feed):
    if not signals:
        return

    # Recovery coins (step>0) have priority
    recovery = [s for s in signals if s["step"] > 0]
    chosen   = random.choice(recovery) if recovery else random.choice(signals)

    coin      = chosen["coin"]
    direction = chosen["direction"]
    amount    = chosen["amount"]
    step      = chosen["step"]
    token_id  = chosen["yes_token"] if direction == "YES" else chosen["no_token"]

    # Balance check
    bal = get_wallet_balance()
    if bal < max(amount, BET_MIN_FUNDS):
        notifier.notify_insufficient_funds(coin, bal, amount)
        return

    # Notify signal
    notifier.notify_signal(coin, direction, amount, step, chosen["closes"])

    # Already has pending?
    with _plock:
        if _pending.get(coin) is not None:
            get_coin_logger(coin).info("Skip — already pending")
            return

    # ── FIXED: Fetch live market price from feed ──
    try:
        st = data_feed.get_state(coin.lower())
        if st:
            # We want current ask price for the token we are buying
            price = st["up_ask"] if direction == "YES" else st["down_ask"]
            if not price or price <= 0:
                price = 0.50 # fallback
        else:
            price = 0.50 # fallback
    except Exception:
        price = 0.50 # fallback

    # ── FIXED: Price bracket to avoid outliers ──
    if step == 0:
        price = max(0.35, min(price, 0.65))  # L1 bracket: 35c to 65c
    else:
        price = max(0.49, min(price, 0.54))  # L2-L5 bracket: 49c to 54c

    ok, price, ot = _place(token_id, amount, coin, step, direction, price=price)

    if ok:
        if DRY_RUN:
            _vbal_write(_vbal_read() - amount)
        hm.log_bet_placed(coin, direction, amount, price, ot, step,
                          chosen["active_ts"], token_id)
        hm.open_position(coin, direction, amount, price, chosen["active_ts"], token_id)
        with _plock:
            _pending[coin] = {
                "direction": direction, "amount": amount, "step": step,
                "ts": chosen["active_ts"],
                "yes_token": chosen["yes_token"],
                "no_token":  chosen["no_token"],
                "token_id":  token_id, "price": price,
            }
        notifier.notify_trade_placed(coin, direction, amount, price, ot, step)
    else:
        notifier.notify_error(f"{coin} order", "Placement failed")

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    # PID guard
    pid_file = "data/bot.pid"
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old = int(f.read().strip())
            os.kill(old, 0)
            print(f"[ERROR] Bot already running (PID {old}). Stop first.")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            pass
    with open(pid_file,"w") as f:
        f.write(str(os.getpid()))

    print(f"""
╔══════════════════════════════════════════════════════════╗
║   Polymarket Streak-Reversal Martingale Bot  v2.0        ║
║   Mode : {'DRY RUN' if DRY_RUN else 'LIVE TRADING':<49}║
║   Coins: {', '.join(ACTIVE_COINS):<49}║
╚══════════════════════════════════════════════════════════╝""")

    if not ACTIVE_COINS:
        print("[ERROR] No coins enabled")
        sys.exit(1)

    # Reset session state
    hm.reset_on_startup()
    _mg.reset_all()
    if DRY_RUN and not os.path.exists(_VBAL):
        _vbal_write(VBAL_START)

    # Build components
    tg_bot   = get_bot()
    notifier = get_notifier()
    dash     = Dashboard(width=160, coins=ACTIVE_COINS)
    tg_bot.active_coins = ACTIVE_COINS

    # Start data feed
    data_feed = DataFeed(config={
        "data_sources": {
            "polymarket": {
                "gamma_api": "https://gamma-api.polymarket.com",
                "ws_url":    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
            }
        }
    })
    data_feed.start()
    print(f"[SYSTEM] Feeds started: {', '.join(ACTIVE_COINS)}")

    # Wire telegram callbacks
    tg_bot.get_balance = get_wallet_balance
    tg_bot.get_real_bal = get_real_balance
    tg_bot.get_in_bets = get_in_bets

    def _live():
        out = {}
        for c in ACTIVE_COINS:
            st = data_feed.get_state(c.lower())
            if st:
                out[c] = {"up_ask": st.get("up_ask",0),
                           "down_ask": st.get("down_ask",0),
                           "seconds_till_end": st.get("seconds_till_end",900)}
        return out
    tg_bot.get_live_state = _live

    def _pause(paused: bool):
        if paused: _paused.set()
        else: _paused.clear()
    tg_bot.on_stop = _pause

    def _health():
        up_sec = int(time.time() - _start_time)
        h, m = divmod(up_sec // 60, 60)
        uptime = f"{h}h {m}m"
        
        pol_bal = 0.0
        if not DRY_RUN:
            try:
                from web3 import Web3
                w3 = Web3(Web3.HTTPProvider(RPC_URL))
                if w3.is_connected():
                    addr = FUNDER_ADDRESS or WALLET_ADDRESS
                    if addr:
                        pol_bal = w3.eth.get_balance(w3.to_checksum_address(addr)) / 1e18
            except: pass
            
        log_sz = "0 KB"
        try:
            sz = os.path.getsize("logs/bot.log")
            if sz > 1024*1024: log_sz = f"{sz/(1024*1024):.1f} MB"
            else: log_sz = f"{sz/1024:.1f} KB"
        except: pass

        return {
            "ok": not _stop.is_set(),
            "uptime": uptime,
            "ws_connected": data_feed.is_alive() if hasattr(data_feed, 'is_alive') else True,
            "redeem_status": _redeem_status,
            "pol_balance": pol_bal,
            "log_size": log_sz
        }
    tg_bot.get_health = _health

    def _manual(coin: str, direction: str, amount: float) -> str:
        """Place a manual bet on a specific coin in a specific direction."""
        coin = coin.upper()
        direction = direction.upper()   # YES or NO

        if _paused.is_set():
            return "⏸ Bot paused. /stop to resume."
        if coin not in ACTIVE_COINS:
            return f"⚠️ {coin} is not active."

        with _plock:
            if _pending.get(coin):
                return f"⚠️ {coin} already has an open bet."

        bal = get_wallet_balance()
        if bal < amount:
            return f"⚠️ Low balance: ${bal:.2f} (need ${amount:.0f})"

        active_ts = (int(time.time()) // INTERVAL_SEC) * INTERVAL_SEC
        tkns = _tokens(coin, active_ts)
        if not tkns:
            return f"⚠️ Cannot fetch {coin} market."

        # ── FIXED: Fetch live price for manual bet ──
        price = 0.50 # fallback
        try:
            st = data_feed.get_state(coin.lower())
            if st:
                price = st["up_ask"] if direction == "YES" else st["down_ask"]
                if not price or price <= 0: price = 0.50
        except Exception: pass

        token_id = tkns["yes_token"] if direction == "YES" else tkns["no_token"]
        step = _mg.get_step(coin)

        # ── FIXED: Price bracket to avoid outliers ──
        if step == 0:
            price = max(0.35, min(price, 0.65))
        else:
            price = max(0.49, min(price, 0.54))

        ok, price, ot = _place(token_id, amount, coin, step, direction, price=price)

        if ok:
            if DRY_RUN:
                _vbal_write(_vbal_read() - amount)
            hm.log_bet_placed(coin, direction, amount, price, ot, step, active_ts, token_id)
            hm.open_position(coin, direction, amount, price, active_ts, token_id)
            with _plock:
                _pending[coin] = {
                    "direction": direction, "amount": amount, "step": step,
                    "ts": active_ts, "yes_token": tkns["yes_token"],
                    "no_token": tkns["no_token"], "token_id": token_id, "price": price,
                }
            arrow = "⬆ UP" if direction == "YES" else "⬇ DOWN"
            return f"✅ *Manual bet placed!*\n{coin}  {arrow}  ${amount:.0f}  @ {price:.3f} ({ot})"
        return f"❌ Order failed for {coin}."
    tg_bot.on_manual_bet = _manual

    # Signal handler
    def _shutdown(sig, frame):
        print("\n[SYSTEM] Stopping...")
        _stop.set()
        data_feed.stop()
        try: os.remove(pid_file)
        except: pass
        sys.exit(0)
    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Telegram startup
    notifier.notify_startup(ACTIVE_COINS, DRY_RUN)

    # Dashboard thread
    def _dash():
        while not _stop.is_set():
            try:
                ms = {}
                for c in ACTIVE_COINS:
                    st = data_feed.get_state(c.lower())
                    if st:
                        ms[c] = {"up_ask":st.get("up_ask",0),
                                 "down_ask":st.get("down_ask",0),
                                 "seconds_till_end":st.get("seconds_till_end",900),
                                 "market_slug":st.get("market_slug","")}
                with _plock:  ps = {c:_pending.get(c) for c in ACTIVE_COINS}
                with _tlock:  tl = list(_tradelog)
                dash.render(ms, {c:_mg.get_step(c) for c in ACTIVE_COINS},
                            ps, tl, get_wallet_balance(), DRY_RUN)
            except Exception as e:
                logging.error(f"[DASH] {e}")
            time.sleep(DASHBOARD_REFRESH)

    threading.Thread(target=_dash, daemon=True, name="dashboard").start()

    # Settlement/Redeem thread (runs every 30m)
    def _settle_loop():
        while not _stop.is_set():
            _redeem_all()
            for _ in range(1800): # 30 mins
                if _stop.is_set(): break
                time.sleep(1)
    threading.Thread(target=_settle_loop, daemon=True, name="settlement").start()

    # Coin processors
    procs = {c: CoinProc(c) for c in ACTIVE_COINS}

    # Main loop with parallel coin processing
    print("[SYSTEM] Running (parallel). Ctrl+C to stop.")
    with ThreadPoolExecutor(max_workers=len(ACTIVE_COINS)) as executor:
        while not _stop.is_set():
            now = int(time.time())
            if _paused.is_set():
                time.sleep(1)
                continue

            # Run p.tick(now) for all coins in parallel
            sigs = []
            futures = {executor.submit(p.tick, now): c for c, p in procs.items()}
            
            for f in futures:
                coin = futures[f]
                try:
                    s = f.result()
                    if s: sigs.append(s)
                except Exception as e:
                    logging.error(f"[{coin}] Parallel tick error: {e}")
                    dash.log_error(f"{coin}: {e}")

            if sigs:
                try:
                    _pick_and_place(sigs, notifier, data_feed)
                except Exception as e:
                    logging.error(f"[PICK] {e}")

            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        try: os.remove("data/bot.pid")
        except: pass
