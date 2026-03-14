"""Real-time WebSocket data feed for Binance Futures."""

import asyncio
import json
import logging
import time

import pandas as pd
import websockets

from config import BotConfig
from exchange import ExchangeClient
from models import OrderBookSnapshot

logger = logging.getLogger("scalper")


class DataFeed:
    """Manages WebSocket connections for real-time market data."""

    def __init__(self, config: BotConfig, exchange: ExchangeClient):
        self.config = config
        self.exchange = exchange

        # State
        self.candles: pd.DataFrame = pd.DataFrame()
        self.orderbook: OrderBookSnapshot = OrderBookSnapshot([], [], time.time())
        self.last_price: float = 0.0
        self.last_volume: float = 0.0

        # Events
        self.new_candle_event = asyncio.Event()
        self._ready_event = asyncio.Event()

        # Control
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start all WebSocket feeds."""
        self._running = True

        # Fetch initial candles via REST
        logger.info("Fetching historical candles...")
        self.candles = await self.exchange.fetch_ohlcv(self.config.kline_limit)
        self.last_price = float(self.candles["close"].iloc[-1])
        logger.info(f"Loaded {len(self.candles)} candles. Last price: ${self.last_price:,.2f}")

        # Start WebSocket streams
        symbol_lower = self.config.raw_symbol.lower()
        streams = [
            f"{symbol_lower}@kline_{self.config.kline_interval}",
            f"{symbol_lower}@depth{self.config.orderbook_depth}@500ms",
            f"{symbol_lower}@aggTrade",
        ]

        # Combined stream URL
        combined_url = self.config.ws_base_url + "/".join(streams)

        self._tasks.append(
            asyncio.create_task(self._consume_combined(combined_url))
        )

        self._ready_event.set()
        logger.info("Data feed started (combined WebSocket stream)")

    async def wait_ready(self) -> None:
        """Wait until initial data is loaded."""
        await self._ready_event.wait()

    async def _consume_combined(self, url: str) -> None:
        """Consume the combined WebSocket stream with auto-reconnect."""
        backoff = 1.0

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info("WebSocket connected")
                    backoff = 1.0  # reset on successful connection

                    async for raw_msg in ws:
                        if not self._running:
                            break

                        try:
                            msg = json.loads(raw_msg)
                            event = msg.get("e", "")

                            if event == "kline":
                                self._handle_kline(msg)
                            elif event == "depthUpdate":
                                self._handle_depth(msg)
                            elif event == "aggTrade":
                                self._handle_agg_trade(msg)

                        except Exception as e:
                            logger.error(f"Error processing message: {e}")

            except websockets.ConnectionClosed as e:
                if self._running:
                    logger.warning(f"WebSocket disconnected: {e}. Reconnecting in {backoff:.0f}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)

            except Exception as e:
                if self._running:
                    logger.error(f"WebSocket error: {e}. Reconnecting in {backoff:.0f}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)

    def _handle_kline(self, msg: dict) -> None:
        """Process kline/candlestick update."""
        k = msg.get("k", {})
        is_closed = k.get("x", False)

        candle_data = {
            "timestamp": pd.Timestamp(k["t"], unit="ms"),
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
        }

        self.last_price = candle_data["close"]

        if is_closed:
            # Append new closed candle
            new_row = pd.DataFrame([candle_data])
            self.candles = pd.concat([self.candles, new_row], ignore_index=True)

            # Keep only last N candles
            if len(self.candles) > self.config.kline_limit:
                self.candles = self.candles.iloc[-self.config.kline_limit:].reset_index(drop=True)

            self.new_candle_event.set()
            logger.debug(f"New candle closed: ${candle_data['close']:,.2f} vol={candle_data['volume']:.2f}")
        else:
            # Update current (unclosed) candle in-place
            if len(self.candles) > 0:
                idx = self.candles.index[-1]
                self.candles.loc[idx, "high"] = max(self.candles.loc[idx, "high"], candle_data["high"])
                self.candles.loc[idx, "low"] = min(self.candles.loc[idx, "low"], candle_data["low"])
                self.candles.loc[idx, "close"] = candle_data["close"]
                self.candles.loc[idx, "volume"] = candle_data["volume"]

    def _handle_depth(self, msg: dict) -> None:
        """Process order book depth update."""
        bids = [(float(p), float(q)) for p, q in msg.get("b", [])]
        asks = [(float(p), float(q)) for p, q in msg.get("a", [])]

        self.orderbook = OrderBookSnapshot(
            bids=bids,
            asks=asks,
            timestamp=time.time(),
        )
        self.orderbook.compute_imbalance()

    def _handle_agg_trade(self, msg: dict) -> None:
        """Process aggregated trade update."""
        self.last_price = float(msg.get("p", self.last_price))
        self.last_volume = float(msg.get("q", 0))

    def get_candles(self) -> pd.DataFrame:
        return self.candles.copy()

    def get_orderbook(self) -> OrderBookSnapshot:
        return self.orderbook

    def get_last_price(self) -> float:
        return self.last_price

    async def stop(self) -> None:
        """Stop all WebSocket feeds."""
        self._running = False
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        logger.info("Data feed stopped")
