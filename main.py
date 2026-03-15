"""
Binance Futures BTC/USDT Aggressive Scalping Bot
=================================================
Multi-indicator scoring strategy with risk management.
Integrated Flask dashboard for Railway deployment.

Usage:
    python main.py

Configuration via .env file (see .env.example)

DISCLAIMER: Trading cryptocurrencies involves substantial risk.
This bot is for educational purposes. Use at your own risk.
"""

import asyncio
import logging
import os
import signal
import sys
import threading
import time

from flask import Flask, render_template_string
from flask_socketio import SocketIO

from bot_state import (
    build_state, save_state, load_state, init_state,
    add_trade_to_history, add_equity_snapshot,
)
from config import load_config, BotConfig
from dashboard import DASHBOARD_HTML
from learner import AdaptiveLearner
from market_analysis import generate_market_analysis
from data_feed import DataFeed
from exchange import ExchangeClient
from logger_setup import setup_logging
from models import Side, Signal
from position_manager import PositionManager
from risk_manager import RiskManager
from strategy import ScalpingStrategy
from utils import format_pnl, format_pct

logger = logging.getLogger("scalper")

# ─── Global shared state for dashboard ───
_current_state: dict = {}
_state_lock = threading.Lock()


def update_shared_state(state: dict):
    global _current_state
    with _state_lock:
        _current_state = state
    save_state(state)


def get_shared_state() -> dict:
    with _state_lock:
        return _current_state.copy()


# ─── Flask Dashboard ───
app = Flask(__name__)
app.config["SECRET_KEY"] = "scalper-bot-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/state")
def api_state():
    import json
    state = get_shared_state()
    return json.dumps(state), 200, {"Content-Type": "application/json"}


def run_dashboard(port: int):
    """Run the Flask dashboard in a background thread."""
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True, log_output=False)


def emit_state_update(state: dict):
    """Emit state to all connected dashboard clients."""
    try:
        socketio.emit("state_update", state)
    except Exception:
        pass


# ─── Bot Logic ───

async def position_monitor_loop(
    position_manager: PositionManager,
    data_feed: DataFeed,
    learner: AdaptiveLearner,
    last_indicators_ref: list,
    strategy_ref: list,
):
    """Monitor position on every price tick (runs every 200ms)."""
    verify_counter = 0
    while True:
        try:
            price = data_feed.get_last_price()
            if price > 0 and position_manager.position is not None:
                trade = await position_manager.monitor_position(price)
                if trade:
                    add_trade_to_history(trade)
                    # Record trade in learner with context
                    strat = strategy_ref[0] if strategy_ref else None
                    learner.record_trade(
                        trade,
                        indicators=last_indicators_ref[0] if last_indicators_ref else None,
                        had_crossover=strat.last_had_crossover if strat else False,
                        htf_aligned=strat.last_htf_aligned if strat else False,
                    )
                    logger.info(f"Position closed by monitor loop: {trade.exit_reason}")

                # Verify position exists on Binance every ~30 seconds
                verify_counter += 1
                if verify_counter >= 150:  # 150 * 0.2s = 30s
                    verify_counter = 0
                    await position_manager.verify_position_exists()
        except Exception as e:
            logger.error(f"Position monitor error: {e}")

        await asyncio.sleep(0.2)


async def daily_reset_loop(risk_manager: RiskManager, exchange: ExchangeClient):
    """Reset daily stats at UTC midnight."""
    while True:
        now = time.time()
        tomorrow = (int(now // 86400) + 1) * 86400
        wait_seconds = tomorrow - now + 5

        await asyncio.sleep(wait_seconds)

        try:
            balance = await exchange.fetch_balance()
            risk_manager.reset_daily(balance)
        except Exception as e:
            logger.error(f"Daily reset error: {e}")


async def compute_equity(exchange: ExchangeClient, position_manager: PositionManager) -> tuple[float, float]:
    """Compute free balance and total equity (balance + margin + unrealized PnL)."""
    free_balance = await exchange.fetch_balance()
    equity = free_balance

    if position_manager.position is not None:
        pos = position_manager.position
        margin = (pos.quantity * pos.entry_price) / pos.leverage
        equity = free_balance + margin + pos.pnl_unrealized

    return free_balance, equity


def build_indicators_dict(indicators) -> dict:
    """Build full indicators dict from IndicatorSnapshot for dashboard."""
    if indicators is None:
        return {}

    bb_range = indicators.bb_upper - indicators.bb_lower
    bb_position = (
        (indicators.close_price - indicators.bb_lower) / bb_range
        if bb_range > 0 else 0.5
    )

    return {
        "ema_fast": indicators.ema_fast,
        "ema_slow": indicators.ema_slow,
        "rsi": indicators.rsi,
        "rsi_prev": indicators.rsi_prev,
        "macd": indicators.macd,
        "macd_signal": indicators.macd_signal,
        "macd_histogram": indicators.macd_histogram,
        "atr": indicators.atr,
        "atr_pct": indicators.atr_pct,
        "volume_ratio": indicators.volume_ratio,
        "volume_delta": indicators.volume_delta,
        "imbalance": indicators.orderbook_imbalance,
        "bb_position": bb_position,
        "bb_width": indicators.bb_width,
        "bb_upper": indicators.bb_upper,
        "bb_lower": indicators.bb_lower,
        "vwap": indicators.vwap,
        "htf_ema_fast": indicators.htf_ema_fast,
        "htf_ema_slow": indicators.htf_ema_slow,
        "consecutive_green": indicators.consecutive_green,
        "consecutive_red": indicators.consecutive_red,
        "close_price": indicators.close_price,
    }


def build_score_breakdown(indicators, config) -> dict:
    """Build individual indicator score contributions for dashboard display."""
    if indicators is None:
        return {}

    bd = {}

    # EMA
    if indicators.ema_fast > indicators.ema_slow:
        bd["long_ema"] = round(config.w_ema_cross * 0.3, 1)
        bd["short_ema"] = 0
    else:
        bd["long_ema"] = 0
        bd["short_ema"] = round(config.w_ema_cross * 0.3, 1)

    # RSI
    bd["long_rsi"] = 0
    bd["short_rsi"] = 0
    if indicators.rsi < 25:
        bd["long_rsi"] = config.w_rsi
    elif indicators.rsi < 35:
        bd["long_rsi"] = round(config.w_rsi * 0.7, 1)
    elif indicators.rsi < 45:
        bd["long_rsi"] = round(config.w_rsi * 0.3, 1)
    if indicators.rsi > 75:
        bd["short_rsi"] = config.w_rsi
    elif indicators.rsi > 65:
        bd["short_rsi"] = round(config.w_rsi * 0.7, 1)
    elif indicators.rsi > 55:
        bd["short_rsi"] = round(config.w_rsi * 0.3, 1)

    # MACD
    bd["long_macd"] = round(config.w_macd * 0.3, 1) if indicators.macd_histogram > 0 else 0
    bd["short_macd"] = round(config.w_macd * 0.3, 1) if indicators.macd_histogram < 0 else 0

    # Volume
    bd["long_volume"] = 0
    bd["short_volume"] = 0
    if indicators.volume_ratio > 2.0:
        bd["long_volume"] = config.w_volume
        bd["short_volume"] = config.w_volume
    elif indicators.volume_ratio > 1.3:
        bd["long_volume"] = round(config.w_volume * 0.5, 1)
        bd["short_volume"] = round(config.w_volume * 0.5, 1)

    # Bollinger
    bb_range = indicators.bb_upper - indicators.bb_lower
    bb_pos = (indicators.close_price - indicators.bb_lower) / bb_range if bb_range > 0 else 0.5
    bd["long_bollinger"] = 0
    bd["short_bollinger"] = 0
    if bb_pos < 0.10:
        bd["long_bollinger"] = config.w_bollinger
    elif bb_pos < 0.25:
        bd["long_bollinger"] = round(config.w_bollinger * 0.5, 1)
    if bb_pos > 0.90:
        bd["short_bollinger"] = config.w_bollinger
    elif bb_pos > 0.75:
        bd["short_bollinger"] = round(config.w_bollinger * 0.5, 1)

    # VWAP
    if indicators.close_price > indicators.vwap:
        bd["long_vwap"] = config.w_vwap
        bd["short_vwap"] = 0
    else:
        bd["long_vwap"] = 0
        bd["short_vwap"] = config.w_vwap

    # Orderbook
    bd["long_orderbook"] = 0
    bd["short_orderbook"] = 0
    imb = indicators.orderbook_imbalance
    if imb > 0.25:
        bd["long_orderbook"] = config.w_orderbook
    elif imb > 0.10:
        bd["long_orderbook"] = round(config.w_orderbook * 0.5, 1)
    if imb < -0.25:
        bd["short_orderbook"] = config.w_orderbook
    elif imb < -0.10:
        bd["short_orderbook"] = round(config.w_orderbook * 0.5, 1)

    # HTF trend
    htf_bullish = indicators.htf_ema_fast > indicators.htf_ema_slow
    bd["long_htf"] = config.w_htf_trend if htf_bullish else 0
    bd["short_htf"] = 0 if htf_bullish else config.w_htf_trend

    # RSI divergence (approximate — actual detection happens in strategy)
    bd["long_rsi_div"] = 0
    bd["short_rsi_div"] = 0

    return bd


async def main():
    """Main entry point for the scalping bot."""
    config = load_config()
    bot_logger = setup_logging(config)
    init_state()

    # Start dashboard in background thread
    dashboard_port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting dashboard on port {dashboard_port}...")
    dashboard_thread = threading.Thread(target=run_dashboard, args=(dashboard_port,), daemon=True)
    dashboard_thread.start()

    logger.info(f"Dashboard available at http://0.0.0.0:{dashboard_port}")

    print("\n" + "=" * 70)
    print("  BINANCE FUTURES SCALPING BOT")
    print("=" * 70)

    if config.dry_run:
        print("  Mode: DRY-RUN (no real orders)")
    elif config.testnet:
        print("  Mode: TESTNET")
    else:
        print("  Mode: LIVE TRADING")

    print(f"  Pair: {config.symbol}")
    print(f"  Leverage: {config.leverage}x (max {config.max_leverage}x)")
    print(f"  SL: {config.stop_loss_pct * 100:.1f}% | TP: {config.take_profit_pct * 100:.1f}%")
    print(f"  Max Daily Loss: {config.max_daily_loss_pct * 100:.0f}%")
    print(f"  Dashboard: http://0.0.0.0:{dashboard_port}")
    print("=" * 70 + "\n")

    # Initialize components
    exchange = ExchangeClient(config)
    await exchange.initialize()

    initial_balance = await exchange.fetch_balance()
    logger.info(f"Initial free balance: ${initial_balance:.4f}")

    data_feed = DataFeed(config, exchange)
    risk_manager = RiskManager(config, max(initial_balance, 10.0))
    strategy = ScalpingStrategy(config)
    position_manager = PositionManager(config, exchange, risk_manager)
    learner = AdaptiveLearner()

    logger.info(
        f"Learner loaded: threshold adj={learner.state.score_threshold_adj:+.1f}, "
        f"leverage mult={learner.state.leverage_multiplier:.2f}, "
        f"min volume={learner.state.min_volume_ratio:.1f}x"
    )

    # Sync existing position from Binance
    has_position = await position_manager.sync_position_from_exchange()

    if initial_balance < 1.0 and not has_position and not config.dry_run:
        logger.error(f"Balance too low (${initial_balance:.2f}) and no position. Need at least $1.00")
        await exchange.close()
        return

    # Initial equity snapshot
    _, init_equity = await compute_equity(exchange, position_manager)
    add_equity_snapshot(init_equity)

    # Shutdown handling
    shutdown_event = asyncio.Event()

    def handle_shutdown(sig, frame):
        logger.info(f"Received signal {sig}. Initiating graceful shutdown...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Start data feed
    await data_feed.start()
    await data_feed.wait_ready()

    logger.info("Bot is ready. Listening for signals...")

    # Shared references for monitor loop access
    _last_indicators_ref = [{}]  # mutable ref for monitor loop
    _strategy_ref = [strategy]

    # Start background tasks
    monitor_task = asyncio.create_task(
        position_monitor_loop(position_manager, data_feed, learner, _last_indicators_ref, _strategy_ref)
    )
    daily_task = asyncio.create_task(daily_reset_loop(risk_manager, exchange))

    last_scores = (0.0, 0.0)
    last_indicators_dict = {}
    last_score_breakdown = {}
    last_indicators_obj = None
    last_analysis = {}
    _processing_signal = False
    _equity_snapshot_counter = 0

    try:
        while not shutdown_event.is_set():
            # Wait for new closed candle or shutdown
            try:
                await asyncio.wait_for(
                    data_feed.new_candle_event.wait(),
                    timeout=2.0,
                )
                data_feed.new_candle_event.clear()
            except asyncio.TimeoutError:
                # Periodic state update for dashboard
                free_bal, equity = await compute_equity(exchange, position_manager)

                # Equity snapshot every ~10 seconds
                _equity_snapshot_counter += 1
                if _equity_snapshot_counter >= 5:
                    add_equity_snapshot(equity)
                    _equity_snapshot_counter = 0

                status = "Esperando senal..."
                if position_manager.position is not None:
                    side = position_manager.position.side.value.upper()
                    pnl = position_manager.position.pnl_unrealized
                    status = f"Posicion {side} abierta | PnL: ${pnl:+.4f}"
                if risk_manager.in_cooldown:
                    remaining = max(0, int(risk_manager.cooldown_until - time.time()))
                    status = f"Cooldown activo ({remaining}s restantes)"

                state = build_state(
                    config, free_bal, equity, position_manager, risk_manager,
                    data_feed.get_last_price(), last_indicators_dict, last_scores,
                    status, last_score_breakdown, last_analysis,
                    learner_stats=learner.get_stats(),
                )
                update_shared_state(state)
                emit_state_update(state)
                continue

            if _processing_signal:
                continue
            _processing_signal = True

            try:
                # Get current data
                candles = data_feed.get_candles()
                orderbook = data_feed.get_orderbook()
                price = data_feed.get_last_price()

                # Compute indicators
                indicators = strategy.compute_indicators(candles, orderbook)

                if indicators:
                    last_indicators_obj = indicators
                    last_indicators_dict = build_indicators_dict(indicators)
                    last_score_breakdown = build_score_breakdown(indicators, config)
                    _last_indicators_ref[0] = last_indicators_dict  # update shared ref

                # Evaluate signal
                signal_result = strategy.evaluate(indicators)

                # Track scores for dashboard
                if indicators:
                    long_s = 0.0
                    short_s = 0.0
                    # Sum up the breakdown values
                    for k, v in last_score_breakdown.items():
                        if k.startswith("long_"):
                            long_s += v
                        elif k.startswith("short_"):
                            short_s += v
                    last_scores = (long_s, short_s)

                if signal_result:
                    if signal_result.side == Side.LONG:
                        last_scores = (signal_result.score, last_scores[1])
                    else:
                        last_scores = (last_scores[0], signal_result.score)

                # Generate market analysis
                last_analysis = generate_market_analysis(last_indicators_dict, last_scores, config)

                # Equity snapshot on each candle
                free_bal, equity = await compute_equity(exchange, position_manager)
                add_equity_snapshot(equity)

                # Update state for dashboard
                status = "Analizando mercado..."
                if position_manager.position is not None:
                    side = position_manager.position.side.value.upper()
                    pnl = position_manager.position.pnl_unrealized
                    status = f"Posicion {side} abierta | PnL: ${pnl:+.4f}"
                elif signal_result:
                    status = f"Senal {signal_result.side.value.upper()} detectada (score: {signal_result.score:.1f}, lev: {signal_result.recommended_leverage}x)"
                elif risk_manager.in_cooldown:
                    remaining = max(0, int(risk_manager.cooldown_until - time.time()))
                    status = f"Cooldown activo ({remaining}s restantes)"

                state = build_state(
                    config, free_bal, equity, position_manager, risk_manager,
                    price, last_indicators_dict, last_scores, status,
                    last_score_breakdown, last_analysis,
                    learner_stats=learner.get_stats(),
                )
                update_shared_state(state)
                emit_state_update(state)

                # If no position and we have a signal, apply learner filters then open
                if position_manager.position is None and signal_result is not None:
                    # ─── Learner filters ───
                    skip, skip_reason = learner.should_skip_trade(
                        last_indicators_dict, strategy.last_had_crossover
                    )
                    if not skip:
                        # Check anti-HTF filter
                        is_long = signal_result.side == Side.LONG
                        if learner.should_skip_against_htf(is_long, last_indicators_dict):
                            skip = True
                            skip_reason = "Learner: against-HTF trades unprofitable"

                    if skip:
                        logger.info(f"Trade BLOCKED by learner: {skip_reason}")
                        status = f"Senal bloqueada: {skip_reason}"
                    else:
                        # Apply learner leverage adjustment
                        original_lev = signal_result.recommended_leverage
                        adjusted_lev = learner.get_effective_leverage(original_lev, config.leverage)
                        signal_result = Signal(
                            side=signal_result.side,
                            score=signal_result.score,
                            indicators=signal_result.indicators,
                            recommended_leverage=adjusted_lev,
                        )
                        if adjusted_lev != original_lev:
                            logger.info(f"Learner adjusted leverage: {original_lev}x -> {adjusted_lev}x")

                        opened = await position_manager.open_position(signal_result, price)
                        if opened:
                            # Refresh state after opening
                            free_bal, equity = await compute_equity(exchange, position_manager)
                            state = build_state(
                                config, free_bal, equity, position_manager, risk_manager,
                                price, last_indicators_dict, last_scores,
                                f"Posicion {signal_result.side.value.upper()} abierta | Lev: {adjusted_lev}x",
                                last_score_breakdown, last_analysis,
                                learner_stats=learner.get_stats(),
                            )
                            update_shared_state(state)
                            emit_state_update(state)

            finally:
                _processing_signal = False

    except Exception as e:
        logger.error(f"Main loop error: {e}", exc_info=True)

    finally:
        # Graceful shutdown
        logger.info("Shutting down...")

        monitor_task.cancel()
        daily_task.cancel()

        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        try:
            await daily_task
        except asyncio.CancelledError:
            pass

        # Close open position
        if position_manager.position is not None:
            logger.info("Closing open position before shutdown...")
            trade = await position_manager.force_close("shutdown")
            if trade:
                add_trade_to_history(trade)
                learner.record_trade(
                    trade,
                    indicators=_last_indicators_ref[0] if _last_indicators_ref else None,
                    had_crossover=strategy.last_had_crossover,
                    htf_aligned=strategy.last_htf_aligned,
                )

        # Stop data feed
        await data_feed.stop()

        # Final stats
        stats = risk_manager.get_stats()
        final_balance = await exchange.fetch_balance()

        logger.info(
            f"FINAL STATS | Balance: ${final_balance:.4f} | "
            f"Total P&L: ${stats['total_pnl']:.4f} | "
            f"Trades: {stats['total_trades']} | Win Rate: {stats['win_rate']}"
        )

        await exchange.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
