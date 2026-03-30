"""
Phase 06: Visual Overhaul — Refined Terminal Dashboard
══════════════════════════════════════════════════════
A high-fidelity terminal dashboard using 'rich.live'.
Features:
- Fixed position rendering (no scrolling)
- Thread-safe internal logging panel
- Sentiment-aware color coding
- Clean, data-dense layout
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
from rich.columns import Columns

from strategy import BET_SEQUENCE

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
            self._log_buffer.append(f"[dim]{ts}[/] {msg}")
            self._log_buffer = self._log_buffer[-8:]

    def log_error(self, msg: str):
        self.log(f"[red]✗ {msg}[/]")

    def live_context(self):
        """Returns the Live context manager for use in 'with' blocks."""
        self._live = Live(None, console=self.console, refresh_per_second=4, screen=False)
        return self._live

    def render(self, market_states: Dict, mg_steps: Dict,
               pending: Dict, trade_log: List, wallet_bal: float,
               dry_run: bool = True, candle_history: Dict = None):
        """Updates the Live display with the latest layout."""
        grid = self._build_layout(market_states, mg_steps, pending,
                                  trade_log, wallet_bal, dry_run)
        if self._live and self._live.is_started:
            self._live.update(grid)
        else:
            self.console.clear()
            self.console.print(grid)

    # ── builders ──────────────────────────────────────────────────────────────
    def _build_layout(self, market_states, mg_steps, pending, trade_log,
                      wallet_bal, dry_run) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main", size=9),
            Layout(name="bottom", size=9)
        )

        # ── 1. Header Bar ─────────────────────────────────────────────────
        runtime = self._fmt_time(time.time() - self.start_time)
        mode = "[bold yellow]DRY[/]" if dry_run else "[bold red]LIVE[/]"
        in_bets = sum(
            p.get("amount", 0) for p in (pending or {}).values() if p
        )
        header = Text()
        header.append(" ◆ ", style="bold cyan")
        header.append("POLYMARKET", style="bold white")
        header.append("  │  ", style="dim")
        header.append(f"⏱ {runtime}", style="green")
        header.append("  │  ", style="dim")
        header.append(f"Mode: ", style="dim")
        header.append_text(Text.from_markup(mode))
        header.append("  │  ", style="dim")
        header.append(f"💰 ${wallet_bal:,.2f}", style="bold green")
        header.append("  │  ", style="dim")
        header.append(f"🔒 ${in_bets:,.2f}", style="yellow")

        layout["header"].update(Panel(header, border_style="cyan", padding=(0, 1)))

        # ── 2. Market Table (No Trend column) ─────────────────────────────
        table = Table(box=None, expand=True, padding=(0, 1), pad_edge=True)
        table.add_column("COIN", justify="left", style="bold cyan", width=5)
        table.add_column("⏱ TIME", justify="center", width=7)
        table.add_column("⬆ UP", justify="right", style="green", width=7)
        table.add_column("⬇ DN", justify="right", style="red", width=7)
        table.add_column("FAV", justify="center", width=8)
        table.add_column("MG", justify="left", width=16)
        table.add_column("POSITION", justify="left", min_width=22)

        for coin in self.coins:
            ms = (market_states or {}).get(coin, {})
            step = (mg_steps or {}).get(coin, 0)
            pen = (pending or {}).get(coin)

            # Timer with color
            sec_left = ms.get("seconds_till_end", 900)
            t_clr = "red bold" if sec_left < 60 else "red" if sec_left < 120 else "yellow" if sec_left < 300 else "green"
            timer_str = f"[{t_clr}]{self._fmt_timer(sec_left)}[/]"

            # Prices
            up = ms.get("up_ask", 0.0)
            dn = ms.get("down_ask", 0.0)
            up_str = f"{up:.3f}" if up > 0 else "[dim]--[/]"
            dn_str = f"{dn:.3f}" if dn > 0 else "[dim]--[/]"

            # Favourite
            if up > 0 and dn > 0:
                conf = abs(up - dn)
                if up > dn:
                    fav_str = f"[green]▲[/] [dim]{conf:.2f}[/]"
                else:
                    fav_str = f"[red]▼[/] [dim]{conf:.2f}[/]"
            else:
                fav_str = "[dim]—[/]"

            # MG Progress Bar
            blocks = []
            for i in range(len(BET_SEQUENCE)):
                if i < step:
                    blocks.append("[red]●[/]")
                elif i == step:
                    blocks.append("[bold yellow]◉[/]")
                else:
                    blocks.append("[dim]○[/]")
            bet_amt = BET_SEQUENCE[min(step, len(BET_SEQUENCE) - 1)]
            mg_str = f"{''.join(blocks)} [dim]${bet_amt}[/]"

            # Position
            if pen:
                d = pen["direction"]
                arrow = "⬆" if d == "YES" else "⬇"
                clr = "green" if d == "YES" else "red"
                pos_str = f"[bold {clr}]{arrow} ${pen['amount']:.0f}[/] [dim]@ {pen['price']:.3f}[/]"
            else:
                pos_str = "[dim]· idle[/]"

            table.add_row(coin, timer_str, up_str, dn_str, fav_str, mg_str, pos_str)

        layout["main"].update(Panel(table, title="[bold white]MARKETS[/]", border_style="grey37", padding=(0, 0)))

        # ── 3. Bottom: Trades + Logs ──────────────────────────────────────
        bottom_layout = Layout()
        bottom_layout.split_row(
            Layout(name="trades", ratio=3),
            Layout(name="logs", ratio=2)
        )

        # Recent Trades
        trades_text = Text()
        if trade_log:
            for t in list(reversed(trade_log))[:5]:
                pnl = t.get("pnl", 0)
                won = t.get("won", False)
                clr = "green" if won else "red"
                sign = "+" if pnl >= 0 else ""
                d_lbl = "UP" if t["direction"] == "YES" else "DN"
                icon = "✓" if won else "✗"
                trades_text.append(f" {icon} ", style=f"bold {clr}")
                trades_text.append(f"{t['coin']:>3} ", style="bold")
                trades_text.append(f"{d_lbl:<3} ", style="dim")
                trades_text.append(f"${t.get('amount',0):<5.0f} ", style="dim")
                trades_text.append(f"{sign}${pnl:.2f}\n", style=f"bold {clr}")
        else:
            trades_text.append(" Waiting for trades...", style="dim italic")

        bottom_layout["trades"].update(
            Panel(trades_text, title="[bold]TRADES[/]", border_style="grey37", padding=(0, 1))
        )

        # System Log
        sys_text = Text()
        with self._lock:
            for line in self._log_buffer:
                sys_text.append_text(Text.from_markup(f" {line}\n"))

        bottom_layout["logs"].update(
            Panel(sys_text, title="[bold]LOG[/]", border_style="grey37", padding=(0, 1))
        )

        layout["bottom"].update(bottom_layout)
        return layout

    @staticmethod
    def _fmt_time(secs: float) -> str:
        s = int(secs)
        if s >= 3600:
            return f"{s // 3600}h{(s % 3600) // 60:02d}m"
        return f"{s // 60}m{s % 60:02d}s"

    @staticmethod
    def _fmt_timer(secs: int) -> str:
        """MM:SS countdown format."""
        m, s = divmod(max(0, secs), 60)
        return f"{m:02d}:{s:02d}"
