"""
Self-Learning Module — Adaptive parameter tuning based on trade outcomes.
=========================================================================
Tracks trade context (indicators, score, leverage, market conditions) and
adjusts strategy parameters dynamically to improve win rate over time.

The learner does NOT modify the strategy code — it produces adjustment
multipliers that the strategy and risk manager read each cycle.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("scalper")

LEARNER_STATE_FILE = "learner_state.json"


@dataclass
class TradeContext:
    """Full context of a trade for learning."""
    timestamp: float
    side: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    leverage: int
    score: float
    exit_reason: str  # sl, tp, trailing
    duration_sec: float
    # Market conditions at entry
    volume_ratio: float = 1.0
    rsi: float = 50.0
    bb_width: float = 0.0
    bb_position: float = 0.5
    atr_pct: float = 0.0
    macd_histogram: float = 0.0
    orderbook_imbalance: float = 0.0
    htf_aligned: bool = False  # was the trade aligned with HTF trend?
    had_crossover: bool = False  # was there an EMA/MACD crossover?


@dataclass
class LearnerState:
    """Persistent learner state."""
    # Recent trade history for analysis (last 50)
    trade_contexts: list = field(default_factory=list)

    # Adaptive parameters — these are MULTIPLIERS applied to base config
    score_threshold_adj: float = 0.0      # added to base threshold
    leverage_multiplier: float = 1.0       # multiplied to computed leverage
    min_volume_ratio: float = 0.7          # minimum volume to trade
    require_strong_signal: bool = True     # require crossover or divergence
    confidence_floor: float = 0.0          # minimum confidence for leverage calc

    # Performance tracking
    rolling_win_rate: float = 0.5
    rolling_avg_pnl: float = 0.0
    total_learned_trades: int = 0
    last_adjustment_time: float = 0.0

    # Per-condition performance (how each indicator performs)
    indicator_win_rates: dict = field(default_factory=lambda: {
        "htf_aligned": {"wins": 0, "losses": 0},
        "htf_against": {"wins": 0, "losses": 0},
        "high_volume": {"wins": 0, "losses": 0},
        "low_volume": {"wins": 0, "losses": 0},
        "had_crossover": {"wins": 0, "losses": 0},
        "no_crossover": {"wins": 0, "losses": 0},
        "rsi_extreme": {"wins": 0, "losses": 0},
        "rsi_neutral": {"wins": 0, "losses": 0},
        "high_leverage": {"wins": 0, "losses": 0},
        "low_leverage": {"wins": 0, "losses": 0},
        "tight_bb": {"wins": 0, "losses": 0},
        "wide_bb": {"wins": 0, "losses": 0},
    })

    # Streak tracking
    current_streak: int = 0  # positive = winning, negative = losing
    max_losing_streak: int = 0
    recent_adjustments: list = field(default_factory=list)


class AdaptiveLearner:
    """
    Learns from trade outcomes and adjusts strategy parameters.

    Key principles:
    1. After EVERY trade, update rolling stats and per-condition performance
    2. Every N trades (5), recompute adaptive parameters
    3. When losing: raise threshold, reduce leverage, tighten filters
    4. When winning: gradually relax back toward baseline
    5. Track which conditions produce wins vs losses
    """

    ADJUSTMENT_INTERVAL = 5  # recalculate every 5 trades
    MAX_HISTORY = 50
    MIN_TRADES_FOR_LEARNING = 3  # start adjusting after 3 trades

    def __init__(self):
        self.state = LearnerState()
        self._load_state()

    def _load_state(self):
        """Load learner state from disk."""
        try:
            if os.path.exists(LEARNER_STATE_FILE):
                with open(LEARNER_STATE_FILE, "r") as f:
                    data = json.load(f)
                # Restore fields
                for key in [
                    "score_threshold_adj", "leverage_multiplier", "min_volume_ratio",
                    "require_strong_signal", "confidence_floor", "rolling_win_rate",
                    "rolling_avg_pnl", "total_learned_trades", "last_adjustment_time",
                    "current_streak", "max_losing_streak",
                ]:
                    if key in data:
                        setattr(self.state, key, data[key])

                if "indicator_win_rates" in data:
                    # Merge with defaults to handle new keys
                    defaults = LearnerState().indicator_win_rates
                    saved = data["indicator_win_rates"]
                    for k in defaults:
                        if k in saved:
                            defaults[k] = saved[k]
                    self.state.indicator_win_rates = defaults

                if "trade_contexts" in data:
                    self.state.trade_contexts = data["trade_contexts"][-self.MAX_HISTORY:]

                if "recent_adjustments" in data:
                    self.state.recent_adjustments = data["recent_adjustments"][-10:]

                logger.info(
                    f"Learner loaded: {self.state.total_learned_trades} trades, "
                    f"WR: {self.state.rolling_win_rate*100:.0f}%, "
                    f"threshold adj: {self.state.score_threshold_adj:+.1f}, "
                    f"leverage mult: {self.state.leverage_multiplier:.2f}"
                )
        except Exception as e:
            logger.warning(f"Could not load learner state: {e}")

    def _save_state(self):
        """Save learner state to disk."""
        try:
            data = {
                "score_threshold_adj": self.state.score_threshold_adj,
                "leverage_multiplier": self.state.leverage_multiplier,
                "min_volume_ratio": self.state.min_volume_ratio,
                "require_strong_signal": self.state.require_strong_signal,
                "confidence_floor": self.state.confidence_floor,
                "rolling_win_rate": self.state.rolling_win_rate,
                "rolling_avg_pnl": self.state.rolling_avg_pnl,
                "total_learned_trades": self.state.total_learned_trades,
                "last_adjustment_time": self.state.last_adjustment_time,
                "current_streak": self.state.current_streak,
                "max_losing_streak": self.state.max_losing_streak,
                "indicator_win_rates": self.state.indicator_win_rates,
                "trade_contexts": self.state.trade_contexts[-self.MAX_HISTORY:],
                "recent_adjustments": self.state.recent_adjustments[-10:],
            }
            tmp = LEARNER_STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, LEARNER_STATE_FILE)
        except Exception as e:
            logger.warning(f"Could not save learner state: {e}")

    def record_trade(self, trade_record, indicators: Optional[dict] = None,
                     had_crossover: bool = False, htf_aligned: bool = False):
        """
        Record a completed trade with its full context.
        Called by main.py after every position close.
        """
        ctx = {
            "timestamp": trade_record.timestamp,
            "side": trade_record.side,
            "entry_price": trade_record.entry_price,
            "exit_price": trade_record.exit_price,
            "pnl": trade_record.pnl,
            "pnl_pct": trade_record.pnl_pct,
            "leverage": trade_record.leverage,
            "score": trade_record.score,
            "exit_reason": trade_record.exit_reason,
            "duration_sec": trade_record.duration_sec,
            "volume_ratio": indicators.get("volume_ratio", 1.0) if indicators else 1.0,
            "rsi": indicators.get("rsi", 50) if indicators else 50,
            "bb_width": indicators.get("bb_width", 0) if indicators else 0,
            "bb_position": indicators.get("bb_position", 0.5) if indicators else 0.5,
            "atr_pct": indicators.get("atr_pct", 0) if indicators else 0,
            "macd_histogram": indicators.get("macd_histogram", 0) if indicators else 0,
            "orderbook_imbalance": indicators.get("imbalance", 0) if indicators else 0,
            "htf_aligned": htf_aligned,
            "had_crossover": had_crossover,
        }

        self.state.trade_contexts.append(ctx)
        if len(self.state.trade_contexts) > self.MAX_HISTORY:
            self.state.trade_contexts.pop(0)

        self.state.total_learned_trades += 1
        is_win = trade_record.pnl > 0

        # Update streak
        if is_win:
            self.state.current_streak = max(0, self.state.current_streak) + 1
        else:
            self.state.current_streak = min(0, self.state.current_streak) - 1
            self.state.max_losing_streak = max(
                self.state.max_losing_streak, abs(self.state.current_streak)
            )

        # Update per-condition stats
        self._update_indicator_stats(ctx, is_win)

        # Recompute rolling stats
        self._compute_rolling_stats()

        # Check if it's time to adjust parameters
        if self.state.total_learned_trades % self.ADJUSTMENT_INTERVAL == 0:
            self._adjust_parameters()

        # Also do immediate emergency adjustments on bad streaks
        if self.state.current_streak <= -3:
            self._emergency_tighten()

        self._save_state()

        logger.info(
            f"LEARNER: Trade #{self.state.total_learned_trades} recorded | "
            f"{'WIN' if is_win else 'LOSS'} | Streak: {self.state.current_streak:+d} | "
            f"Rolling WR: {self.state.rolling_win_rate*100:.0f}% | "
            f"Threshold adj: {self.state.score_threshold_adj:+.1f} | "
            f"Lev mult: {self.state.leverage_multiplier:.2f}"
        )

    def _update_indicator_stats(self, ctx: dict, is_win: bool):
        """Update per-condition win/loss counts."""
        iwr = self.state.indicator_win_rates
        result = "wins" if is_win else "losses"

        # HTF alignment
        key = "htf_aligned" if ctx.get("htf_aligned") else "htf_against"
        iwr.setdefault(key, {"wins": 0, "losses": 0})[result] += 1

        # Volume
        key = "high_volume" if ctx.get("volume_ratio", 1) > 1.3 else "low_volume"
        iwr.setdefault(key, {"wins": 0, "losses": 0})[result] += 1

        # Crossover
        key = "had_crossover" if ctx.get("had_crossover") else "no_crossover"
        iwr.setdefault(key, {"wins": 0, "losses": 0})[result] += 1

        # RSI
        rsi = ctx.get("rsi", 50)
        key = "rsi_extreme" if (rsi < 30 or rsi > 70) else "rsi_neutral"
        iwr.setdefault(key, {"wins": 0, "losses": 0})[result] += 1

        # Leverage
        key = "high_leverage" if ctx.get("leverage", 15) >= 30 else "low_leverage"
        iwr.setdefault(key, {"wins": 0, "losses": 0})[result] += 1

        # BB width
        key = "tight_bb" if ctx.get("bb_width", 0) < 0.003 else "wide_bb"
        iwr.setdefault(key, {"wins": 0, "losses": 0})[result] += 1

    def _compute_rolling_stats(self):
        """Compute rolling win rate and avg PnL from recent trades."""
        recent = self.state.trade_contexts[-20:]  # last 20 trades
        if not recent:
            return

        wins = sum(1 for t in recent if t["pnl"] > 0)
        self.state.rolling_win_rate = wins / len(recent)
        self.state.rolling_avg_pnl = sum(t["pnl"] for t in recent) / len(recent)

    def _adjust_parameters(self):
        """
        Core learning logic: adjust parameters based on performance.
        Called every ADJUSTMENT_INTERVAL trades.
        """
        if len(self.state.trade_contexts) < self.MIN_TRADES_FOR_LEARNING:
            return

        wr = self.state.rolling_win_rate
        avg_pnl = self.state.rolling_avg_pnl
        adjustments = []

        # ─── Score Threshold Adjustment ───
        # Target: 50%+ win rate
        # If below 45%: increase threshold (be more selective)
        # If above 60%: decrease threshold slightly (capture more opportunities)
        if wr < 0.40:
            delta = 1.0  # big increase
            self.state.score_threshold_adj = min(3.0, self.state.score_threshold_adj + delta)
            adjustments.append(f"Threshold +{delta} (WR too low: {wr*100:.0f}%)")
        elif wr < 0.50:
            delta = 0.5
            self.state.score_threshold_adj = min(3.0, self.state.score_threshold_adj + delta)
            adjustments.append(f"Threshold +{delta} (WR below target: {wr*100:.0f}%)")
        elif wr > 0.65 and self.state.score_threshold_adj > 0:
            delta = -0.3
            self.state.score_threshold_adj = max(0.0, self.state.score_threshold_adj + delta)
            adjustments.append(f"Threshold {delta} (WR good: {wr*100:.0f}%)")

        # ─── Leverage Multiplier ───
        # Reduce leverage when losing, increase when winning
        if wr < 0.40:
            self.state.leverage_multiplier = max(0.3, self.state.leverage_multiplier - 0.15)
            adjustments.append(f"Leverage mult down to {self.state.leverage_multiplier:.2f}")
        elif wr < 0.50:
            self.state.leverage_multiplier = max(0.4, self.state.leverage_multiplier - 0.10)
            adjustments.append(f"Leverage mult down to {self.state.leverage_multiplier:.2f}")
        elif wr > 0.60 and self.state.leverage_multiplier < 1.0:
            self.state.leverage_multiplier = min(1.0, self.state.leverage_multiplier + 0.05)
            adjustments.append(f"Leverage mult up to {self.state.leverage_multiplier:.2f}")

        # ─── Volume Filter ───
        iwr = self.state.indicator_win_rates
        low_vol = iwr.get("low_volume", {"wins": 0, "losses": 0})
        lv_total = low_vol["wins"] + low_vol["losses"]
        if lv_total >= 3:
            lv_wr = low_vol["wins"] / lv_total
            if lv_wr < 0.35:
                self.state.min_volume_ratio = min(1.0, self.state.min_volume_ratio + 0.1)
                adjustments.append(f"Min volume raised to {self.state.min_volume_ratio:.1f}x (low vol WR: {lv_wr*100:.0f}%)")

        # ─── Strong Signal Requirement ───
        no_cross = iwr.get("no_crossover", {"wins": 0, "losses": 0})
        nc_total = no_cross["wins"] + no_cross["losses"]
        if nc_total >= 3:
            nc_wr = no_cross["wins"] / nc_total
            if nc_wr < 0.40:
                self.state.require_strong_signal = True
                adjustments.append(f"Strong signal required (no-crossover WR: {nc_wr*100:.0f}%)")
            elif nc_wr > 0.55:
                self.state.require_strong_signal = False
                adjustments.append("Strong signal relaxed (no-crossover performing OK)")

        # ─── High Leverage Performance ───
        high_lev = iwr.get("high_leverage", {"wins": 0, "losses": 0})
        hl_total = high_lev["wins"] + high_lev["losses"]
        if hl_total >= 3:
            hl_wr = high_lev["wins"] / hl_total
            if hl_wr < 0.40:
                self.state.confidence_floor = min(0.5, self.state.confidence_floor + 0.1)
                adjustments.append(f"Confidence floor raised to {self.state.confidence_floor:.1f} (high lev WR: {hl_wr*100:.0f}%)")

        if adjustments:
            adj_record = {
                "time": time.time(),
                "trades": self.state.total_learned_trades,
                "win_rate": round(wr * 100, 1),
                "adjustments": adjustments,
            }
            self.state.recent_adjustments.append(adj_record)
            if len(self.state.recent_adjustments) > 10:
                self.state.recent_adjustments.pop(0)

            for adj in adjustments:
                logger.info(f"LEARNER ADJUSTMENT: {adj}")

        self.state.last_adjustment_time = time.time()

    def _emergency_tighten(self):
        """Emergency parameter tightening on bad losing streaks."""
        streak = abs(self.state.current_streak)
        logger.warning(f"LEARNER: Emergency tightening (losing streak: {streak})")

        # Proportional to streak severity
        if streak >= 5:
            self.state.score_threshold_adj = min(4.0, self.state.score_threshold_adj + 1.5)
            self.state.leverage_multiplier = max(0.25, self.state.leverage_multiplier * 0.6)
        elif streak >= 4:
            self.state.score_threshold_adj = min(3.5, self.state.score_threshold_adj + 1.0)
            self.state.leverage_multiplier = max(0.3, self.state.leverage_multiplier * 0.7)
        elif streak >= 3:
            self.state.score_threshold_adj = min(3.0, self.state.score_threshold_adj + 0.5)
            self.state.leverage_multiplier = max(0.4, self.state.leverage_multiplier * 0.8)

        self.state.recent_adjustments.append({
            "time": time.time(),
            "trades": self.state.total_learned_trades,
            "win_rate": round(self.state.rolling_win_rate * 100, 1),
            "adjustments": [f"EMERGENCY: streak={streak}, threshold adj={self.state.score_threshold_adj:+.1f}, lev mult={self.state.leverage_multiplier:.2f}"],
        })

        self._save_state()

    # ─── Public API for strategy/risk manager ───

    def get_effective_threshold(self, base_threshold: float) -> float:
        """Get the adjusted score threshold."""
        return base_threshold + self.state.score_threshold_adj

    def get_effective_leverage(self, computed_leverage: int, base_leverage: int) -> int:
        """Apply leverage multiplier and floor."""
        adjusted = int(computed_leverage * self.state.leverage_multiplier)
        return max(base_leverage, min(adjusted, 45))

    def should_skip_trade(self, indicators: dict, has_strong_signal: bool) -> tuple[bool, str]:
        """
        Additional learner-based filters on top of strategy filters.
        Returns (should_skip, reason).
        """
        # Volume filter
        vol = indicators.get("volume_ratio", 1.0)
        if vol < self.state.min_volume_ratio:
            return True, f"Learner: volume too low ({vol:.1f}x < {self.state.min_volume_ratio:.1f}x)"

        # Strong signal requirement
        if self.state.require_strong_signal and not has_strong_signal:
            return True, "Learner: no strong signal (crossover/divergence required)"

        # Anti-HTF filter: if trading against HTF has been unprofitable, block it
        htf_against = self.state.indicator_win_rates.get("htf_against", {"wins": 0, "losses": 0})
        ha_total = htf_against["wins"] + htf_against["losses"]
        if ha_total >= 3:
            ha_wr = htf_against["wins"] / ha_total
            if ha_wr < 0.30:
                # Check if current trade is against HTF
                htf_f = indicators.get("htf_ema_fast", 0)
                htf_s = indicators.get("htf_ema_slow", 0)
                # This will be checked by the caller with the signal side
                pass  # handled by caller

        return False, ""

    def should_skip_against_htf(self, side_is_long: bool, indicators: dict) -> bool:
        """Check if we should skip a trade that goes against HTF trend."""
        htf_f = indicators.get("htf_ema_fast", 0)
        htf_s = indicators.get("htf_ema_slow", 0)
        if htf_f == 0 or htf_s == 0:
            return False

        htf_bullish = htf_f > htf_s
        is_against = (side_is_long and not htf_bullish) or (not side_is_long and htf_bullish)

        if not is_against:
            return False

        # Check historical performance of against-HTF trades
        htf_against = self.state.indicator_win_rates.get("htf_against", {"wins": 0, "losses": 0})
        total = htf_against["wins"] + htf_against["losses"]
        if total >= 3:
            wr = htf_against["wins"] / total
            if wr < 0.35:
                return True  # Skip — against-HTF trades have been unprofitable

        return False

    def get_stats(self) -> dict:
        """Get learner stats for dashboard display."""
        # Compute per-condition win rates
        condition_stats = {}
        for key, data in self.state.indicator_win_rates.items():
            total = data["wins"] + data["losses"]
            if total > 0:
                condition_stats[key] = {
                    "wins": data["wins"],
                    "losses": data["losses"],
                    "total": total,
                    "win_rate": round(data["wins"] / total * 100, 1),
                }

        return {
            "total_learned_trades": self.state.total_learned_trades,
            "rolling_win_rate": round(self.state.rolling_win_rate * 100, 1),
            "rolling_avg_pnl": round(self.state.rolling_avg_pnl, 6),
            "current_streak": self.state.current_streak,
            "max_losing_streak": self.state.max_losing_streak,
            "score_threshold_adj": round(self.state.score_threshold_adj, 1),
            "leverage_multiplier": round(self.state.leverage_multiplier, 2),
            "min_volume_ratio": round(self.state.min_volume_ratio, 1),
            "require_strong_signal": self.state.require_strong_signal,
            "confidence_floor": round(self.state.confidence_floor, 1),
            "effective_threshold": None,  # filled by caller
            "effective_max_leverage": None,  # filled by caller
            "condition_stats": condition_stats,
            "recent_adjustments": self.state.recent_adjustments[-5:],
        }
