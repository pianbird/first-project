"""
Pure Static Grid Signal Engine (src/strategy/static_grid_strategy.py)
Monitors real-time current price against grid levels and transitions level status.
"""
from typing import List, Optional, Tuple
from src.domain.models import GridLevel, LevelStatus, TradeSignal, TradeAction, OrderType


def check_signals(current_price: float, grid_levels: List[GridLevel]) -> Optional[Tuple[GridLevel, str]]:
    """
    [Requirement 2 Specification]
    check_signals(current_price: float, grid_levels: List[GridLevel]) -> Optional[Tuple[GridLevel, str]]

    1. [매수 감시]
       - 조건: level.status == LevelStatus.READY AND current_price <= level.buy_price
       - 만족하는 레벨 중 buy_price가 현재가와 가장 가까운 1개 레벨 선별.
       - 레벨 상태를 즉시 LevelStatus.BUY_ORDERING으로 변경 후 (level, "BUY") 반환.
    2. [매도 감시]
       - 조건: level.status == LevelStatus.POSITION_HOLD AND current_price >= level.sell_price
       - 만족하는 레벨 중 sell_price가 현재가와 가장 가까운 1개 레벨 선별.
       - 레벨 상태를 즉시 LevelStatus.SELL_ORDERING으로 변경 후 (level, "SELL") 반환.
    """
    if current_price <= 0 or not grid_levels:
        return None

    # 1. Check SELL signals for held positions (prioritize profit realization)
    hold_levels = [
        lvl for lvl in grid_levels
        if lvl.status == LevelStatus.POSITION_HOLD and current_price >= lvl.sell_price
    ]
    if hold_levels:
        closest_level = min(hold_levels, key=lambda l: abs(l.sell_price - current_price))
        closest_level.status = LevelStatus.SELL_ORDERING
        return (closest_level, "SELL")

    # 2. Check BUY signals for ready levels
    ready_levels = [
        lvl for lvl in grid_levels
        if lvl.status == LevelStatus.READY and current_price <= lvl.buy_price
    ]
    if ready_levels:
        closest_level = min(ready_levels, key=lambda l: abs(l.buy_price - current_price))
        closest_level.status = LevelStatus.BUY_ORDERING
        return (closest_level, "BUY")

    return None


class StaticGridStrategy:
    """
    Class wrapper for static grid strategy.
    """

    def __init__(self, symbol: str, levels: List[GridLevel]):
        self.symbol = symbol
        self.levels = levels

    def evaluate(self, current_price: float) -> Optional[TradeSignal]:
        res = check_signals(current_price, self.levels)
        if not res:
            return None
        level, action = res
        trade_action = TradeAction.BUY if action == "BUY" else TradeAction.SELL
        price = level.buy_price if action == "BUY" else level.sell_price
        return TradeSignal(
            symbol=self.symbol,
            action=trade_action,
            order_type=OrderType.MARKET,
            price=price,
            quantity=level.quantity
        )
