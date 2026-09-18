"""
Thread-safe Portfolio Manager (src/portfolio/portfolio_manager.py)
Tracks live cash balances and open positions using RLock.
"""
import threading
from typing import Dict, Optional, List
from src.core.models import Position


class PortfolioManager:
    """
    Thread-safe Portfolio Manager.
    Uses threading.RLock to manage live cash balance and open positions map safely.
    """

    def __init__(self, initial_cash: int = 100000000):
        self._lock = threading.RLock()
        self._cash: int = initial_cash
        self._positions: Dict[str, Position] = {}

    @property
    def cash(self) -> int:
        with self._lock:
            return self._cash

    def set_cash(self, amount: int) -> None:
        with self._lock:
            self._cash = amount

    def get_position(self, code: str) -> Optional[Position]:
        clean_code = str(code).strip().zfill(6)
        with self._lock:
            pos = self._positions.get(clean_code)
            if pos:
                # Return defensive copy
                return Position(
                    code=pos.code,
                    quantity=pos.quantity,
                    avg_price=pos.avg_price,
                    current_price=pos.current_price,
                    unrealized_pnl=pos.unrealized_pnl
                )
            return None

    def get_all_positions(self) -> List[Position]:
        with self._lock:
            return [
                Position(
                    code=p.code,
                    quantity=p.quantity,
                    avg_price=p.avg_price,
                    current_price=p.current_price,
                    unrealized_pnl=p.unrealized_pnl
                )
                for p in self._positions.values()
            ]

    def update_position(self, code: str, quantity: int, avg_price: int, current_price: int = 0) -> None:
        clean_code = str(code).strip().zfill(6)
        with self._lock:
            if quantity <= 0:
                self._positions.pop(clean_code, None)
            else:
                pnl = (current_price - avg_price) * quantity if current_price > 0 else 0
                self._positions[clean_code] = Position(
                    code=clean_code,
                    quantity=quantity,
                    avg_price=avg_price,
                    current_price=current_price,
                    unrealized_pnl=pnl
                )

    def apply_execution(self, code: str, action: str, price: int, quantity: int) -> None:
        """Apply executed fill to cash and position model."""
        clean_code = str(code).strip().zfill(6)
        with self._lock:
            existing = self._positions.get(clean_code)
            total_val = price * quantity

            if action.upper() == "BUY":
                self._cash -= total_val
                if existing and existing.quantity > 0:
                    new_qty = existing.quantity + quantity
                    new_avg = int((existing.avg_price * existing.quantity + total_val) / new_qty)
                    self._positions[clean_code] = Position(
                        code=clean_code,
                        quantity=new_qty,
                        avg_price=new_avg,
                        current_price=price,
                        unrealized_pnl=(price - new_avg) * new_qty
                    )
                else:
                    self._positions[clean_code] = Position(
                        code=clean_code,
                        quantity=quantity,
                        avg_price=price,
                        current_price=price,
                        unrealized_pnl=0
                    )
            elif action.upper() == "SELL":
                self._cash += total_val
                if existing:
                    new_qty = existing.quantity - quantity
                    if new_qty <= 0:
                        self._positions.pop(clean_code, None)
                    else:
                        self._positions[clean_code] = Position(
                            code=clean_code,
                            quantity=new_qty,
                            avg_price=existing.avg_price,
                            current_price=price,
                            unrealized_pnl=(price - existing.avg_price) * new_qty
                        )
