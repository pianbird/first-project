import os
import sys
import inspect
import unittest

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bot_dir = os.path.join(base_dir, "trading_bot")
for p in [bot_dir, base_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from trading_bot.kiwoom_client import KiwoomRESTClient
from trading_bot.storage import SQLiteStorage
from trading_bot.order_manager import OrderManager, OrderStatus
from trading_bot.trading_mode import TradingModeManager

class TestSchemaAndReconciliationRecovery(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_storage.db")
        self.storage = SQLiteStorage(self.db_path)

    def test_condition_1_get_positions_signature(self):
        """1. inspect.signature(KiwoomRESTClient.get_positions) 에 force_refresh 포함 검증"""
        sig = inspect.signature(KiwoomRESTClient.get_positions)
        self.assertIn("force_refresh", sig.parameters)
        param = sig.parameters["force_refresh"]
        self.assertEqual(param.default, False)

    def test_condition_2_storage_before_qty_persistence(self):
        """2. storage.save_order({... "before_qty": 777 ...}) 후 get_order_by_id() 결과에 before_qty == 777 검증"""
        order_info = {
            "order_id": "ORD_TEST_777",
            "client_order_key": "KEY_777",
            "stock_code": "005930",
            "side": "BUY",
            "requested_qty": 10,
            "filled_qty": 0,
            "remaining_qty": 10,
            "before_qty": 777,
            "price": 70000,
            "status": "UNKNOWN",
            "message": "Timeout"
        }
        self.storage.save_order(order_info)

        fetched = self.storage.get_order_by_id("ORD_TEST_777")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.get("before_qty"), 777)

        # Ensure ON CONFLICT update does NOT overwrite before_qty
        update_info = dict(order_info)
        update_info["status"] = "FAILED"
        update_info["before_qty"] = 999  # Attempting to overwrite snapshot
        self.storage.save_order(update_info)

        updated_fetched = self.storage.get_order_by_id("ORD_TEST_777")
        self.assertEqual(updated_fetched.get("status"), "FAILED")
        self.assertEqual(updated_fetched.get("before_qty"), 777)  # Preserved initial snapshot

    def test_condition_3_unknown_buy_order_resolved_as_failed_when_no_qty_change(self):
        """
        3. 보유 10주 상태에서 매수 주문 전 보유수량(before_qty)=10 스냅샷이 존재할 때
           현재 보유수량도 10주인 경우 UNKNOWN 매수 주문이 FAILED로 확정되는지 검증 (FILLED 오판정 방지)
        """
        om = OrderManager(self.storage, TradingModeManager("MOCK"))

        # Save an UNKNOWN buy order for 10 shares when before_qty was 10
        unknown_order = {
            "order_id": "ORD_BUY_UNK_10",
            "client_order_key": "KEY_BUY_UNK_10",
            "stock_code": "005930",
            "side": "BUY",
            "requested_qty": 10,
            "filled_qty": 0,
            "remaining_qty": 10,
            "before_qty": 10,  # Snapshot before order was 10 shares
            "price": 70000,
            "status": "UNKNOWN",
            "message": "Order Timeout"
        }
        self.storage.save_order(unknown_order)

        # Mock kiwoom returning positions with current qty = 10 (no change in position quantity)
        class MockKiwoomWithHolding:
            def get_positions(self, account_no=None):
                return {
                    "success": True,
                    "positions": [{"stock_code": "005930", "qty": 10, "avg_price": 70000}]
                }
            def get_open_orders(self, account_no=None, stock_code=None):
                return {"success": True, "open_orders": []}

        resolved_count = om.resolve_unknown_orders(MockKiwoomWithHolding())
        self.assertEqual(resolved_count, 1)

        resolved_order = self.storage.get_order_by_id("ORD_BUY_UNK_10")
        self.assertEqual(resolved_order["status"], OrderStatus.FAILED)

if __name__ == "__main__":
    unittest.main()
