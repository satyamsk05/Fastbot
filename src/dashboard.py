"""
Phase 06: Visual Overhaul (Terminal & Rich) — Scroll Fix
══════════════════════════════════════════════════════
A high-fidelity terminal dashboard using 'rich.live'.
Features:
- Fixed position rendering (no scrolling)
- Thread-safe internal logging panel
- Sentiment-aware color coding
- Real-time 3-candle trend visualizer [ 🟢 🟢 🔴 ]
══════════════════════════════════════════════════════
"""
import time
import threading
from typing import Dict, List, Optional
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text

from strategy import BET_SEQUENCE, STREAK_THRESHOLD

COINS = ["BTC", "ETH", "SOL", "XRP"]

class Dashboard:
    """Premium Terminal Dashboard using Rich Live (Scroll-Free)."""

    def __init__(self, coins: list = None):
        self.start_time = time.time()
        self.coins = [c.upper() for c in (coins or COINS)]
        self._log_buffer: List[str] = []
        self._lock = threading.Lock()
        self.console = Console()
        self._live: Optional[Live] = None

    def log(self, msg: str, style: str = "white"):
        """Add a message to the internal system log panel."""
        ts = time.strftime("%H:%M:%S")
        with self._lock:
            self._log_buffer.append(f"[{ts}] {msg}")
            self._log_buffer = self._log_buffer[-10:] # Keep last 10

    def log_error(self, msg: str):
        self.log(f"✗ {msg}", style="bold red")

    def live_context(self):
        """Returns the Live context manager for use in 'with' blocks."""
        # Note: We don't use the refresh_per_second here because main loop calls render()
        self._live = Live(None, console=self.console, refresh_per_second=4, screen=False)
        return self._live

    def render(self, market_states: Dict, mg_steps: Dict,
               pending: Dict, trade_log: List, wallet_bal: float,
               dry_run: bool = True, candle_history: Dict = None):
        """Updates the Live display with the latest layout."""
        grid = self._build_layout(market_states, mg_steps, pending, 
                                  trade_log, wallet_bal, dry_run, candle_history)
        if self._live and self._live.is_started:
            self._live.update(grid)
        else:
            # Fallback for when not in Live context
            self.console.clear()
            self.console.print(grid)

    # ── builders ──────────────────────────────────────────────────────────────
    def _build_layout(self, market_states, mg_steps, pending, trade_log,
                      wallet_bal, dry_run, candle_history) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main", size=10),
            Layout(name="bottom", size=8)
        )

        # 1. Header
        runtime = self._fmt_time(time.time() - self.start_time)
        mode = "[bold yellow]DRY RUN[/]" if dry_run else "[bold red]LIVE[/]"
        header_text = Text.assemble(
            (f" 🛰️  POLYMARKET COMMAND CENTER ", "bold cyan"),
            f" | Uptime: [green]{runtime}[/] | Mode: {mode} | ",
            (f"Wallet: [bold green]${wallet_bal:,.2f} USDC[/]", "")
        )
        layout["header"].update(Panel(header_text, border_style="cyan"))

        # 2. Main Coin Table
        table = Table(box=None, expand=True, padding=(0, 1))
        table.add_column("Coin", justify="left", style="bold cyan", width=6)
        table.add_column("Trend", justify="center", width=8)
        table.add_column("Timer", justify="center", width=7)
        table.add_column("UP/DN", justify="center", width=15)
        table.add_column("Fav (Conf)", justify="center", width=12)
        table.add_column("MG Step", justify="left")
        table.add_column("Status", justify="left")

        for coin in self.coins:
            ms = (market_states or {}).get(coin, {})
            step = (mg_steps or {}).get(coin, 0)
            pen = (pending or {}).get(coin)
            history = (candle_history or {}).get(coin, [])
            
            # Trend Visualizer
            trend_str = ""
            for c in history[-3:]:
                trend_str += "🟢" if c.get("dir") == "UP" else "🔴"
            if not trend_str: trend_str = "[grey37]- - -[/]"

            # Timer color
            sec_left = ms.get("seconds_till_end", 900)
            t_clr = "red" if sec_left < 120 else "yellow" if sec_left < 300 else "green"
            timer_str = f"[{t_clr}]{self._fmt_time(sec_left)}[/]"

            # Prices
            up = ms.get("up_ask", 0.0)
            dn = ms.get("down_ask", 0.0)
            price_str = f"[green]{up:.3f}[/] / [red]{dn:.3f}[/]"

            # Fav/Conf
            fav = "UP" if up > dn else "DOWN"
            fav_clr = "green" if fav == "UP" else "red"
            conf = abs(up - dn)
            fav_str = f"[{fav_clr}]{fav}[/] ({conf:.3f})"

            # MG Progress
            progress = ""
            for i in range(len(BET_SEQUENCE)):
                if i < step: progress += "[green]■[/]"
                elif i == step: progress += "[bold yellow]▶[/]"
                else: progress += "[grey37]□[/]"
            
            bet_amt = BET_SEQUENCE[step] if step < len(BET_SEQUENCE) else BET_SEQUENCE[-1]
            mg_str = f"{progress} [dim]L{step+1}[/]"

            # Status / Pending
            if pen:
                dir_emoji = "⬆️" if pen['direction'] == "YES" else "⬇️"
                status_str = f"[bold white on blue] PENDING: {dir_emoji} {pen['direction']} ${pen['amount']:.0f} [/]"
            else:
                status_str = "[dim]Waiting...[/]"

            table.add_row(coin, trend_str, timer_str, price_str, fav_str, mg_str, status_str)

        layout["main"].update(Panel(table, title="[bold white]Market Activity[/]", border_style="grey37"))

        # 3. Bottom (Trades & Logs)
        log_layout = Layout()
        log_layout.split_row(
            Layout(name="trades", ratio=2),
            Layout(name="logs", ratio=1)
        )

        # Recent Trades Panel
        trades_text = Text()
        if trade_log:
            for t in list(reversed(trade_log))[:4]:
                pnl = t.get("pnl", 0)
                clr = "green" if pnl >= 0 else "red"
                sign = "+" if pnl >= 0 else ""
                dir_lbl = "UP" if t['direction'] == "YES" else "DOWN"
                trades_text.append(f"• {t['coin']:>3} ", style="bold cyan")
                trades_text.append(f"{dir_lbl:<5} ", style="dim")
                trades_text.append(f"{'WIN' if t.get('won') else 'LOSS'} ", style=clr)
                trades_text.append(f"{sign}${pnl:.2f}\n", style=f"bold {clr}")
        else:
            trades_text.append("Waiting for first trade...", style="dim")
        
        log_layout["trades"].update(Panel(trades_text, title="[bold]Recent Trades[/]", border_style="grey37"))

        # System Logic Panel
        sys_text = Text()
        with self._lock:
            for line in self._log_buffer:
                sys_text.append(f"{line}\n", style="dim")
        
        log_layout["logs"].update(Panel(sys_text, title="[bold]System Log[/]", border_style="grey37"))

        layout["bottom"].update(log_layout)
        return layout

    @staticmethod
    def _fmt_time(secs: float) -> str:
        s = int(secs)
        if s >= 3600:
            return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"
        return f"{s//60:02d}:{s%60:02d}"
