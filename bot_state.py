"""Shared bot state via JSON file for dashboard communication."""

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("scalper")

STATE_FILE = "state.json"


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
) -> dict:
    """Build the full state dict for the dashboard."""
    pos_info = position_manager.get_position_info()
    long_score, short_score = last_scores

    state = {
        "timestamp": time.time(),
        "status": status,
        "balance": balance,
        "equity": equity,
        "price": last_price,
        "symbol": config.symbol,
        "leverage": config.leverage,
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
        "long_score": long_score,
        "short_score": short_score,
        "score_threshold": config.score_threshold_long,
        "position": pos_info,
        "indicators": indicators or {},
        "dry_run": config.dry_run,
        "testnet": config.testnet,
    }
    return state
