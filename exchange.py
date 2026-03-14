"""Binance Futures exchange client wrapper using ccxt."""

import logging
import time

import ccxt.async_support as ccxt
import pandas as pd

from config import BotConfig
from models import Side
from utils import retry_async, round_quantity, round_price

logger = logging.getLogger("scalper")


class ExchangeClient:
    """Wrapper around ccxt for Binance USD-M Futures."""

    def __init__(self, config: BotConfig):
        self.config = config
        self.exchange = ccxt.binanceusdm({
            "apiKey": config.api_key,
            "secret": config.api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        if config.testnet:
            self.exchange.set_sandbox_mode(True)

        # Market info (populated on initialize)
        self.tick_size: float = 0.10
        self.step_size: float = 0.001
        self.min_notional: float = 5.0
        self.min_qty: float = 0.001

        # Dry-run state
        self._dry_balance: float = 10.0
        self._dry_position: dict | None = None

    async def initialize(self) -> None:
        """Load markets and configure leverage/margin type."""
        logger.info("Initializing exchange connection...")
        await self.exchange.load_markets()

        market = self.exchange.market(self.config.symbol)
        self.tick_size = float(market.get("precision", {}).get("price", 0.10))
        self.step_size = float(market.get("precision", {}).get("amount", 0.001))

        # Extract limits
        limits = market.get("limits", {})
        self.min_qty = float(limits.get("amount", {}).get("min", 0.001))
        self.min_notional = float(limits.get("cost", {}).get("min", 5.0))

        # For ccxt, precision is often in decimals count, not tick size
        if self.tick_size > 1:
            self.tick_size = 10 ** (-int(self.tick_size))
        if self.step_size > 1:
            self.step_size = 10 ** (-int(self.step_size))

        if not self.config.dry_run:
            try:
                await self.exchange.fapiPrivate_post_leverage({
                    "symbol": self.config.raw_symbol,
                    "leverage": self.config.leverage,
                })
                logger.info(f"Leverage set to {self.config.leverage}x")
            except Exception as e:
                logger.warning(f"Could not set leverage: {e}")

            try:
                await self.exchange.fapiPrivate_post_margintype({
                    "symbol": self.config.raw_symbol,
                    "marginType": self.config.margin_type.upper(),
                })
                logger.info(f"Margin type set to {self.config.margin_type}")
            except Exception as e:
                # Already set to this margin type
                if "No need to change" not in str(e):
                    logger.warning(f"Could not set margin type: {e}")

        logger.info(
            f"Exchange initialized: {self.config.symbol} | "
            f"tick={self.tick_size} | step={self.step_size} | "
            f"min_qty={self.min_qty} | min_notional={self.min_notional}"
        )

    @retry_async(max_retries=3)
    async def fetch_balance(self) -> float:
        """Get available USDT balance."""
        if self.config.dry_run:
            return self._dry_balance

        balance = await self.exchange.fetch_balance()
        usdt = balance.get("USDT", {})
        return float(usdt.get("free", 0.0))

    @retry_async(max_retries=3)
    async def fetch_ohlcv(self, limit: int | None = None) -> pd.DataFrame:
        """Fetch historical candles."""
        limit = limit or self.config.kline_limit
        ohlcv = await self.exchange.fetch_ohlcv(
            self.config.symbol,
            timeframe=self.config.kline_interval,
            limit=limit,
        )
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    @retry_async(max_retries=3)
    async def fetch_ticker_price(self) -> float:
        """Get current market price."""
        ticker = await self.exchange.fetch_ticker(self.config.symbol)
        return float(ticker["last"])

    async def fetch_position(self) -> dict | None:
        """Get current open position for the symbol."""
        if self.config.dry_run:
            return self._dry_position

        positions = await self.exchange.fetch_positions([self.config.symbol])
        for pos in positions:
            contracts = float(pos.get("contracts", 0))
            if contracts > 0:
                return pos
        return None

    def calculate_quantity(self, usdt_margin: float, price: float) -> float:
        """Calculate order quantity from margin amount."""
        notional = usdt_margin * self.config.leverage
        # Ensure minimum notional with buffer
        if notional < self.min_notional:
            notional = self.min_notional * 1.005
        qty = notional / price
        qty = round_quantity(qty, self.step_size)
        return qty

    async def place_market_order(self, side: Side, quantity: float, price: float | None = None) -> dict:
        """Place a market order. Returns order info dict."""
        order_side = "buy" if side == Side.LONG else "sell"

        if self.config.dry_run:
            fill_price = price or await self.fetch_ticker_price()
            notional = quantity * fill_price
            margin_used = notional / self.config.leverage

            self._dry_balance -= margin_used
            self._dry_position = {
                "side": side.value,
                "entry_price": fill_price,
                "quantity": quantity,
                "margin": margin_used,
            }

            logger.info(
                f"[DRY-RUN] {order_side.upper()} {quantity:.6f} BTC @ ${fill_price:,.2f} "
                f"(notional: ${notional:,.2f}, margin: ${margin_used:.2f})"
            )
            return {
                "id": f"dry_{int(time.time())}",
                "price": fill_price,
                "amount": quantity,
                "side": order_side,
                "status": "closed",
            }

        order = await self.exchange.create_market_order(
            self.config.symbol,
            order_side,
            quantity,
        )
        logger.info(f"Order placed: {order_side.upper()} {quantity:.6f} @ market")
        return order

    async def close_position_market(self, side: Side, quantity: float, price: float | None = None) -> dict:
        """Close a position with a market order in the opposite direction."""
        close_side = Side.SHORT if side == Side.LONG else Side.LONG
        order_side = "sell" if side == Side.LONG else "buy"

        if self.config.dry_run:
            fill_price = price or await self.fetch_ticker_price()
            pos = self._dry_position
            if pos:
                entry = pos["entry_price"]
                if side == Side.LONG:
                    pnl = (fill_price - entry) * quantity
                else:
                    pnl = (entry - fill_price) * quantity

                self._dry_balance += pos["margin"] + pnl
                self._dry_position = None

                logger.info(
                    f"[DRY-RUN] CLOSE {order_side.upper()} {quantity:.6f} BTC @ ${fill_price:,.2f} "
                    f"(PnL: ${pnl:+.4f})"
                )
                return {
                    "id": f"dry_close_{int(time.time())}",
                    "price": fill_price,
                    "amount": quantity,
                    "side": order_side,
                    "status": "closed",
                    "pnl": pnl,
                }

        order = await self.exchange.create_market_order(
            self.config.symbol,
            order_side,
            quantity,
            params={"reduceOnly": True},
        )
        logger.info(f"Position closed: {order_side.upper()} {quantity:.6f} @ market")
        return order

    async def close(self) -> None:
        """Close the exchange connection."""
        await self.exchange.close()
