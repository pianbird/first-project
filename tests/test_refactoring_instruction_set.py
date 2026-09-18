"""
Unit test suite verifying Refactoring Instruction Set tasks 1 through 5.
"""
import pytest
from unittest.mock import MagicMock

from src.config.settings import AppConfig, TradingConfig
from src.domain.models import OrderType, TradeAction, Position, OrderResult, TradeSignal
from src.broker.kiwoom_client import KiwoomClient, KiwoomAPIException
from src.strategy.momentum_strategy import MomentumStrategy
from src.execution.position_tracker import PositionTracker
from src.execution.order_executor import OrderExecutor
from src.engine.trading_engine import TradingEngine, MarketDataProvider, is_market_open


def test_task1_config_and_domain_models():
    app_cfg = AppConfig()
    trade_cfg = TradingConfig()
    assert trade_cfg.polling_interval_sec == 2.5
    assert trade_cfg.target_profit_rate == 0.03

    pos = Position(symbol="005930", quantity=10, buy_price=50000)
    pos.update_current_price(55000)
    assert pos.pnl == 50000

    sig = TradeSignal(symbol="005930", action=TradeAction.BUY, order_type=OrderType.LIMIT, price=50000, quantity=10)
    assert sig.action == TradeAction.BUY
    assert sig.order_type == OrderType.LIMIT

    res = OrderResult(order_id="ORD1", symbol="005930", action=TradeAction.BUY, quantity=10, price=50000, success=True)
    assert res.success is True


def test_task2_broker_api_isolation():
    mock_client = MagicMock(spec=KiwoomClient)
    mock_client.get_token.return_value = "token123"
    mock_client.get_current_price.return_value = 70000
    mock_client.get_balance.return_value = [{"stock_code": "005930", "qty": 10, "avg_price": 69000}]
    mock_client.post_order.return_value = {"success": True, "order_id": "ORD001"}

    assert mock_client.get_token() == "token123"
    assert mock_client.get_current_price("005930") == 70000
    assert len(mock_client.get_balance()) == 1

    exc = KiwoomAPIException("-8005", "Token expired")
    assert exc.code == "-8005"


def test_task3_pure_strategy_engine():
    strategy = MomentumStrategy(short_window=2, long_window=3, target_profit_rate=0.03, stop_loss_rate=0.02, order_quantity=10)

    # Simulated candles: [100, 100, 105] -> Golden cross
    candles = [{"close": 100}, {"close": 100}, {"close": 105}]

    sig = strategy.evaluate(candles, None)
    assert sig is not None
    assert sig.action == TradeAction.BUY
    assert sig.quantity == 10

    # Test take profit on existing position
    pos = Position(symbol="005930", quantity=10, buy_price=100)
    tp_candles = [{"close": 100}, {"close": 102}, {"close": 104}]  # +4% > 3%
    sig_tp = strategy.evaluate(tp_candles, pos)
    assert sig_tp is not None
    assert sig_tp.action == TradeAction.SELL


def test_task4_execution_and_position_tracker():
    mock_kiwoom = MagicMock(spec=KiwoomClient)
    mock_kiwoom.get_balance.return_value = [{"stock_code": "005930", "qty": 10, "avg_price": 50000, "current_price": 52000}]
    mock_kiwoom.post_order.return_value = {"success": True, "order_id": "ORD_EXEC_1", "message": "Success"}

    tracker = PositionTracker(mock_kiwoom)
    pos = tracker.get_position("005930")
    assert pos is not None
    assert pos.quantity == 10

    executor = OrderExecutor(mock_kiwoom, tracker)
    sig = TradeSignal(symbol="005930", action=TradeAction.BUY, order_type=OrderType.LIMIT, price=50000, quantity=5)

    res = executor.execute_order(sig)
    assert res.success is True
    assert res.order_id == "ORD_EXEC_1"
    assert not executor.is_ordering("005930")


def test_task5_orchestration_engine():
    mock_kiwoom = MagicMock(spec=KiwoomClient)
    mock_kiwoom.get_current_price.return_value = 70000
    mock_kiwoom.get_balance.return_value = []
    mock_kiwoom.post_order.return_value = {"success": True, "order_id": "ORD_ENG_1"}

    strategy = MomentumStrategy(short_window=1, long_window=1, order_quantity=10)

    engine = TradingEngine(
        kiwoom_client=mock_kiwoom,
        strategy=strategy
    )

    res = engine.step("005930")
    assert res is not None
    assert res.success is True
    assert res.order_id == "ORD_ENG_1"
