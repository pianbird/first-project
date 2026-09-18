"""
Thread-safe Execution & Order Manager (src/execution/order_manager.py)
Implements symbol-level pending order lock checks and idempotency guards.
"""
import threading
from typing import Dict, Set, Optional, Any
from src.core.models import OrderSignal, OrderResult
from src.broker.kiwoom_broker import KiwoomBrokerClient


class OrderManager:
    """
    Thread-safe Order Execution Manager.
    - Check if symbol is already in pending_orders set. If yes, ignore signal.
    - Locks symbol during pending state and releases upon order acknowledgment/rejection.
    """

    def __init__(self, broker: KiwoomBrokerClient):
        self.broker = broker
        self._lock = threading.RLock()
        self._pending_orders: Set[str] = set()

    def is_symbol_pending(self, code: str) -> bool:
        clean_code = str(code).strip().zfill(6)
        with self._lock:
            return clean_code in self._pending_orders

    def execute_signal(self, signal: Any) -> Optional[OrderResult]:
        """
        Pre-trade check and execution:
        1. If symbol is in pending_orders, ignore signal.
        2. Acquire lock, place order via broker, and update pending state.
        """
        clean_code = str(getattr(signal, "symbol", getattr(signal, "code", ""))).strip().zfill(6)
        action = getattr(signal.action, "value", str(signal.action))
        order_type = getattr(signal.order_type, "value", str(signal.order_type))

        with self._lock:
            if clean_code in self._pending_orders:
                # Idempotency guard: pending order exists for this symbol
                return OrderResult(
                    order_id="",
                    code=clean_code,
                    status="REJECTED",
                    msg=f"Order blocked: pending order exists for symbol {clean_code}"
                )

            # Acquire pending lock for symbol
            self._pending_orders.add(clean_code)

        try:
            result = self.broker.place_order(
                code=clean_code,
                action=action,
                price=signal.price,
                quantity=signal.quantity,
                order_type=order_type
            )
            return result
        finally:
            with self._lock:
                # Release lock upon acknowledgment / rejection
                self._pending_orders.discard(clean_code)
