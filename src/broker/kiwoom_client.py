"""
Isolated Kiwoom Broker REST API Client (src/broker/kiwoom_client.py)
Provides only 4 low-level I/O functions: get_token(), get_current_price(), get_balance(), post_order()
and raises KiwoomAPIException when API error response (rt_cd != '0') occurs.
"""
import sys
from typing import Dict, Any, List, Optional
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from kiwoom_client import KiwoomRESTClient
except (ImportError, ModuleNotFoundError):
    from trading_bot.kiwoom_client import KiwoomRESTClient

from src.config.settings import AppConfig


class KiwoomAPIException(Exception):
    """Standard Kiwoom API exception raised when return code (rt_cd) != '0'."""
    def __init__(self, code: str, message: str, raw_response: Optional[dict] = None):
        super().__init__(f"Kiwoom API Error [{code}]: {message}")
        self.code = str(code)
        self.message = str(message)
        self.raw_response = raw_response or {}


class KiwoomClient:
    """
    Isolated Low-Level Broker Client.
    Contains ONLY 4 low-level communication methods with ZERO trading business logic.
    """

    def __init__(self, app_config: Optional[AppConfig] = None):
        self.config = app_config or AppConfig()
        self._raw_client = KiwoomRESTClient(self.config)

    def get_token(self, force_refresh: bool = False) -> str:
        """1. Low-level OAuth 2.0 Access Token fetch."""
        try:
            return self._raw_client.get_access_token(force_refresh=force_refresh)
        except Exception as e:
            raise KiwoomAPIException("TOKEN_ERROR", f"Failed to acquire OAuth token: {e}") from e

    def get_current_price(self, symbol: str) -> int:
        """2. Low-level current price query."""
        clean_symbol = str(symbol).strip().zfill(6)
        price = self._raw_client.get_current_price(clean_symbol)
        if price <= 0:
            # Check for API error response fallback
            raise KiwoomAPIException("PRICE_ERROR", f"Invalid price received for symbol {clean_symbol}: {price}")
        return price

    def get_balance(self, account_no: Optional[str] = None) -> List[Dict[str, Any]]:
        """3. Low-level account balance and position query."""
        acc_no = account_no or self.config.account_no
        res = self._raw_client.get_positions(acc_no)
        if isinstance(res, dict) and not res.get("success", False) and "API Key" in res.get("message", ""):
            raise KiwoomAPIException("AUTH_ERROR", res.get("message", "Credentials invalid"))
        return res.get("positions", []) if isinstance(res, dict) else []

    def post_order(
        self,
        symbol: str,
        action: str,
        price: int,
        quantity: int,
        order_type: str = "LIMIT",
        account_no: Optional[str] = None
    ) -> Dict[str, Any]:
        """4. Low-level order placement function."""
        acc_no = account_no or self.config.account_no
        clean_symbol = str(symbol).strip().zfill(6)
        ord_dvsn = "00" if order_type.upper() in ["LIMIT", "00"] else "03"

        res = self._raw_client.send_order(
            account_no=acc_no,
            order_type=action.upper(),
            stock_code=clean_symbol,
            qty=quantity,
            price=price,
            ord_dvsn=ord_dvsn
        )

        if isinstance(res, dict):
            status = res.get("status")
            if not res.get("success") and status not in ["UNKNOWN_PENDING", "UNKNOWN"]:
                raise KiwoomAPIException(
                    code=str(status or "ORDER_REJECTED"),
                    message=str(res.get("message", "Order execution failed")),
                    raw_response=res
                )
            return res

        raise KiwoomAPIException("COMMUNICATION_ERROR", "Invalid response from Kiwoom API", {"raw": res})
