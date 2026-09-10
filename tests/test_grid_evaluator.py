"""
Unit tests for GridEvaluator module.
Verifies signal calculations for clear price, hard stop loss, grid profit sell, grid buy, and safe stop.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategy.grid_evaluator import GridEvaluator

def test_grid_evaluator_safe_stop_drop():
    stock = {"name": "삼성전자", "clear_price": 80000}
    mdata = {"current_price": 68000, "prev_close": 85000, "change_rate": -20.0}
    signal = GridEvaluator.evaluate("005930", stock, mdata, actual_qty=10)
    assert signal.action == "SAFE_STOP_DROP"

def test_grid_evaluator_clear_sell():
    stock = {"name": "삼성전자", "clear_price": 80000, "current_step": 3}
    mdata = {"current_price": 80500, "prev_close": 78000}
    signal = GridEvaluator.evaluate("005930", stock, mdata, actual_qty=10)
    assert signal.action == "CLEAR_SELL"
    assert signal.qty == 10
    assert signal.order_type == "SELL"
    assert signal.strategy_id == "GRID_CLEAR"

def test_grid_evaluator_hard_stop_loss():
    stock = {
        "name": "삼성전자",
        "clear_price": 80000,
        "hard_stop_loss_enabled": True,
        "hard_stop_loss_price": 60000,
        "stop_loss_halted": False
    }
    mdata = {"current_price": 59000, "prev_close": 62000}
    signal = GridEvaluator.evaluate("005930", stock, mdata, actual_qty=15)
    assert signal.action == "HARD_STOP_LOSS"
    assert signal.qty == 15
    assert signal.strategy_id == "HARD_STOP_LOSS"

def test_grid_evaluator_buy_signal():
    steps = [
        {"step": 1, "price": 70000, "target_total_qty": 10},
        {"step": 2, "price": 65000, "target_total_qty": 25},
    ]
    stock = {"name": "삼성전자", "clear_price": 80000, "grid": {"steps": steps, "start_step": 1}}
    mdata = {"current_price": 64000, "prev_close": 68000}
    signal = GridEvaluator.evaluate("005930", stock, mdata, actual_qty=10)
    assert signal.action == "GRID_BUY"
    assert signal.qty == 15
    assert signal.order_type == "BUY"
    assert signal.current_step == 2

def test_grid_evaluator_profit_sell_signal():
    steps = [
        {"step": 1, "price": 70000, "target_total_qty": 10},
        {"step": 2, "price": 65000, "target_total_qty": 25},
    ]
    stock = {"name": "삼성전자", "clear_price": 80000, "grid": {"steps": steps, "start_step": 1}}
    mdata = {"current_price": 69000, "prev_close": 65000}
    signal = GridEvaluator.evaluate("005930", stock, mdata, actual_qty=25)
    assert signal.action == "GRID_PROFIT_SELL"
    assert signal.qty == 15
    assert signal.order_type == "SELL"
    assert signal.current_step == 1

def test_missing_clear_price_returns_invalid_config():
    stock = {"name": "X", "grid": {"steps": [], "start_step": 1}}
    mdata = {"current_price": 5000, "prev_close": 5000}
    signal = GridEvaluator.evaluate("000001", stock, mdata, actual_qty=10)
    assert signal.action == "INVALID_CONFIG"

def test_zero_clear_price_returns_invalid_config():
    stock = {"name": "X", "clear_price": 0, "grid": {"steps": [], "start_step": 1}}
    mdata = {"current_price": 5000, "prev_close": 5000}
    signal = GridEvaluator.evaluate("000001", stock, mdata, actual_qty=10)
    assert signal.action == "INVALID_CONFIG"

def test_normal_clear_price_still_sells():
    stock = {"name": "X", "clear_price": 80000, "grid": {"steps": [], "start_step": 1}}
    mdata = {"current_price": 80500, "prev_close": 78000}
    signal = GridEvaluator.evaluate("000001", stock, mdata, actual_qty=10)
    assert signal.action == "CLEAR_SELL"

