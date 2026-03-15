"""Risk management module - protects capital and enforces trading rules."""

import logging
import time

from config import BotConfig
from models import Side, Signal

logger = logging.getLogger("scalper")


class RiskManager:
    """Enforces risk management rules before and during trades."""

    def __init__(self, config: BotConfig, initial_balance: float):
        self.config = config
        self.daily_starting_balance = initial_balance
        self.daily_pnl: float = 0.0
        self.total_pnl: float = 0.0
        self.consecutive_losses: int = 0
        self.last_trade_time: float = 0.0
        self.in_cooldown: bool = False
        self.cooldown_until: float = 0.0
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.losing_trades: int = 0

    def can_open_trade(self, balance: float, signal: Signal) -> tuple[bool, str]:
        """Check if a new trade is allowed under risk rules."""
        now = time.time()

        # Check 1: daily loss limit
        max_loss = self.daily_starting_balance * self.config.max_daily_loss_pct
        if self.daily_pnl <= -max_loss:
            return False, f"Daily loss limit reached (${self.daily_pnl:.2f} / -${max_loss:.2f})"

        # Check 2: consecutive losses cooldown
        if self.in_cooldown and now < self.cooldown_until:
            remaining = int(self.cooldown_until - now)
            return False, f"Cooldown active ({remaining}s remaining)"

        # Reset cooldown if expired
        if self.in_cooldown and now >= self.cooldown_until:
            self.in_cooldown = False
            self.consecutive_losses = 0
            logger.info("Cooldown expired. Resuming trading.")

        # Check 3: minimum time between trades
        if now - self.last_trade_time < self.config.min_time_between_trades_sec:
            remaining = int(self.config.min_time_between_trades_sec - (now - self.last_trade_time))
            return False, f"Min time between trades ({remaining}s remaining)"

        # Check 4: balance too low
        if balance < 1.0:
            return False, f"Balance too low (${balance:.2f})"

        # Check 5: minimum notional check
        min_margin = 5.0 / self.config.leverage  # $5 min notional
        if balance * self.config.max_position_pct < min_margin:
            return False, f"Insufficient margin for minimum notional"

        return True, "ok"

    def compute_position_size(self, balance: float) -> float:
        """
        Compute the margin to use for a new position.
        v4.0: Progressive sizing — reduce after consecutive losses.
        - 0 consecutive losses: 100% of balance
        - 1 loss: 80%
        - 2 losses: 60%
        - 3 losses: 40%
        - 4+ losses: cooldown kicks in
        """
        base_pct = self.config.max_position_pct

        # Reduce position size proportionally to consecutive losses
        if self.consecutive_losses > 0:
            reduction = 0.2 * self.consecutive_losses  # 20% per loss
            size_multiplier = max(0.4, 1.0 - reduction)
            margin = balance * base_pct * size_multiplier
            logger.info(
                f"Progressive sizing: {size_multiplier*100:.0f}% "
                f"({self.consecutive_losses} consecutive losses)"
            )
        else:
            margin = balance * base_pct

        return margin

    def compute_stop_take(self, entry_price: float, side: Side, atr_pct: float = 0.0) -> tuple[float, float]:
        """
        Compute stop-loss and take-profit prices.
        v4.0: Wider SL minimums to avoid noise-triggered stops.
        Uses ATR-based dynamic SL/TP when available.
        """
        if atr_pct > 0:
            # Dynamic SL/TP based on ATR — adapts to current volatility
            dynamic_sl = atr_pct * 2.0   # v4.0: was 1.5, now 2.0 ATR for more room
            dynamic_tp = atr_pct * 3.0   # v4.0: was 2.5, now 3.0 ATR — R:R = 1.5
            # Clamp within safe bounds — wider minimums
            sl_pct = max(0.003, min(dynamic_sl, 0.008))   # v4.0: min 0.3% (was 0.15%)
            tp_pct = max(0.004, min(dynamic_tp, 0.012))   # v4.0: min 0.4% (was 0.25%)
        else:
            sl_pct = max(self.config.stop_loss_pct, 0.003)  # floor at 0.3%
            tp_pct = max(self.config.take_profit_pct, 0.004)

        if side == Side.LONG:
            sl = entry_price * (1 - sl_pct)
            tp = entry_price * (1 + tp_pct)
        else:
            sl = entry_price * (1 + sl_pct)
            tp = entry_price * (1 - tp_pct)

        logger.info(f"SL/TP set: SL={sl_pct*100:.2f}% TP={tp_pct*100:.2f}% (ATR-based: {atr_pct > 0})")
        return sl, tp

    def record_trade_result(self, pnl: float) -> None:
        """Record the result of a completed trade."""
        self.daily_pnl += pnl
        self.total_pnl += pnl
        self.total_trades += 1
        self.last_trade_time = time.time()

        if pnl < 0:
            self.consecutive_losses += 1
            self.losing_trades += 1
            logger.warning(
                f"Loss #{self.consecutive_losses}: ${pnl:.4f} | "
                f"Daily P&L: ${self.daily_pnl:.4f}"
            )

            if self.consecutive_losses >= self.config.max_consecutive_losses:
                self.in_cooldown = True
                self.cooldown_until = time.time() + self.config.cooldown_seconds
                logger.warning(
                    f"Max consecutive losses ({self.config.max_consecutive_losses}) reached! "
                    f"Cooldown for {self.config.cooldown_seconds}s"
                )
        else:
            self.consecutive_losses = 0
            self.in_cooldown = False
            self.winning_trades += 1
            logger.info(
                f"Win! +${pnl:.4f} | Daily P&L: ${self.daily_pnl:.4f}"
            )

    def reset_daily(self, balance: float) -> None:
        """Reset daily tracking (call at UTC midnight)."""
        logger.info(
            f"Daily reset. Previous day P&L: ${self.daily_pnl:.4f} | "
            f"New starting balance: ${balance:.2f}"
        )
        self.daily_starting_balance = balance
        self.daily_pnl = 0.0

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    def get_stats(self) -> dict:
        """Get current risk manager statistics."""
        return {
            "total_trades": self.total_trades,
            "winning": self.winning_trades,
            "losing": self.losing_trades,
            "win_rate": f"{self.win_rate * 100:.1f}%",
            "daily_pnl": self.daily_pnl,
            "total_pnl": self.total_pnl,
            "consecutive_losses": self.consecutive_losses,
            "in_cooldown": self.in_cooldown,
        }
