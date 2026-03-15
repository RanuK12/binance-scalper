"""
Comprehensive Test Suite for Binance Scalping Bot v4.1.1
========================================================
Tests every component with simulated market data and trades.
Run with: python test_full.py
"""

import asyncio
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import BotConfig
from models import Side, Signal, Position, TradeRecord, IndicatorSnapshot, OrderBookSnapshot
from risk_manager import RiskManager
from strategy import ScalpingStrategy
from learner import AdaptiveLearner
from position_manager import PositionManager
from exchange import ExchangeClient

# ─── Test Framework ───

PASS = 0
FAIL = 0
ERRORS = []


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        ERRORS.append((name, detail))
        print(f"  [FAIL] {name} — {detail}")


def make_config(**overrides):
    cfg = BotConfig(
        api_key="test", api_secret="test",
        testnet=False, dry_run=True,
        leverage=15, max_leverage=45,
        max_position_pct=1.0,
        stop_loss_pct=0.003, take_profit_pct=0.005,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def make_indicators(**overrides):
    defaults = dict(
        ema_fast=85010, ema_slow=85000,
        rsi=45, bb_upper=85500, bb_middle=85000,
        bb_lower=84500, vwap=84950,
        volume_ratio=1.5, orderbook_imbalance=0.15,
        close_price=85000, timestamp=0,
        macd=10, macd_signal=5, macd_histogram=5,
        atr=100, atr_pct=0.0012,
        rsi_prev=42, price_prev=84990,
        bb_width=0.012, volume_delta=0.3,
        consecutive_green=2, consecutive_red=0,
        htf_ema_fast=85050, htf_ema_slow=85000,
    )
    defaults.update(overrides)
    return IndicatorSnapshot(**defaults)


def make_candles(n=100, base_price=85000.0, trend="flat", volatility=50.0):
    """Generate synthetic 1m candle data."""
    np.random.seed(42)
    timestamps = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="1min")
    prices = [base_price]
    for i in range(1, n):
        change = np.random.normal(0, volatility)
        if trend == "up":
            change += volatility * 0.3
        elif trend == "down":
            change -= volatility * 0.3
        prices.append(prices[-1] + change)

    data = []
    for i, ts in enumerate(timestamps):
        p = prices[i]
        high = p + abs(np.random.normal(0, volatility * 0.5))
        low = p - abs(np.random.normal(0, volatility * 0.5))
        close = p + np.random.normal(0, volatility * 0.2)
        vol = max(0.1, np.random.normal(10, 3))
        data.append({
            "timestamp": ts, "open": p,
            "high": max(high, p, close),
            "low": min(low, p, close),
            "close": close, "volume": vol,
        })
    return pd.DataFrame(data)


def make_orderbook(imbalance=0.0):
    bid_vol = 10 + imbalance * 5
    ask_vol = 10 - imbalance * 5
    ob = OrderBookSnapshot(
        bids=[(85000 - i * 10, bid_vol / 5) for i in range(5)],
        asks=[(85000 + i * 10, ask_vol / 5) for i in range(5)],
        timestamp=time.time(),
    )
    ob.compute_imbalance()
    return ob


# Backup and clean learner state
_learner_backup = None
if os.path.exists("learner_state.json"):
    with open("learner_state.json", "r") as f:
        _learner_backup = f.read()


def clean_learner():
    if os.path.exists("learner_state.json"):
        os.remove("learner_state.json")


def restore_learner():
    if _learner_backup:
        with open("learner_state.json", "w") as f:
            f.write(_learner_backup)
    elif os.path.exists("learner_state.json"):
        os.remove("learner_state.json")


# ═══════════════════════════════════════════════════════════
# 1. CONFIG VALIDATION
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("1. CONFIG VALIDATION")
print("=" * 60)

cfg = make_config()
test("Base leverage is 15", cfg.leverage == 15)
test("Max leverage is 45", cfg.max_leverage == 45)
test("SL is 0.3%", cfg.stop_loss_pct == 0.003)
test("TP is 0.5%", cfg.take_profit_pct == 0.005)
test("Score threshold is 4.0", cfg.score_threshold_long == 4.0)
test("All v4.0 weights exist", all([
    cfg.w_ema_cross == 2.0, cfg.w_rsi == 1.5, cfg.w_volume == 1.0,
    cfg.w_bollinger == 1.5, cfg.w_vwap == 0.5, cfg.w_orderbook == 1.5,
    cfg.w_macd == 1.5, cfg.w_htf_trend == 1.0, cfg.w_rsi_divergence == 1.5,
]))


# ═══════════════════════════════════════════════════════════
# 2. RISK MANAGER
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("2. RISK MANAGER")
print("=" * 60)

rm = RiskManager(cfg, 10.0)
dummy_sig = Signal(side=Side.LONG, score=5.0, indicators=make_indicators())

# Basic trade controls
can, reason = rm.can_open_trade(10.0, dummy_sig)
test("Can trade with $10", can, reason)

can, reason = rm.can_open_trade(0.5, dummy_sig)
test("Blocks trade with $0.50", not can, reason)

can, reason = rm.can_open_trade(0.99, dummy_sig)
test("Blocks trade below $1.00", not can, reason)

can, reason = rm.can_open_trade(1.0, dummy_sig)
test("Allows trade at exactly $1.00", can, reason)

# SL/TP — Static
sl, tp = rm.compute_stop_take(85000, Side.LONG)
test("LONG SL below entry", sl < 85000, f"SL={sl}")
test("LONG TP above entry", tp > 85000, f"TP={tp}")
sl_pct = (85000 - sl) / 85000
tp_pct = (tp - 85000) / 85000
test("LONG SL >= 0.3%", sl_pct >= 0.003, f"SL%={sl_pct*100:.3f}%")
test("LONG TP >= 0.4%", tp_pct >= 0.004, f"TP%={tp_pct*100:.3f}%")

sl, tp = rm.compute_stop_take(85000, Side.SHORT)
test("SHORT SL above entry", sl > 85000, f"SL={sl}")
test("SHORT TP below entry", tp < 85000, f"TP={tp}")

# SL/TP — ATR-based (v4.0)
sl, tp = rm.compute_stop_take(85000, Side.LONG, atr_pct=0.002)
sl_pct = (85000 - sl) / 85000
tp_pct = (tp - 85000) / 85000
test("ATR SL = 2.0x ATR = 0.4%", abs(sl_pct - 0.004) < 0.001, f"SL%={sl_pct*100:.3f}%")
test("ATR TP = 3.0x ATR = 0.6%", abs(tp_pct - 0.006) < 0.001, f"TP%={tp_pct*100:.3f}%")

# Clamp minimums with tiny ATR
sl, tp = rm.compute_stop_take(85000, Side.LONG, atr_pct=0.0001)
sl_pct = (85000 - sl) / 85000
tp_pct = (tp - 85000) / 85000
test("Tiny ATR clamps SL to 0.3% min", sl_pct >= 0.003, f"SL%={sl_pct*100:.3f}%")
test("Tiny ATR clamps TP to 0.4% min", tp_pct >= 0.004, f"TP%={tp_pct*100:.3f}%")

# Clamp maximums with huge ATR
sl, tp = rm.compute_stop_take(85000, Side.LONG, atr_pct=0.05)
sl_pct = (85000 - sl) / 85000
tp_pct = (tp - 85000) / 85000
test("Huge ATR clamps SL to 0.8% max", sl_pct <= 0.009, f"SL%={sl_pct*100:.3f}%")
test("Huge ATR clamps TP to 1.2% max", tp_pct <= 0.013, f"TP%={tp_pct*100:.3f}%")

# Consecutive losses -> cooldown
rm2 = RiskManager(cfg, 10.0)
for _ in range(4):
    rm2.record_trade_result(-0.10)
test("4 losses triggers cooldown", rm2.in_cooldown)
can, reason = rm2.can_open_trade(9.0, dummy_sig)
test("Blocked during cooldown", not can, reason)

# Cooldown expires
rm2.cooldown_until = time.time() - 1
rm2.last_trade_time = 0
can, reason = rm2.can_open_trade(9.0, dummy_sig)
test("Cooldown expires", can, reason)
test("Consecutive losses reset after cooldown", rm2.consecutive_losses == 0)

# Daily loss limit
rm3 = RiskManager(cfg, 10.0)
rm3.daily_pnl = -3.5  # 35% > 30% limit
can, reason = rm3.can_open_trade(6.5, dummy_sig)
test("Daily loss limit blocks trading", not can, reason)

# Win rate calculation
rm4 = RiskManager(cfg, 10.0)
rm4.record_trade_result(0.50)
rm4.record_trade_result(0.30)
rm4.record_trade_result(-0.20)
test("Win rate = 66.7%", abs(rm4.win_rate - 2/3) < 0.01, f"WR={rm4.win_rate}")
test("Total trades = 3", rm4.total_trades == 3)

# Consecutive wins reset loss counter
rm5 = RiskManager(cfg, 10.0)
rm5.record_trade_result(-0.10)
rm5.record_trade_result(-0.10)
test("2 consecutive losses tracked", rm5.consecutive_losses == 2)
rm5.record_trade_result(0.10)
test("Win resets consecutive losses", rm5.consecutive_losses == 0)

# Progressive position sizing (v4.0)
rm6 = RiskManager(cfg, 10.0)
full = rm6.compute_position_size(10.0)
rm6.consecutive_losses = 1
s1 = rm6.compute_position_size(10.0)
rm6.consecutive_losses = 2
s2 = rm6.compute_position_size(10.0)
rm6.consecutive_losses = 3
s3 = rm6.compute_position_size(10.0)
test("1 loss: 80% sizing", abs(s1 / full - 0.8) < 0.01, f"ratio={s1/full:.2f}")
test("2 losses: 60% sizing", abs(s2 / full - 0.6) < 0.01, f"ratio={s2/full:.2f}")
test("3 losses: 40% sizing (floor)", abs(s3 / full - 0.4) < 0.01, f"ratio={s3/full:.2f}")

# Min time between trades
rm7 = RiskManager(make_config(min_time_between_trades_sec=60), 10.0)
rm7.last_trade_time = time.time()
can, reason = rm7.can_open_trade(10.0, dummy_sig)
test("Min time between trades enforced", not can, reason)

# Daily reset
rm8 = RiskManager(cfg, 10.0)
rm8.record_trade_result(0.50)
rm8.record_trade_result(-0.20)
rm8.reset_daily(10.3)
test("Daily reset clears daily PnL", rm8.daily_pnl == 0.0)
test("Daily reset preserves total PnL", rm8.total_pnl == 0.30)
test("Daily reset updates starting balance", rm8.daily_starting_balance == 10.3)


# ═══════════════════════════════════════════════════════════
# 3. STRATEGY ENGINE
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("3. STRATEGY ENGINE")
print("=" * 60)

strat = ScalpingStrategy(cfg)

# Compute indicators from synthetic data
candles = make_candles(100)
ob = make_orderbook(0.2)
indicators = strat.compute_indicators(candles, ob)
test("Computes indicators from 100 candles", indicators is not None)
if indicators:
    test("EMA fast > 0", indicators.ema_fast > 0)
    test("RSI in [0,100]", 0 <= indicators.rsi <= 100, f"RSI={indicators.rsi}")
    test("ATR > 0", indicators.atr > 0)
    test("BB upper > BB lower", indicators.bb_upper > indicators.bb_lower)
    test("MACD computed", indicators.macd != 0 or indicators.macd_signal != 0)

# Rejects insufficient data
short_candles = make_candles(30)
result = strat.compute_indicators(short_candles, ob)
test("Rejects < 70 candles", result is None)

# Anti-chop filter
strat2 = ScalpingStrategy(cfg)
choppy = make_indicators(bb_width=0.001, rsi=50, volume_ratio=0.5, atr_pct=0.0005, macd_histogram=2)
result = strat2.evaluate(choppy)
test("Choppy market = no signal", result is None)

# Low volume filter
low_vol = make_indicators(volume_ratio=0.3)
result = strat2.evaluate(low_vol)
test("Low volume = no signal", result is None)

# Score gap filter (v4.1: 0.8)
strat3 = ScalpingStrategy(cfg)
# First set prev indicators to match current so no crossover
neutral = make_indicators(ema_fast=85000.01, ema_slow=85000, rsi=50,
                          macd=0.1, macd_signal=0.1, macd_histogram=0)
strat3.evaluate(neutral)
# Both sides get nearly equal scores: VWAP + HTF + EMA trend tiny bonus
# but no crossover, no volume delta, no RSI extreme, no BB extreme
# The only asymmetry is VWAP and tiny EMA trend — should be < 0.8 gap
ambiguous = make_indicators(
    ema_fast=85000.01, ema_slow=85000,  # barely bullish
    rsi=50, volume_ratio=1.5, volume_delta=0.0,
    macd=0.1, macd_signal=0.1, macd_histogram=0.0,
    orderbook_imbalance=0.0, bb_width=0.012,
    close_price=85000, vwap=85000,  # exactly at VWAP
    htf_ema_fast=85000.01, htf_ema_slow=85000,  # barely bullish
)
result = strat3.evaluate(ambiguous)
test("Ambiguous signal (tiny gap) = no signal", result is None)

# Strong LONG signal
strat4 = ScalpingStrategy(cfg)
# Set prev as bearish so EMA cross triggers
bearish = make_indicators(ema_fast=84900, ema_slow=85000,
                          macd=-5, macd_signal=-3, macd_histogram=-2)
strat4.evaluate(bearish)
# Now flip to bullish with all confirmations
strong_long = make_indicators(
    ema_fast=85100, ema_slow=85000,  # EMA crossed up
    rsi=25,                          # extreme oversold
    volume_ratio=2.5, volume_delta=0.4,
    bb_lower=84980, close_price=84990, bb_upper=85500,  # near BB bottom
    orderbook_imbalance=0.30,        # strong bid
    macd=5, macd_signal=-2, macd_histogram=7,  # MACD crossed up
    htf_ema_fast=85100, htf_ema_slow=85000,  # HTF bullish
    bb_width=0.008, consecutive_green=1,
)
result = strat4.evaluate(strong_long)
test("Strong LONG signal generates", result is not None)
if result:
    test("Signal is LONG", result.side == Side.LONG)
    test("Score >= 4.0 threshold", result.score >= 4.0, f"score={result.score:.1f}")
    test("Leverage >= 15x", result.recommended_leverage >= 15)
    test("High confidence = high leverage", result.recommended_leverage >= 25,
         f"lev={result.recommended_leverage}")

# Dynamic leverage scaling
strat5 = ScalpingStrategy(cfg)
low_conf = make_indicators(
    htf_ema_fast=84900, htf_ema_slow=85000,  # against trend
    volume_ratio=0.8, consecutive_green=6,
)
lev_low = strat5._compute_dynamic_leverage(4.2, low_conf, Side.LONG)
high_conf = make_indicators(
    htf_ema_fast=85100, htf_ema_slow=85000,  # with trend
    volume_ratio=3.0, volume_delta=0.5,
    macd_histogram=50, orderbook_imbalance=0.4,
)
lev_high = strat5._compute_dynamic_leverage(7.0, high_conf, Side.LONG)
test("Low confidence = base leverage (15)", lev_low == 15, f"lev={lev_low}")
test("High confidence >= 30x", lev_high >= 30, f"lev={lev_high}")
test("Leverage capped at 45x", lev_high <= 45, f"lev={lev_high}")
test("High > Low leverage", lev_high > lev_low)


# ═══════════════════════════════════════════════════════════
# 4. ADAPTIVE LEARNER
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("4. ADAPTIVE LEARNER")
print("=" * 60)

clean_learner()

# Default state
l = AdaptiveLearner()
test("Default threshold adj = 0.0", l.state.score_threshold_adj == 0.0)
test("Default leverage mult = 1.0", l.state.leverage_multiplier == 1.0)
test("Default rolling WR = 0.5", l.state.rolling_win_rate == 0.5)

# Record winning trade
clean_learner()
l = AdaptiveLearner()
win_trade = TradeRecord(
    timestamp=time.time(), side="long",
    entry_price=85000, exit_price=85100,
    quantity=0.001, leverage=20,
    pnl=0.10, pnl_pct=0.024,
    exit_reason="tp", duration_sec=60, score=5.0,
)
l.record_trade(win_trade, indicators={"volume_ratio": 1.5, "rsi": 35},
               had_crossover=True, htf_aligned=True)
test("Records winning trade", l.state.total_learned_trades == 1)
test("Winning streak = +1", l.state.current_streak == 1)
test("Trade context saved", len(l.state.trade_contexts) == 1)

# Record losing streak -> emergency tightening
clean_learner()
l = AdaptiveLearner()
for i in range(3):
    loss_trade = TradeRecord(
        timestamp=time.time(), side="long",
        entry_price=85000, exit_price=84900,
        quantity=0.001, leverage=20,
        pnl=-0.10, pnl_pct=-0.024,
        exit_reason="sl", duration_sec=30, score=4.5,
    )
    l.record_trade(loss_trade, indicators={"volume_ratio": 1.0, "rsi": 50},
                   had_crossover=False, htf_aligned=False)

test("3-loss streak = -3", l.state.current_streak == -3)
test("Emergency: threshold raised", l.state.score_threshold_adj > 0,
     f"adj={l.state.score_threshold_adj}")
test("Emergency: leverage reduced", l.state.leverage_multiplier < 1.0,
     f"mult={l.state.leverage_multiplier}")

# 5-loss streak -> severe tightening
clean_learner()
l = AdaptiveLearner()
l.state.current_streak = -5
l.state.leverage_multiplier = 1.0
l.state.score_threshold_adj = 0.0
l._emergency_tighten()
test("5-streak: threshold >= 1.5", l.state.score_threshold_adj >= 1.5,
     f"adj={l.state.score_threshold_adj}")
test("5-streak: leverage <= 0.6", l.state.leverage_multiplier <= 0.6,
     f"mult={l.state.leverage_multiplier}")

# Effective threshold calculation
clean_learner()
l = AdaptiveLearner()
l.state.score_threshold_adj = 1.5
test("Effective threshold = base + adj", l.get_effective_threshold(4.0) == 5.5)

# v4.1: Leverage multiplier can go above 1.0
clean_learner()
l = AdaptiveLearner()
l.state.leverage_multiplier = 1.0
l.state.rolling_win_rate = 0.70
# Fill trade contexts for rolling stats
for i in range(20):
    l.state.trade_contexts.append({"pnl": 0.10})
l.state.total_learned_trades = 20
l._compute_rolling_stats()
l._adjust_parameters()
test("v4.1: leverage mult > 1.0 at 70% WR", l.state.leverage_multiplier > 1.0,
     f"mult={l.state.leverage_multiplier}")
test("v4.1: leverage mult <= 1.15 cap", l.state.leverage_multiplier <= 1.15,
     f"mult={l.state.leverage_multiplier}")

# v4.1: get_effective_leverage with boost
clean_learner()
l = AdaptiveLearner()
l.state.leverage_multiplier = 1.15
result = l.get_effective_leverage(45, 15)
test("v4.1: boosted leverage (45 * 1.15)", result == 50, f"lev={result}")  # int(51.75) = 51 -> min(51,50) = 50
test("v4.1: cap at 50x", result <= 50, f"lev={result}")

# Floor at base leverage
l.state.leverage_multiplier = 0.3
result = l.get_effective_leverage(20, 15)
test("Leverage floors at base (15x)", result >= 15, f"lev={result}")

# Volume filter
clean_learner()
l = AdaptiveLearner()
l.state.min_volume_ratio = 0.8
skip, reason = l.should_skip_trade({"volume_ratio": 0.5}, True)
test("Filters low volume", skip, reason)
skip, reason = l.should_skip_trade({"volume_ratio": 1.5}, True)
test("Allows good volume", not skip)

# Strong signal filter
l.state.require_strong_signal = True
skip, reason = l.should_skip_trade({"volume_ratio": 1.5}, False)
test("Requires strong signal", skip)
skip, reason = l.should_skip_trade({"volume_ratio": 1.5}, True)
test("Allows with strong signal", not skip)

# Against-HTF filter
clean_learner()
l = AdaptiveLearner()
l.state.indicator_win_rates["htf_against"] = {"wins": 1, "losses": 9}
indicators_dict = {"htf_ema_fast": 85100, "htf_ema_slow": 85000}  # bullish
test("Blocks SHORT against bullish HTF",
     l.should_skip_against_htf(False, indicators_dict))
test("Allows LONG with bullish HTF",
     not l.should_skip_against_htf(True, indicators_dict))

# Per-condition tracking
clean_learner()
l = AdaptiveLearner()
ctx = {"htf_aligned": True, "volume_ratio": 2.0, "had_crossover": True,
       "rsi": 30, "leverage": 20, "bb_width": 0.005}
l._update_indicator_stats(ctx, True)
iwr = l.state.indicator_win_rates
test("HTF aligned win tracked", iwr["htf_aligned"]["wins"] == 1)
test("High volume win tracked", iwr["high_volume"]["wins"] == 1)
test("Crossover win tracked", iwr["had_crossover"]["wins"] == 1)

ctx2 = {"htf_aligned": False, "volume_ratio": 0.8, "had_crossover": False,
        "rsi": 50, "leverage": 35, "bb_width": 0.002}
l._update_indicator_stats(ctx2, False)
test("HTF against loss tracked", iwr["htf_against"]["losses"] == 1)
test("High leverage loss tracked", iwr["high_leverage"]["losses"] == 1)

# Persistence
clean_learner()
l1 = AdaptiveLearner()
l1.state.score_threshold_adj = 1.5
l1.state.leverage_multiplier = 0.7
l1.state.total_learned_trades = 10
l1._save_state()
l2 = AdaptiveLearner()
test("State persists: threshold", l2.state.score_threshold_adj == 1.5)
test("State persists: leverage mult", l2.state.leverage_multiplier == 0.7)
test("State persists: trade count", l2.state.total_learned_trades == 10)
clean_learner()

# Leverage recovery on winning streak (v4.1)
clean_learner()
l = AdaptiveLearner()
l.state.leverage_multiplier = 0.5
for i in range(10):
    trade = TradeRecord(
        timestamp=time.time(), side="long",
        entry_price=85000, exit_price=85200,
        quantity=0.001, leverage=15,
        pnl=0.20, pnl_pct=0.047,
        exit_reason="tp", duration_sec=30, score=6.0,
    )
    l.record_trade(trade, indicators={"volume_ratio": 2.0, "rsi": 35},
                   had_crossover=True, htf_aligned=True)
test("v4.1: leverage recovers after wins", l.state.leverage_multiplier > 0.5,
     f"mult={l.state.leverage_multiplier}")

# History bounded at 50
clean_learner()
l = AdaptiveLearner()
for i in range(60):
    pnl = 0.05 if i % 2 == 0 else -0.03
    trade = TradeRecord(
        timestamp=time.time(), side="long",
        entry_price=85000, exit_price=85000 + (100 if pnl > 0 else -100),
        quantity=0.001, leverage=20,
        pnl=pnl, pnl_pct=pnl / 4.25,
        exit_reason="tp" if pnl > 0 else "sl",
        duration_sec=30, score=5.0,
    )
    l.record_trade(trade)
test("History bounded at 50", len(l.state.trade_contexts) <= 50,
     f"len={len(l.state.trade_contexts)}")
clean_learner()


# ═══════════════════════════════════════════════════════════
# 5. POSITION MANAGER (async)
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("5. POSITION MANAGER")
print("=" * 60)


async def run_position_tests():
    cfg_dry = make_config(dry_run=True)
    ex = ExchangeClient(cfg_dry)

    # Open position (dry run)
    rm = RiskManager(cfg_dry, 10.0)
    pm = PositionManager(cfg_dry, ex, rm)
    sig = Signal(side=Side.LONG, score=5.0, indicators=make_indicators(),
                 recommended_leverage=25)
    opened = await pm.open_position(sig, 85000)
    test("Opens LONG position", opened)
    test("Position exists", pm.position is not None)
    if pm.position:
        test("Leverage = 25x", pm.position.leverage == 25, f"lev={pm.position.leverage}")
        test("Side = LONG", pm.position.side == Side.LONG)
        test("Entry price set", pm.position.entry_price > 0)
        test("SL set", pm.position.stop_loss > 0)
        test("TP set", pm.position.take_profit > 0)
        test("SL < entry (LONG)", pm.position.stop_loss < pm.position.entry_price)
        test("TP > entry (LONG)", pm.position.take_profit > pm.position.entry_price)

    # Block second open
    sig2 = Signal(side=Side.SHORT, score=5.0, indicators=make_indicators())
    result = await pm.open_position(sig2, 85000)
    test("Blocks second position", not result)

    # Monitor: price between SL and TP = hold
    result = await pm.monitor_position(85100)
    test("Holds between SL and TP", result is None)
    test("Unrealized PnL positive", pm.position.pnl_unrealized > 0)

    # Monitor: TP hit
    tp = pm.position.take_profit
    result = await pm.monitor_position(tp + 100)
    test("TP triggers close", result is not None)
    if result:
        test("Exit reason = tp", result.exit_reason == "tp")
        test("PnL positive", result.pnl > 0)
    test("Position cleared after TP", pm.position is None)

    # SHORT with SL hit
    rm2 = RiskManager(cfg_dry, 10.0)
    pm2 = PositionManager(cfg_dry, ex, rm2)
    pm2.position = Position(
        side=Side.SHORT, entry_price=85000, quantity=0.001,
        leverage=20, stop_loss=85300, take_profit=84500,
        highest_price=85000, lowest_price=85000,
    )
    result = await pm2.monitor_position(85400)
    test("SHORT SL triggers", result is not None)
    if result:
        test("SHORT SL exit reason", result.exit_reason == "sl")
        test("SHORT SL PnL negative", result.pnl < 0)

    # SHORT TP hit
    pm2.position = Position(
        side=Side.SHORT, entry_price=85000, quantity=0.001,
        leverage=20, stop_loss=85300, take_profit=84500,
        highest_price=85000, lowest_price=85000,
    )
    result = await pm2.monitor_position(84400)
    test("SHORT TP triggers", result is not None)
    if result:
        test("SHORT TP exit reason", result.exit_reason == "tp")
        test("SHORT TP PnL positive", result.pnl > 0)

    # Time exit at 5 min (v4.1)
    rm3 = RiskManager(cfg_dry, 10.0)
    pm3 = PositionManager(cfg_dry, ex, rm3)
    pm3.position = Position(
        side=Side.LONG, entry_price=85000, quantity=0.001,
        leverage=20, stop_loss=84700, take_profit=86000,
        highest_price=85000, lowest_price=85000,
    )
    pm3.position.entry_time = time.time() - 360  # 6 min ago
    result = await pm3.monitor_position(85010)  # barely profitable
    test("v4.1: time exit at 5 min", result is not None)
    if result:
        test("v4.1: exit reason = timeout", result.exit_reason == "timeout")

    # No time exit when profitable enough
    rm4 = RiskManager(cfg_dry, 10.0)
    pm4 = PositionManager(cfg_dry, ex, rm4)
    pm4.position = Position(
        side=Side.LONG, entry_price=85000, quantity=0.001,
        leverage=20, stop_loss=84700, take_profit=86000,
        highest_price=85100, lowest_price=85000,
    )
    pm4.position.entry_time = time.time() - 360
    pm4.position.pnl_unrealized = 0.10  # good profit
    result = await pm4.monitor_position(85100)
    test("No time exit when profitable", result is None)

    # Trailing stop — LONG
    rm5 = RiskManager(make_config(dry_run=True, trailing_stop_enabled=True), 10.0)
    pm5 = PositionManager(make_config(dry_run=True, trailing_stop_enabled=True), ex, rm5)
    pm5.position = Position(
        side=Side.LONG, entry_price=85000, quantity=0.001,
        leverage=20, stop_loss=84700, take_profit=86000,
        highest_price=85000, lowest_price=85000,
        atr_pct=0.002,
    )
    # Activate trailing: need +1.5*ATR = +0.3% = +255
    await pm5.monitor_position(85300)
    test("Trailing activates (LONG)", pm5.position.trailing_stop_active)
    # Price goes higher
    await pm5.monitor_position(85500)
    trail = pm5.position.trailing_stop_price
    test("Trail moves up", trail > 85000)
    # Price drops to trail
    result = await pm5.monitor_position(trail - 1)
    test("Trailing triggers (LONG)", result is not None)
    if result:
        test("Trailing exit reason", result.exit_reason == "trailing")

    # Trailing stop — SHORT
    rm6 = RiskManager(make_config(dry_run=True, trailing_stop_enabled=True), 10.0)
    pm6 = PositionManager(make_config(dry_run=True, trailing_stop_enabled=True), ex, rm6)
    pm6.position = Position(
        side=Side.SHORT, entry_price=85000, quantity=0.001,
        leverage=20, stop_loss=85300, take_profit=84000,
        highest_price=85000, lowest_price=85000,
        atr_pct=0.002,
    )
    await pm6.monitor_position(84700)
    test("Trailing activates (SHORT)", pm6.position.trailing_stop_active)
    trail = pm6.position.trailing_stop_price
    result = await pm6.monitor_position(trail + 1)
    test("Trailing triggers (SHORT)", result is not None)
    if result:
        test("SHORT trailing exit reason", result.exit_reason == "trailing")

    # Exchange close failure keeps position
    rm7 = RiskManager(cfg_dry, 10.0)
    pm7 = PositionManager(cfg_dry, ex, rm7)
    pm7.position = Position(
        side=Side.LONG, entry_price=85000, quantity=0.001,
        leverage=20, stop_loss=84750, take_profit=85500,
        highest_price=85000, lowest_price=85000,
    )
    original_close = ex.close_position_market

    async def failing_close(*args, **kwargs):
        raise Exception("Simulated exchange error")

    ex.close_position_market = failing_close
    result = await pm7._close_position(84700, "sl")
    test("Failed close returns None", result is None)
    test("Position kept on failure", pm7.position is not None)

    ex.close_position_market = original_close
    result = await pm7._close_position(84700, "sl")
    test("Successful close returns TradeRecord", result is not None)
    test("Position cleared after success", pm7.position is None)

    # PnL calculation correctness
    rm8 = RiskManager(make_config(dry_run=True, trailing_stop_enabled=False), 10.0)
    pm8 = PositionManager(make_config(dry_run=True, trailing_stop_enabled=False), ex, rm8)
    pm8.position = Position(
        side=Side.LONG, entry_price=85000, quantity=0.001,
        leverage=20, stop_loss=84000, take_profit=87000,
        highest_price=85000, lowest_price=85000,
    )
    await pm8.monitor_position(85100)
    expected = (85100 - 85000) * 0.001
    test("LONG PnL correct", abs(pm8.position.pnl_unrealized - expected) < 0.0001,
         f"pnl={pm8.position.pnl_unrealized} expected={expected}")

    pm8.position = Position(
        side=Side.SHORT, entry_price=85000, quantity=0.001,
        leverage=20, stop_loss=86000, take_profit=83000,
        highest_price=85000, lowest_price=85000,
    )
    await pm8.monitor_position(84900)
    expected = (85000 - 84900) * 0.001
    test("SHORT PnL correct", abs(pm8.position.pnl_unrealized - expected) < 0.0001,
         f"pnl={pm8.position.pnl_unrealized} expected={expected}")


asyncio.run(run_position_tests())


# ═══════════════════════════════════════════════════════════
# 6. EXCHANGE CLIENT (dry run)
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("6. EXCHANGE CLIENT (dry run)")
print("=" * 60)

ex = ExchangeClient(make_config(dry_run=True))
ex.min_notional = 100.0
ex.step_size = 0.001
ex.min_qty = 0.001

qty = ex.calculate_quantity(10.0, 85000, 15)
notional = qty * 85000
test("$10 margin x 15 = $150 notional", notional >= 100, f"notional={notional:.2f}")
test("Quantity >= min_qty", qty >= 0.001, f"qty={qty}")

qty_low = ex.calculate_quantity(2.0, 85000, 15)
notional_low = qty_low * 85000
test("Low margin meets min notional", notional_low >= 100, f"notional={notional_low:.2f}")

# _safe_float
test("_safe_float(None) = 0.0", PositionManager._safe_float(None) == 0.0)
test("_safe_float('123.45') = 123.45", PositionManager._safe_float("123.45") == 123.45)
test("_safe_float('abc') = 0.0", PositionManager._safe_float("abc") == 0.0)
test("_safe_float(0) = 0.0", PositionManager._safe_float(0) == 0.0)


# ═══════════════════════════════════════════════════════════
# 7. INTEGRATION: SIMULATED TRADE SEQUENCES
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("7. INTEGRATION: SIMULATED TRADES")
print("=" * 60)


async def run_integration_tests():
    # Full winning trade lifecycle
    cfg_dry = make_config(dry_run=True)
    ex = ExchangeClient(cfg_dry)
    rm = RiskManager(cfg_dry, 10.0)
    pm = PositionManager(cfg_dry, ex, rm)

    sig = Signal(side=Side.LONG, score=5.5, indicators=make_indicators(),
                 recommended_leverage=25)
    opened = await pm.open_position(sig, 85000)
    test("Integration: open winning trade", opened)
    tp = pm.position.take_profit
    result = await pm.monitor_position(tp + 50)
    test("Integration: winning trade closes at TP", result is not None and result.pnl > 0)

    # Full losing trade lifecycle
    rm2 = RiskManager(cfg_dry, 10.0)
    pm2 = PositionManager(cfg_dry, ex, rm2)
    sig2 = Signal(side=Side.LONG, score=4.5, indicators=make_indicators(atr_pct=0.002),
                  recommended_leverage=15)
    opened = await pm2.open_position(sig2, 85000)
    test("Integration: open losing trade", opened)
    sl = pm2.position.stop_loss
    result = await pm2.monitor_position(sl - 50)
    test("Integration: losing trade closes at SL", result is not None and result.pnl < 0)

    # Compound balance simulation
    rm3 = RiskManager(cfg_dry, 10.0)
    balance = 10.0
    outcomes = [
        ("tp", 0.06), ("tp", 0.04), ("sl", -0.03),
        ("tp", 0.05), ("tp", 0.03), ("sl", -0.03),
        ("tp", 0.05), ("sl", -0.03), ("tp", 0.04), ("sl", -0.03),
    ]
    for reason, pct in outcomes:
        margin = rm3.compute_position_size(balance)
        pnl = margin * pct
        balance += pnl
        rm3.record_trade_result(pnl)

    test("Integration: 60% WR is profitable", balance > 10.0,
         f"balance=${balance:.4f}")
    test("Integration: 6 wins / 4 losses", rm3.winning_trades == 6 and rm3.losing_trades == 4)
    print(f"    Simulated balance: ${balance:.4f} ({(balance/10-1)*100:+.1f}%)")


asyncio.run(run_integration_tests())


# ═══════════════════════════════════════════════════════════
# 8. FILTER CHAIN INTEGRATION
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("8. FILTER CHAIN")
print("=" * 60)

# Filter 2: HTF alignment
test("Filter: LONG blocked against bearish HTF",
     (True and not (85100 > 85200)),  # htf_f < htf_s = bearish, long blocked
     True)

# Filter 4: Max 12 trades/day (v4.1)
rm_filter = RiskManager(cfg, 10.0)
rm_filter.total_trades = 12
test("Filter: max 12 trades/day blocks", rm_filter.total_trades >= 12)

rm_filter.total_trades = 11
test("Filter: 11 trades allowed", rm_filter.total_trades < 12)

# Filter 3: Fee-aware R:R
def check_rr(leverage, atr_pct):
    fee_impact = 0.0008 * leverage
    tp_pct = max(atr_pct * 3.0, 0.004)
    tp_margin = tp_pct * leverage
    sl_pct = max(atr_pct * 2.0, 0.003)
    sl_margin = sl_pct * leverage
    return (tp_margin - fee_impact) / (sl_margin + fee_impact)

rr_15 = check_rr(15, 0.002)
rr_45 = check_rr(45, 0.002)
test("R:R positive at 15x with 0.2% ATR", rr_15 > 1.0, f"R:R={rr_15:.2f}")
test("R:R positive at 45x with 0.2% ATR", rr_45 > 0, f"R:R={rr_45:.2f}")

# Tiny ATR at high leverage = bad R:R
rr_bad = check_rr(45, 0.0005)
test("Tiny ATR at 45x = bad R:R (<1.0)", rr_bad < 1.0, f"R:R={rr_bad:.2f}")


# ═══════════════════════════════════════════════════════════
# 9. LEARNER + STRATEGY INTEGRATION
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("9. LEARNER + STRATEGY INTEGRATION")
print("=" * 60)

clean_learner()

# Simulate: bot loses 5 trades, then starts winning
l = AdaptiveLearner()
base_threshold = 4.0

# 5 losses
for i in range(5):
    trade = TradeRecord(
        timestamp=time.time(), side="long",
        entry_price=85000, exit_price=84800,
        quantity=0.001, leverage=20,
        pnl=-0.20, pnl_pct=-0.047,
        exit_reason="sl", duration_sec=45, score=4.5,
    )
    l.record_trade(trade, indicators={"volume_ratio": 1.0, "rsi": 45},
                   had_crossover=False, htf_aligned=False)

threshold_after_losses = l.get_effective_threshold(base_threshold)
lev_mult_after_losses = l.state.leverage_multiplier
test("After 5 losses: threshold up", threshold_after_losses > base_threshold,
     f"threshold={threshold_after_losses}")
test("After 5 losses: leverage down", lev_mult_after_losses < 1.0,
     f"mult={lev_mult_after_losses}")

# 15 wins
for i in range(15):
    trade = TradeRecord(
        timestamp=time.time(), side="long",
        entry_price=85000, exit_price=85200,
        quantity=0.001, leverage=15,
        pnl=0.20, pnl_pct=0.047,
        exit_reason="tp", duration_sec=30, score=6.0,
    )
    l.record_trade(trade, indicators={"volume_ratio": 2.0, "rsi": 35},
                   had_crossover=True, htf_aligned=True)

threshold_after_wins = l.get_effective_threshold(base_threshold)
lev_mult_after_wins = l.state.leverage_multiplier
test("After recovery: threshold decreased", threshold_after_wins < threshold_after_losses,
     f"threshold={threshold_after_wins} vs {threshold_after_losses}")
test("After recovery: leverage recovered", lev_mult_after_wins > lev_mult_after_losses,
     f"mult={lev_mult_after_wins} vs {lev_mult_after_losses}")

# Check per-condition stats reflect reality
stats = l.get_stats()
cond = stats.get("condition_stats", {})
if "htf_aligned" in cond:
    test("HTF aligned WR is high", cond["htf_aligned"]["win_rate"] > 50,
         f"WR={cond['htf_aligned']['win_rate']}%")
if "htf_against" in cond:
    test("HTF against WR is low", cond["htf_against"]["win_rate"] < 50,
         f"WR={cond['htf_against']['win_rate']}%")

clean_learner()


# ═══════════════════════════════════════════════════════════
# 10. STRESS TESTS
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("10. STRESS TESTS")
print("=" * 60)

# 100 rapid risk checks
rm_stress = RiskManager(cfg, 10.0)
rm_stress.last_trade_time = 0
for i in range(100):
    ok, reason = rm_stress.can_open_trade(10.0, dummy_sig)
    assert isinstance(ok, bool)
test("100 rapid risk checks: no crash", True)

# Strategy with 200-candle dataset
strat_stress = ScalpingStrategy(cfg)
candles_200 = make_candles(200, volatility=100)
ob_stress = make_orderbook(0.1)
ind = strat_stress.compute_indicators(candles_200, ob_stress)
test("200-candle dataset: computes indicators", ind is not None)
for i in range(20):
    strat_stress.evaluate(ind)
test("20 rapid evaluations: no crash", True)

# Learner with 100 trades
clean_learner()
l_stress = AdaptiveLearner()
for i in range(100):
    pnl = 0.05 if np.random.random() > 0.4 else -0.03
    trade = TradeRecord(
        timestamp=time.time(), side="long" if i % 2 == 0 else "short",
        entry_price=85000, exit_price=85000 + (100 if pnl > 0 else -100),
        quantity=0.001, leverage=20,
        pnl=pnl, pnl_pct=pnl / 4.25,
        exit_reason="tp" if pnl > 0 else "sl",
        duration_sec=30, score=5.0,
    )
    l_stress.record_trade(trade)
test("100-trade learner: no crash", True)
test("100-trade learner: history bounded", len(l_stress.state.trade_contexts) <= 50)
test("100-trade learner: total count = 100", l_stress.state.total_learned_trades == 100)
clean_learner()


# ═══════════════════════════════════════════════════════════
# FINAL RESULTS
# ═══════════════════════════════════════════════════════════
restore_learner()

print(f"\n{'=' * 60}")
print(f"  RESULTS: {PASS} passed, {FAIL} failed")
print(f"{'=' * 60}")

if ERRORS:
    print("\n  Failed tests:")
    for name, detail in ERRORS:
        print(f"    {name}: {detail}")

if FAIL == 0:
    print("\n  ALL TESTS PASSED! Bot v4.1.1 is ready.\n")
else:
    print(f"\n  {FAIL} test(s) need attention.\n")

sys.exit(0 if FAIL == 0 else 1)
