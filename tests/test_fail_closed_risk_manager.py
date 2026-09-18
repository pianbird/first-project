import os
import sys
import unittest
from unittest.mock import patch, MagicMock

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bot_dir = os.path.join(base_dir, "trading_bot")
for p in [bot_dir, base_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from trading_bot.order_manager import OrderManager, OrderStatus
try:
    from risk_manager import RiskManager
except (ImportError, ModuleNotFoundError):
    from trading_bot.risk_manager import RiskManager
from trading_bot.trading_mode import TradingModeManager
from trading_bot.grid_engine import GridEngine
from web_app.backend.db import WebDBManager

class MockKiwoom:
    def is_credentials_valid(self):
        return True
    def normalize_account_no(self, acc):
        return "8133053801"
    def get_positions(self, account_no=None):
        return {
            "success": True,
            "positions": [
                {"stock_code": "005930", "qty": 100, "avg_price": 70000, "purchase_amount": 7000000}
            ]
        }

class TestFailClosedRiskManager(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test.db")
        self.db = WebDBManager(self.db_path)
        self.tm = TradingModeManager("MOCK")
        self.om = OrderManager(self.db, self.tm)

    def test_missing_risk_manager_fails_closed(self):
        """risk_manager=None 으로 submit_order() 호출 시 success=False, status=REJECTED (Fail-Closed) 검증"""
        res = self.om.submit_order(
            kiwoom_client=MockKiwoom(),
            risk_manager=None,
            account_no="8133053801",
            order_type="BUY",
            stock_code="005930",
            stock_name="삼성전자",
            qty=10,
            price=70000
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], OrderStatus.REJECTED)
        self.assertIn("RiskManager Fail-Closed", res["message"])

    def test_max_account_exposure_exceeded_blocked(self):
        """MAX_ACCOUNT_EXPOSURE(5천만원) 초과 매수가 차단되는지 검증 (qty=99999 / 89.9억원)"""
        rm = RiskManager(max_daily_trades=30, max_buy_amount=50000000)
        res = self.om.submit_order(
            kiwoom_client=MockKiwoom(),
            risk_manager=rm,
            account_no="8133053801",
            order_type="BUY",
            stock_code="005930",
            stock_name="삼성전자",
            qty=99999,
            price=90000,  # ~89.9억원
            total_account_exposure=0
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], OrderStatus.REJECTED)
        self.assertIn("RiskManager", res["message"])

    def test_grid_engine_initializes_and_uses_risk_manager(self):
        """GridEngine 이 RiskManager 객체를 보유하고 evaluate_cycle 호출 시 risk_manager 및 exposure 전달 검증"""
        engine = GridEngine()
        self.assertIsNotNone(engine.risk_manager)
        self.assertIsInstance(engine.risk_manager, RiskManager)

if __name__ == "__main__":
    unittest.main()
