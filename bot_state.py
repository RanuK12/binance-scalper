"""Shared bot state via JSON file for dashboard communication."""

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("scalper")

STATE_FILE = "state.json"

# In-memory trade history for dashboard (last 50 trades)
_trade_history: list[dict] = []
# In-memory equity snapshots for chart (last 200 points)
_equity_history: list[dict] = []
# Bot start time
_start_time: float = time.time()


def init_state():
    """Initialize state tracking."""
    global _start_time
    _start_time = time.time()


def add_trade_to_history(record) -> None:
    """Add a completed trade to the in-memory history."""
    global _trade_history
    _trade_history.append({
        "time": record.timestamp,
        "side": record.side.upper(),
        "entry": record.entry_price,
        "exit": record.exit_price,
        "qty": record.quantity,
        "lev": record.leverage,
        "pnl": round(record.pnl, 6),
        "pnl_pct": round(record.pnl_pct * 100, 2),
        "reason": record.exit_reason,
        "duration": round(record.duration_sec, 1),
        "score": round(record.score, 1),
    })
    if len(_trade_history) > 50:
        _trade_history.pop(0)


def add_equity_snapshot(equity: float) -> None:
    """Add an equity snapshot for the chart."""
    global _equity_history
    _equity_history.append({
        "t": time.time(),
        "eq": round(equity, 4),
    })
    if len(_equity_history) > 200:
        _equity_history.pop(0)


def save_state(data: dict) -> None:
    """Save bot state to JSON file atomically."""
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logger.debug(f"Could not save state: {e}")


def load_state() -> dict:
    """Load bot state from JSON file."""
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def build_state(
    config,
    balance: float,
    equity: float,
    position_manager,
    risk_manager,
    last_price: float,
    indicators: dict | None,
    last_scores: tuple[float, float],
    status: str = "Esperando senal...",
    score_breakdown: dict | None = None,
    market_analysis: dict | None = None,
    learner_stats: dict | None = None,
    current_leverage: int | None = None,
) -> dict:
    """Build the full state dict for the dashboard."""
    pos_info = position_manager.get_position_info()
    long_score, short_score = last_scores

    # Uptime
    uptime_sec = time.time() - _start_time

    # Cooldown remaining
    cooldown_remaining = 0
    if risk_manager.in_cooldown:
        cooldown_remaining = max(0, int(risk_manager.cooldown_until - time.time()))

    # Daily loss remaining
    max_daily = risk_manager.daily_starting_balance * config.max_daily_loss_pct
    daily_loss_remaining = max(0, max_daily + risk_manager.daily_pnl)

    state = {
        "timestamp": time.time(),
        "status": status,
        "balance": balance,
        "equity": equity,
        "price": last_price,
        "symbol": config.symbol,
        "leverage": current_leverage or config.leverage,
        "max_leverage": config.max_leverage,
        "sl_pct": config.stop_loss_pct * 100,
        "tp_pct": config.take_profit_pct * 100,
        "daily_pnl": risk_manager.daily_pnl,
        "total_pnl": risk_manager.total_pnl,
        "total_trades": risk_manager.total_trades,
        "win_rate": risk_manager.win_rate * 100,
        "winning_trades": risk_manager.winning_trades,
        "losing_trades": risk_manager.losing_trades,
        "consecutive_losses": risk_manager.consecutive_losses,
        "in_cooldown": risk_manager.in_cooldown,
        "cooldown_remaining": cooldown_remaining,
        "daily_loss_remaining": round(daily_loss_remaining, 4),
        "max_daily_loss": round(max_daily, 4),
        "long_score": long_score,
        "short_score": short_score,
        "score_threshold": config.score_threshold_long,
        "score_breakdown": score_breakdown or {},
        "position": pos_info,
        "indicators": indicators or {},
        "dry_run": config.dry_run,
        "testnet": config.testnet,
        "uptime": uptime_sec,
        "trade_history": _trade_history[-20:],  # last 20 for dashboard
        "equity_history": _equity_history[-100:],  # last 100 for chart
        "market_analysis": market_analysis or {},
        "learner": learner_stats or {},
    }
    return state
