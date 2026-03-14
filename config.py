"""Centralized configuration for the scalping bot."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class BotConfig:
    # --- API ---
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = True

    # --- Trading pair ---
    symbol: str = "BTC/USDT"
    raw_symbol: str = "BTCUSDT"

    # --- Leverage & position ---
    leverage: int = 15                    # base leverage (low confidence)
    max_leverage: int = 45               # max leverage (extreme confidence)
    margin_type: str = "isolated"
    max_position_pct: float = 1.0
    max_open_positions: int = 1

    # --- Timeframe ---
    kline_interval: str = "1m"
    kline_limit: int = 100

    # --- Indicator parameters ---
    ema_fast: int = 5
    ema_slow: int = 13
    rsi_period: int = 7
    bb_period: int = 20
    bb_std: float = 2.0
    volume_avg_period: int = 20
    orderbook_depth: int = 10

    # --- Scoring weights (v3.0) ---
    score_threshold_long: float = 3.0
    score_threshold_short: float = 3.0
    w_ema_cross: float = 2.0
    w_rsi: float = 1.5
    w_volume: float = 1.0
    w_bollinger: float = 1.5
    w_vwap: float = 0.5
    w_orderbook: float = 1.5
    w_macd: float = 1.5
    w_htf_trend: float = 1.0
    w_rsi_divergence: float = 1.5

    # --- Risk management ---
    stop_loss_pct: float = 0.003      # 0.3%
    take_profit_pct: float = 0.005    # 0.5%
    trailing_stop_enabled: bool = True
    trailing_stop_activation_pct: float = 0.003  # activate after 0.3% profit
    trailing_stop_callback_pct: float = 0.002    # trail by 0.2%
    max_daily_loss_pct: float = 0.30             # 30% of starting daily balance
    max_consecutive_losses: int = 4
    cooldown_seconds: int = 300

    # --- Operational ---
    min_time_between_trades_sec: int = 10
    dry_run: bool = False
    log_level: str = "INFO"
    trade_journal_path: str = "trades.csv"

    # --- WebSocket URLs ---
    @property
    def ws_base_url(self) -> str:
        if self.testnet:
            return "wss://stream.binancefuture.com/ws/"
        return "wss://fstream.binance.com/ws/"

    @property
    def rest_base_url(self) -> str:
        if self.testnet:
            return "https://testnet.binancefuture.com"
        return "https://fapi.binance.com"


def load_config() -> BotConfig:
    """Load configuration from .env file and environment variables."""
    load_dotenv()

    config = BotConfig(
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
        testnet=os.getenv("TESTNET", "true").lower() == "true",
        dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
    )

    # Override from env if provided
    if os.getenv("LEVERAGE"):
        config.leverage = int(os.getenv("LEVERAGE"))
    if os.getenv("STOP_LOSS_PCT"):
        config.stop_loss_pct = float(os.getenv("STOP_LOSS_PCT"))
    if os.getenv("TAKE_PROFIT_PCT"):
        config.take_profit_pct = float(os.getenv("TAKE_PROFIT_PCT"))
    if os.getenv("SCORE_THRESHOLD"):
        config.score_threshold_long = float(os.getenv("SCORE_THRESHOLD"))
        config.score_threshold_short = float(os.getenv("SCORE_THRESHOLD"))

    _validate_config(config)
    return config


def _validate_config(config: BotConfig) -> None:
    """Validate configuration values."""
    if not config.api_key or not config.api_secret:
        if not config.dry_run:
            raise ValueError("API key and secret are required when not in dry-run mode")

    if config.leverage < 1 or config.leverage > 125:
        raise ValueError(f"Leverage must be between 1 and 125, got {config.leverage}")

    if config.stop_loss_pct <= 0 or config.stop_loss_pct > 0.1:
        raise ValueError(f"Stop loss must be between 0 and 10%, got {config.stop_loss_pct}")

    if config.take_profit_pct <= 0 or config.take_profit_pct > 0.1:
        raise ValueError(f"Take profit must be between 0 and 10%, got {config.take_profit_pct}")

    if config.max_daily_loss_pct <= 0 or config.max_daily_loss_pct > 1.0:
        raise ValueError(f"Max daily loss must be between 0 and 100%, got {config.max_daily_loss_pct}")
