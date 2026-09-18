import os
import sys
import tempfile
import pytest

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
trading_bot_dir = os.path.join(base_dir, "trading_bot")
for p in [base_dir, trading_bot_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from storage import Storage, load_json, atomic_write_json


def test_storage_static_methods():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        json_path = tf.name

    try:
        sample_levels = [
            {"level_id": 1, "status": "FILLED", "buy_price": 50000},
            {"level_id": 2, "status": "IDLE", "buy_price": 53000}
        ]
        sample_orders = [
            {"order_id": "ORD001", "status": "ACCEPTED", "stock_code": "005930"}
        ]
        sample_positions = [
            {"code": "005930", "qty": 10, "avg_price": 50000}
        ]

        # 1. Test save_grid_state
        ok = Storage.save_grid_state(
            file_path=json_path,
            stock_code="005930",
            levels=sample_levels,
            active_orders=sample_orders,
            positions=sample_positions,
            is_reconciled=True
        )
        assert ok is True

        # 2. Test load_grid_state
        loaded = Storage.load_grid_state(json_path)
        assert loaded.get("stock_code") == "005930"
        assert loaded.get("is_reconciled") is True
        assert len(loaded.get("levels", [])) == 2
        assert loaded.get("levels")[0]["buy_price"] == 50000
        assert len(loaded.get("active_orders", [])) == 1
        assert len(loaded.get("positions", [])) == 1

        # 3. Test general load_state & save_state
        direct_data = {"custom_key": "custom_value", "number": 123}
        assert Storage.save_state(json_path, direct_data) is True

        reloaded = Storage.load_state(json_path)
        assert reloaded.get("custom_key") == "custom_value"
        assert reloaded.get("number") == 123

    finally:
        if os.path.exists(json_path):
            os.remove(json_path)
