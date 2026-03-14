"""Utility functions for the scalping bot."""

import asyncio
import functools
import logging
import math
from datetime import datetime, timezone

logger = logging.getLogger("scalper")


def round_price(price: float, tick_size: float) -> float:
    """Round price to the nearest tick size."""
    if tick_size <= 0:
        return price
    precision = max(0, -int(math.log10(tick_size)))
    return round(round(price / tick_size) * tick_size, precision)


def round_quantity(qty: float, step_size: float) -> float:
    """Round quantity to the nearest step size."""
    if step_size <= 0:
        return qty
    precision = max(0, -int(math.log10(step_size)))
    return round(math.floor(qty / step_size) * step_size, precision)


def timestamp_to_dt(ts: float) -> datetime:
    """Convert Unix timestamp to datetime (UTC)."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def format_pnl(pnl: float) -> str:
    """Format P&L with color indicators."""
    if pnl > 0:
        return f"\033[92m+${pnl:.4f}\033[0m"  # green
    elif pnl < 0:
        return f"\033[91m-${abs(pnl):.4f}\033[0m"  # red
    return f"${pnl:.4f}"


def format_pct(pct: float) -> str:
    """Format percentage with color."""
    val = pct * 100
    if val > 0:
        return f"\033[92m+{val:.2f}%\033[0m"
    elif val < 0:
        return f"\033[91m{val:.2f}%\033[0m"
    return f"{val:.2f}%"


def retry_async(max_retries: int = 3, backoff: float = 1.0):
    """Decorator for retrying async functions with exponential backoff."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    wait_time = backoff * (2 ** attempt)
                    logger.warning(
                        f"{func.__name__} failed (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {wait_time:.1f}s..."
                    )
                    await asyncio.sleep(wait_time)
            raise last_exception
        return wrapper
    return decorator


def clear_screen() -> None:
    """Clear terminal screen."""
    print("\033[2J\033[H", end="")
