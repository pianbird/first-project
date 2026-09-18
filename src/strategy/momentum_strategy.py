"""
Pure Momentum & Technical Strategy Module (src/strategy/momentum_strategy.py)
Contains ZERO external network API or global variable imports.
Provides signature: evaluate(candles: Any, current_position: Optional[Position]) -> Optional[TradeSignal]
"""
from typing import Optional, List, Any, Union
from src.domain.models import Position, TradeSignal, TradeAction, OrderType


class MomentumStrategy:
    """
    Pure Technical Strategy Engine.
    Evaluates candle price data against position state and returns a TradeSignal or None.
    Contains ZERO external network I/O or broker imports.
    """

    def __init__(
        self,
        short_window: int = 5,
        long_window: int = 20,
        window_size: Optional[int] = None,
        threshold_pct: float = 0.015,
        target_profit_rate: float = 0.03,
        stop_loss_rate: float = 0.02,
        order_quantity: int = 10
    ):
        if window_size is not None:
            self.short_window = window_size
            self.long_window = window_size
            self.window_size = window_size
        else:
            self.short_window = short_window
            self.long_window = long_window
            self.window_size = short_window

        self.threshold_pct = threshold_pct
        self.target_profit_rate = target_profit_rate
        self.stop_loss_rate = stop_loss_rate
        self.order_quantity = order_quantity
        self._price_history: List[int] = []

    def _extract_close_prices(self, candles: Any) -> List[int]:
        """Safely extract close prices list from pandas DataFrame or list of dicts/ints."""
        if hasattr(candles, "get") and callable(getattr(candles, "get")):
            # pandas DataFrame or dict-like
            if "close" in candles:
                return [int(x) for x in candles["close"]]
        elif isinstance(candles, list):
            prices = []
            for item in candles:
                if isinstance(item, dict):
                    prices.append(int(item.get("close", item.get("price", 0))))
                elif isinstance(item, (int, float)):
                    prices.append(int(item))
            return prices
        return []

    def evaluate(self, candles: Any, current_position: Optional[Position]) -> Optional[TradeSignal]:
        """
        Pure evaluation method.
        :param candles: Candle DataFrame, list of price records, or single TickData model
        :param current_position: Optional Position domain model
        :return: Optional TradeSignal
        """
        if hasattr(candles, "price") and hasattr(candles, "code"):
            # Single TickData model passed
            symbol = candles.code
            self._price_history.append(candles.price)
            if len(self._price_history) > max(self.long_window, self.short_window):
                self._price_history.pop(0)

            if len(self._price_history) < self.short_window:
                return None

            prices = self._price_history
            current_price = candles.price
        else:
            prices = self._extract_close_prices(candles)
            if not prices or len(prices) < min(self.long_window, self.short_window):
                return None
            current_price = prices[-1]
            symbol = current_position.symbol if current_position else "005930"

        if len(prices) >= 2:
            baseline = prices[0]
            if baseline > 0:
                p_change = (current_price - baseline) / baseline
                if p_change >= self.threshold_pct:
                    if current_position is None or current_position.quantity == 0:
                        return TradeSignal(
                            symbol=symbol,
                            action=TradeAction.BUY,
                            order_type=OrderType.LIMIT,
                            price=current_price,
                            quantity=self.order_quantity
                        )
                elif p_change <= -self.threshold_pct:
                    if current_position is not None and current_position.quantity > 0:
                        return TradeSignal(
                            symbol=symbol,
                            action=TradeAction.SELL,
                            order_type=OrderType.LIMIT,
                            price=current_price,
                            quantity=current_position.quantity
                        )

        short_ma = sum(prices[-self.short_window:]) / max(1, self.short_window)
        long_ma = sum(prices[-self.long_window:]) / max(1, min(len(prices), self.long_window))

        # 1. Stop Loss & Take Profit Risk Evaluation if Position Exists
        if current_position and current_position.quantity > 0:
            buy_price = current_position.buy_price
            if buy_price > 0:
                pnl_rate = (current_price - buy_price) / buy_price
                if pnl_rate >= self.target_profit_rate or pnl_rate <= -self.stop_loss_rate:
                    return TradeSignal(
                        symbol=current_position.symbol,
                        action=TradeAction.SELL,
                        order_type=OrderType.LIMIT,
                        price=current_price,
                        quantity=current_position.quantity
                    )

        # 2. Golden Cross / Price Rise (BUY) Condition
        if short_ma > long_ma or current_price > prices[0]:
            if current_position is None or current_position.quantity == 0:
                return TradeSignal(
                    symbol=symbol,
                    action=TradeAction.BUY,
                    order_type=OrderType.LIMIT,
                    price=current_price,
                    quantity=self.order_quantity
                )

        # 3. Dead Cross (SELL) Condition
        elif short_ma < long_ma:
            if current_position is not None and current_position.quantity > 0:
                return TradeSignal(
                    symbol=current_position.symbol,
                    action=TradeAction.SELL,
                    order_type=OrderType.LIMIT,
                    price=current_price,
                    quantity=current_position.quantity
                )

        return None
