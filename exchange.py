"""Binance Futures exchange client wrapper using ccxt."""

import asyncio
import logging
import os
import time

import ccxt.async_support as ccxt
import pandas as pd

from config import BotConfig
from models import Side
from utils import retry_async, round_quantity, round_price

logger = logging.getLogger("scalper")

# Alternative Binance hostnames for geo-restricted regions
_BINANCE_HOSTNAMES = [
    None,           # default (fapi.binance.com)
    "binance.me",
    "binance1.com",
    "binance2.com",
    "binance3.com",
    "binance4.com",
]


class ExchangeClient:
    """Wrapper around ccxt for Binance USD-M Futures."""

    def __init__(self, config: BotConfig):
        self.config = config
        # Allow hostname override via env var
        hostname = os.environ.get("BINANCE_HOSTNAME", "").strip() or None
        exchange_opts = {
            "apiKey": config.api_key,
            "secret": config.api_secret,
            "enableRateLimit": True,
            "timeout": 30000,  # 30s timeout (default 10s too short for Railway)
            "options": {
                "defaultType": "future",
                "fetchCurrencies": False,  # Skip spot API call that gets geo-blocked
            },
        }
        if hostname:
            exchange_opts["hostname"] = hostname
            logger.info(f"Using custom Binance hostname: {hostname}")

        self.exchange = ccxt.binanceusdm(exchange_opts)
        if config.testnet:
            self.exchange.set_sandbox_mode(True)

        # Market info (populated on initialize)
        self.tick_size: float = 0.10
        self.step_size: float = 0.001
        self.min_notional: float = 5.0
        self.min_qty: float = 0.001
        self._current_leverage: int = config.leverage

        # Dry-run state
        self._dry_balance: float = 10.0
        self._dry_position: dict | None = None

    async def initialize(self, max_retries: int = 5) -> None:
        """Load markets and configure leverage/margin type.
        Retries with alternative hostnames on geo-block (451),
        and retries the entire sequence on timeout with exponential backoff.
        """
        logger.info("Initializing exchange connection...")

        # Try default hostname first, then alternatives if geo-blocked
        hostnames_to_try = _BINANCE_HOSTNAMES.copy()
        # If a custom hostname is already set, just retry with it
        current_hostname = getattr(self.exchange, 'hostname', None)
        if current_hostname:
            hostnames_to_try = [current_hostname]

        last_error = None
        for attempt in range(1, max_retries + 1):
            connected = False
            for hostname in hostnames_to_try:
                try:
                    if hostname and hostname != current_hostname:
                        logger.info(f"Trying Binance hostname: {hostname}")
                        self.exchange.hostname = hostname
                        # Reset markets cache to force reload
                        self.exchange.markets = None
                        self.exchange.markets_by_id = None

                    await self.exchange.load_markets()
                    if hostname:
                        logger.info(f"Connected successfully via {hostname}")
                    connected = True
                    break  # Success
                except ccxt.ExchangeNotAvailable as e:
                    last_error = e
                    if "451" in str(e):
                        logger.warning(f"Geo-blocked{' on ' + hostname if hostname else ''}: {e}")
                        continue  # Try next hostname
                    else:
                        raise  # Non-geo-block error, don't retry
                except (ccxt.RequestTimeout, ccxt.NetworkError, Exception) as e:
                    last_error = e
                    logger.warning(f"Connection failed{' on ' + hostname if hostname else ''}: {e}")
                    await asyncio.sleep(1)
                    continue

            if connected:
                break

            # All hostnames failed this attempt — retry with backoff
            if attempt < max_retries:
                wait = min(10, 2 ** attempt)
                logger.warning(
                    f"All hostnames failed (attempt {attempt}/{max_retries}). "
                    f"Retrying in {wait}s..."
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    f"Failed to connect after {max_retries} attempts. "
                    f"Set BINANCE_HOSTNAME env var or check network."
                )
                raise last_error

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
                await self.exchange.set_leverage(self.config.leverage, self.config.symbol)
                self._current_leverage = self.config.leverage
                logger.info(f"Leverage set to {self.config.leverage}x")
            except Exception as e:
                logger.warning(f"Could not set leverage: {e}")

            try:
                await self.exchange.set_margin_mode(
                    self.config.margin_type.lower(),
                    self.config.symbol,
                )
                logger.info(f"Margin type set to {self.config.margin_type}")
            except Exception as e:
                if "No need to change" not in str(e):
                    logger.warning(f"Could not set margin type: {e}")

        logger.info(
            f"Exchange initialized: {self.config.symbol} | "
            f"tick={self.tick_size} | step={self.step_size} | "
            f"min_qty={self.min_qty} | min_notional={self.min_notional}"
        )

    async def set_dynamic_leverage(self, leverage: int) -> bool:
        """Dynamically change leverage for the next trade."""
        if self.config.dry_run:
            self._current_leverage = leverage
            return True

        if leverage == self._current_leverage:
            return True

        try:
            await self.exchange.set_leverage(leverage, self.config.symbol)
            self._current_leverage = leverage
            logger.info(f"Leverage changed to {leverage}x")
            return True
        except Exception as e:
            logger.error(f"Failed to set leverage to {leverage}x: {e}")
            return False

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
            contracts = float(pos.get("contracts", 0) or 0)
            # Also check raw positionAmt from Binance info
            info = pos.get("info", {})
            position_amt = abs(float(info.get("positionAmt", 0) or 0))
            # Position is real only if it has actual contracts
            actual_size = max(contracts, position_amt)
            if actual_size > 0:
                logger.debug(f"Found position: contracts={contracts}, positionAmt={position_amt}, side={pos.get('side')}")
                return pos
        return None

    def calculate_quantity(self, usdt_margin: float, price: float, leverage: int | None = None) -> float:
        """Calculate order quantity from margin amount."""
        lev = leverage or self._current_leverage
        notional = usdt_margin * lev
        # Ensure minimum notional with buffer
        if notional < self.min_notional:
            notional = self.min_notional * 1.05
        qty = notional / price
        qty = round_quantity(qty, self.step_size)
        # After rounding, verify notional still meets minimum
        if qty * price < self.min_notional and self.step_size > 0:
            qty += self.step_size
            qty = round_quantity(qty, self.step_size)
        return qty

    async def place_market_order(self, side: Side, quantity: float, price: float | None = None) -> dict:
        """Place a market order. Returns order info dict."""
        order_side = "buy" if side == Side.LONG else "sell"

        if self.config.dry_run:
            fill_price = price or await self.fetch_ticker_price()
            notional = quantity * fill_price
            margin_used = notional / self._current_leverage

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
        logger.info(f"Order placed: {order_side.upper()} {quantity:.6f} @ market (leverage: {self._current_leverage}x)")
        return order

    async def close_position_market(self, side: Side, quantity: float, price: float | None = None) -> dict:
        """Close a position with a market order in the opposite direction."""
        close_side = Side.SHORT if side == Side.LONG else Side.LONG
        order_side = "sell" if side == Side.LONG else "buy"

        if self.config.dry_run:
            fill_price = price or await self.fetch_ticker_price()
            pos = self._dry_position
            pnl = 0.0
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
