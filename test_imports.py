"""Quick test to verify all imports work."""
import os
os.environ['DRY_RUN'] = 'true'

from config import load_config, BotConfig
from models import Side, Signal, Position, IndicatorSnapshot, OrderBookSnapshot, TradeRecord
from logger_setup import setup_logging
from utils import round_price, round_quantity, format_pnl
from risk_manager import RiskManager
from strategy import ScalpingStrategy
from position_manager import PositionManager

config = load_config()
print(f"Config loaded: symbol={config.symbol}, leverage={config.leverage}x, dry_run={config.dry_run}")

rm = RiskManager(config, 10.0)
print(f"Risk manager: daily_starting=${rm.daily_starting_balance}")

strat = ScalpingStrategy(config)
print(f"Strategy: threshold={config.score_threshold_long}")

ob = OrderBookSnapshot(
    bids=[(50000, 1.0), (49999, 0.5)],
    asks=[(50001, 0.8), (50002, 0.3)],
    timestamp=0
)
ob.compute_imbalance()
print(f"Orderbook imbalance: {ob.imbalance:.4f}")

print("\nAll imports OK!")
