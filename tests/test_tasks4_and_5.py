import os
import sys
import tempfile
import pytest
from unittest.mock import MagicMock

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
trading_bot_dir = os.path.join(base_dir, "trading_bot")
for p in [base_dir, trading_bot_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from order_manager import OrderManager, OrderStatus
from storage import SQLiteStorage
from grid_engine import GridEngine, GridStatus
from config import Config


def test_poll_open_order_status():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        storage = SQLiteStorage(db_path)
        order_mgr = OrderManager(storage)

        # Create a pending order
        order_mgr.register_new_order(
            order_id="PENDING_1001",
            stock_code="005930",
            stock_name="삼성전자",
            side="BUY",
            qty=10,
            price=50000,
            initial_status=OrderStatus.ACCEPTED,
            before_qty=5
        )

        mock_kiwoom = MagicMock()
        mock_kiwoom.get_open_orders.return_value = {"success": True, "open_orders": []}
        mock_kiwoom.is_credentials_valid.return_value = True
        # Holding balance indicates 15 shares now (+10 delta -> FILLED)
        mock_kiwoom.get_positions.return_value = {
            "success": True,
            "positions": [{"code": "005930", "qty": 15, "avg_price": 50000}]
        }

        count = order_mgr.poll_open_order_status(mock_kiwoom, "005930")
        assert count == 1

        updated_order = storage.get_order_by_id("PENDING_1001")
        assert updated_order["status"] == OrderStatus.FILLED
        assert updated_order["filled_qty"] == 10

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_grid_engine_h3_and_h8():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        storage = SQLiteStorage(db_path)
        mock_kiwoom = MagicMock()
        mock_kiwoom.is_credentials_valid.return_value = True
        mock_kiwoom.get_positions.return_value = {"success": True, "positions": []}
        mock_kiwoom.get_open_orders.return_value = {"success": True, "open_orders": []}

        mock_notifier = MagicMock()
        engine = GridEngine(Config, storage, mock_kiwoom, mock_notifier)
        engine.is_reconciled = True

        # Test H-8: Rejected BUY order should NOT trigger notify_order_sent
        lvl1 = engine.levels[0]
        lvl1.status = GridStatus.IDLE

        # Mock submit_order returning rejected
        engine.order_manager.submit_order = MagicMock(return_value={
            "success": False, "status": OrderStatus.REJECTED, "message": "Risk limit"
        })

        engine.evaluate_cycle(current_price=lvl1.buy_price)

        # notify_order_sent should NOT have been called
        mock_notifier.notify_order_sent.assert_not_called()
        assert lvl1.status == GridStatus.IDLE

        # Test H-3: Sell exception fail-closed (continue)
        lvl_sell = engine.levels[1]
        lvl_sell.status = GridStatus.FILLED
        lvl_sell.filled_qty = 5

        # Force exception in get_positions during sell holding check
        mock_kiwoom.get_positions.side_effect = Exception("API Timeout")

        engine.evaluate_cycle(current_price=lvl_sell.sell_price)

        # Status should remain FILLED, not ORDER_SUBMITTED
        assert lvl_sell.status == GridStatus.FILLED

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
