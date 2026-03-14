"""Logging configuration and trade journal."""

import csv
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from config import BotConfig
from models import TradeRecord


def setup_logging(config: BotConfig) -> logging.Logger:
    """Configure logging with console and file handlers."""
    logger = logging.getLogger("scalper")
    logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

    # Console handler with colored output
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s │ %(levelname)-7s │ %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)

    # File handler with rotation
    file_handler = RotatingFileHandler(
        "bot.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


def log_trade(record: TradeRecord, path: str) -> None:
    """Append a trade record to the CSV journal."""
    file_exists = os.path.exists(path)

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "timestamp", "side", "entry_price", "exit_price",
                "quantity", "leverage", "pnl", "pnl_pct",
                "exit_reason", "duration_sec", "score",
            ])
        writer.writerow([
            record.timestamp,
            record.side,
            record.entry_price,
            record.exit_price,
            record.quantity,
            record.leverage,
            round(record.pnl, 6),
            round(record.pnl_pct, 4),
            record.exit_reason,
            round(record.duration_sec, 1),
            round(record.score, 2),
        ])
