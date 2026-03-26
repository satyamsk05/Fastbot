"""
Terminal Dashboard
4coinsbot's rich terminal UI + tredebot's Martingale state overlay.
Shows: orderbook prices, streak state, martingale step, pending bets.
"""
import time
from typing import Dict, List, Optional

from strategy import BET_SEQUENCE, STREAK_THRESHOLD

COINS = ["BTC", "ETH", "SOL", "XRP"]


class Dashboard:
    """Multi-coin terminal dashboard with streak/martingale overlay."""

    def __init__(self, width: int = 160, coins: list = None):
        self.width = width
        self.start_time = time.time()
        self.coins = [c.upper() for c in (coins or COINS)]
        self._error_log: List[str] = []

    # ── public ───────────────────────────────────────────────────────────────
    def render(self, market_states: Dict, mg_steps: Dict,
               pending: Dict, trade_log: List, wallet_bal: float,
               dry_run: bool = True):
        print("\033[2J\033[H", end="")   # clear screen
        lines = self._build(market_states, mg_steps, pending,
                            trade_log, wallet_bal, dry_run)
        print(lines, end="", flush=True)

    def log_error(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self._error_log.append(f"[{ts}] ✗ {msg[:70]}")
        self._error_log = self._error_log[-8:]

    # ── build ─────────────────────────────────────────────────────────────────
    def _build(self, market_states, mg_steps, pending, trade_log,
               wallet_bal, dry_run) -> str:
        W = self.width
        out = []

        runtime = self._fmt_time(time.time() - self.start_time)
        mode_tag = "  ~ DRY RUN ~  " if dry_run else "  LIVE TRADING  "
        out.append("=" * W)
        out.append(
            (f"  Polymarket Streak Bot  |  {runtime}  |  {mode_tag}"
             f"  Balance: ${wallet_bal:,.2f} USDC").center(W)
        )
        out.append("=" * W)
        out.append("")

        ladder = "  ".join(f"L{i+1}=${b}" for i, b in enumerate(BET_SEQUENCE))
        out.append(f"  Martingale Ladder: {ladder}   |   Signal: {STREAK_THRESHOLD}-streak reversal")
        out.append("")

        out.append(f"+{'-'*(W-2)}+")
        out.append(f"|{'  COIN STATUS':^{W-2}}|")
        out.append(f"+{'-'*(W-2)}+")

        for coin in self.coins:
            ms   = (market_states or {}).get(coin, {})
            step = (mg_steps or {}).get(coin, 0)
            pen  = (pending  or {}).get(coin)
            self._coin_row(out, coin, ms, step, pen, W)
            out.append(f"|{'-'*(W-2)}|")

        out.append(f"+{'-'*(W-2)}+")
        out.append("")

        out.append("Recent Trades:")
        if trade_log:
            for t in list(reversed(trade_log))[:6]:
                pnl  = t.get("pnl", 0)
                sign = "+" if pnl >= 0 else ""
                clr  = "\033[92m" if pnl >= 0 else "\033[91m"
                rst  = "\033[0m"
                out.append(
                    f"  [{t['coin']:>3}]  {'UP YES' if t['direction']=='YES' else 'DN NO ':>6}"
                    f"  {'WIN ' if t.get('won') else 'LOSS'}  "
                    f"{clr}{sign}${pnl:.2f}{rst}"
                )
        else:
            out.append("  (none yet)")
        out.append("")

        if self._error_log:
            out.append("Errors:")
            for e in self._error_log:
                out.append(f"  {e}")
            out.append("")

        out.append("-" * W)
        out.append("[Q] Quit  |  [E] Emergency Stop  |  Ctrl+C Stop".center(W))
        return "\n".join(out)

    def _coin_row(self, out, coin, ms, step, pending, W):
        up_ask   = ms.get("up_ask", 0.0)
        dn_ask   = ms.get("down_ask", 0.0)
        sec_left = ms.get("seconds_till_end", 900)
        slug     = ms.get("market_slug", "---")
        conf     = abs(up_ask - dn_ask)

        fav = "UP  " if up_ask > dn_ask else "DOWN"

        if sec_left < 60:
            t_clr = "\033[91m"
        elif sec_left < 180:
            t_clr = "\033[93m"
        else:
            t_clr = "\033[0m"
        rst = "\033[0m"

        ladder_bar = ""
        for i in range(len(BET_SEQUENCE)):
            if i < step:
                ladder_bar += "#"
            elif i == step:
                ladder_bar += ">"
            else:
                ladder_bar += "."
        bet_now = BET_SEQUENCE[step] if step < len(BET_SEQUENCE) else BET_SEQUENCE[-1]

        if pending:
            pen_str = f"  PENDING: {'YES' if pending['direction']=='YES' else 'NO '} ${pending['amount']:.0f}"
        else:
            pen_str = "  waiting for signal"

        slug_short = slug.split("-")[-1] if slug else "---"

        row1 = (
            f"|  [{coin:>3}] {slug_short:<12}"
            f"  Timer: {t_clr}{self._fmt_time(sec_left)}{rst}"
            f"  UP:{up_ask:.3f}  DN:{dn_ask:.3f}  Fav:{fav}"
            f"  Conf:{conf:.3f}"
        )
        row2 = (
            f"|     MG: [{ladder_bar}] L{step+1} Bet=${bet_now}"
            f"{pen_str}"
        )
        out.append(row1)
        out.append(row2)

    @staticmethod
    def _fmt_time(secs: float) -> str:
        s = int(secs)
        if s >= 3600:
            return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"
        return f"{s//60:02d}:{s%60:02d}"
