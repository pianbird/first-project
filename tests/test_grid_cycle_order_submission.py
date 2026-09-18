import os
import sys
import tempfile
import pytest
from unittest.mock import MagicMock, patch

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from web_app.backend.engine import WebTradingEngine
from strategy.grid_evaluator import TradingSignal


@pytest.fixture
def engine_setup():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    engine = WebTradingEngine(db_path=db_path)
    engine.status = "RUNNING"
    engine.order_manager = MagicMock()
    engine.order_manager.submit_order.return_value = {"success": True, "order_id": "ORD_12345"}
    engine.kiwoom_client = MagicMock()
    engine.kiwoom_client.is_credentials_valid.return_value = True
    engine.get_kiwoom_positions = MagicMock(return_value={"positions": []})

    stock_info = {
        "name": "삼성전자",
        "status": "TRACKING",
        "clear_price": 70000,
        "hard_stop_loss_price": 40000,
        "current_step": 0,
        "stop_loss_halted": False
    }
    engine.stocks["005930"] = stock_info
    engine.market_data["005930"] = {"current_price": 50000}
    engine.positions["005930"] = {"qty": 10, "avg_price": 45000}

    yield engine

    try:
        if os.path.exists(db_path):
            os.remove(db_path)
    except Exception:
        pass


def test_clear_sell_submit_order_kwargs(engine_setup):
    engine = engine_setup
    sig = TradingSignal(
        action="CLEAR_SELL",
        stock_code="005930",
        stock_name="삼성전자",
        cur_price=50000,
        qty=10,
        message="",
        strategy_id="GRID_V1",
        cycle_id=5
    )

    with patch("strategy.grid_evaluator.GridEvaluator.evaluate", return_value=sig), \
         patch("time.sleep"):
        engine._evaluate_grid_cycle_internal()

    engine.order_manager.submit_order.assert_called_once()
    kwargs = engine.order_manager.submit_order.call_args.kwargs
    assert kwargs["order_type"] == "SELL"
    assert kwargs["stock_code"] == "005930"
    assert kwargs["stock_name"] == "삼성전자"
    assert kwargs["qty"] == 10
    assert kwargs["price"] == 50000
    assert kwargs["ord_dvsn"] == "01"
    assert kwargs["strategy_id"] == "GRID_V1"
    assert kwargs["cycle_id"] == 5  # Must equal signal.cycle_id


def test_hard_stop_loss_submit_order_kwargs(engine_setup):
    engine = engine_setup
    sig = TradingSignal(
        action="HARD_STOP_LOSS",
        stock_code="005930",
        stock_name="삼성전자",
        cur_price=50000,
        qty=10,
        message="",
        strategy_id="GRID_V1",
        cycle_id=5
    )

    with patch("strategy.grid_evaluator.GridEvaluator.evaluate", return_value=sig), \
         patch("time.sleep"):
        engine._evaluate_grid_cycle_internal()

    engine.order_manager.submit_order.assert_called_once()
    kwargs = engine.order_manager.submit_order.call_args.kwargs
    assert kwargs["order_type"] == "SELL"
    assert kwargs["stock_code"] == "005930"
    assert kwargs["stock_name"] == "삼성전자"
    assert kwargs["qty"] == 10
    assert kwargs["price"] == 50000
    assert kwargs["ord_dvsn"] == "01"
    assert kwargs["strategy_id"] == "GRID_V1"
    assert kwargs["cycle_id"] == 0  # CRITICAL: Must be fixed 0 for HARD_STOP_LOSS!
    assert engine.stocks["005930"]["stop_loss_halted"] is True


def test_grid_profit_sell_submit_order_kwargs(engine_setup):
    engine = engine_setup
    sig = TradingSignal(
        action="GRID_PROFIT_SELL",
        stock_code="005930",
        stock_name="삼성전자",
        cur_price=50000,
        qty=5,
        message="",
        strategy_id="GRID_V1",
        cycle_id=3,
        current_step=2
    )

    with patch("strategy.grid_evaluator.GridEvaluator.evaluate", return_value=sig), \
         patch("time.sleep"):
        engine._evaluate_grid_cycle_internal()

    engine.order_manager.submit_order.assert_called_once()
    kwargs = engine.order_manager.submit_order.call_args.kwargs
    assert kwargs["order_type"] == "SELL"
    assert kwargs["stock_code"] == "005930"
    assert kwargs["stock_name"] == "삼성전자"
    assert kwargs["qty"] == 5
    assert kwargs["price"] == 50000
    assert kwargs["ord_dvsn"] == "01"
    assert kwargs["strategy_id"] == "GRID_V1"
    assert kwargs["cycle_id"] == 3  # Must equal signal.cycle_id


def test_grid_buy_submit_order_kwargs(engine_setup):
    engine = engine_setup
    sig = TradingSignal(
        action="GRID_BUY",
        stock_code="005930",
        stock_name="삼성전자",
        cur_price=50000,
        qty=5,
        message="",
        strategy_id="GRID_V1",
        cycle_id=2,
        current_step=2
    )

    with patch("strategy.grid_evaluator.GridEvaluator.evaluate", return_value=sig), \
         patch("time.sleep"):
        engine._evaluate_grid_cycle_internal()

    engine.order_manager.submit_order.assert_called_once()
    kwargs = engine.order_manager.submit_order.call_args.kwargs
    assert kwargs["order_type"] == "BUY"
    assert kwargs["stock_code"] == "005930"
    assert kwargs["stock_name"] == "삼성전자"
    assert kwargs["qty"] == 5
    assert kwargs["price"] == 50000
    assert kwargs["ord_dvsn"] == "01"
    assert kwargs["strategy_id"] == "GRID_V1"
    assert kwargs["cycle_id"] == 2  # Must equal signal.cycle_id


def test_grid_buy_blocked_when_stop_loss_halted(engine_setup):
    engine = engine_setup
    engine.stocks["005930"]["stop_loss_halted"] = True
    sig = TradingSignal(
        action="GRID_BUY",
        stock_code="005930",
        stock_name="삼성전자",
        cur_price=50000,
        qty=5,
        message="",
        strategy_id="GRID_V1",
        cycle_id=2,
        current_step=2
    )

    with patch("strategy.grid_evaluator.GridEvaluator.evaluate", return_value=sig), \
         patch("time.sleep"):
        engine._evaluate_grid_cycle_internal()

    # Should NOT submit order because stop_loss_halted is True
    engine.order_manager.submit_order.assert_not_called()
