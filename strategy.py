"""Multi-indicator scalping strategy with scoring system."""

import logging
from typing import Optional

import pandas as pd
import ta

from config import BotConfig
from models import IndicatorSnapshot, OrderBookSnapshot, Side, Signal

logger = logging.getLogger("scalper")


class ScalpingStrategy:
    """
    Aggressive scalping strategy using a weighted scoring system.

    Indicators:
    - EMA Crossover (5/13) for trend direction
    - RSI (7) for overbought/oversold
    - Volume ratio for move confirmation
    - Bollinger Bands (20, 2σ) for mean reversion
    - VWAP for intraday bias
    - Order book imbalance for short-term direction

    Each indicator contributes a weighted score.
    Trade is opened when score >= threshold (~40% confluence).
    """

    def __init__(self, config: BotConfig):
        self.config = config
        self._prev_indicators: Optional[IndicatorSnapshot] = None

    def compute_indicators(self, df: pd.DataFrame, ob: OrderBookSnapshot) -> Optional[IndicatorSnapshot]:
        """Compute all technical indicators from candle data."""
        if len(df) < max(self.config.bb_period, self.config.volume_avg_period, self.config.ema_slow) + 5:
            logger.warning(f"Not enough candles for indicators ({len(df)} available)")
            return None

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float)

        # --- EMA ---
        ema_fast = ta.trend.ema_indicator(close, window=self.config.ema_fast)
        ema_slow = ta.trend.ema_indicator(close, window=self.config.ema_slow)

        # --- RSI ---
        rsi = ta.momentum.rsi(close, window=self.config.rsi_period)

        # --- Bollinger Bands ---
        bb = ta.volatility.BollingerBands(
            close, window=self.config.bb_period, window_dev=self.config.bb_std
        )

        # --- VWAP (session-based approximation) ---
        typical_price = (high + low + close) / 3
        cum_tp_vol = (typical_price * volume).cumsum()
        cum_vol = volume.cumsum()
        vwap = cum_tp_vol / cum_vol

        # --- Volume ratio ---
        vol_avg = volume.rolling(self.config.volume_avg_period).mean()
        current_vol = volume.iloc[-1]
        avg_vol = vol_avg.iloc[-1]
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

        return IndicatorSnapshot(
            ema_fast=float(ema_fast.iloc[-1]),
            ema_slow=float(ema_slow.iloc[-1]),
            rsi=float(rsi.iloc[-1]),
            bb_upper=float(bb.bollinger_hband().iloc[-1]),
            bb_middle=float(bb.bollinger_mavg().iloc[-1]),
            bb_lower=float(bb.bollinger_lband().iloc[-1]),
            vwap=float(vwap.iloc[-1]),
            volume_ratio=float(vol_ratio),
            orderbook_imbalance=ob.imbalance,
            close_price=float(close.iloc[-1]),
            timestamp=float(df["timestamp"].iloc[-1].timestamp()) if hasattr(df["timestamp"].iloc[-1], "timestamp") else 0,
        )

    def evaluate(self, indicators: Optional[IndicatorSnapshot]) -> Optional[Signal]:
        """
        Evaluate indicators and generate a trade signal using the scoring system.

        Scoring (max 7.5 per side):
        - EMA Cross: 2.0 pts (trend alignment: 0.6 pts)
        - RSI: 1.0 pts
        - Volume: 1.0 pts
        - Bollinger: 1.5 pts
        - VWAP: 0.5 pts
        - Orderbook: 1.5 pts

        Threshold: 3.0 pts (~40% confluence)
        """
        if indicators is None:
            return None

        cfg = self.config
        long_score = 0.0
        short_score = 0.0
        reasons_long = []
        reasons_short = []

        # ═══════════════════════════════════════════
        # 1. EMA CROSSOVER (weight: 2.0)
        # ═══════════════════════════════════════════
        prev = self._prev_indicators
        if prev is not None:
            # Bullish cross: fast was below slow, now above
            if prev.ema_fast <= prev.ema_slow and indicators.ema_fast > indicators.ema_slow:
                long_score += cfg.w_ema_cross
                reasons_long.append(f"EMA↑ cross +{cfg.w_ema_cross}")

            # Bearish cross: fast was above slow, now below
            if prev.ema_fast >= prev.ema_slow and indicators.ema_fast < indicators.ema_slow:
                short_score += cfg.w_ema_cross
                reasons_short.append(f"EMA↓ cross +{cfg.w_ema_cross}")

        # Trend alignment (no cross, but aligned)
        if indicators.ema_fast > indicators.ema_slow:
            bonus = cfg.w_ema_cross * 0.3
            long_score += bonus
            reasons_long.append(f"EMA trend↑ +{bonus:.1f}")
        elif indicators.ema_fast < indicators.ema_slow:
            bonus = cfg.w_ema_cross * 0.3
            short_score += bonus
            reasons_short.append(f"EMA trend↓ +{bonus:.1f}")

        # ═══════════════════════════════════════════
        # 2. RSI (weight: 1.0)
        # ═══════════════════════════════════════════
        if indicators.rsi < 30:
            long_score += cfg.w_rsi
            reasons_long.append(f"RSI oversold({indicators.rsi:.0f}) +{cfg.w_rsi}")
        elif indicators.rsi < 45:
            bonus = cfg.w_rsi * 0.5
            long_score += bonus
            reasons_long.append(f"RSI low({indicators.rsi:.0f}) +{bonus:.1f}")

        if indicators.rsi > 70:
            short_score += cfg.w_rsi
            reasons_short.append(f"RSI overbought({indicators.rsi:.0f}) +{cfg.w_rsi}")
        elif indicators.rsi > 55:
            bonus = cfg.w_rsi * 0.5
            short_score += bonus
            reasons_short.append(f"RSI high({indicators.rsi:.0f}) +{bonus:.1f}")

        # ═══════════════════════════════════════════
        # 3. VOLUME (weight: 1.0)
        # ═══════════════════════════════════════════
        if indicators.volume_ratio > 1.5:
            long_score += cfg.w_volume
            short_score += cfg.w_volume
            reasons_long.append(f"Vol high({indicators.volume_ratio:.1f}x) +{cfg.w_volume}")
            reasons_short.append(f"Vol high({indicators.volume_ratio:.1f}x) +{cfg.w_volume}")
        elif indicators.volume_ratio > 1.1:
            bonus = cfg.w_volume * 0.4
            long_score += bonus
            short_score += bonus
            reasons_long.append(f"Vol above avg({indicators.volume_ratio:.1f}x) +{bonus:.1f}")
            reasons_short.append(f"Vol above avg({indicators.volume_ratio:.1f}x) +{bonus:.1f}")

        # ═══════════════════════════════════════════
        # 4. BOLLINGER BANDS (weight: 1.5)
        # ═══════════════════════════════════════════
        bb_range = indicators.bb_upper - indicators.bb_lower
        if bb_range > 0:
            bb_position = (indicators.close_price - indicators.bb_lower) / bb_range

            if bb_position < 0.15:
                long_score += cfg.w_bollinger
                reasons_long.append(f"BB bottom({bb_position:.0%}) +{cfg.w_bollinger}")
            elif bb_position < 0.30:
                bonus = cfg.w_bollinger * 0.5
                long_score += bonus
                reasons_long.append(f"BB low({bb_position:.0%}) +{bonus:.1f}")

            if bb_position > 0.85:
                short_score += cfg.w_bollinger
                reasons_short.append(f"BB top({bb_position:.0%}) +{cfg.w_bollinger}")
            elif bb_position > 0.70:
                bonus = cfg.w_bollinger * 0.5
                short_score += bonus
                reasons_short.append(f"BB high({bb_position:.0%}) +{bonus:.1f}")

        # ═══════════════════════════════════════════
        # 5. VWAP (weight: 0.5)
        # ═══════════════════════════════════════════
        if indicators.close_price > indicators.vwap:
            long_score += cfg.w_vwap
            reasons_long.append(f"Above VWAP +{cfg.w_vwap}")
        else:
            short_score += cfg.w_vwap
            reasons_short.append(f"Below VWAP +{cfg.w_vwap}")

        # ═══════════════════════════════════════════
        # 6. ORDER BOOK IMBALANCE (weight: 1.5)
        # ═══════════════════════════════════════════
        imb = indicators.orderbook_imbalance
        if imb > 0.20:
            long_score += cfg.w_orderbook
            reasons_long.append(f"OB bid-heavy({imb:.0%}) +{cfg.w_orderbook}")
        elif imb > 0.08:
            bonus = cfg.w_orderbook * 0.5
            long_score += bonus
            reasons_long.append(f"OB slight bid({imb:.0%}) +{bonus:.1f}")

        if imb < -0.20:
            short_score += cfg.w_orderbook
            reasons_short.append(f"OB ask-heavy({imb:.0%}) +{cfg.w_orderbook}")
        elif imb < -0.08:
            bonus = cfg.w_orderbook * 0.5
            short_score += bonus
            reasons_short.append(f"OB slight ask({imb:.0%}) +{bonus:.1f}")

        # ═══════════════════════════════════════════
        # SAVE STATE FOR NEXT EVALUATION
        # ═══════════════════════════════════════════
        self._prev_indicators = indicators

        # ═══════════════════════════════════════════
        # DECISION
        # ═══════════════════════════════════════════
        if long_score >= cfg.score_threshold_long and long_score > short_score:
            logger.info(
                f"📈 LONG signal (score: {long_score:.1f}/{cfg.score_threshold_long:.1f}) | "
                f"Price: ${indicators.close_price:,.2f} | "
                f"{' | '.join(reasons_long)}"
            )
            return Signal(side=Side.LONG, score=long_score, indicators=indicators)

        if short_score >= cfg.score_threshold_short and short_score > long_score:
            logger.info(
                f"📉 SHORT signal (score: {short_score:.1f}/{cfg.score_threshold_short:.1f}) | "
                f"Price: ${indicators.close_price:,.2f} | "
                f"{' | '.join(reasons_short)}"
            )
            return Signal(side=Side.SHORT, score=short_score, indicators=indicators)

        # Log the scores even when no signal (debug level)
        logger.debug(
            f"No signal | Long: {long_score:.1f} | Short: {short_score:.1f} | "
            f"Price: ${indicators.close_price:,.2f} | RSI: {indicators.rsi:.0f}"
        )

        return None
