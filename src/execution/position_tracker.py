"""
Position Tracker Module (src/execution/position_tracker.py)
Calls KiwoomClient.get_balance() at startup and post-fill to synchronize internal positions map with live broker account positions.
"""
import threading
from typing import Dict, Optional, List, Any
from src.domain.models import Position
from src.broker.kiwoom_client import KiwoomClient


class PositionTracker:
    """
    Live Account Position Tracker.
    Maintains thread-safe internal positions map synchronized with live broker account positions.
    """

    def __init__(self, kiwoom_client: KiwoomClient):
        self.kiwoom_client = kiwoom_client
        self._lock = threading.RLock()
        self._positions: Dict[str, Position] = {}
        self.sync_positions()

    def sync_positions(self) -> List[Position]:
        """
        Query KiwoomClient.get_balance() and refresh internal position map.
        """
        with self._lock:
            try:
                raw_positions = self.kiwoom_client.get_balance()
                updated_positions: Dict[str, Position] = {}
                for item in raw_positions:
                    symbol = str(item.get("stock_code", "")).strip().zfill(6)
                    qty = int(item.get("qty", 0))
                    avg_p = int(item.get("avg_price", 0))
                    cur_p = int(item.get("current_price", avg_p))

                    if qty > 0 and symbol:
                        pos = Position(
                            symbol=symbol,
                            quantity=qty,
                            buy_price=avg_p,
                            current_price=cur_p,
                            pnl=(cur_p - avg_p) * qty
                        )
                        updated_positions[symbol] = pos

                self._positions = updated_positions
            except Exception as e:
                print(f"[PositionTracker Warning] Failed to sync positions: {e}")

            return list(self._positions.values())

    def get_position(self, symbol: str) -> Optional[Position]:
        clean_symbol = str(symbol).strip().zfill(6)
        with self._lock:
            pos = self._positions.get(clean_symbol)
            if pos:
                return Position(
                    symbol=pos.symbol,
                    quantity=pos.quantity,
                    buy_price=pos.buy_price,
                    current_price=pos.current_price,
                    pnl=pos.pnl
                )
            return None

    def update_position_manually(self, symbol: str, quantity: int, price: int, action: str) -> None:
        """Immediate internal position map update post-execution."""
        clean_symbol = str(symbol).strip().zfill(6)
        with self._lock:
            existing = self._positions.get(clean_symbol)
            if action.upper() == "BUY":
                if existing and existing.quantity > 0:
                    new_qty = existing.quantity + quantity
                    new_avg = int((existing.buy_price * existing.quantity + price * quantity) / new_qty)
                    self._positions[clean_symbol] = Position(
                        symbol=clean_symbol,
                        quantity=new_qty,
                        buy_price=new_avg,
                        current_price=price,
                        pnl=(price - new_avg) * new_qty
                    )
                else:
                    self._positions[clean_symbol] = Position(
                        symbol=clean_symbol,
                        quantity=quantity,
                        buy_price=price,
                        current_price=price,
                        pnl=0
                    )
            elif action.upper() == "SELL":
                if existing:
                    new_qty = existing.quantity - quantity
                    if new_qty <= 0:
                        self._positions.pop(clean_symbol, None)
                    else:
                        self._positions[clean_symbol] = Position(
                            symbol=clean_symbol,
                            quantity=new_qty,
                            buy_price=existing.buy_price,
                            current_price=price,
                            pnl=(price - existing.buy_price) * new_qty
                        )


    def sync_grid_positions(self, grid_levels: List[Any], current_holding_qty: int) -> List[Any]:
        """Method wrapper for sync_grid_positions."""
        return sync_grid_positions(grid_levels, current_holding_qty)


def sync_grid_positions(grid_levels: List[Any], current_holding_qty: int) -> List[Any]:
    """
    [Requirement 4 Specification]
    - BUY_PENDING 레벨에 해당하는 주식 잔고 증가 확인 시 -> level.status = LevelStatus.POSITION_HOLD
    - SELL_PENDING 레벨에 해당하는 주식 잔고 차감 확인 시 -> level.status = LevelStatus.READY (무한 재가동)
    """
    from src.domain.models import LevelStatus

    # 1. Process BUY_PENDING / BUY_ORDERING fills on balance increase
    current_held_base = sum(
        getattr(lvl, "quantity", 1)
        for lvl in grid_levels
        if getattr(lvl, "status", None) == LevelStatus.POSITION_HOLD
    )
    pending_buys = [
        lvl for lvl in grid_levels
        if getattr(lvl, "status", None) in (LevelStatus.BUY_PENDING, LevelStatus.BUY_ORDERING)
    ]
    for lvl in pending_buys:
        qty = getattr(lvl, "quantity", 1)
        if current_holding_qty >= current_held_base + qty:
            lvl.status = LevelStatus.POSITION_HOLD
            lvl.order_id = None
            current_held_base += qty

    # 2. Process SELL_PENDING / SELL_ORDERING fills on balance decrease
    expected_held_total = sum(
        getattr(lvl, "quantity", 1)
        for lvl in grid_levels
        if getattr(lvl, "status", None) in (LevelStatus.POSITION_HOLD, LevelStatus.SELL_PENDING, LevelStatus.SELL_ORDERING)
    )
    pending_sells = [
        lvl for lvl in grid_levels
        if getattr(lvl, "status", None) in (LevelStatus.SELL_PENDING, LevelStatus.SELL_ORDERING)
    ]
    for lvl in pending_sells:
        qty = getattr(lvl, "quantity", 1)
        if current_holding_qty <= expected_held_total - qty:
            lvl.status = LevelStatus.READY
            lvl.order_id = None
            expected_held_total -= qty

    return grid_levels


