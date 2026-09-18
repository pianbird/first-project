"""
Pure Grid Strategy Engine (src/strategy/grid_strategy.py)
Contains pure mathematical grid evaluation logic with ZERO network or I/O imports.
"""
from typing import Optional, List, Dict
from src.core.models import TickData, Position, OrderSignal


class GridStrategy:
    """
    Pure Grid Strategy Evaluator.
    Calculates static grid lines and evaluates TickData against current Position.
    """

    def __init__(self, min_price: int = 50000, max_price: int = 80000, grid_levels: int = 10, amount_per_level: int = 100000):
        self.min_price = min_price
        self.max_price = max_price
        self.grid_levels = grid_levels
        self.amount_per_level = amount_per_level
        self.levels: List[Dict[str, int]] = self._calculate_levels()

    def _calculate_levels(self) -> List[Dict[str, int]]:
        step = (self.max_price - self.min_price) / max(1, self.grid_levels)
        calculated = []
        for i in range(1, self.grid_levels + 1):
            buy_p = int(self.min_price + (i - 1) * step)
            sell_p = int(buy_p * 1.015)
            qty = max(1, self.amount_per_level // buy_p) if buy_p > 0 else 1
            calculated.append({
                "level_id": i,
                "buy_price": buy_p,
                "sell_price": sell_p,
                "qty": qty
            })
        return calculated

    def evaluate(self, tick: TickData, position: Optional[Position]) -> Optional[OrderSignal]:
        """
        Pure grid signal evaluation.
        """
        if tick.price <= 0:
            return None

        # 1. Evaluate Buy Condition
        for lvl in self.levels:
            if tick.price <= lvl["buy_price"]:
                if position is None or position.quantity == 0:
                    return OrderSignal(
                        code=tick.code,
                        action="BUY",
                        order_type="LIMIT",
                        price=lvl["buy_price"],
                        quantity=lvl["qty"]
                    )

        # 2. Evaluate Sell Condition
        if position is not None and position.quantity > 0:
            for lvl in self.levels:
                if tick.price >= lvl["sell_price"]:
                    return OrderSignal(
                        code=tick.code,
                        action="SELL",
                        order_type="LIMIT",
                        price=lvl["sell_price"],
                        quantity=min(lvl["qty"], position.quantity)
                    )

        return None
