"""
Unit test suite verifying src/ architecture refactoring tasks 1 through 5.
"""
import time
import pytest
from unittest.mock import MagicMock

from src.core.models import TickData, OrderSignal, Position, OrderResult
from src.broker.kiwoom_broker import KiwoomBrokerClient
from src.strategy.momentum_strategy import MomentumStrategy
from src.strategy.grid_strategy import GridStrategy
from src.portfolio.portfolio_manager import PortfolioManager
from src.execution.order_manager import OrderManager
from src.engine.trading_engine import TradingEngine, NotificationWorker


def test_task1_domain_entities():
    tick = TickData(code="005930", price=70000, volume=100, timestamp="2026-09-10 10:00:00")
    assert tick.code == "005930"
    assert tick.price == 70000

    signal = OrderSignal(code="005930", action="BUY", order_type="LIMIT", price=70000, quantity=10)
    assert signal.action == "BUY"

    pos = Position(code="005930", quantity=10, avg_price=69000)
    pos.update_price(70000)
    assert pos.unrealized_pnl == 10000

    res = OrderResult(order_id="ORD123", code="005930", status="FILLED", msg="Success")
    assert res.status == "FILLED"


def test_task2_broker_client():
    broker = KiwoomBrokerClient()
    assert hasattr(broker, "fetch_balance")
    assert hasattr(broker, "fetch_price")
    assert hasattr(broker, "place_order")


def test_task3_pure_strategy_engine():
    strategy = MomentumStrategy(window_size=3, threshold_pct=0.01, order_quantity=5)

    t1 = TickData(code="005930", price=10000, volume=10, timestamp="10:00:00")
    t2 = TickData(code="005930", price=10050, volume=10, timestamp="10:00:01")
    t3 = TickData(code="005930", price=10200, volume=10, timestamp="10:00:02")  # 2% rise

    sig1 = strategy.evaluate(t1, None)
    sig2 = strategy.evaluate(t2, None)
    sig3 = strategy.evaluate(t3, None)

    assert sig1 is None
    assert sig2 is None
    assert sig3 is not None
    assert sig3.action == "BUY"
    assert sig3.quantity == 5


def test_task4_portfolio_and_order_manager():
    pm = PortfolioManager(initial_cash=1000000)
    assert pm.cash == 1000000

    pm.apply_execution("005930", "BUY", 50000, 10)
    assert pm.cash == 500000
    pos = pm.get_position("005930")
    assert pos is not None
    assert pos.quantity == 10
    assert pos.avg_price == 50000

    mock_broker = MagicMock(spec=KiwoomBrokerClient)
    mock_broker.place_order.return_value = OrderResult(order_id="ORD1", code="005930", status="FILLED")

    om = OrderManager(mock_broker)
    sig = OrderSignal(code="005930", action="BUY", order_type="LIMIT", price=50000, quantity=10)

    res = om.execute_signal(sig)
    assert res is not None
    assert res.status == "FILLED"
    assert not om.is_symbol_pending("005930")


def test_task5_trading_engine_orchestration():
    mock_broker = MagicMock(spec=KiwoomBrokerClient)
    mock_broker.place_order.return_value = OrderResult(order_id="ORD999", code="005930", status="FILLED")

    strategy = MomentumStrategy(window_size=2, threshold_pct=0.01, order_quantity=10)
    pm = PortfolioManager(initial_cash=10000000)
    om = OrderManager(mock_broker)
    mock_notifier = MagicMock()

    engine = TradingEngine(
        broker=mock_broker,
        strategy=strategy,
        portfolio=pm,
        order_manager=om,
        notifier=mock_notifier
    )

    t1 = TickData(code="005930", price=50000, volume=100, timestamp="10:00:00")
    t2 = TickData(code="005930", price=52000, volume=100, timestamp="10:00:01")  # +4%

    res1 = engine.process_tick(t1)
    assert res1 is None

    res2 = engine.process_tick(t2)
    assert res2 is not None
    assert res2.order_id == "ORD999"

    time.sleep(0.2)
    engine.shutdown()
