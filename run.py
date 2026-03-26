#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║   Polymarket Streak-Reversal Martingale Bot — Launcher   ║
║   Usage:  python run.py                                  ║
╚══════════════════════════════════════════════════════════╝
"""
import sys
import os

# ── Set working directory to project root ─────────────────────
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT_DIR)

# ── Add src/ to Python path so all imports work ───────────────
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# ── Now import and run the main bot ───────────────────────────
from main import main

if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            os.remove(os.path.join(ROOT_DIR, "data", "bot.pid"))
        except Exception:
            pass
