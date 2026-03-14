"""Test the strategy scoring system with simulated data."""
import os
os.environ['DRY_RUN'] = 'true'

import numpy as np
import pandas as pd
from config import load_config
from models import OrderBookSnapshot, Side
from strategy import ScalpingStrategy

config = load_config()
strategy = ScalpingStrategy(config)

# Generate realistic 1m BTC candles
np.random.seed(42)
n_candles = 100
base_price = 85000.0
prices = [base_price]

for i in range(n_candles - 1):
    change = np.random.normal(0, 15)  # ~$15 std per minute
    prices.append(prices[-1] + change)

candles = []
for i, close in enumerate(prices):
    high = close + abs(np.random.normal(0, 8))
    low = close - abs(np.random.normal(0, 8))
    open_p = close + np.random.normal(0, 5)
    volume = abs(np.random.normal(100, 30))
    candles.append({
        "timestamp": pd.Timestamp("2026-03-14") + pd.Timedelta(minutes=i),
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })

df = pd.DataFrame(candles)

# Simulate different orderbook states
scenarios = [
    ("Neutral OB", OrderBookSnapshot(
        bids=[(85000, 1.0), (84999, 1.0)],
        asks=[(85001, 1.0), (85002, 1.0)],
        timestamp=0,
    )),
    ("Bid-heavy OB", OrderBookSnapshot(
        bids=[(85000, 5.0), (84999, 3.0)],
        asks=[(85001, 1.0), (85002, 0.5)],
        timestamp=0,
    )),
    ("Ask-heavy OB", OrderBookSnapshot(
        bids=[(85000, 0.5), (84999, 0.3)],
        asks=[(85001, 4.0), (85002, 3.0)],
        timestamp=0,
    )),
]

print("=" * 70)
print("  STRATEGY TEST - Scoring System Validation")
print("=" * 70)

for name, ob in scenarios:
    ob.compute_imbalance()
    indicators = strategy.compute_indicators(df, ob)

    if indicators:
        print(f"\n--- {name} (imbalance: {ob.imbalance:+.2%}) ---")
        print(f"  EMA fast/slow: {indicators.ema_fast:.2f} / {indicators.ema_slow:.2f}")
        print(f"  RSI: {indicators.rsi:.1f}")
        print(f"  BB: [{indicators.bb_lower:.2f} | {indicators.bb_middle:.2f} | {indicators.bb_upper:.2f}]")
        print(f"  VWAP: {indicators.vwap:.2f}")
        print(f"  Volume ratio: {indicators.volume_ratio:.2f}x")
        print(f"  Price: ${indicators.close_price:,.2f}")

        signal = strategy.evaluate(indicators)
        if signal:
            print(f"  >>> SIGNAL: {signal.side.value.upper()} (score: {signal.score:.1f})")
        else:
            print(f"  >>> No signal")

# Test with trending data (force a clear uptrend)
print("\n" + "=" * 70)
print("  TRENDING DATA TEST (forced uptrend)")
print("=" * 70)

strategy2 = ScalpingStrategy(config)
trend_prices = [85000 + i * 5 for i in range(100)]  # steady uptrend
trend_candles = []
for i, close in enumerate(trend_prices):
    trend_candles.append({
        "timestamp": pd.Timestamp("2026-03-14") + pd.Timedelta(minutes=i),
        "open": close - 3,
        "high": close + 2,
        "low": close - 5,
        "close": close,
        "volume": 150 if i > 80 else 80,  # volume spike at end
    })

df_trend = pd.DataFrame(trend_candles)
ob_bullish = OrderBookSnapshot(
    bids=[(85500, 5.0), (85499, 3.0)],
    asks=[(85501, 1.0), (85502, 0.5)],
    timestamp=0,
)
ob_bullish.compute_imbalance()

# Run two evaluations (need prev for crossover detection)
indicators1 = strategy2.compute_indicators(df_trend.iloc[:99], ob_bullish)
signal1 = strategy2.evaluate(indicators1)
indicators2 = strategy2.compute_indicators(df_trend, ob_bullish)
signal2 = strategy2.evaluate(indicators2)

if indicators2:
    print(f"  EMA fast/slow: {indicators2.ema_fast:.2f} / {indicators2.ema_slow:.2f}")
    print(f"  RSI: {indicators2.rsi:.1f}")
    print(f"  Volume ratio: {indicators2.volume_ratio:.2f}x")
    print(f"  OB imbalance: {ob_bullish.imbalance:+.2%}")
    if signal2:
        print(f"  >>> SIGNAL: {signal2.side.value.upper()} (score: {signal2.score:.1f})")
    else:
        print(f"  >>> No signal")

print("\nStrategy tests complete!")
