"""
Grid Execution and Synchronization Manager (src/execution/grid_executor.py)
Executes market orders via Kiwoom client and transitions level state machine.
"""
from typing import List, Optional, Any
from src.domain.models import GridLevel, LevelStatus, TradeAction


def execute_grid_order(kiwoom_client: Any, symbol: str, level: GridLevel, action: str) -> bool:
    """
    [Requirement 3 Specification]
    시장가 주문 처리:
    - 매수: kiwoom_client.send_order(action="BUY", order_type="MARKET", quantity=level.quantity)
      성공 시 level.status = LevelStatus.BUY_PENDING, 실패 시 즉시 LevelStatus.READY로 롤백.
    - 매도: kiwoom_client.send_order(action="SELL", order_type="MARKET", quantity=level.quantity)
      성공 시 level.status = LevelStatus.SELL_PENDING, 실패 시 즉시 LevelStatus.POSITION_HOLD로 롤백.
    """
    try:
        # Determine send_order argument names dynamically for compatibility with mock or real client
        send_fn = getattr(kiwoom_client, "send_order", None) or getattr(kiwoom_client, "post_order", None)
        if not send_fn:
            raise AttributeError("Client does not have send_order or post_order method")

        res = send_fn(
            symbol=symbol,
            action=action,
            order_type="MARKET",
            quantity=level.quantity,
            price=0
        )

        success = True
        order_id: Optional[str] = None
        if isinstance(res, dict):
            success = res.get("success", res.get("rt_cd") == "0")
            order_id = res.get("order_id") or res.get("ODNO")
        elif hasattr(res, "success"):
            success = getattr(res, "success", True)
            order_id = getattr(res, "order_id", None)
        elif res is False:
            success = False

        if success:
            if action == "BUY":
                level.status = LevelStatus.BUY_PENDING
            else:
                level.status = LevelStatus.SELL_PENDING
            if order_id:
                level.order_id = str(order_id)
            return True
        else:
            # Rollback on failure
            if action == "BUY":
                level.status = LevelStatus.READY
            else:
                level.status = LevelStatus.POSITION_HOLD
            level.order_id = None
            return False

    except Exception:
        # Immediate rollback on exception
        if action == "BUY":
            level.status = LevelStatus.READY
        else:
            level.status = LevelStatus.POSITION_HOLD
        level.order_id = None
        return False


class GridExecutor:
    """
    Grid Executor wrapper class.
    """

    def __init__(self, levels: List[GridLevel]):
        self.levels = levels

    def execute(self, kiwoom_client: Any, symbol: str, level: GridLevel, action: str) -> bool:
        return execute_grid_order(kiwoom_client, symbol, level, action)

    def bind_order(self, level_id: int, order_id: str) -> bool:
        for lvl in self.levels:
            if lvl.level_id == level_id:
                lvl.order_id = order_id
                return True
        return False

    def handle_fill(self, order_id: Optional[str] = None, level_id: Optional[int] = None, action: Optional[TradeAction] = None) -> Optional[GridLevel]:
        target_level: Optional[GridLevel] = None
        for lvl in self.levels:
            if order_id and lvl.order_id == order_id:
                target_level = lvl
                break
            elif level_id and lvl.level_id == level_id:
                target_level = lvl
                break

        if not target_level and action:
            for lvl in self.levels:
                if action in (TradeAction.BUY, "BUY") and lvl.status in (LevelStatus.BUY_PENDING, LevelStatus.BUY_ORDERING):
                    target_level = lvl
                    break
                elif action in (TradeAction.SELL, "SELL") and lvl.status in (LevelStatus.SELL_PENDING, LevelStatus.SELL_ORDERING):
                    target_level = lvl
                    break

        if not target_level:
            return None

        if target_level.status in (LevelStatus.BUY_PENDING, LevelStatus.BUY_ORDERING) or action in (TradeAction.BUY, "BUY"):
            target_level.status = LevelStatus.POSITION_HOLD
            target_level.order_id = None
            return target_level
        elif target_level.status in (LevelStatus.SELL_PENDING, LevelStatus.SELL_ORDERING) or action in (TradeAction.SELL, "SELL"):
            target_level.status = LevelStatus.READY
            target_level.order_id = None
            return target_level

        return None
