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
    from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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
        self.active_coins:   list = ["BTC", "ETH", "SOL", "XRP"]
        self._custom_bet_pending = None   # {coin, direction} waiting for amount
        self.get_health:     Optional[Callable] = None    # () → dict

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
        app.add_handler(CommandHandler("health",    self._cmd_health))
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
                BotCommand("health",    "🏥 System health status"),
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
        stop_label = "▶ Start" if self.is_paused else "⏸ Stop"
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("📊 Live"),    KeyboardButton("💰 Balance"), KeyboardButton("📌 Position")],
                [KeyboardButton("📈 History"), KeyboardButton("📅 PnL"),     KeyboardButton("🏥 Health")],
                [KeyboardButton("📉 Trend"),     KeyboardButton("🎯 Manual Bet"), KeyboardButton(stop_label)],
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
        
        status = "⏸ PAUSED — no new bets." if self.is_paused else "▶ RESUMED — active."
        await update.message.reply_text(f"*Bot {status}*", 
                                        parse_mode="Markdown",
                                        reply_markup=self._get_kb())

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

    # ── /health ────────────────────────────────────────────────────────────────
    async def _cmd_health(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self.get_health or not self.get_live_state:
            await update.message.reply_text("Health data not available.")
            return

        h = self.get_health()
        states = self.get_live_state()
        now = time.time()
        
        status = "🟢 OK" if h.get("ok") else "🔴 ERROR"
        
        lines = [
            f"*OVERALL*  → {status}",
            f"*UPTIME*   → {h.get('uptime', '0h')}",
            f"*REDEEM*   → {h.get('redeem_status', 'Idle')}",
            f"*POL BAL*  → {h.get('pol_balance', 0.0):.2f} POL",
            f"*LOG SIZE* → {h.get('log_size', '0 KB')}",
            "──────────────────",
            "📡 *DATA FEED STATUS:*"
        ]
        
        for coin in self.active_coins:
            st = states.get(coin, {})
            last_ms = st.get("last_msg_time", 0.0)
            diff = int(now - last_ms) if last_ms > 0 else 999
            
            up_p = st.get("up_ask", 0.0)
            dn_p = st.get("down_ask", 0.0)
            
            # Use new thresholds for UI
            emoji = "🟢" if diff < 30 else "🟡" if diff < 60 else "🔴"
            label = "OK" if diff < 30 else "LAG" if diff < 60 else "STALE"
            
            lines.append(f"{emoji} *{coin:<3}* [{label}] ({diff}s)")
            lines.append(f"      📈 *{up_p:.3f}* | 📉 *{dn_p:.3f}*")
            lines.append("      ┈┈┈┈┈┈┈┈┈┈")
            
        inline_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh Status", callback_data="refresh_health")]])
        await update.message.reply_text(_box("🏥 SYSTEM HEALTH", lines), parse_mode="Markdown", reply_markup=inline_kb)

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
        """Entry point for Manual Bet flow."""
        if self.is_paused:
            await update.message.reply_text("⏸ Bot is paused. Resume with /stop first.")
            return
        
        # Initialize default amount if not set
        if "manual_amount" not in ctx.user_data:
            ctx.user_data["manual_amount"] = 1.0
            
        await self._show_manual_trade_menu(update, ctx)

    def _manual_header(self, coin: str = "BTC") -> str:
        """User-requested header design for manual trade."""
        now_ts = int(time.time())
        t_str = time.strftime("%H:%M:%S")
        
        # Calculate 15m window
        window_start = (now_ts // 900) * 900
        window_end = window_start + 900
        s_str = time.strftime("%-I:%M%p", time.localtime(window_start))
        e_str = time.strftime("%-I:%M%p", time.localtime(window_end))
        d_str = time.strftime("%B %d", time.localtime(window_start))
        
        return (
            f"🎯 *MANUAL TRADE*\n"
            f"──────────────────────────\n"
            f"⏰ *Time:* {t_str}\n"
            f"📍 *Market:* {coin} Up/Down\n"
            f"📅 - {d_str}, {s_str}-{e_str} ET\n"
            f"──────────────────────────\n"
        )

    def _get_betting_inline(self, sel_coin: str = None, sel_amount: float = None) -> InlineKeyboardMarkup:
        """Sequential multi-step inline row builder with ✅ highlight on the LEFT."""
        rows = []
        
        # Step 1: Coin Row
        coins = ["BTC", "ETH", "SOL", "XRP"]
        rows.append([
            InlineKeyboardButton(f"✅ {c}" if c == sel_coin else c, callback_data=f"mcoin_{c}") 
            for c in coins
        ])
        
        # Step 2: Amount Row (only if coin selected)
        if sel_coin:
            amounts = [1, 2, 3, 5]
            rows.append([
                InlineKeyboardButton(f"✅ ${a}" if a == sel_amount else f"${a}", callback_data=f"mamt_{a}")
                for a in amounts
            ])
            
        # Step 3: Direction Row (only if coin AND amount selected)
        if sel_coin and sel_amount:
            rows.append([
                InlineKeyboardButton("⬆️ UP (YES)", callback_data=f"mdir_{sel_coin}_YES"),
                InlineKeyboardButton("⬇️ DOWN (NO)", callback_data=f"mdir_{sel_coin}_NO")
            ])
            
        # Navigation / Back
        rows.append([InlineKeyboardButton("🔙 Back to Coins", callback_data="mback_coins")])
        
        return InlineKeyboardMarkup(rows)

    def _get_manual_text(self, step: int, coin: str = None, amount: float = None) -> str:
        """Helper to build the cumulative beauty text."""
        header = self._manual_header(coin or "BTC")
        lines = [header]
        
        if step == 1:
            lines.append("Step 1: 🪙 *Select Coin*")
            lines.append("Choose a market to trade:")
        
        if step >= 2:
            lines.append(f"Step 1: 🪙 *Coin* → {coin} ✅")
            if step == 2:
                lines.append("\nStep 2: 💰 *Select Amount*")
                lines.append("Choose investment in USDC:")
        
        if step >= 3:
            lines.append(f"Step 2: 💰 *Amount* → ${amount:.0f} ✅")
            if step == 3:
                # Add live price context for the selected coin
                price_info = ""
                if self.get_live_state:
                    st = self.get_live_state().get(coin, {})
                    up = st.get("up_ask", 0.0)
                    dn = st.get("down_ask", 0.0)
                    price_info = f"\n📊 *Market:* UP at ${up:.2f} | DOWN at ${dn:.2f}"
                
                lines.append(f"{price_info}\n")
                lines.append("Step 3: 📈 *Select Direction*")
                lines.append("Finalize your position:")
            
        return "\n".join(lines)

    async def _show_manual_trade_menu(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        text = self._get_manual_text(step=1)
        inline_kb = self._get_betting_inline()
        
        # ✅ FIX: Keep main menu buttons instead of hiding them
        reply_kb = self._get_kb()
        
        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=inline_kb)
            except:
                pass
        else:
            # Show the main menu at bottom to ensure no "duplicate" $1, $2 buttons
            await update.message.reply_text("🎯 *Manual Trade Mode*", 
                                           parse_mode="Markdown",
                                           reply_markup=reply_kb)
            
            query_msg = await update.message.reply_text(text, parse_mode="Markdown", reply_markup=inline_kb)
            ctx.user_data["menu_msg_id"] = query_msg.message_id
            # Reset selection state
            ctx.user_data["manual_coin"] = None
            ctx.user_data["manual_amount"] = None

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
                inline_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="refresh_live")]])
                try:
                    await query.edit_message_text(content, parse_mode="Markdown", reply_markup=inline_kb)
                except Exception:
                    pass # ignore "message is not modified"
            return

        # ── Refresh Health Status ──
        if data == "refresh_health":
            if not self.get_health or not self.get_live_state:
                return
            h = self.get_health()
            states = self.get_live_state()
            now = time.time()
            status = "🟢 OK" if h.get("ok") else "🔴 ERROR"
            lines = [
                f"*OVERALL*  → {status}",
                f"*UPTIME*   → {h.get('uptime', '0h')}",
                f"*REDEEM*   → {h.get('redeem_status', 'Idle')}",
                f"*POL BAL*  → {h.get('pol_balance', 0.0):.2f} POL",
                f"*LOG SIZE* → {h.get('log_size', '0 KB')}",
                "──────────────────",
                "📡 *DATA FEED STATUS:*"
            ]
            for coin in self.active_coins:
                st = states.get(coin, {})
                diff = int(now - st.get("last_msg_time", 0.0)) if st.get("last_msg_time") else 999
                emoji = "🟢" if diff < 30 else "🟡" if diff < 60 else "🔴"
                label = "OK" if diff < 30 else "LAG" if diff < 60 else "STALE"
                lines.append(f"{emoji} *{coin:<3}* [{label}] ({diff}s)")
                lines.append(f"      📈 *{st.get('up_ask',0):.3f}* | 📉 *{st.get('down_ask',0):.3f}*")
                lines.append("      ┈┈┈┈┈┈┈┈┈┈")
            
            inline_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh Status", callback_data="refresh_health")]])
            try:
                await query.edit_message_text(_box("🏥 SYSTEM HEALTH", lines), parse_mode="Markdown", reply_markup=inline_kb)
            except:
                pass
            return

        # ── Step 1: Coin Selection ──────────────
        if data.startswith("mcoin_"):
            coin = data.replace("mcoin_", "")
            ctx.user_data["manual_coin"] = coin
            text = self._get_manual_text(step=2, coin=coin)
            inline_kb = self._get_betting_inline(sel_coin=coin)
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=inline_kb)

        # ── Step 2: Amount Selection ────────────
        elif data.startswith("mamt_"):
            amount = float(data.replace("mamt_", ""))
            ctx.user_data["manual_amount"] = amount
            coin = ctx.user_data.get("manual_coin", "BTC")
            text = self._get_manual_text(step=3, coin=coin, amount=amount)
            inline_kb = self._get_betting_inline(sel_coin=coin, sel_amount=amount)
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=inline_kb)

        # ── Step 3: Direction Selection → Place Trade ──
        elif data.startswith("mdir_"):
            parts = data.replace("mdir_", "").split("_")
            coin, direction = parts[0], parts[1]
            amount = ctx.user_data.get("manual_amount", 1.0)
            
            if self.on_manual_bet:
                result = self.on_manual_bet(coin, direction, amount)
                # Final display
                header = self._manual_header(coin)
                text = (
                    f"{header}\n"
                    f"Step 1: 🪙 *Coin Select* → {coin} ✅\n"
                    f"Step 2: 💰 *Amount*      → ${amount:.0f} ✅\n"
                    f"Step 3: 📈 *Direction*   → {'⬆️ UP' if direction=='YES' else '⬇️ DOWN'} ✅\n\n"
                    f"🚀 *Result:* {result}"
                )
                await query.edit_message_text(text, parse_mode="Markdown")
                # ✅ Restore bottom menu after trade is placed
                await self._app.bot.send_message(chat_id=CHAT_ID, text="🔄 *Menu Restored*", 
                                                 parse_mode="Markdown", reply_markup=self._get_kb())
            else:
                await query.edit_message_text("⚠️ Bot not ready for manual trade.")

        elif data == "mback_coins":
            await self._show_manual_trade_menu(update, ctx)

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
            "⏸ Stop":        self._cmd_stop,
            "▶ Start":       self._cmd_stop,
            "▶ Resume":      self._cmd_stop,
            "📉 Trend":      self._cmd_trend,
            "🎯 Manual Bet": self._cmd_manual_bet,
            "🔄 Refresh":    self._cmd_start,
            "🏥 Health":     self._cmd_health,
            "⌨️ Hide":       self._cmd_hide,
            "⌨️ Hide Menu":  self._cmd_hide,
        }
        if text in button_map:
            await button_map[text](update, ctx)
            return

        # ── Manual Bet Sub-Menu Handlers ─────────────────────────
        if text == "🔙 Back":
            # Return to main 3x3 menu
            await update.message.reply_text("🔄 Returning to menu...", reply_markup=self._get_kb())
            return await self._cmd_start(update, ctx)

        # ❌ REMOVED: Duplicate $1, $2, $3, $5 logic from _on_message 
        # (Users should use the Inline buttons for clean multi-step flow)

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
            # Redirect to manual bet menu
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
        
        # ── Visual Streak Analysis ──
        # If betting UP (YES), the previous streak was DOWN (red)
        # If betting DOWN (NO), the previous streak was UP (green)
        streak_emoji = "🔴🔴🔴" if direction == "YES" else "🟢🟢🟢"
        
        # ── Market Context ──
        price_str = "???"
        if self._bot.get_live_state:
            st = self._bot.get_live_state().get(coin, {})
            p = st.get("up_ask" if direction == "YES" else "down_ask", 0.0)
            price_str = f"${p:.2f}"
            
        self.send(_box(f"🎯 SIGNAL ALERT: {coin}", [
            f"SIDE   → {emoji} {arrow}",
            f"BET    → ${amount:.0f} (L{step+1})",
            f"STREAK → {streak_emoji}",
            f"PRICE  → {price_str}",
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
