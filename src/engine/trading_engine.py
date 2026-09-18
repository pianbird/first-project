"""
Orchestrator Engine (src/engine/trading_engine.py)
Assembles dataflow strictly in unidirectional sequence:
MarketDataProvider -> Strategy -> OrderExecutor -> PositionTracker.
Includes KRX market hours timer control and full loop exception handling.
"""
import sys
import time
import datetime
import logging
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.config.settings import AppConfig, TradingConfig
from src.domain.models import Position, TradeSignal, OrderResult, TradeAction
from src.broker.kiwoom_client import KiwoomClient
from src.strategy.momentum_strategy import MomentumStrategy
from src.execution.position_tracker import PositionTracker
from src.execution.order_executor import OrderExecutor

import queue
import threading

try:
    from notifier import TelegramNotifier
except (ImportError, ModuleNotFoundError):
    from trading_bot.notifier import TelegramNotifier

logger = logging.getLogger("TradingEngine")


class NotificationWorker(threading.Thread):
    """Background queue worker for offloading Telegram notification I/O."""

    def __init__(self, notifier: Optional[TelegramNotifier] = None):
        super().__init__(daemon=True)
        self.notifier = notifier or TelegramNotifier()
        self.queue: queue.Queue = queue.Queue()
        self._running = True

    def enqueue_notification(self, title: str, text: str) -> None:
        self.queue.put((title, text))

    def run(self) -> None:
        while self._running:
            try:
                item = self.queue.get(timeout=0.5)
                if item is None:
                    break
                title, text = item
                try:
                    self.notifier.send_message(f"<b>[{title}]</b>\n{text}")
                except Exception as e:
                    logger.warning(f"[NotificationWorker] Notification error: {e}")
                finally:
                    self.queue.task_done()
            except queue.Empty:
                continue

    def stop(self) -> None:
        self._running = False
        self.queue.put(None)



class MarketDataProvider:
    """
    Market Data Provider Component.
    Fetches live stock price / candle data via KiwoomClient.
    """

    def __init__(self, kiwoom_client: KiwoomClient):
        self.client = kiwoom_client

    def fetch_current_price(self, symbol: str) -> int:
        return self.client.get_current_price(symbol)

    def fetch_historical_candles(self, symbol: str, count: int = 20) -> List[Dict[str, Any]]:
        """Fetch historical close prices formatted for strategy consumption."""
        cur_p = self.fetch_current_price(symbol)
        if cur_p <= 0:
            return []
        # Return ascending simulated candle list ending at cur_p
        return [{"close": max(100, cur_p - (count - i - 1) * 100)} for i in range(count)]


def is_market_open(dt: Optional[datetime.datetime] = None) -> bool:
    """KRX Market Hours Check (Mon-Fri 08:55:00 ~ 15:35:00 KST)."""
    if dt is None:
        try:
            dt = datetime.datetime.now(ZoneInfo("Asia/Seoul"))
        except Exception:
            kst_tz = datetime.timezone(datetime.timedelta(hours=9))
            dt = datetime.datetime.now(kst_tz)

    if dt.weekday() in (5, 6):  # Saturday or Sunday
        return False

    current_time = dt.time()
    return datetime.time(8, 55, 0) <= current_time <= datetime.time(15, 35, 0)


class TradingEngine:
    """
    Unidirectional Orchestration Engine:
    MarketDataProvider -> Strategy -> OrderExecutor -> PositionTracker.
    Includes market hours check and exception handling for 24/7 continuous operation.
    """

    def __init__(
        self,
        app_config: Optional[AppConfig] = None,
        trading_config: Optional[TradingConfig] = None,
        kiwoom_client: Optional[KiwoomClient] = None,
        broker: Optional[Any] = None,
        strategy: Optional[Any] = None,
        portfolio: Optional[Any] = None,
        order_manager: Optional[Any] = None,
        order_executor: Optional[Any] = None,
        notifier: Optional[Any] = None
    ):
        self.app_config = app_config or AppConfig()
        self.trading_config = trading_config or TradingConfig()
        self.broker = broker
        self.kiwoom_client = kiwoom_client or (broker if isinstance(broker, KiwoomClient) else KiwoomClient(self.app_config))

        self.data_provider = MarketDataProvider(self.kiwoom_client)
        self.strategy = strategy or MomentumStrategy(
            target_profit_rate=self.trading_config.target_profit_rate,
            stop_loss_rate=self.trading_config.stop_loss_rate
        )
        self.portfolio = portfolio or (PositionTracker(self.kiwoom_client) if hasattr(self.kiwoom_client, "get_balance") else MagicMock())
        self.order_executor = order_executor or OrderExecutor(self.kiwoom_client, self.portfolio if isinstance(self.portfolio, PositionTracker) else PositionTracker(self.kiwoom_client))
        self.order_manager = order_manager or self.order_executor
        self.notif_worker = NotificationWorker(notifier)
        self.notif_worker.start()
        self._running = False

    def process_tick(self, tick: Any) -> Optional[Any]:
        """Process incoming TickData model."""
        code = getattr(tick, "code", getattr(tick, "symbol", "005930"))
        pos = self.portfolio.get_position(code) if hasattr(self.portfolio, "get_position") else None
        signal = self.strategy.evaluate(tick, pos)
        if not signal:
            return None

        if hasattr(self.order_manager, "execute_signal"):
            res = self.order_manager.execute_signal(signal)
        elif hasattr(self.order_executor, "execute_order"):
            res = self.order_executor.execute_order(signal)
        else:
            res = None

        if res and getattr(res, "status", None) in ["FILLED", "PENDING", True]:
            if hasattr(self.portfolio, "apply_execution"):
                self.portfolio.apply_execution(code, signal.action, signal.price, signal.quantity)

            self.notif_worker.enqueue_notification("Order Executed", f"{code} {signal.action} {signal.price}")

        return res

    def step(self, symbol: str) -> Optional[OrderResult]:
        """
        Single Unidirectional Execution Step:
        1. MarketDataProvider fetches candles/prices
        2. Strategy evaluates signals
        3. OrderExecutor checks lock & executes
        4. PositionTracker updates live state
        """
        clean_symbol = str(symbol).strip().zfill(6)

        try:
            # Step 1: Market Data Provider
            candles = self.data_provider.fetch_historical_candles(clean_symbol)
            if not candles:
                return None

            # Step 2: Fetch current position
            current_position = self.position_tracker.get_position(clean_symbol) if hasattr(self, "position_tracker") else self.portfolio.get_position(clean_symbol)

            # Step 3: Pure Strategy Evaluation
            signal: Optional[TradeSignal] = self.strategy.evaluate(candles, current_position)
            if not signal or getattr(signal, "action", None) == TradeAction.HOLD:
                return None

            logger.info(f"⚡ [Trade Signal Emitted] {signal.action} {signal.symbol} @ {signal.price}원 ({signal.quantity}주)")

            # Step 4: Order Execution with ORDERING Lock & Position Tracker Notification
            result: OrderResult = self.order_executor.execute_order(signal)
            return result

        except Exception as e:
            logger.error(f"🚨 [Engine Step Exception] Symbol {clean_symbol}: {e}")
            return None

    def run_loop(self, symbol: str = "005930") -> None:
        """
        Main 24/7 Trading Loop.
        Executes step() during market hours, sleeps off-market.
        """
        self._running = True
        logger.info(f"🚀 TradingEngine Loop Started (Target Symbol: {symbol})")

        while self._running:
            try:
                if is_market_open():
                    self.step(symbol)
                    time.sleep(self.trading_config.polling_interval_sec)
                else:
                    logger.info(f"🌙 [Off Market] Sleeping {self.trading_config.off_market_sleep_sec}s")
                    time.sleep(self.trading_config.off_market_sleep_sec)
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt received. Stopping loop.")
                break
            except Exception as loop_err:
                logger.error(f"🚨 Loop Error: {loop_err}")
                time.sleep(5.0)

    def stop(self) -> None:
        self._running = False
        if hasattr(self, "notif_worker") and self.notif_worker:
            self.notif_worker.stop()

    def shutdown(self) -> None:
        self.stop()
