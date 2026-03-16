"""Position lifecycle management with trailing stop logic."""

import asyncio
import logging
import time
from typing import Optional

from config import BotConfig
from exchange import ExchangeClient
from models import Position, Side, Signal, TradeRecord
from risk_manager import RiskManager
from logger_setup import log_trade

logger = logging.getLogger("scalper")


class PositionManager:
    """Manages the full lifecycle of trading positions."""

    def __init__(
        self,
        config: BotConfig,
        exchange: ExchangeClient,
        risk_manager: RiskManager,
    ):
        self.config = config
        self.exchange = exchange
        self.risk_manager = risk_manager
        self.position: Optional[Position] = None
        self._last_signal_score: float = 0.0

    async def open_position(self, signal: Signal, current_price: float) -> bool:
        """
        Attempt to open a new position based on a signal.
        Uses dynamic leverage from signal recommendation.
        Returns True if the position was opened successfully.
        """
        if self.position is not None:
            logger.warning("Cannot open position: already in a position")
            return False

        # Get current balance
        balance = await self.exchange.fetch_balance()

        # Risk checks
        can_trade, reason = self.risk_manager.can_open_trade(balance, signal)
        if not can_trade:
            logger.info(f"Trade blocked by risk manager: {reason}")
            return False

        # Set dynamic leverage before opening
        trade_leverage = signal.recommended_leverage
        await self.exchange.set_dynamic_leverage(trade_leverage)

        # Calculate position size with dynamic leverage
        margin = self.risk_manager.compute_position_size(balance)
        quantity = self.exchange.calculate_quantity(margin, current_price, trade_leverage)

        if quantity <= 0:
            logger.warning(f"Calculated quantity is 0. Margin: ${margin:.2f}, Price: ${current_price:,.2f}")
            return False

        if quantity < self.exchange.min_qty:
            logger.warning(
                f"Quantity {quantity} below minimum {self.exchange.min_qty}. "
                f"Need more balance or higher leverage."
            )
            return False

        # Place market order
        try:
            order = await self.exchange.place_market_order(
                signal.side, quantity, current_price
            )
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return False

        # Get fill price
        fill_price = float(order.get("price", current_price))
        if fill_price == 0:
            fill_price = current_price

        # Compute dynamic SL/TP based on ATR
        atr_pct = signal.indicators.atr_pct if signal.indicators else 0
        sl, tp = self.risk_manager.compute_stop_take(fill_price, signal.side, atr_pct)

        # Create position
        self.position = Position(
            side=signal.side,
            entry_price=fill_price,
            quantity=quantity,
            leverage=trade_leverage,
            stop_loss=sl,
            take_profit=tp,
            highest_price=fill_price,
            lowest_price=fill_price,
            atr_pct=atr_pct,
        )
        self._last_signal_score = signal.score

        notional = quantity * fill_price

        logger.info(
            f"POSITION OPENED: {signal.side.value.upper()} | "
            f"Entry: ${fill_price:,.2f} | Qty: {quantity:.6f} BTC | "
            f"Notional: ${notional:,.2f} | Margin: ${margin:.2f} | "
            f"Leverage: {trade_leverage}x | "
            f"SL: ${sl:,.2f} | TP: ${tp:,.2f} | Score: {signal.score:.1f}"
        )

        return True

    async def monitor_position(self, current_price: float) -> Optional[TradeRecord]:
        """
        Monitor the current position and close if exit conditions are met.
        Called on every price update.
        Returns a TradeRecord if the position was closed, None otherwise.
        """
        if self.position is None:
            return None

        pos = self.position

        # Update unrealized P&L
        if pos.side == Side.LONG:
            pos.pnl_unrealized = (current_price - pos.entry_price) * pos.quantity
        else:
            pos.pnl_unrealized = (pos.entry_price - current_price) * pos.quantity

        # --- CHECK STOP LOSS ---
        if pos.side == Side.LONG and current_price <= pos.stop_loss:
            return await self._close_position(current_price, "sl")

        if pos.side == Side.SHORT and current_price >= pos.stop_loss:
            return await self._close_position(current_price, "sl")

        # --- CHECK TAKE PROFIT ---
        if pos.side == Side.LONG and current_price >= pos.take_profit:
            return await self._close_position(current_price, "tp")

        if pos.side == Side.SHORT and current_price <= pos.take_profit:
            return await self._close_position(current_price, "tp")

        # --- TIME-BASED EXIT (v5.0) ---
        # v5.0: 15 min timeout, only exit if losing or flat (let winners run)
        age_sec = time.time() - pos.entry_time
        if age_sec > 900:  # 15 minutes
            margin = (pos.quantity * pos.entry_price) / pos.leverage if pos.quantity > 0 else 1
            pnl_pct = pos.pnl_unrealized / margin
            if pnl_pct < 0.01:  # less than 1% margin profit after 15 min
                logger.info(
                    f"TIME EXIT: Position age {age_sec:.0f}s > 900s with "
                    f"marginal PnL ({pnl_pct*100:.2f}%)"
                )
                return await self._close_position(current_price, "timeout")

        # --- TRAILING STOP ---
        if self.config.trailing_stop_enabled:
            triggered = self._update_trailing_stop(current_price)
            if triggered:
                return await self._close_position(current_price, "trailing")

        return None

    def _update_trailing_stop(self, price: float) -> bool:
        """
        Update trailing stop and return True if triggered.
        v4.0: Uses ATR-based distances instead of fixed percentages.
        Activation: 1.5x ATR profit, Callback: 1.0x ATR distance.
        Falls back to config percentages if ATR not available.
        """
        pos = self.position
        if pos is None:
            return False

        # v5.0: Wider trailing — let winners run further
        if pos.atr_pct > 0:
            activation_pct = max(pos.atr_pct * 3.0, 0.005)  # v5.0: 3x ATR, min 0.5%
            callback_pct = max(pos.atr_pct * 1.5, 0.003)    # v5.0: 1.5x ATR, min 0.3%
        else:
            activation_pct = self.config.trailing_stop_activation_pct
            callback_pct = self.config.trailing_stop_callback_pct

        if pos.side == Side.LONG:
            if price > pos.highest_price:
                pos.highest_price = price

            profit_pct = (price - pos.entry_price) / pos.entry_price
            if not pos.trailing_stop_active and profit_pct >= activation_pct:
                pos.trailing_stop_active = True
                pos.trailing_stop_price = price * (1 - callback_pct)
                logger.info(
                    f"Trailing ACTIVATED at ${pos.trailing_stop_price:,.2f} "
                    f"(profit: {profit_pct*100:.2f}%, activation: {activation_pct*100:.2f}%)"
                )

            if pos.trailing_stop_active:
                new_trail = pos.highest_price * (1 - callback_pct)
                if new_trail > (pos.trailing_stop_price or 0):
                    pos.trailing_stop_price = new_trail

                if price <= pos.trailing_stop_price:
                    logger.info(
                        f"Trailing TRIGGERED at ${price:,.2f} "
                        f"(trail: ${pos.trailing_stop_price:,.2f})"
                    )
                    return True

        elif pos.side == Side.SHORT:
            if price < pos.lowest_price:
                pos.lowest_price = price

            profit_pct = (pos.entry_price - price) / pos.entry_price
            if not pos.trailing_stop_active and profit_pct >= activation_pct:
                pos.trailing_stop_active = True
                pos.trailing_stop_price = price * (1 + callback_pct)
                logger.info(
                    f"Trailing ACTIVATED at ${pos.trailing_stop_price:,.2f} "
                    f"(profit: {profit_pct*100:.2f}%, activation: {activation_pct*100:.2f}%)"
                )

            if pos.trailing_stop_active:
                new_trail = pos.lowest_price * (1 + callback_pct)
                if new_trail < (pos.trailing_stop_price or 999_999_999):
                    pos.trailing_stop_price = new_trail

                if price >= pos.trailing_stop_price:
                    logger.info(
                        f"Trailing TRIGGERED at ${price:,.2f} "
                        f"(trail: ${pos.trailing_stop_price:,.2f})"
                    )
                    return True

        return False

    async def _close_position(self, exit_price: float, reason: str) -> Optional[TradeRecord]:
        """Close the current position and create a trade record."""
        pos = self.position
        if pos is None:
            raise RuntimeError("No position to close")

        # Place close order - if it fails, DO NOT clear the position
        try:
            order = await self.exchange.close_position_market(
                pos.side, pos.quantity, exit_price
            )
            actual_exit = float(order.get("price", exit_price))
            if actual_exit == 0:
                actual_exit = exit_price
        except Exception as e:
            logger.error(f"CRITICAL: Failed to close position on exchange: {e}")
            logger.error("Position remains OPEN on Binance. Will retry on next tick.")
            return None

        # Calculate P&L
        if pos.side == Side.LONG:
            pnl = (actual_exit - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - actual_exit) * pos.quantity

        margin = (pos.quantity * pos.entry_price) / pos.leverage
        pnl_pct = pnl / margin if margin > 0 else 0.0

        duration = time.time() - pos.entry_time

        # Create trade record
        record = TradeRecord(
            timestamp=time.time(),
            side=pos.side.value,
            entry_price=pos.entry_price,
            exit_price=actual_exit,
            quantity=pos.quantity,
            leverage=pos.leverage,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            duration_sec=duration,
            score=self._last_signal_score,
        )

        # Log to journal
        log_trade(record, self.config.trade_journal_path)

        # Update risk manager
        self.risk_manager.record_trade_result(pnl)

        # Clear position
        self.position = None

        # Log result
        reason_labels = {
            "sl": "STOP LOSS",
            "tp": "TAKE PROFIT",
            "trailing": "TRAILING STOP",
            "timeout": "TIME EXIT",
            "shutdown": "SHUTDOWN",
            "daily_limit": "DAILY LIMIT",
            "manual": "MANUAL",
        }
        label = reason_labels.get(reason, reason.upper())
        pnl_str = f"+${pnl:.4f}" if pnl >= 0 else f"-${abs(pnl):.4f}"
        pnl_pct_str = f"+{pnl_pct * 100:.2f}%" if pnl_pct >= 0 else f"{pnl_pct * 100:.2f}%"

        logger.info(
            f"{label} | Exit: ${actual_exit:,.2f} | PnL: {pnl_str} ({pnl_pct_str}) | "
            f"Duration: {duration:.0f}s | "
            f"Stats: {self.risk_manager.total_trades} trades, "
            f"WR: {self.risk_manager.win_rate * 100:.0f}%"
        )

        return record

    async def force_close(self, reason: str = "manual") -> Optional[TradeRecord]:
        """Force close the current position (used for shutdown, daily limit, etc.)."""
        if self.position is None:
            return None

        current_price = await self.exchange.fetch_ticker_price()
        # Retry up to 3 times for force close
        for attempt in range(3):
            result = await self._close_position(current_price, reason)
            if result is not None:
                return result
            logger.warning(f"Force close attempt {attempt + 1}/3 failed, retrying...")
            await asyncio.sleep(1)

        logger.error("CRITICAL: Could not force close position after 3 attempts!")
        return None

    async def sync_position_from_exchange(self) -> bool:
        """
        Check Binance for an existing open position and sync it locally.
        Returns True if a position was found and synced.
        """
        try:
            exchange_pos = await self.exchange.fetch_position()
            if exchange_pos is None:
                logger.info("No existing position found on exchange.")
                return False

            # Debug: log raw position data
            info = exchange_pos.get("info", {})
            logger.info(
                f"Raw position: side={exchange_pos.get('side')}, "
                f"contracts={exchange_pos.get('contracts')}, "
                f"entryPrice={exchange_pos.get('entryPrice')}, "
                f"positionAmt={info.get('positionAmt')}, "
                f"notional={exchange_pos.get('notional')}"
            )

            # ccxt unified format uses 'side' (string)
            side_str = str(exchange_pos.get("side", "")).lower()
            if side_str == "long":
                side = Side.LONG
            elif side_str == "short":
                side = Side.SHORT
            else:
                logger.warning(f"Unknown position side: '{side_str}'. Raw: {exchange_pos}")
                return False

            # ccxt unified format: entryPrice, contracts, leverage
            # Also check info dict for raw Binance fields
            info = exchange_pos.get("info", {})

            entry_price = (
                self._safe_float(exchange_pos.get("entryPrice"))
                or self._safe_float(info.get("entryPrice"))
                or 0
            )
            contracts = (
                self._safe_float(exchange_pos.get("contracts"))
                or self._safe_float(exchange_pos.get("contractSize"))
                or self._safe_float(info.get("positionAmt"))
                or 0
            )
            contracts = abs(contracts)  # positionAmt can be negative for shorts

            leverage = int(
                self._safe_float(exchange_pos.get("leverage"))
                or self._safe_float(info.get("leverage"))
                or self.config.leverage
            )

            if entry_price <= 0 or contracts <= 0:
                logger.warning(f"Invalid position data: entry={entry_price}, contracts={contracts}")
                return False

            # Compute SL/TP from the entry price
            sl, tp = self.risk_manager.compute_stop_take(entry_price, side)

            self.position = Position(
                side=side,
                entry_price=entry_price,
                quantity=contracts,
                leverage=leverage,
                stop_loss=sl,
                take_profit=tp,
                highest_price=entry_price,
                lowest_price=entry_price,
            )

            unrealized_pnl = (
                self._safe_float(exchange_pos.get("unrealizedPnl"))
                or self._safe_float(info.get("unRealizedProfit"))
                or 0
            )
            self.position.pnl_unrealized = unrealized_pnl

            notional = contracts * entry_price
            logger.info(
                f"SYNCED existing {side.value.upper()} position from exchange | "
                f"Entry: ${entry_price:,.2f} | Qty: {contracts:.6f} BTC | "
                f"Notional: ${notional:,.2f} | Leverage: {leverage}x | "
                f"UPnL: ${unrealized_pnl:.4f} | SL: ${sl:,.2f} | TP: ${tp:,.2f}"
            )
            return True

        except Exception as e:
            logger.error(f"Error syncing position from exchange: {e}", exc_info=True)
            return False

    async def verify_position_exists(self) -> bool:
        """
        Check if the local position actually exists on Binance.
        If not, clear the phantom position. Returns True if position is valid.
        """
        if self.position is None:
            return True  # No local position, nothing to verify

        if self.config.dry_run:
            return True  # Can't verify in dry-run

        try:
            exchange_pos = await self.exchange.fetch_position()
            if exchange_pos is None:
                logger.warning(
                    "PHANTOM POSITION detected! Local position exists but "
                    "Binance has NO open position. Clearing local state."
                )
                self.position = None
                return False

            # Verify it's the same side
            side_str = str(exchange_pos.get("side", "")).lower()
            if side_str != self.position.side.value:
                logger.warning(
                    f"Position side mismatch! Local: {self.position.side.value}, "
                    f"Exchange: {side_str}. Clearing local state."
                )
                self.position = None
                return False

            return True
        except Exception as e:
            logger.error(f"Error verifying position: {e}")
            return True  # Don't clear on error, assume it exists

    @staticmethod
    def _safe_float(val) -> float:
        """Safely convert a value to float, returning 0 on failure."""
        if val is None:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def get_position_info(self) -> Optional[dict]:
        """Get current position info for dashboard display."""
        if self.position is None:
            return None

        pos = self.position
        margin = (pos.quantity * pos.entry_price) / pos.leverage

        return {
            "side": pos.side.value.upper(),
            "entry_price": pos.entry_price,
            "quantity": pos.quantity,
            "margin": margin,
            "stop_loss": pos.stop_loss,
            "take_profit": pos.take_profit,
            "trailing_active": pos.trailing_stop_active,
            "trailing_price": pos.trailing_stop_price,
            "pnl_unrealized": pos.pnl_unrealized,
            "duration": time.time() - pos.entry_time,
        }
