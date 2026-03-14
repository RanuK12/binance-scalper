"""
Strategy v3.0 — Multi-indicator scoring with dynamic leverage.
===============================================================
Full analytical brain: EMA crossover, RSI + divergence, MACD momentum,
Bollinger Bands squeeze, ATR dynamic SL/TP, VWAP, volume delta,
order book imbalance, higher timeframe trend, exhaustion filter,
anti-chop regime detection. Dynamic leverage 15x-45x based on confluence.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd
import ta

from config import BotConfig
from models import IndicatorSnapshot, OrderBookSnapshot, Side, Signal

logger = logging.getLogger("scalper")


class ScalpingStrategy:
    """v3.0 aggressive scalping strategy with dynamic leverage."""

    def __init__(self, config: BotConfig):
        self.config = config
        self._prev_indicators: Optional[IndicatorSnapshot] = None
        self._rsi_history: list[float] = []
        self._price_history: list[float] = []
        self._max_history = 30

    def compute_indicators(self, df: pd.DataFrame, ob: OrderBookSnapshot) -> Optional[IndicatorSnapshot]:
        """Compute all technical indicators from candle data."""
        min_needed = max(65, self.config.bb_period, self.config.volume_avg_period, self.config.ema_slow) + 5
        if len(df) < min_needed:
            logger.warning(f"Not enough candles ({len(df)}/{min_needed})")
            return None

        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        volume = df["volume"].astype(float)
        open_price = df["open"].astype(float)

        # --- EMA (fast timeframe) ---
        ema_fast = ta.trend.ema_indicator(close, window=self.config.ema_fast)
        ema_slow = ta.trend.ema_indicator(close, window=self.config.ema_slow)

        # --- Higher Timeframe EMAs (25/65 period as 5m proxy on 1m chart) ---
        htf_ema_fast = ta.trend.ema_indicator(close, window=25)
        htf_ema_slow = ta.trend.ema_indicator(close, window=65)

        # --- RSI ---
        rsi_series = ta.momentum.rsi(close, window=self.config.rsi_period)
        rsi_val = float(rsi_series.iloc[-1])
        rsi_prev = float(rsi_series.iloc[-2]) if len(rsi_series) > 1 else rsi_val

        # --- MACD (12/26/9) ---
        macd_obj = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
        macd_line = macd_obj.macd()
        macd_signal = macd_obj.macd_signal()
        macd_hist = macd_obj.macd_diff()

        # --- Bollinger Bands ---
        bb = ta.volatility.BollingerBands(
            close, window=self.config.bb_period, window_dev=self.config.bb_std
        )
        bb_upper_val = float(bb.bollinger_hband().iloc[-1])
        bb_lower_val = float(bb.bollinger_lband().iloc[-1])
        bb_middle_val = float(bb.bollinger_mavg().iloc[-1])
        bb_width = (bb_upper_val - bb_lower_val) / bb_middle_val if bb_middle_val > 0 else 0

        # --- ATR (14) ---
        atr_series = ta.volatility.average_true_range(high, low, close, window=14)
        atr_val = float(atr_series.iloc[-1])
        current_price = float(close.iloc[-1])
        atr_pct = atr_val / current_price if current_price > 0 else 0

        # --- VWAP ---
        typical_price = (high + low + close) / 3
        cum_tp_vol = (typical_price * volume).cumsum()
        cum_vol = volume.cumsum()
        vwap = cum_tp_vol / cum_vol

        # --- Volume ratio ---
        vol_avg = volume.rolling(self.config.volume_avg_period).mean()
        current_vol = float(volume.iloc[-1])
        avg_vol = float(vol_avg.iloc[-1])
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

        # --- Volume delta (buy vs sell estimate) ---
        # Candles where close > open are "buy" candles
        buy_vol = volume.where(close > open_price, 0)
        sell_vol = volume.where(close <= open_price, 0)
        recent_buy = float(buy_vol.iloc[-5:].sum())
        recent_sell = float(sell_vol.iloc[-5:].sum())
        total_recent = recent_buy + recent_sell
        volume_delta = (recent_buy - recent_sell) / total_recent if total_recent > 0 else 0

        # --- Consecutive candles ---
        consecutive_green = 0
        consecutive_red = 0
        for i in range(len(close) - 1, max(len(close) - 15, -1), -1):
            if float(close.iloc[i]) > float(open_price.iloc[i]):
                if consecutive_red > 0:
                    break
                consecutive_green += 1
            elif float(close.iloc[i]) < float(open_price.iloc[i]):
                if consecutive_green > 0:
                    break
                consecutive_red += 1
            else:
                break

        # Track RSI/price history for divergence detection
        self._rsi_history.append(rsi_val)
        self._price_history.append(current_price)
        if len(self._rsi_history) > self._max_history:
            self._rsi_history.pop(0)
            self._price_history.pop(0)

        return IndicatorSnapshot(
            ema_fast=float(ema_fast.iloc[-1]),
            ema_slow=float(ema_slow.iloc[-1]),
            rsi=rsi_val,
            bb_upper=bb_upper_val,
            bb_middle=bb_middle_val,
            bb_lower=bb_lower_val,
            vwap=float(vwap.iloc[-1]),
            volume_ratio=vol_ratio,
            orderbook_imbalance=ob.imbalance,
            close_price=current_price,
            timestamp=float(df["timestamp"].iloc[-1].timestamp()) if hasattr(df["timestamp"].iloc[-1], "timestamp") else 0,
            macd=float(macd_line.iloc[-1]),
            macd_signal=float(macd_signal.iloc[-1]),
            macd_histogram=float(macd_hist.iloc[-1]),
            atr=atr_val,
            atr_pct=atr_pct,
            rsi_prev=rsi_prev,
            price_prev=float(close.iloc[-2]) if len(close) > 1 else current_price,
            bb_width=bb_width,
            volume_delta=volume_delta,
            consecutive_green=consecutive_green,
            consecutive_red=consecutive_red,
            htf_ema_fast=float(htf_ema_fast.iloc[-1]),
            htf_ema_slow=float(htf_ema_slow.iloc[-1]),
        )

    def _detect_rsi_divergence(self, indicators: IndicatorSnapshot) -> tuple[bool, bool]:
        """
        Detect RSI divergence (bullish and bearish).
        Bullish: price makes lower low but RSI makes higher low -> reversal up
        Bearish: price makes higher high but RSI makes lower high -> reversal down
        """
        if len(self._rsi_history) < 10:
            return False, False

        prices = self._price_history[-10:]
        rsis = self._rsi_history[-10:]

        # Find recent swing lows/highs
        bullish_div = False
        bearish_div = False

        # Check last few entries for divergence pattern
        recent_price = prices[-1]
        recent_rsi = rsis[-1]
        lookback_price = min(prices[:-3]) if len(prices) > 3 else prices[0]
        lookback_rsi = rsis[prices.index(lookback_price)] if lookback_price in prices else rsis[0]

        # Bullish divergence: price lower low, RSI higher low
        if recent_price < lookback_price and recent_rsi > lookback_rsi and recent_rsi < 40:
            bullish_div = True

        # Bearish divergence: price higher high, RSI lower high
        lookback_price_high = max(prices[:-3]) if len(prices) > 3 else prices[0]
        lookback_rsi_idx = prices.index(lookback_price_high) if lookback_price_high in prices else 0
        lookback_rsi_high = rsis[lookback_rsi_idx]

        if recent_price > lookback_price_high and recent_rsi < lookback_rsi_high and recent_rsi > 60:
            bearish_div = True

        return bullish_div, bearish_div

    def _is_choppy_market(self, indicators: IndicatorSnapshot) -> bool:
        """
        Detect choppy/ranging market where signals are unreliable.
        Uses BB width squeeze + low volume + RSI near 50.
        """
        # Tight BB = low volatility = chop
        if indicators.bb_width < 0.002:
            return True

        # RSI stuck in 45-55 range with low volume = no direction
        if 45 < indicators.rsi < 55 and indicators.volume_ratio < 0.6:
            return True

        return False

    def _compute_dynamic_leverage(self, score: float, indicators: IndicatorSnapshot, side: Side) -> int:
        """
        Compute dynamic leverage between base (15x) and max (45x).
        Higher confidence = higher leverage.

        Factors that INCREASE leverage:
        - High signal score (strong confluence)
        - HTF trend alignment
        - Strong volume confirmation
        - RSI divergence
        - Clean MACD momentum

        Factors that DECREASE leverage:
        - Choppy market
        - Trading against HTF trend
        - Low volume
        - Exhaustion (too many consecutive candles)
        """
        cfg = self.config
        base = cfg.leverage  # 15
        max_lev = cfg.max_leverage  # 45

        # Start at base
        confidence = 0.0

        # Score contribution (0 to 0.35)
        # Score 3.0 = threshold -> 0 confidence
        # Score 6.0+ = very high -> max contribution
        score_ratio = max(0, (score - cfg.score_threshold_long) / 5.0)
        confidence += min(score_ratio, 0.35)

        # HTF trend alignment (0 to 0.25)
        htf_bullish = indicators.htf_ema_fast > indicators.htf_ema_slow
        if (side == Side.LONG and htf_bullish) or (side == Side.SHORT and not htf_bullish):
            confidence += 0.25
        else:
            # Against trend: penalty
            confidence -= 0.15

        # Volume confirmation (0 to 0.15)
        if indicators.volume_ratio > 2.0:
            confidence += 0.15
        elif indicators.volume_ratio > 1.3:
            confidence += 0.08

        # Volume delta alignment (0 to 0.10)
        if (side == Side.LONG and indicators.volume_delta > 0.3) or \
           (side == Side.SHORT and indicators.volume_delta < -0.3):
            confidence += 0.10

        # MACD momentum alignment (0 to 0.10)
        if (side == Side.LONG and indicators.macd_histogram > 0) or \
           (side == Side.SHORT and indicators.macd_histogram < 0):
            confidence += 0.10

        # Orderbook pressure alignment (0 to 0.05)
        if (side == Side.LONG and indicators.orderbook_imbalance > 0.15) or \
           (side == Side.SHORT and indicators.orderbook_imbalance < -0.15):
            confidence += 0.05

        # Penalties
        # Exhaustion penalty
        if (side == Side.LONG and indicators.consecutive_green >= 5) or \
           (side == Side.SHORT and indicators.consecutive_red >= 5):
            confidence -= 0.20

        # Choppy market penalty
        if self._is_choppy_market(indicators):
            confidence -= 0.15

        # Low volume penalty
        if indicators.volume_ratio < 0.6:
            confidence -= 0.10

        # Clamp confidence to [0, 1]
        confidence = max(0.0, min(1.0, confidence))

        # Map confidence to leverage
        leverage = int(base + confidence * (max_lev - base))

        # Round to nearest 5 for clean values
        leverage = max(base, min(max_lev, round(leverage / 5) * 5))

        logger.info(f"Dynamic leverage: {leverage}x (confidence: {confidence:.2f})")
        return leverage

    def evaluate(self, indicators: Optional[IndicatorSnapshot]) -> Optional[Signal]:
        """
        v3.0 Evaluate indicators with full analytical power.

        Scoring (max ~12 per side):
        - EMA Cross: 2.0 pts
        - RSI: 1.5 pts (with divergence bonus)
        - MACD: 1.5 pts
        - Volume: 1.0 pts
        - Bollinger: 1.5 pts
        - VWAP: 0.5 pts
        - Orderbook: 1.5 pts
        - HTF Trend: 1.0 pts
        - RSI Divergence: 1.5 pts

        Threshold: 3.0 pts
        Dynamic leverage: 15x-45x based on confluence
        """
        if indicators is None:
            return None

        cfg = self.config
        long_score = 0.0
        short_score = 0.0
        reasons_long = []
        reasons_short = []

        # ═══════════════════════════════════════════
        # ANTI-CHOP FILTER
        # ═══════════════════════════════════════════
        if self._is_choppy_market(indicators):
            logger.debug(f"Choppy market detected (BB width: {indicators.bb_width:.4f}). Skipping signals.")
            self._prev_indicators = indicators
            return None

        # ═══════════════════════════════════════════
        # VOLUME FILTER — skip if dead market
        # ═══════════════════════════════════════════
        if indicators.volume_ratio < 0.5:
            logger.debug(f"Volume too low ({indicators.volume_ratio:.1f}x). Skipping.")
            self._prev_indicators = indicators
            return None

        # ═══════════════════════════════════════════
        # 1. EMA CROSSOVER (weight: 2.0)
        # ═══════════════════════════════════════════
        prev = self._prev_indicators
        if prev is not None:
            if prev.ema_fast <= prev.ema_slow and indicators.ema_fast > indicators.ema_slow:
                long_score += cfg.w_ema_cross
                reasons_long.append(f"EMA CROSS UP +{cfg.w_ema_cross}")

            if prev.ema_fast >= prev.ema_slow and indicators.ema_fast < indicators.ema_slow:
                short_score += cfg.w_ema_cross
                reasons_short.append(f"EMA CROSS DOWN +{cfg.w_ema_cross}")

        # Trend alignment bonus
        if indicators.ema_fast > indicators.ema_slow:
            bonus = cfg.w_ema_cross * 0.3
            long_score += bonus
            reasons_long.append(f"EMA trend +{bonus:.1f}")
        elif indicators.ema_fast < indicators.ema_slow:
            bonus = cfg.w_ema_cross * 0.3
            short_score += bonus
            reasons_short.append(f"EMA trend +{bonus:.1f}")

        # ═══════════════════════════════════════════
        # 2. RSI (weight: 1.5)
        # ═══════════════════════════════════════════
        if indicators.rsi < 25:
            long_score += cfg.w_rsi
            reasons_long.append(f"RSI extreme({indicators.rsi:.0f}) +{cfg.w_rsi}")
        elif indicators.rsi < 35:
            bonus = cfg.w_rsi * 0.7
            long_score += bonus
            reasons_long.append(f"RSI oversold({indicators.rsi:.0f}) +{bonus:.1f}")
        elif indicators.rsi < 45:
            bonus = cfg.w_rsi * 0.3
            long_score += bonus
            reasons_long.append(f"RSI low({indicators.rsi:.0f}) +{bonus:.1f}")

        if indicators.rsi > 75:
            short_score += cfg.w_rsi
            reasons_short.append(f"RSI extreme({indicators.rsi:.0f}) +{cfg.w_rsi}")
        elif indicators.rsi > 65:
            bonus = cfg.w_rsi * 0.7
            short_score += bonus
            reasons_short.append(f"RSI overbought({indicators.rsi:.0f}) +{bonus:.1f}")
        elif indicators.rsi > 55:
            bonus = cfg.w_rsi * 0.3
            short_score += bonus
            reasons_short.append(f"RSI high({indicators.rsi:.0f}) +{bonus:.1f}")

        # ═══════════════════════════════════════════
        # 3. RSI DIVERGENCE (weight: 1.5)
        # ═══════════════════════════════════════════
        bullish_div, bearish_div = self._detect_rsi_divergence(indicators)
        if bullish_div:
            long_score += cfg.w_rsi_divergence
            reasons_long.append(f"RSI BULL DIV +{cfg.w_rsi_divergence}")
        if bearish_div:
            short_score += cfg.w_rsi_divergence
            reasons_short.append(f"RSI BEAR DIV +{cfg.w_rsi_divergence}")

        # ═══════════════════════════════════════════
        # 4. MACD (weight: 1.5)
        # ═══════════════════════════════════════════
        if prev is not None:
            # MACD crossover
            if prev.macd <= prev.macd_signal and indicators.macd > indicators.macd_signal:
                long_score += cfg.w_macd
                reasons_long.append(f"MACD CROSS UP +{cfg.w_macd}")
            if prev.macd >= prev.macd_signal and indicators.macd < indicators.macd_signal:
                short_score += cfg.w_macd
                reasons_short.append(f"MACD CROSS DOWN +{cfg.w_macd}")

        # MACD histogram momentum
        if indicators.macd_histogram > 0:
            bonus = cfg.w_macd * 0.3
            long_score += bonus
            reasons_long.append(f"MACD momentum +{bonus:.1f}")
        elif indicators.macd_histogram < 0:
            bonus = cfg.w_macd * 0.3
            short_score += bonus
            reasons_short.append(f"MACD momentum +{bonus:.1f}")

        # ═══════════════════════════════════════════
        # 5. VOLUME (weight: 1.0)
        # ═══════════════════════════════════════════
        if indicators.volume_ratio > 2.0:
            long_score += cfg.w_volume
            short_score += cfg.w_volume
            reasons_long.append(f"Vol spike({indicators.volume_ratio:.1f}x) +{cfg.w_volume}")
            reasons_short.append(f"Vol spike({indicators.volume_ratio:.1f}x) +{cfg.w_volume}")
        elif indicators.volume_ratio > 1.3:
            bonus = cfg.w_volume * 0.5
            long_score += bonus
            short_score += bonus
            reasons_long.append(f"Vol above avg({indicators.volume_ratio:.1f}x) +{bonus:.1f}")
            reasons_short.append(f"Vol above avg({indicators.volume_ratio:.1f}x) +{bonus:.1f}")

        # Volume delta direction
        if indicators.volume_delta > 0.25:
            bonus = cfg.w_volume * 0.4
            long_score += bonus
            reasons_long.append(f"Vol delta buy +{bonus:.1f}")
        elif indicators.volume_delta < -0.25:
            bonus = cfg.w_volume * 0.4
            short_score += bonus
            reasons_short.append(f"Vol delta sell +{bonus:.1f}")

        # ═══════════════════════════════════════════
        # 6. BOLLINGER BANDS + SQUEEZE (weight: 1.5)
        # ═══════════════════════════════════════════
        bb_range = indicators.bb_upper - indicators.bb_lower
        if bb_range > 0:
            bb_position = (indicators.close_price - indicators.bb_lower) / bb_range

            # BB squeeze breakout — tight bands + price pushing boundary
            is_squeeze = indicators.bb_width < 0.005

            if bb_position < 0.10:
                long_score += cfg.w_bollinger
                reasons_long.append(f"BB bottom({bb_position:.0%}) +{cfg.w_bollinger}")
            elif bb_position < 0.25:
                bonus = cfg.w_bollinger * 0.5
                long_score += bonus
                reasons_long.append(f"BB low({bb_position:.0%}) +{bonus:.1f}")

            if bb_position > 0.90:
                short_score += cfg.w_bollinger
                reasons_short.append(f"BB top({bb_position:.0%}) +{cfg.w_bollinger}")
            elif bb_position > 0.75:
                bonus = cfg.w_bollinger * 0.5
                short_score += bonus
                reasons_short.append(f"BB high({bb_position:.0%}) +{bonus:.1f}")

            # Squeeze breakout bonus
            if is_squeeze:
                if bb_position > 0.7:
                    long_score += cfg.w_bollinger * 0.4
                    reasons_long.append("BB squeeze breakout UP")
                elif bb_position < 0.3:
                    short_score += cfg.w_bollinger * 0.4
                    reasons_short.append("BB squeeze breakout DOWN")

        # ═══════════════════════════════════════════
        # 7. VWAP (weight: 0.5)
        # ═══════════════════════════════════════════
        if indicators.close_price > indicators.vwap:
            long_score += cfg.w_vwap
            reasons_long.append(f"Above VWAP +{cfg.w_vwap}")
        else:
            short_score += cfg.w_vwap
            reasons_short.append(f"Below VWAP +{cfg.w_vwap}")

        # ═══════════════════════════════════════════
        # 8. ORDER BOOK IMBALANCE (weight: 1.5)
        # ═══════════════════════════════════════════
        imb = indicators.orderbook_imbalance
        if imb > 0.25:
            long_score += cfg.w_orderbook
            reasons_long.append(f"OB strong bid({imb:.0%}) +{cfg.w_orderbook}")
        elif imb > 0.10:
            bonus = cfg.w_orderbook * 0.5
            long_score += bonus
            reasons_long.append(f"OB bid({imb:.0%}) +{bonus:.1f}")

        if imb < -0.25:
            short_score += cfg.w_orderbook
            reasons_short.append(f"OB strong ask({imb:.0%}) +{cfg.w_orderbook}")
        elif imb < -0.10:
            bonus = cfg.w_orderbook * 0.5
            short_score += bonus
            reasons_short.append(f"OB ask({imb:.0%}) +{bonus:.1f}")

        # ═══════════════════════════════════════════
        # 9. HIGHER TIMEFRAME TREND (weight: 1.0)
        # ═══════════════════════════════════════════
        htf_bullish = indicators.htf_ema_fast > indicators.htf_ema_slow
        if htf_bullish:
            long_score += cfg.w_htf_trend
            reasons_long.append(f"HTF trend UP +{cfg.w_htf_trend}")
            # Penalty for shorting against HTF
            short_score -= cfg.w_htf_trend * 0.5
        else:
            short_score += cfg.w_htf_trend
            reasons_short.append(f"HTF trend DOWN +{cfg.w_htf_trend}")
            long_score -= cfg.w_htf_trend * 0.5

        # ═══════════════════════════════════════════
        # EXHAUSTION FILTER
        # ═══════════════════════════════════════════
        # 5+ consecutive candles same direction = exhaustion risk
        if indicators.consecutive_green >= 5:
            penalty = 1.5
            long_score -= penalty
            reasons_long.append(f"EXHAUSTION({indicators.consecutive_green} green) -{penalty}")
        if indicators.consecutive_red >= 5:
            penalty = 1.5
            short_score -= penalty
            reasons_short.append(f"EXHAUSTION({indicators.consecutive_red} red) -{penalty}")

        # ═══════════════════════════════════════════
        # SAVE STATE
        # ═══════════════════════════════════════════
        self._prev_indicators = indicators

        # ═══════════════════════════════════════════
        # DECISION + DYNAMIC LEVERAGE
        # ═══════════════════════════════════════════
        if long_score >= cfg.score_threshold_long and long_score > short_score:
            lev = self._compute_dynamic_leverage(long_score, indicators, Side.LONG)
            logger.info(
                f"LONG signal (score: {long_score:.1f}/{cfg.score_threshold_long:.1f}, lev: {lev}x) | "
                f"Price: ${indicators.close_price:,.2f} | "
                f"{' | '.join(reasons_long)}"
            )
            return Signal(side=Side.LONG, score=long_score, indicators=indicators, recommended_leverage=lev)

        if short_score >= cfg.score_threshold_short and short_score > long_score:
            lev = self._compute_dynamic_leverage(short_score, indicators, Side.SHORT)
            logger.info(
                f"SHORT signal (score: {short_score:.1f}/{cfg.score_threshold_short:.1f}, lev: {lev}x) | "
                f"Price: ${indicators.close_price:,.2f} | "
                f"{' | '.join(reasons_short)}"
            )
            return Signal(side=Side.SHORT, score=short_score, indicators=indicators, recommended_leverage=lev)

        logger.debug(
            f"No signal | Long: {long_score:.1f} | Short: {short_score:.1f} | "
            f"Price: ${indicators.close_price:,.2f} | RSI: {indicators.rsi:.0f} | "
            f"MACD: {indicators.macd_histogram:+.2f}"
        )

        return None
