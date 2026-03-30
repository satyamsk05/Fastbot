# ROADMAP.md

> **Current Phase**: Phase 1: GSD Setup & Core Stability
> **Milestone**: v2.1 (Stability Release)

## Must-Haves (from SPEC)

- [x] Zero Sequential Delay (Parallel Processing)
- [x] Adaptive Martingale Ladder ($3 → $60)
- [ ] Refined PnL Reporting (Accurate entry-price payout)
- [ ] Interactive Manual Trade (Refined 3-step menu)

## Phases

### Phase 1: GSD Foundation & Stability ✅
**Status**: ✅ Completed
**Objective**: Transition project to GSD system for spec-driven development.
- [x] Initialize .gsd structure.
- [x] Map codebase (Architecture & Stack).
- [x] Finalize initial SPEC.md.

### Phase 2: Refined Calculations & Reporting ✅
**Status**: ✅ Completed
**Objective**: Address technical debt in trade resolution and PnL reporting.
- [x] Implement payout resolution based on entry-price.
- [x] Add session-over-session PnL persistence (Bet History).
- [x] Finalize premium interactive Manual Trade flow.

### Phase 3: Interactive Refinement ✅
**Status**: ✅ Completed
**Objective**: Finalize the premium Telegram experience.
- [x] Standardize 🟢/🔴 streak visualization in Telegram.
- [x] Implement dynamic Stop/Resume button with state feedback.
- [x] Update Signal alerts with emoji streaks and market context.

### Phase 4: Production Hardening ✅
**Status**: ✅ Completed
**Objective**: Hardening connectivity and reporting for 24/7 ops.
- [x] Implement WebSocket health monitoring (Watchdog).
- [x] Add auto-reconnect logic for stalled data feeds.
- [x] Implement /health command for live status reporting.
- [x] Add staleness checks to the main strategy loop.
