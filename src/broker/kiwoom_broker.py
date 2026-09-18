"""
Isolated Kiwoom Broker Client Module (src/broker/kiwoom_broker.py)
Encapsulates REST API & WebSocket I/O with automatic OAuth 2.0 token management
and thread-safe balance, price, and order execution interfaces.
"""
import os
import sys
import time
import threading
from typing import Dict, Any, Optional, List
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from kiwoom_client import KiwoomRESTClient
except (ImportError, ModuleNotFoundError):
    from trading_bot.kiwoom_client import KiwoomRESTClient

from src.core.models import Position, OrderResult


class KiwoomBrokerClient:
    """
    Isolated Broker Client wrapping Kiwoom REST API interactions.
    Provides clean thread-safe methods: fetch_balance(), fetch_price(), place_order().
    """

    def __init__(self, config: Optional[Any] = None):
        self._lock = threading.RLock()
        self.client = KiwoomRESTClient(config)
        self.account_no = getattr(self.client.config, "ACCOUNT_NO", "")

    def _ensure_valid_token(self) -> str:
        """Ensure token is auto-refreshed before expiration."""
        with self._lock:
            return self.client.get_access_token(force_refresh=False)

    def fetch_balance(self, account_no: Optional[str] = None) -> List[Position]:
        """
        Thread-safe account balance fetch returning domain Position models.
        """
        acc_no = account_no or self.account_no
        with self._lock:
            self._ensure_valid_token()
            res = self.client.get_positions(account_no=acc_no)
            positions: List[Position] = []

            if isinstance(res, dict) and res.get("success"):
                raw_positions = res.get("positions", [])
                for item in raw_positions:
                    code = str(item.get("stock_code", "")).strip().zfill(6)
                    qty = int(item.get("qty", 0))
                    avg_p = int(item.get("avg_price", 0))
                    cur_p = int(item.get("current_price", avg_p))
                    pos = Position(
                        code=code,
                        quantity=qty,
                        avg_price=avg_p,
                        current_price=cur_p,
                        unrealized_pnl=(cur_p - avg_p) * qty
                    )
                    positions.append(pos)

            return positions

    def fetch_price(self, code: str) -> int:
        """
        Thread-safe current stock price fetch.
        """
        with self._lock:
            self._ensure_valid_token()
            return self.client.get_current_price(code)

    def place_order(
        self,
        code: str,
        action: str,
        price: int,
        quantity: int,
        order_type: str = "LIMIT",
        account_no: Optional[str] = None
    ) -> OrderResult:
        """
        Thread-safe order execution method returning a domain OrderResult.
        """
        acc_no = account_no or self.account_no
        clean_code = str(code).strip().zfill(6)
        side = action.upper()
        ord_dvsn = "00" if order_type.upper() in ["LIMIT", "00"] else "03"

        with self._lock:
            self._ensure_valid_token()
            res = self.client.send_order(
                account_no=acc_no,
                order_type=side,
                stock_code=clean_code,
                qty=quantity,
                price=price,
                ord_dvsn=ord_dvsn
            )

            if isinstance(res, dict) and res.get("success"):
                ord_id = str(res.get("order_id", f"ORD_{int(time.time()*1000)}"))
                return OrderResult(
                    order_id=ord_id,
                    code=clean_code,
                    status="FILLED" if res.get("status") == "FILLED" else "PENDING",
                    msg=res.get("message", "Order placed successfully")
                )
            else:
                msg = res.get("message", "Order rejected") if isinstance(res, dict) else str(res)
                return OrderResult(
                    order_id="",
                    code=clean_code,
                    status="REJECTED",
                    msg=msg
                )
