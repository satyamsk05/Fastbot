"""
Telegram Bot
══════════════════════════════════════════════════════
Commands:
  /start       → Bot status + menu
  /history     → 7-day candle trend (🟩🟥) per coin
  /live        → Live odds (UP/DOWN price) for all coins
  /stop        → Pause bot trading
  /balance     → Wallet balance (available, in bets, total)
  /position    → Current open bets
  /daily_pnl   → Last 7 days P&L

Manual bets (type in chat):
  $3   → Place $3 manual bet on best signal coin
  $6   → Place $6 manual bet
  (any amount with $ prefix)
══════════════════════════════════════════════════════
"""
import os
import logging
import asyncio
import threading
import time
from typing import Optional, Callable, Dict

try:
    from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (Application, CommandHandler, MessageHandler,
                               ContextTypes, filters)
    TELEGRAM_OK = True
except ImportError:
    TELEGRAM_OK = False

import history_manager as hm

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── format helpers ─────────────────────────────────────────────────────────────
def _box(title: str, lines: list) -> str:
    """Og-style ╔══╗ box."""
    pad = max(len(title) + 6, max((len(l) for l in lines), default=20) + 4)
    bar = "═" * pad
    body = "\n".join(f"» {l}" for l in lines)
    return f"╔{bar}╗\n» *{title}*\n{body}\n╚{bar}╝"


def _divider(n: int = 30) -> str:
    return "——" * (n // 2)


# ══════════════════════════════════════════════════════════════════════════════
# BOT CLASS
# ══════════════════════════════════════════════════════════════════════════════
class TelegramBot:
    """
    Full async Telegram bot.
    Runs in a background thread (separate event loop).
    Main bot registers callbacks here for /stop and manual bets.
    """

    def __init__(self):
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._app     = None
        self._running = False

        # Callbacks set by main.py
        self.get_live_state: Optional[Callable] = None    # () → {coin: {up_ask, down_ask, seconds_till_end}}
        self.get_balance:    Optional[Callable] = None    # () → float
        self.get_in_bets:    Optional[Callable] = None    # () → float  (USDC locked in open bets)
        self.on_stop:        Optional[Callable] = None    # () → None   (pause trading)
        self.on_manual_bet:  Optional[Callable] = None   # (amount) → str  (result message)
        self.is_paused:      bool = False
        self.active_coins:   list = ["BTC", "ETH", "SOL"]

        if TELEGRAM_OK and BOT_TOKEN and CHAT_ID:
            self._start_thread()
        else:
            logging.warning("[TG] Telegram not configured — notifications only mode")

    # ── thread setup ──────────────────────────────────────────────────────────
    def _start_thread(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True, name="telegram")
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._run_app())

    async def _run_app(self):
        self._app = Application.builder().token(BOT_TOKEN).build()
        app = self._app

        # Register handlers
        app.add_handler(CommandHandler("start",     self._cmd_start))
        app.add_handler(CommandHandler("history",   self._cmd_history))
        app.add_handler(CommandHandler("live",      self._cmd_live))
        app.add_handler(CommandHandler("stop",      self._cmd_stop))
        app.add_handler(CommandHandler("balance",   self._cmd_balance))
        app.add_handler(CommandHandler("position",  self._cmd_position))
        app.add_handler(CommandHandler("daily_pnl", self._cmd_daily_pnl))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))

        self._running = True
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logging.info("[TG] Bot polling started")

        # Keep running
        while self._running:
            await asyncio.sleep(1)

        await app.updater.stop()
        await app.stop()
        await app.shutdown()

    # ── send helper (thread-safe) ──────────────────────────────────────────────
    def send(self, text: str, parse_mode: str = "Markdown"):
        if not TELEGRAM_OK or not BOT_TOKEN or not CHAT_ID:
            print(f"[TG] {text}")
            return
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._send_async(text, parse_mode), self._loop
            )
        else:
            print(f"[TG-OFFLINE] {text}")

    async def _send_async(self, text: str, parse_mode: str):
        try:
            await self._app.bot.send_message(
                chat_id=CHAT_ID, text=text, parse_mode=parse_mode
            )
        except Exception as e:
            logging.error(f"[TG] send error: {e}")

    # ── /start ─────────────────────────────────────────────────────────────────
    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        pause_status = "⏸ PAUSED" if self.is_paused else "▶ RUNNING"
        coins_str = ", ".join(self.active_coins)
        await update.message.reply_text(
            _box("🚀 Polymarket Streak Bot", [
                f"Status  : {pause_status}",
                f"Coins   : {coins_str}",
                f"Strategy: 3-streak reversal",
                f"Ladder  : $3→$6→$13→$28→$60",
                _divider(),
                "/history  — 7-day candle trend",
                "/live     — live odds",
                "/stop     — pause/resume trading",
                "/balance  — wallet balance",
                "/position — open bets",
                "/daily_pnl — P&L summary",
                "$3 / $6  — manual bet",
            ]),
            parse_mode="Markdown"
        )

    # ── /history ───────────────────────────────────────────────────────────────
    async def _cmd_history(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        lines = ["7-day candle trend (last 48 candles = 12h):", ""]
        for coin in self.active_coins:
            trend = hm.get_7day_trend_bar(coin)
            lines.append(f"*{coin}*")
            lines.append(trend)
            lines.append("")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    # ── /live ──────────────────────────────────────────────────────────────────
    async def _cmd_live(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self.get_live_state:
            await update.message.reply_text("Live data not available yet.")
            return

        states = self.get_live_state()
        lines = []
        for coin in self.active_coins:
            ms = states.get(coin, {})
            up  = ms.get("up_ask",  0.0)
            dn  = ms.get("down_ask", 0.0)
            sec = ms.get("seconds_till_end", 900)
            fav = "⬆ UP" if up > dn else "⬇ DN"
            mins = sec // 60
            secs = sec % 60
            lines.append(f"*{coin}*  UP:{up:.3f}  DN:{dn:.3f}  {fav}  ⏰{mins:02d}:{secs:02d}")
        await update.message.reply_text(
            _box("📊 Live Odds", lines), parse_mode="Markdown"
        )

    # ── /stop ──────────────────────────────────────────────────────────────────
    async def _cmd_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        self.is_paused = not self.is_paused
        if self.on_stop:
            self.on_stop(self.is_paused)
        status = "⏸ PAUSED — no new bets will be placed." if self.is_paused \
                 else "▶ RESUMED — trading active."
        await update.message.reply_text(f"*Bot {status}*", parse_mode="Markdown")

    # ── /balance ───────────────────────────────────────────────────────────────
    async def _cmd_balance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        avail   = self.get_balance() if self.get_balance else 0.0
        in_bets = self.get_in_bets() if self.get_in_bets else 0.0
        total   = avail + in_bets
        await update.message.reply_text(
            _box("💰 Wallet Balance", [
                f"Available : ${avail:.2f} USDC",
                f"In Bets   : ${in_bets:.2f} USDC",
                f"Total     : ${total:.2f} USDC",
            ]),
            parse_mode="Markdown"
        )

    # ── /position ──────────────────────────────────────────────────────────────
    async def _cmd_position(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        positions = hm.get_open_positions()
        if not positions:
            await update.message.reply_text("📌 *No open positions.*", parse_mode="Markdown")
            return

        lines = []
        total = 0.0
        for coin, pos in positions.items():
            arrow = "⬆" if pos["direction"] == "YES" else "⬇"
            lines.append(f"{arrow} *{coin}*: ${pos['amount']:.0f} @ {pos['price']:.3f}")
            total += pos["amount"]
        lines.append(_divider())
        lines.append(f"Total in bets: ${total:.2f}")

        await update.message.reply_text(
            _box("📌 Open Positions", lines), parse_mode="Markdown"
        )

    # ── /daily_pnl ─────────────────────────────────────────────────────────────
    async def _cmd_daily_pnl(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        summary = hm.get_pnl_summary(days=7)
        await update.message.reply_text(
            f"*📅 Daily PNL (last 7 days)*\n\n{summary}",
            parse_mode="Markdown"
        )

    # ── manual bet ($3, $6, etc.) ──────────────────────────────────────────────
    async def _on_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip()
        if not text.startswith("$"):
            return
        try:
            amount = float(text[1:].replace(",", ""))
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Try: `$3` or `$6`",
                                             parse_mode="Markdown")
            return

        if self.is_paused:
            await update.message.reply_text("⏸ Bot is paused. Resume with /stop first.")
            return

        if self.on_manual_bet:
            result = self.on_manual_bet(amount)
            await update.message.reply_text(result, parse_mode="Markdown")
        else:
            await update.message.reply_text(
                f"⚠️ Manual bet (${amount:.0f}) queued — no active signal right now.",
                parse_mode="Markdown"
            )


# ══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION SHORTCUTS  (called from main.py)
# ══════════════════════════════════════════════════════════════════════════════
class TelegramNotifier:
    """
    Thin wrapper that uses TelegramBot.send() for all alert messages.
    Keeps notification format centralized.
    """

    def __init__(self, bot: "TelegramBot"):
        self._bot = bot

    def send(self, text: str):
        self._bot.send(text)

    def notify_startup(self, coins: list, dry_run: bool):
        mode = "~ DRY RUN ~" if dry_run else "LIVE"
        self.send(_box("🚀 BOT STARTED", [
            f"Mode   : {mode}",
            f"Coins  : {', '.join(coins)}",
            f"Ladder : $3→$6→$13→$28→$60",
            f"Signal : 3-streak reversal",
        ]))

    def notify_signal(self, coin: str, direction: str, amount: float,
                      step: int, closes: list):
        arrow   = "⬆ UP" if direction == "YES" else "⬇ DOWN"
        streak  = "↑↑↑" if direction == "NO" else "↓↓↓"   # streak that triggered
        self.send(_box(f"🎯 SIGNAL {coin}", [
            f"Side  : {arrow}",
            f"Bet   : ${amount:.0f}  (L{step+1})",
            f"Streak: {streak}",
            f"Closes: {[f'{c:.2f}' for c in closes[-3:]]}",
        ]))

    def notify_trade_placed(self, coin: str, direction: str, amount: float,
                            price: float, order_type: str, step: int):
        arrow = "⬆ YES" if direction == "YES" else "⬇ NO"
        self.send(_box(f"✅ PLACED {coin}", [
            f"Side  : {arrow}",
            f"Bet   : ${amount:.0f}",
            f"Price : {price:.3f}",
            f"Type  : {order_type}",
            f"Level : L{step+1}",
        ]))

    def notify_result(self, coin: str, direction: str, amount: float,
                      won: bool, payout: float, next_step: int):
        emoji = "✅ WON" if won else "❌ LOST"
        pnl   = payout - amount if won else -amount
        sign  = "+" if pnl >= 0 else ""
        nxt   = "Reset L1" if won else f"Recovery L{next_step+1}"
        self.send(_box(f"{emoji} {coin}", [
            f"Side  : {'⬆ YES' if direction=='YES' else '⬇ NO'}",
            f"PnL   : {sign}${pnl:.2f}",
            f"Next  : {nxt}",
        ]))

    def notify_insufficient_funds(self, coin: str, balance: float, need: float):
        self.send(_box("⚠️ LOW FUNDS", [
            f"Coin   : {coin}",
            f"Wallet : ${balance:.2f}",
            f"Need   : ${need:.2f}",
            f"Action : Signal skipped",
        ]))

    def notify_error(self, context: str, error: str):
        self.send(f"🚨 *ERROR* `{context}`\n```{str(error)[:200]}```")


# ── singleton ─────────────────────────────────────────────────────────────────
_bot_instance: Optional[TelegramBot] = None

def get_bot() -> TelegramBot:
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = TelegramBot()
    return _bot_instance

def get_notifier() -> TelegramNotifier:
    return TelegramNotifier(get_bot())
