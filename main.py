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

from bot_state import build_state, save_state, load_state
from config import load_config, BotConfig
from data_feed import DataFeed
from exchange import ExchangeClient
from logger_setup import setup_logging
from models import Side
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

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Scalper Bot</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.4/socket.io.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0e17; color: #e0e0e0; font-family: 'Courier New', monospace; padding: 10px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 10px 15px; border-bottom: 2px solid #1a2332; margin-bottom: 10px; }
        .header h1 { color: #f0b90b; font-size: 1.2em; letter-spacing: 2px; }
        .live-dot { width: 10px; height: 10px; background: #00c853; border-radius: 50%; display: inline-block; animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
        .live-badge { border: 1px solid #555; padding: 4px 12px; border-radius: 4px; font-size: 0.8em; }
        .status-bar { background: #1a2332; padding: 8px 15px; text-align: center; color: #f0b90b; font-size: 0.95em; margin-bottom: 15px; border-radius: 4px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 15px; }
        .card { background: #111827; border: 1px solid #1e2d3d; border-radius: 6px; padding: 12px 15px; }
        .card-label { font-size: 0.7em; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
        .card-value { font-size: 1.3em; font-weight: bold; }
        .green { color: #00c853; }
        .red { color: #ff1744; }
        .yellow { color: #f0b90b; }
        .section { background: #111827; border: 1px solid #1e2d3d; border-radius: 6px; padding: 12px 15px; margin-bottom: 10px; }
        .section-title { font-size: 0.75em; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }
        .stats-row { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
        .stat { text-align: center; min-width: 80px; }
        .stat-label { font-size: 0.65em; color: #888; text-transform: uppercase; }
        .stat-value { font-size: 1em; font-weight: bold; }
        .score-bar { height: 8px; background: #1a2332; border-radius: 4px; margin: 4px 0; position: relative; }
        .score-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
        .score-threshold { position: absolute; top: -2px; bottom: -2px; width: 2px; background: #f0b90b; }
        .indicator-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .position-card { background: #0d1b2a; border: 1px solid #1e2d3d; border-radius: 6px; padding: 12px 15px; margin-bottom: 10px; }
        .position-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
        .side-badge { padding: 2px 10px; border-radius: 3px; font-weight: bold; font-size: 0.85em; }
        .side-long { background: #00c85333; color: #00c853; border: 1px solid #00c853; }
        .side-short { background: #ff174433; color: #ff1744; border: 1px solid #ff1744; }
        .pos-detail { display: flex; justify-content: space-between; font-size: 0.85em; padding: 2px 0; }
    </style>
</head>
<body>
    <div class="header">
        <h1>SCALPER BOT</h1>
        <div><span class="live-dot" id="liveDot"></span> <span class="live-badge" id="liveLabel">LIVE</span></div>
    </div>
    <div class="status-bar" id="statusBar">Conectando...</div>

    <div class="grid">
        <div class="card">
            <div class="card-label">Balance USDT</div>
            <div class="card-value" id="balance">$0.00</div>
        </div>
        <div class="card">
            <div class="card-label">BTC/USDT</div>
            <div class="card-value yellow" id="price">$0.00</div>
        </div>
        <div class="card">
            <div class="card-label">P&L Diario</div>
            <div class="card-value" id="dailyPnl">$0.00</div>
        </div>
        <div class="card">
            <div class="card-label">P&L Total</div>
            <div class="card-value" id="totalPnl">$0.00</div>
        </div>
    </div>

    <div id="positionSection"></div>

    <div class="section">
        <div class="section-title">Estadisticas</div>
        <div class="stats-row">
            <div class="stat"><div class="stat-label">Trades</div><div class="stat-value" id="trades">0</div></div>
            <div class="stat"><div class="stat-label">Win Rate</div><div class="stat-value green" id="winRate">0%</div></div>
            <div class="stat"><div class="stat-label">Wins</div><div class="stat-value" id="wins">0</div></div>
            <div class="stat"><div class="stat-label">Losses</div><div class="stat-value" id="losses">0</div></div>
            <div class="stat"><div class="stat-label">Leverage</div><div class="stat-value" id="leverage">15x</div></div>
            <div class="stat"><div class="stat-label">SL / TP</div><div class="stat-value" id="sltp">0.3% / 0.5%</div></div>
        </div>
    </div>

    <div class="section">
        <div class="section-title">Scoring de Senales</div>
        <div>
            <div style="font-size:0.85em;">LONG: <span id="longScore">0</span></div>
            <div class="score-bar">
                <div class="score-fill green" id="longBar" style="width:0%;background:#00c853;"></div>
                <div class="score-threshold" id="longThreshold" style="left:30%;"></div>
            </div>
            <div style="font-size:0.85em; margin-top:8px;">SHORT: <span id="shortScore" class="red">0</span></div>
            <div class="score-bar">
                <div class="score-fill" id="shortBar" style="width:0%;background:#ff1744;"></div>
                <div class="score-threshold" id="shortThreshold" style="left:30%;"></div>
            </div>
        </div>
    </div>

    <div class="indicator-grid">
        <div class="section">
            <div class="section-title">Indicadores</div>
            <div style="font-size:0.85em;">
                <div>EMA: <span id="ema">-</span></div>
                <div>RSI: <span id="rsi">-</span></div>
                <div>Volumen: <span id="volume">-</span></div>
                <div>Orderbook: <span id="orderbook">-</span></div>
            </div>
        </div>
        <div class="section">
            <div class="section-title">Mercado</div>
            <div style="font-size:0.85em;">
                <div>Bias: <span id="bias" class="green">-</span></div>
                <div>BB Pos: <span id="bbPos">-</span></div>
                <div>VWAP: <span id="vwap">-</span></div>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        socket.on('connect', () => {
            document.getElementById('liveDot').style.background = '#00c853';
            document.getElementById('liveLabel').textContent = 'LIVE';
        });
        socket.on('disconnect', () => {
            document.getElementById('liveDot').style.background = '#ff1744';
            document.getElementById('liveLabel').textContent = 'OFFLINE';
            document.getElementById('statusBar').textContent = 'Desconectado del bot...';
        });
        socket.on('state_update', (s) => {
            document.getElementById('statusBar').textContent = s.status || 'Esperando senal...';
            document.getElementById('balance').textContent = '$' + (s.equity || s.balance || 0).toFixed(4);
            document.getElementById('price').textContent = '$' + (s.price || 0).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});

            let dp = s.daily_pnl || 0;
            let dpEl = document.getElementById('dailyPnl');
            dpEl.textContent = (dp >= 0 ? '+$' : '-$') + Math.abs(dp).toFixed(4);
            dpEl.className = 'card-value ' + (dp >= 0 ? 'green' : 'red');

            let tp = s.total_pnl || 0;
            let tpEl = document.getElementById('totalPnl');
            tpEl.textContent = (tp >= 0 ? '+$' : '-$') + Math.abs(tp).toFixed(4);
            tpEl.className = 'card-value ' + (tp >= 0 ? 'green' : 'red');

            document.getElementById('trades').textContent = s.total_trades || 0;
            document.getElementById('winRate').textContent = (s.win_rate || 0).toFixed(1) + '%';
            document.getElementById('wins').textContent = s.winning_trades || 0;
            document.getElementById('losses').textContent = s.losing_trades || 0;
            document.getElementById('leverage').textContent = (s.leverage || 15) + 'x';
            document.getElementById('sltp').textContent = (s.sl_pct || 0.3).toFixed(1) + '% / ' + (s.tp_pct || 0.5).toFixed(1) + '%';

            let ls = s.long_score || 0;
            let ss = s.short_score || 0;
            let thresh = s.score_threshold || 3.0;
            let maxScore = 10;
            document.getElementById('longScore').textContent = ls.toFixed(1);
            document.getElementById('shortScore').textContent = ss.toFixed(1);
            document.getElementById('longBar').style.width = Math.min(ls/maxScore*100, 100) + '%';
            document.getElementById('shortBar').style.width = Math.min(ss/maxScore*100, 100) + '%';
            document.getElementById('longThreshold').style.left = (thresh/maxScore*100) + '%';
            document.getElementById('shortThreshold').style.left = (thresh/maxScore*100) + '%';

            let ind = s.indicators || {};
            document.getElementById('ema').textContent = (ind.ema_fast||0).toFixed(1) + ' / ' + (ind.ema_slow||0).toFixed(1);
            document.getElementById('rsi').textContent = (ind.rsi||0).toFixed(0);
            document.getElementById('volume').textContent = (ind.volume_ratio||0).toFixed(1) + 'x';
            document.getElementById('orderbook').textContent = ((ind.imbalance||0)*100).toFixed(0) + '%';
            document.getElementById('bbPos').textContent = ((ind.bb_position||0)*100).toFixed(0) + '%';

            let bias = (ind.ema_fast||0) > (ind.ema_slow||0) ? 'LONG' : 'SHORT';
            let biasEl = document.getElementById('bias');
            biasEl.textContent = bias;
            biasEl.className = bias === 'LONG' ? 'green' : 'red';

            // Position
            let posSection = document.getElementById('positionSection');
            if (s.position) {
                let p = s.position;
                let sideClass = p.side === 'LONG' ? 'side-long' : 'side-short';
                let pnl = p.pnl_unrealized || 0;
                let margin = p.margin || 0;
                let pnlPct = margin > 0 ? (pnl/margin*100) : 0;
                posSection.innerHTML = '<div class="position-card">' +
                    '<div class="position-header"><span class="side-badge ' + sideClass + '">' + p.side + '</span>' +
                    '<span class="' + (pnl>=0?'green':'red') + '">' + (pnl>=0?'+':'') + '$' + pnl.toFixed(4) + ' (' + pnlPct.toFixed(2) + '%)</span></div>' +
                    '<div class="pos-detail"><span>Entry</span><span>$' + (p.entry_price||0).toLocaleString('en-US',{minimumFractionDigits:2}) + '</span></div>' +
                    '<div class="pos-detail"><span>Qty</span><span>' + (p.quantity||0).toFixed(6) + ' BTC</span></div>' +
                    '<div class="pos-detail"><span>Margin</span><span>$' + margin.toFixed(2) + '</span></div>' +
                    '<div class="pos-detail"><span>SL</span><span>$' + (p.stop_loss||0).toLocaleString('en-US',{minimumFractionDigits:2}) + '</span></div>' +
                    '<div class="pos-detail"><span>TP</span><span>$' + (p.take_profit||0).toLocaleString('en-US',{minimumFractionDigits:2}) + '</span></div>' +
                    (p.trailing_active ? '<div class="pos-detail"><span>Trail</span><span>$' + (p.trailing_price||0).toLocaleString('en-US',{minimumFractionDigits:2}) + '</span></div>' : '') +
                    '<div class="pos-detail"><span>Duracion</span><span>' + Math.floor((p.duration||0)/60) + 'm ' + Math.floor((p.duration||0)%60) + 's</span></div>' +
                    '</div>';
            } else {
                posSection.innerHTML = '';
            }
        });
    </script>
</body>
</html>
"""


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
):
    """Monitor position on every price tick (runs every 200ms)."""
    while True:
        try:
            price = data_feed.get_last_price()
            if price > 0 and position_manager.position is not None:
                trade = await position_manager.monitor_position(price)
                if trade:
                    logger.info(f"Position closed by monitor loop: {trade.exit_reason}")
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


async def main():
    """Main entry point for the scalping bot."""
    config = load_config()
    bot_logger = setup_logging(config)

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
    print(f"  Leverage: {config.leverage}x")
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

    # Sync existing position from Binance
    has_position = await position_manager.sync_position_from_exchange()

    if initial_balance < 1.0 and not has_position and not config.dry_run:
        logger.error(f"Balance too low (${initial_balance:.2f}) and no position. Need at least $1.00")
        await exchange.close()
        return

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

    # Start background tasks
    monitor_task = asyncio.create_task(position_monitor_loop(position_manager, data_feed))
    daily_task = asyncio.create_task(daily_reset_loop(risk_manager, exchange))

    last_scores = (0.0, 0.0)
    last_indicators_dict = None
    _processing_signal = False

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
                status = "Esperando senal..."
                if position_manager.position is not None:
                    side = position_manager.position.side.value.upper()
                    pnl = position_manager.position.pnl_unrealized
                    status = f"Posicion {side} abierta | PnL: ${pnl:+.4f}"

                state = build_state(
                    config, free_bal, equity, position_manager, risk_manager,
                    data_feed.get_last_price(), last_indicators_dict, last_scores, status,
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
                    last_indicators_dict = {
                        "ema_fast": indicators.ema_fast,
                        "ema_slow": indicators.ema_slow,
                        "rsi": indicators.rsi,
                        "volume_ratio": indicators.volume_ratio,
                        "imbalance": indicators.orderbook_imbalance,
                        "bb_position": (indicators.close_price - indicators.bb_lower)
                        / (indicators.bb_upper - indicators.bb_lower)
                        if (indicators.bb_upper - indicators.bb_lower) > 0
                        else 0.5,
                    }

                # Evaluate signal
                signal_result = strategy.evaluate(indicators)

                # Track scores for dashboard
                if indicators:
                    long_s = 0.0
                    short_s = 0.0
                    if indicators.ema_fast > indicators.ema_slow:
                        long_s += config.w_ema_cross * 0.3
                    else:
                        short_s += config.w_ema_cross * 0.3
                    if indicators.rsi < 45:
                        long_s += config.w_rsi * (1.0 if indicators.rsi < 30 else 0.5)
                    if indicators.rsi > 55:
                        short_s += config.w_rsi * (1.0 if indicators.rsi > 70 else 0.5)
                    if indicators.volume_ratio > 1.1:
                        bonus = config.w_volume if indicators.volume_ratio > 1.5 else config.w_volume * 0.4
                        long_s += bonus
                        short_s += bonus
                    last_scores = (long_s, short_s)

                if signal_result:
                    if signal_result.side == Side.LONG:
                        last_scores = (signal_result.score, last_scores[1])
                    else:
                        last_scores = (last_scores[0], signal_result.score)

                # Update state for dashboard
                free_bal, equity = await compute_equity(exchange, position_manager)
                status = "Analizando mercado..."
                if position_manager.position is not None:
                    side = position_manager.position.side.value.upper()
                    pnl = position_manager.position.pnl_unrealized
                    status = f"Posicion {side} abierta | PnL: ${pnl:+.4f}"
                elif signal_result:
                    status = f"Senal {signal_result.side.value.upper()} detectada (score: {signal_result.score:.1f})"

                state = build_state(
                    config, free_bal, equity, position_manager, risk_manager,
                    price, last_indicators_dict, last_scores, status,
                )
                update_shared_state(state)
                emit_state_update(state)

                # If no position and we have a signal, try to open
                if position_manager.position is None and signal_result is not None:
                    await position_manager.open_position(signal_result, price)

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
            await position_manager.force_close("shutdown")

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
