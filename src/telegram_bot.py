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
                               CallbackQueryHandler, ContextTypes, filters)
    TELEGRAM_OK = True
except ImportError:
    TELEGRAM_OK = False

import history_manager as hm

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── format helpers ─────────────────────────────────────────────────────────────
def _box(title: str, lines: list) -> str:
    """OG-style ━━━━━━━━━━━━━━━━━━━━ box."""
    sep = "━━━━━━━━━━━━━━━━━━━━"
    body = "\n".join(lines)
    return f"*{title}*\n{sep}\n{body}\n{sep}"


def _divider() -> str:
    return "━━━━━━━━━━━━━━━━━━━━"


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
        self.get_real_bal:   Optional[Callable] = None    # () → float (on-chain balance)
        self.get_in_bets:    Optional[Callable] = None    # () → float  (USDC locked in open bets)
        self.on_stop:        Optional[Callable] = None    # () → None   (pause trading)
        self.on_manual_bet:  Optional[Callable] = None   # (amount) → str  (result message)
        self.is_paused:      bool = False
        self.active_coins:   list = ["BTC", "ETH", "SOL"]
        self._custom_bet_pending = None   # {coin, direction} waiting for amount

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
        app.add_handler(CommandHandler("menu",      self._cmd_start))
        app.add_handler(CommandHandler("history",   self._cmd_history))
        app.add_handler(CommandHandler("live",      self._cmd_live))
        app.add_handler(CommandHandler("stop",      self._cmd_stop))
        app.add_handler(CommandHandler("balance",   self._cmd_balance))
        app.add_handler(CommandHandler("position",  self._cmd_position))
        app.add_handler(CommandHandler("daily_pnl", self._cmd_daily_pnl))
        app.add_handler(CommandHandler("trend",     self._cmd_trend))
        app.add_handler(CommandHandler("hide",      self._cmd_hide))
        app.add_handler(CallbackQueryHandler(self._on_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))

        self._running = True
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logging.info("[TG] Bot polling started")

        # ── Register /command menu in Telegram ────────────────────
        try:
            from telegram import BotCommand
            await app.bot.set_my_commands([
                BotCommand("start",     "🚀 Bot status + menu"),
                BotCommand("menu",      "Show command buttons"),
                BotCommand("live",      "📊 Live odds (UP/DOWN)"),
                BotCommand("history",   "📈 Trade history"),
                BotCommand("balance",   "💰 Wallet balance"),
                BotCommand("position",  "📌 Open positions"),
                BotCommand("daily_pnl", "📅 Last 7 days P&L"),
                BotCommand("stop",      "⏸ Pause / Resume bot"),
                BotCommand("trend",     "📉 10-candle history"),
            ])
            logging.info("[TG] ✅ Bot commands menu registered")
        except Exception as e:
            logging.warning(f"[TG] Could not set commands menu: {e}")

        # ── Send startup message with Reply Keyboard buttons ──────
        try:
            await app.bot.send_message(
                chat_id=CHAT_ID,
                text="⌨️ *Bot Controls Ready*\nUse buttons below 👇",
                parse_mode="Markdown",
                reply_markup=self._get_kb(),
            )
            logging.info("[TG] ✅ Reply keyboard sent")
        except Exception as e:
            logging.warning(f"[TG] Could not send keyboard: {e}")

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
            _box("📊 BOT STATUS", [
                f"STATUS   → {pause_status}",
                f"COINS    → {coins_str}",
                f"STRATEGY → 3-streak reversal",
                f"LADDER   → $3 → $6 → $13 → $28 → $60",
                "",
                "Commands: /live /history /stop /balance /position /daily_pnl",
            ]),
            parse_mode="Markdown",
            reply_markup=self._get_kb()
        )

    # ── keyboard helper ───────────
    def _get_kb(self):
        from telegram import ReplyKeyboardMarkup, KeyboardButton
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("📊 Live"),    KeyboardButton("💰 Balance"), KeyboardButton("📌 Position")],
                [KeyboardButton("📈 History"), KeyboardButton("📅 PnL"),     KeyboardButton("⏸ Stop/Resume")],
                [KeyboardButton("📉 Trend"),     KeyboardButton("🎯 Manual Bet"), KeyboardButton("🔄 Refresh")],
            ],
            resize_keyboard=True,
            is_persistent=True,
            one_time_keyboard=False,
        )

    # ── hide keyboard (obsolete, but keeping for safety) ───────────
    async def _cmd_hide(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("⌨️ Re-sending menu...", reply_markup=self._get_kb())

    # ── /history ───────────────────────────────────────────────────────────────
    async def _cmd_history(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show recent trade results."""
        bets = hm.get_bet_history(n=10)
        if not bets:
            await update.message.reply_text("📊 *Trade History is empty.*", parse_mode="Markdown")
            return

        lines = []
        for b in reversed(bets):
            res = b.get("result", "OPEN")
            pnl = b.get("pnl", 0.0)
            coin = b.get("coin", "???")
            amt = b.get("amount", 0.0)
            
            icon = "🟢" if res == "WIN" else "🔴" if res == "LOSS" else "⏳"
            sign = "+" if pnl > 0 else ""
            res_str = f"{sign}${pnl:.2f}" if res != "OPEN" else "PENDING"
            
            lines.append(f"{icon} {coin} - ${amt:.0f} ({res_str})")

        await update.message.reply_text(_box("📊 RECENT TRADES", lines), parse_mode="Markdown")

    # ── /live ──────────────────────────────────────────────────────────────────
    async def _cmd_live(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        content = self._get_live_content()
        if not content:
            await update.message.reply_text("Live data not available yet.")
            return

        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        inline_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_live")]])
        await update.message.reply_text(content, parse_mode="Markdown", reply_markup=inline_kb)

    def _get_live_content(self) -> Optional[str]:
        """Helper to generate the OG-style live price box string."""
        if not self.get_live_state:
            return None

        states = self.get_live_state()
        lines = []
        timer_str = "00:00"
        
        for coin in self.active_coins:
            ms = states.get(coin, {})
            up  = ms.get("up_ask",  0.0)
            dn  = ms.get("down_ask", 0.0)
            sec = ms.get("seconds_till_end", 900)
            
            mins = sec // 60
            secs = sec % 60
            timer_str = f"{mins:02d}:{secs:02d}"
            
            lines.append(f"🌟 *{coin}*")
            lines.append(f"  🟢 YES: ${up:.2f}  |  🔴 NO: ${dn:.2f}")
            lines.append("┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈")

        lines.append(f"⏰ {timer_str}")
        return _box("📉 LIVE PRICES", lines)

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
        real    = self.get_real_bal() if self.get_real_bal else 0.0
        in_bets = self.get_in_bets() if self.get_in_bets else 0.0
        
        await update.message.reply_text(
            _box("📊 WALLET BALANCE", [
                f"VIRTUAL → ${avail:.2f} USDC",
                f"In Bets → ${in_bets:.2f} USDC",
                f"REAL    → ${real:.2f} USDC",
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
            arrow = "UP" if pos["direction"] == "YES" else "DOWN"
            emoji = "🟢" if arrow == "UP" else "🔴"
            lines.append(f"{emoji} {coin} → ${pos['amount']:.0f} @ {pos['price']:.3f} ({arrow})")
            total += pos["amount"]

        lines.append(f"TOTAL IN BETS → ${total:.2f}")

        await update.message.reply_text(
            _box("📌 OPEN POSITIONS", lines), parse_mode="Markdown"
        )

    # ── /daily_pnl ─────────────────────────────────────────────────────────────
    async def _cmd_daily_pnl(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        summary = hm.get_pnl_summary(days=7)
        await update.message.reply_text(
            f"*📅 Daily PNL (last 7 days)*\n\n{summary}",
            parse_mode="Markdown"
        )

    async def _cmd_trend(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show clean trend summary for all coins."""
        lines = []
        for coin in self.active_coins:
            candles = hm.get_candle_history(coin, n=10)
            
            # Create 10-box bar
            emojis = []
            for i in range(10):
                # We want the last 10, but if we have fewer, we pad from the left
                # Index calculation: if we have 2 candles, we want them at index 8 and 9
                # So we pad 8 ⚪ at start.
                idx = i - (10 - len(candles))
                if idx >= 0:
                    c = candles[idx]
                    emojis.append("🟢" if c["dir"] == "UP" else "🔴")
                else:
                    emojis.append("⚪")
            
            bar = "".join(emojis)
            
            # Counts from last 10
            ups = sum(1 for c in candles if c["dir"] == "UP")
            dns = len(candles) - ups
            lines.append(f"*{coin}*  {bar} → UP:{ups} | DN:{dns}")
            lines.append("") # Extra newline for spacing

        lines.append("Trend → Streak Analysis Active")
        await update.message.reply_text(
            _box("📊  TREND DATA", lines), parse_mode="Markdown"
        )

    # ── hide keyboard ──────────────────────────────────────────────────────────
    async def _cmd_hide(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        from telegram import ReplyKeyboardRemove
        await update.message.reply_text(
            "⌨️ Keyboard hidden. Type /menu to show it again.",
            reply_markup=ReplyKeyboardRemove()
        )

    # ── manual bet menu (Step 1: Pick Coin) ─────────────────────────────────
    async def _cmd_manual_bet(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show coin selection for manual bet."""
        if self.is_paused:
            await update.message.reply_text("⏸ Bot is paused. Resume with /stop first.")
            return

        coin_buttons = [
            InlineKeyboardButton(f"₿ {c}", callback_data=f"mb_coin_{c}")
            for c in self.active_coins
        ]
        # 2 coins per row
        rows = [coin_buttons[i:i+2] for i in range(0, len(coin_buttons), 2)]
        inline_kb = InlineKeyboardMarkup(rows)
        await update.message.reply_text(
            "🎯 *Manual Bet — Step 1: Pick Coin*",
            parse_mode="Markdown",
            reply_markup=inline_kb,
        )

    # ── inline callback handler (multi-step) ──────────────────────────────
    async def _on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Handle inline button taps for manual bet flow and refresh."""
        query = update.callback_query
        await query.answer()
        data = query.data or ""

        # ── Refresh Live Prices ──
        if data == "refresh_live":
            content = self._get_live_content()
            if content:
                from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                inline_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_live")]])
                try:
                    await query.edit_message_text(content, parse_mode="Markdown", reply_markup=inline_kb)
                except Exception:
                    pass # ignore "message is not modified"
            return

        # ── Step 2: User picked a coin → show UP / DOWN ──────────
        if data.startswith("mb_coin_"):
            coin = data.replace("mb_coin_", "")
            inline_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("⬆ UP  (YES)", callback_data=f"mb_dir_{coin}_YES"),
                    InlineKeyboardButton("⬇ DOWN (NO)", callback_data=f"mb_dir_{coin}_NO"),
                ],
            ])
            await query.edit_message_text(
                f"🎯 *Manual Bet — {coin}*\n\n*Step 2: Pick Direction*",
                parse_mode="Markdown",
                reply_markup=inline_kb,
            )

        # ── Step 3: User picked direction → show amounts ─────────
        elif data.startswith("mb_dir_"):
            parts = data.replace("mb_dir_", "").split("_")
            coin, direction = parts[0], parts[1]
            arrow = "⬆ UP" if direction == "YES" else "⬇ DOWN"
            inline_kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("$3",  callback_data=f"mb_go_{coin}_{direction}_3"),
                    InlineKeyboardButton("$6",  callback_data=f"mb_go_{coin}_{direction}_6"),
                    InlineKeyboardButton("$13", callback_data=f"mb_go_{coin}_{direction}_13"),
                ],
                [
                    InlineKeyboardButton("$28", callback_data=f"mb_go_{coin}_{direction}_28"),
                    InlineKeyboardButton("$60", callback_data=f"mb_go_{coin}_{direction}_60"),
                ],
                [
                    InlineKeyboardButton("✏️ Custom Amount", callback_data=f"mb_custom_{coin}_{direction}"),
                ],
            ])
            await query.edit_message_text(
                f"🎯 *Manual Bet — {coin} {arrow}*\n\n*Step 3: Pick Amount*",
                parse_mode="Markdown",
                reply_markup=inline_kb,
            )

        # ── Final: User picked amount → place bet ────────────────
        elif data.startswith("mb_go_"):
            parts = data.replace("mb_go_", "").split("_")
            coin, direction, amt_str = parts[0], parts[1], parts[2]
            amount = float(amt_str)
            arrow = "⬆ UP" if direction == "YES" else "⬇ DOWN"

            if self.on_manual_bet:
                result = self.on_manual_bet(coin, direction, amount)
                await query.edit_message_text(result, parse_mode="Markdown")
            else:
                await query.edit_message_text(
                    f"⚠️ Bot not ready — manual bet ${amount:.0f} {coin} {arrow} could not be placed.",
                )

        # ── Custom amount: ask user to type ──────────────────────
        elif data.startswith("mb_custom_"):
            parts = data.replace("mb_custom_", "").split("_")
            coin, direction = parts[0], parts[1]
            arrow = "⬆ UP" if direction == "YES" else "⬇ DOWN"
            # Store state for next text message
            self._custom_bet_pending = {"coin": coin, "direction": direction}
            await query.edit_message_text(
                f"🎯 *{coin} {arrow}*\n\n✏️ Type your bet amount (e.g. `5` or `25`):",
                parse_mode="Markdown",
            )

    # ── manual bet ($3, $6, etc.) + button handler ──────────────────────────
    async def _on_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip()

        # ── Map Reply Keyboard button taps to commands ────────────
        button_map = {
            "📊 Live":       self._cmd_live,
            "💰 Balance":    self._cmd_balance,
            "📌 Position":   self._cmd_position,
            "📈 History":    self._cmd_history,
            "📅 PnL":       self._cmd_daily_pnl,
            "⏸ Stop/Resume": self._cmd_stop,
            "📉 Trend":      self._cmd_trend,
            "🎯 Manual Bet": self._cmd_manual_bet,
            "⌨️ Hide Menu":   self._cmd_hide,
        }
        if text in button_map:
            await button_map[text](update, ctx)
            return

        # ── Custom amount pending (user typed a number) ──────────
        if hasattr(self, '_custom_bet_pending') and self._custom_bet_pending:
            pending = self._custom_bet_pending
            self._custom_bet_pending = None
            try:
                amount = float(text.replace("$", "").replace(",", "").strip())
            except ValueError:
                await update.message.reply_text("❌ Invalid number. Try again: `5` or `25`",
                                                 parse_mode="Markdown")
                return
            if amount < 1:
                await update.message.reply_text("❌ Minimum bet is $1.")
                return
            coin = pending["coin"]
            direction = pending["direction"]
            if self.on_manual_bet:
                result = self.on_manual_bet(coin, direction, amount)
                await update.message.reply_text(result, parse_mode="Markdown")
            else:
                await update.message.reply_text("⚠️ Bot not ready.")
            return

        # ── Direct $amount typing → open manual bet flow ─────────
        if text.startswith("$"):
            # Redirect to manual bet menu instead of auto-betting
            await self._cmd_manual_bet(update, ctx)
            return


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
        self.send(_box("📊 BOT START", [
            f"MODE   → {mode}",
            f"COINS  → {', '.join(coins)}",
            f"LADDER → $3 → $6 → $13 → $28 → $60",
            f"SIGNAL → 3-streak reversal",
        ]))

    def notify_signal(self, coin: str, direction: str, amount: float,
                      step: int, closes: list):
        arrow   = "UP" if direction == "YES" else "DOWN"
        emoji   = "🟢" if arrow == "UP" else "🔴"
        streak  = "↑↑↑" if direction == "NO" else "↓↓↓"   # streak that triggered
        self.send(_box(f"🎯 SIGNAL {coin}", [
            f"SIDE   → {emoji} {arrow}",
            f"BET    → ${amount:.0f} (L{step+1})",
            f"STREAK → {streak}",
            f"CL_AVG → {sum(closes[-3:])/3:.3f}",
        ]))

    def notify_trade_placed(self, coin: str, direction: str, amount: float,
                            price: float, order_type: str, step: int):
        arrow = "UP" if direction == "YES" else "DOWN"
        emoji = "🟢" if arrow == "UP" else "🔴"
        self.send(_box(f"✅ TRADE PLACED {coin}", [
            f"SIDE   → {emoji} {arrow}",
            f"AMOUNT → ${amount:.0f} (L{step+1})",
            f"PRICE  → {price:.3f}",
            f"TYPE   → {order_type}",
        ]))

    def notify_result(self, coin: str, direction: str, amount: float,
                      won: bool, payout: float, next_step: int):
        status = "✅ TRADE WON" if won else "❌ TRADE LOST"
        pnl    = payout - amount if won else -amount
        sign   = "+" if pnl >= 0 else ""
        nxt    = "L1 (RESET)" if won else f"L{next_step+1} (RECOVERY)"
        self.send(_box(f"{status} {coin}", [
            f"RESULT → {sign}${pnl:.2f}",
            f"NEXT   → {nxt}",
        ]))

    def notify_insufficient_funds(self, coin: str, balance: float, need: float):
        self.send(_box("⚠️ LOW FUNDS", [
            f"COIN   → {coin}",
            f"WALLET → ${balance:.2f}",
            f"NEED   → ${need:.2f}",
            f"ACTION → Signal skipped",
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
