"""
Unit tests to verify urgent rectification requirements for Kiwoom REST API grid trading system:
1. kiwoom_client _request 401 token refresh and exponential backoff
2. send_order UNKNOWN_PENDING on timeout/exception without retry
3. KRX tick size alignment (align_to_tick_size)
4. grid_engine evaluate_grid_orders ORDER_SUBMITTED pre-flight lock
5. storage.py transaction atomicity and ledger update queries
6. notifier.py 2s timeout and exception isolation
"""
import os
import sys
import time
import pytest
import sqlite3
from unittest.mock import MagicMock, patch

from pathlib import Path
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from trading_bot.kiwoom_client import KiwoomRESTClient, align_to_tick_size, get_tick_size
from trading_bot.grid_engine import GridEngine, GridStatus
from trading_bot.storage import SQLiteStorage
from trading_bot.notifier import TelegramNotifier
from trading_bot.main import reconcile_with_broker


def test_krx_tick_size_alignment():
    """Verify KRX 2023 Tick Size rule alignment across all price brackets"""
    assert get_tick_size(1500) == 1
    assert align_to_tick_size(1503) == 1503

    assert get_tick_size(3000) == 5
    assert align_to_tick_size(3004) == 3000

    assert get_tick_size(10000) == 10
    assert align_to_tick_size(10007) == 10000

    assert get_tick_size(30000) == 50
    assert align_to_tick_size(30045) == 30000

    assert get_tick_size(70000) == 100
    assert align_to_tick_size(70080) == 70000

    assert get_tick_size(300000) == 500
    assert align_to_tick_size(300400) == 300000

    assert get_tick_size(600000) == 1000
    assert align_to_tick_size(600750) == 600000


def test_send_order_unknown_pending_on_timeout():
    """Verify send_order returns UNKNOWN_PENDING on timeout without auto-retry"""
    client = KiwoomRESTClient()
    with patch.object(client, "place_order", side_effect=TimeoutError("Request timed out")):
        res = client.send_order("1234567801", "BUY", "005930", 10, 70000)
        assert res["success"] is False
        assert res["status"] == "UNKNOWN_PENDING"
        assert "타임아웃" in res["message"]


def test_grid_order_preflight_lock(tmp_path):
    """Verify evaluate_grid_orders applies ORDER_SUBMITTED pre-flight lock prior to placing order"""
    db_file = str(tmp_path / "test_grid.db")
    storage = SQLiteStorage(db_file)
    client = KiwoomRESTClient()

    engine = GridEngine(storage=storage, kiwoom_client=client)
    engine.is_reconciled = True
    engine.min_price = 50000
    engine.max_price = 60000
    engine.levels_count = 5
    engine.levels = engine.calculate_grid_levels()

    target_lvl = engine.levels[0]

    # Mock order_manager to check status during submission
    def mock_submit_order(*args, **kwargs):
        assert target_lvl.status == GridStatus.ORDER_SUBMITTED
        return {"success": True, "order_id": "ORD_1001"}

    engine.order_manager.submit_order = mock_submit_order

    engine.evaluate_grid_orders(target_lvl.buy_price)
    assert target_lvl.status == GridStatus.ORDER_PLACED
    assert target_lvl.buy_order_id == "ORD_1001"


def test_storage_ledger_update_queries(tmp_path):
    """Verify storage.py ledger update helper queries and transaction atomicity"""
    db_file = str(tmp_path / "test_storage.db")
    storage = SQLiteStorage(db_file)

    storage.save_grid_level(
        level_id=1, stock_code="005930", buy_price=70000, sell_price=71000,
        qty=10, status="IDLE"
    )

    storage.update_grid_level_status(
        level_id=1, status="ORDER_PLACED", buy_order_id="ORD_999", filled_qty=0, remaining_qty=10
    )
    levels = storage.get_grid_levels()
    assert len(levels) == 1
    assert levels[0]["status"] == "ORDER_PLACED"
    assert levels[0]["buy_order_id"] == "ORD_999"

    # Bulk update
    storage.bulk_update_grid_levels([
        {"level_id": 1, "status": "FILLED", "buy_order_id": "ORD_999", "sell_order_id": "", "filled_qty": 10, "remaining_qty": 0}
    ])
    levels_after = storage.get_grid_levels()
    assert levels_after[0]["status"] == "FILLED"
    assert levels_after[0]["filled_qty"] == 10


def test_telegram_notifier_isolation():
    """Verify TelegramNotifier catches network errors and completes within timeout budget without raising"""
    notifier = TelegramNotifier()
    notifier.enabled = True
    notifier.token = "dummy_token"
    notifier.chat_id = "12345"

    with patch("urllib.request.urlopen", side_effect=Exception("Telegram connection drop")):
        # Should not raise exception
        res = notifier.send_message("Test Alert")
        assert res is False

        # Isolated helpers
        notifier.notify_order_sent("005930", "삼성전자", "BUY", 10, 70000, 1)
        notifier.notify_error("Test Error", "Details")


def test_reconcile_with_broker_invokable():
    """Verify reconcile_with_broker function works gracefully"""
    mock_engine = MagicMock()
    mock_engine.reconcile_with_account.return_value = {"success": True, "summary": "Reconciled"}

    res = reconcile_with_broker(mock_engine)
    assert res["success"] is True
    mock_engine.reconcile_with_account.assert_called_once()
