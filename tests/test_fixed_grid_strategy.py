"""
Unit tests for Gemini Antigravity Real-Time Market-Order Fixed Grid Engine:
- src/strategy/grid_generator.py (generate_grid_levels)
- src/strategy/static_grid_strategy.py (check_signals)
- src/execution/grid_executor.py (execute_grid_order)
- src/execution/position_tracker.py (sync_grid_positions)
"""
import pytest
from unittest.mock import MagicMock
from src.domain.models import GridLevel, LevelStatus, GridLevelStatus, TradeAction
from src.strategy.grid_generator import generate_grid_levels, GridGenerator
from src.strategy.static_grid_strategy import check_signals, StaticGridStrategy
from src.execution.grid_executor import execute_grid_order, GridExecutor
from src.execution.position_tracker import sync_grid_positions


def test_generate_grid_levels_specification():
    levels = generate_grid_levels(lower_price=50000.0, upper_price=60000.0, grid_count=5, quantity=10)

    assert len(levels) == 5
    # 1:1 adjacent level sell_price mapping
    assert levels[0].buy_price == 50000
    assert levels[0].sell_price == 52000
    assert levels[1].buy_price == 52000
    assert levels[1].sell_price == 54000
    assert levels[2].buy_price == 54000
    assert levels[2].sell_price == 56000
    assert levels[3].buy_price == 56000
    assert levels[3].sell_price == 58000
    assert levels[4].buy_price == 58000
    assert levels[4].sell_price == 60000

    for lvl in levels:
        assert lvl.status == LevelStatus.READY


def test_check_signals_buy_and_sell_watch():
    levels = generate_grid_levels(lower_price=50000.0, upper_price=60000.0, grid_count=5, quantity=10)

    # 1. Price is high -> No signal
    res = check_signals(65000.0, levels)
    assert res is None

    # 2. BUY Watch (current_price <= buy_price 54000)
    res_buy = check_signals(53900.0, levels)
    assert res_buy is not None
    level_buy, action_buy = res_buy
    assert action_buy == "BUY"
    assert level_buy.buy_price == 54000
    assert level_buy.status == LevelStatus.BUY_ORDERING

    # 3. Simulate position hold for level 3 (buy 54000, sell 56000)
    level_buy.status = LevelStatus.POSITION_HOLD

    # 4. SELL Watch (current_price >= sell_price 56000)
    res_sell = check_signals(56100.0, levels)
    assert res_sell is not None
    level_sell, action_sell = res_sell
    assert action_sell == "SELL"
    assert level_sell.sell_price == 56000
    assert level_sell.status == LevelStatus.SELL_ORDERING


def test_execute_grid_order_success_and_failure_rollback():
    levels = generate_grid_levels(lower_price=50000.0, upper_price=60000.0, grid_count=5, quantity=10)
    lvl = levels[0]

    # Mock client success
    mock_client = MagicMock()
    mock_client.send_order.return_value = {"success": True, "order_id": "ORD001"}

    # BUY Order Success
    lvl.status = LevelStatus.BUY_ORDERING
    success = execute_grid_order(mock_client, "005930", lvl, "BUY")
    assert success is True
    assert lvl.status == LevelStatus.BUY_PENDING
    assert lvl.order_id == "ORD001"

    # BUY Order Failure Rollback -> READY
    mock_client.send_order.return_value = {"success": False}
    lvl.status = LevelStatus.BUY_ORDERING
    fail_buy = execute_grid_order(mock_client, "005930", lvl, "BUY")
    assert fail_buy is False
    assert lvl.status == LevelStatus.READY

    # SELL Order Success
    mock_client.send_order.return_value = {"success": True, "order_id": "ORD002"}
    lvl.status = LevelStatus.SELL_ORDERING
    success_sell = execute_grid_order(mock_client, "005930", lvl, "SELL")
    assert success_sell is True
    assert lvl.status == LevelStatus.SELL_PENDING

    # SELL Order Failure Rollback -> POSITION_HOLD
    mock_client.send_order.return_value = {"success": False}
    lvl.status = LevelStatus.SELL_ORDERING
    fail_sell = execute_grid_order(mock_client, "005930", lvl, "SELL")
    assert fail_sell is False
    assert lvl.status == LevelStatus.POSITION_HOLD


def test_sync_grid_positions_balance_updates():
    levels = generate_grid_levels(lower_price=50000.0, upper_price=60000.0, grid_count=5, quantity=10)

    # 1. BUY_PENDING transition to POSITION_HOLD on balance increase
    levels[0].status = LevelStatus.BUY_PENDING
    synced = sync_grid_positions(levels, current_holding_qty=10)
    assert synced[0].status == LevelStatus.POSITION_HOLD

    # 2. SELL_PENDING transition to READY on balance decrease
    synced[0].status = LevelStatus.SELL_PENDING
    synced_after_sell = sync_grid_positions(levels, current_holding_qty=0)
    assert synced_after_sell[0].status == LevelStatus.READY
