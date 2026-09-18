"""
Order Executor Module (src/execution/order_executor.py)
Locks symbol state to ORDERING pre-trade to block duplicate concurrent orders,
and notifies PositionTracker upon order result acknowledgment.
"""
import time
import threading
from typing import Dict, Set, Optional
from src.domain.models import TradeSignal, OrderResult, TradeAction
from src.broker.kiwoom_client import KiwoomClient, KiwoomAPIException
from src.execution.position_tracker import PositionTracker


class OrderExecutor:
    """
    Thread-safe Order Executor.
    - Applies pre-trade ORDERING lock per symbol.
    - Submits order to KiwoomClient.
    - Immediately notifies PositionTracker to sync positions upon completion.
    """

    def __init__(self, kiwoom_client: KiwoomClient, position_tracker: PositionTracker):
        self.kiwoom_client = kiwoom_client
        self.position_tracker = position_tracker
        self._lock = threading.RLock()
        self._order_locks: Set[str] = set()

    def is_ordering(self, symbol: str) -> bool:
        clean_symbol = str(symbol).strip().zfill(6)
        with self._lock:
            return clean_symbol in self._order_locks

    def execute_order(self, signal: TradeSignal) -> OrderResult:
        """
        Execute trade signal with pre-trade ORDERING lock and post-trade PositionTracker sync.
        """
        clean_symbol = str(signal.symbol).strip().zfill(6)

        # 1. Pre-trade lock check
        with self._lock:
            if clean_symbol in self._order_locks:
                return OrderResult(
                    order_id="",
                    symbol=clean_symbol,
                    action=signal.action,
                    quantity=signal.quantity,
                    price=signal.price,
                    success=False,
                    message=f"Order blocked: Symbol {clean_symbol} is in ORDERING state"
                )
            self._order_locks.add(clean_symbol)

        try:
            # 2. Submit order to low-level KiwoomClient.post_order()
            raw_res = self.kiwoom_client.post_order(
                symbol=clean_symbol,
                action=signal.action.value if isinstance(signal.action, TradeAction) else str(signal.action),
                price=signal.price,
                quantity=signal.quantity,
                order_type=signal.order_type.value if hasattr(signal.order_type, "value") else str(signal.order_type)
            )

            success = bool(raw_res.get("success", False))
            ord_id = str(raw_res.get("order_id", f"ORD_{int(time.time()*1000)}")) if success else ""
            msg = str(raw_res.get("message", "Order placed"))

            result = OrderResult(
                order_id=ord_id,
                symbol=clean_symbol,
                action=signal.action,
                quantity=signal.quantity,
                price=signal.price,
                success=success,
                message=msg
            )

            # 3. Notify PositionTracker post-trade
            if success:
                self.position_tracker.update_position_manually(
                    symbol=clean_symbol,
                    quantity=signal.quantity,
                    price=signal.price,
                    action=signal.action.value if isinstance(signal.action, TradeAction) else str(signal.action)
                )
                self.position_tracker.sync_positions()

            return result

        except KiwoomAPIException as api_err:
            return OrderResult(
                order_id="",
                symbol=clean_symbol,
                action=signal.action,
                quantity=signal.quantity,
                price=signal.price,
                success=False,
                message=f"KiwoomAPIException: {api_err.message}"
            )
        except Exception as e:
            return OrderResult(
                order_id="",
                symbol=clean_symbol,
                action=signal.action,
                quantity=signal.quantity,
                price=signal.price,
                success=False,
                message=f"Execution Exception: {e}"
            )
        finally:
            with self._lock:
                # Release ORDERING lock
                self._order_locks.discard(clean_symbol)
