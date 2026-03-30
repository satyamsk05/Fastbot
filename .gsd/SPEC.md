# SPEC.md — Project Specification

> **Status**: `FINALIZED`

## Vision

A high-frequency, multi-market trading bot for Polymarket that leverages historical price streaks (3+ same-direction closes) and a Martingale betting strategy to capture reversal points. The bot aims for zero-sequential-lag execution and a premium interactive experience via Telegram.

## Goals

1.  **Low Latency Execution**: All 4 markets (BTC/ETH/SOL/XRP) processed in parallel.
2.  **Interactive Control**: 3x3 Telegram menu for full bot management without terminal access.
3.  **Risk Management**: Strictly defined Martingale ladder ($3 → $60) with automatic resets.
4.  **Data Integrity**: Persistent history of candles and trades for session-over-session consistency.

## Non-Goals (Out of Scope)

-   General-purpose prediction market trading (restricted to 15-min price markets).
-   Multi-account support in this version.
-   Direct wallet-to-wallet transfers (restricted to Polymarket CLOB interactions).

## Users

-   Traders who want automated price-action strategies on Polymarket.
-   Users who prefer mobile management (Telegram) for their trading bots.

## Success Criteria

-   [ ] **Zero Sequential Delay**: Proved by parallel coin processing logs.
-   [ ] **Accurate PnL Reporting**: Win/Loss results reflect the difference between entry price and payout.
-   [ ] **Interactive Manual Trade**: Fully functional multi-step Telegram flow for $N bets.
-   [ ] **Self-Recovery**: Auto-restart capability (implemented in `run.py`).

## Technical Constraints

-   Polymarket CLOB API rate limits.
-   Polygon network RPC availability for balance checks.
-   Must run within a Python 3.x environment.
