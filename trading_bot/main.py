"""
MagicTrader - Always-on Headless Daemon Main Entry Point (main.py)
Production-Ready 24/7 Cloud Daemon Engine
"""
import os
import sys
import time
import signal
import datetime
import logging
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo
from typing import Optional

# Ensure package path registration
base_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(base_dir)
for p in [parent_dir, base_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from config import Config, get_kst_now, get_kst_now_str
from storage import SQLiteStorage
from kiwoom_client import KiwoomRESTClient
from notifier import TelegramNotifier
from grid_engine import GridEngine

try:
    from legacy.main import AutoTradingEngine
except Exception:
    AutoTradingEngine = GridEngine

try:
    from trading_mode import (
        acquire_real_trading_lock, release_real_trading_lock, RealTradingLockError
    )
except (ImportError, ModuleNotFoundError):
    from trading_bot.trading_mode import (
        acquire_real_trading_lock, release_real_trading_lock, RealTradingLockError
    )


def setup_logger(log_file: str = "trading_bot.log") -> logging.Logger:
    """
    Setup logging module with RotatingFileHandler (5MB maxBytes, 5 backup files) and StreamHandler.
    """
    logger = logging.getLogger("MagicTrader")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        log_path = os.path.join(parent_dir, log_file) if os.path.isabs(log_file) else log_file
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

        # RotatingFileHandler: 5MB (5 * 1024 * 1024 bytes) limit, 5 backup files
        file_handler = RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)

        # StreamHandler for stdout console log
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(logging.INFO)
        logger.addHandler(console_handler)

    return logger


logger = setup_logger()


def is_market_open(dt: Optional[datetime.datetime] = None) -> bool:
    """
    Determine if current time is within KRX regular market hours (09:00:00 ~ 15:30:00 KST, Mon-Fri).
    Allows 5-minute pre/post buffer (08:55:00 ~ 15:35:00) for grid sync.
    """
    if dt is None:
        dt = get_kst_now()
    elif dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Seoul"))
        except Exception:
            kst_tz = datetime.timezone(datetime.timedelta(hours=9))
            dt = dt.replace(tzinfo=kst_tz)

    # Weekend check (5: Saturday, 6: Sunday)
    if dt.weekday() in (5, 6):
        return False

    current_time = dt.time()
    start_time = datetime.time(8, 55, 0)
    end_time = datetime.time(15, 35, 0)

    return start_time <= current_time <= end_time


# Alias for backward compatibility
is_market_hours = is_market_open


class GracefulKiller:
    """Signal handler for graceful daemon shutdown on Linux / PythonAnywhere"""
    kill_now = False

    def __init__(self):
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        logger.info("[Daemon Shutdown Signal Received] Gracefully shutting down...")
        self.kill_now = True


def reconcile_with_broker(engine: GridEngine) -> dict:
    """
    시작 시 및 매일 장 시작 전(08:50 KST) 실제 키움 원장 잔고 및 미체결 주문을 조회하여
    로컬 DB 및 레벨 상태를 보정하는 루틴
    """
    now_str = get_kst_now_str()
    logger.info("🔄 [reconcile_with_broker] 키움 증권 원장 상태 조회 및 로컬 DB 보정 실행...")
    return engine.reconcile_with_account()


def run_daemon():
    """Main Always-on Task Daemon Loop"""
    now_str = get_kst_now_str()
    logger.info("=" * 70)
    logger.info(f" 🚀 [MagicTrader Always-on Headless Daemon Engine Started] ({now_str})")
    logger.info("=" * 70)

    config = Config
    if not config.validate():
        logger.error("🚨 Configuration validation failed. Halting daemon.")
        sys.exit(1)

    real_lock_acquired = False
    if not config.IS_MOCK:
        try:
            acquire_real_trading_lock(config.ACCOUNT_NO)
            real_lock_acquired = True
        except RealTradingLockError as e:
            logger.error(f"🚨 [RealTradingLock] 이중 기동 차단: {e}")
            sys.exit(1)

    try:
        storage = SQLiteStorage(config.DB_PATH)
        kiwoom = KiwoomRESTClient(config)
        notifier = TelegramNotifier(config)
        engine = GridEngine(config, storage, kiwoom, notifier)

        killer = GracefulKiller()

        logger.info(f"[*] Trading Mode  : {'Mock Trading (모의)' if config.IS_MOCK else 'Real Trading (실전)'}")
        logger.info(f"[*] Account No    : {kiwoom.normalize_account_no(config.ACCOUNT_NO)}")
        logger.info(f"[*] Target Stock  : {config.TARGET_STOCK_NAME} ({config.TARGET_STOCK_CODE})")
        logger.info(f"[*] Grid Range    : {config.GRID_MIN_PRICE:,} 원 ~ {config.GRID_MAX_PRICE:,} 원 ({config.GRID_LEVELS} 차수)")
        logger.info(f"[*] DB Path       : {config.DB_PATH}")
        logger.info("-" * 70)

        notifier.send_message(
            f"🚀 <b>[MagicTrader Daemon Engine 기동]</b>\n"
            f"⏱ 시각: {now_str}\n"
            f"📌 모드: {'모의투자' if config.IS_MOCK else '실전투자'}\n"
            f"🎯 종목: {config.TARGET_STOCK_NAME} ({config.TARGET_STOCK_CODE})"
        )

        try:
            recon_result = reconcile_with_broker(engine)
            logger.info(f"🔍 [Startup Recovery] 대사 결과: {recon_result.get('summary')}")
        except Exception as e:
            err_msg = f"프로세스 시작 대사 중 예외 발생: {e}"
            logger.error(f"🚨 [Startup Error] {err_msg}")
            notifier.notify_error("Startup Reconciliation Error", err_msg)

        cycle_counter = 0
        last_daily_reconcile_date = ""

        while not killer.kill_now:
            try:
                kst_dt = get_kst_now()
                cur_time_str = get_kst_now_str()
                today_str = kst_dt.strftime("%Y-%m-%d")

                # 매일 장 시작 전(08:50 KST ~ 08:55 KST) 키움 원장 상태 자동 대사
                if kst_dt.hour == 8 and 50 <= kst_dt.minute < 55 and last_daily_reconcile_date != today_str:
                    logger.info("⏰ [08:50 Pre-Market Reconciliation] 장 시작 전 키움 원장 동기화 수행...")
                    try:
                        reconcile_with_broker(engine)
                    except Exception as re_err:
                        logger.error(f"🚨 [Pre-Market Reconciliation Error] {re_err}")
                        notifier.notify_error("Pre-Market Reconciliation Error", str(re_err))
                    last_daily_reconcile_date = today_str

                in_market = is_market_open(kst_dt)

                if in_market:
                    # 0. UNKNOWN 및 로컬 미체결 주문 상태 복구/폴링
                    try:
                        engine.order_manager.resolve_unknown_orders(kiwoom)
                        engine.order_manager.poll_open_order_status(kiwoom)
                    except Exception as poll_err:
                        logger.warning(f"⚠️ [Order Status Poll Error] {poll_err}")

                    # 1. 실시간 시세 조회 및 그리드 평가
                    cur_price = 0
                    if kiwoom.is_credentials_valid():
                        try:
                            cur_price = kiwoom.get_current_price(config.TARGET_STOCK_CODE)
                        except Exception as pe:
                            logger.warning(f"시세 조회 예외 발생: {pe}")

                    if cur_price <= 0:
                        logger.warning("⚠️ 시세 조회 실패 - 이번 사이클 그리드 평가를 건너뜁니다.")
                        time.sleep(config.CYCLE_INTERVAL_SEC)
                        continue

                    engine.evaluate_grid_orders(current_price=cur_price)

                    # 60 사이클(약 2.5분)마다 계좌 잔고 정합성 주기적 재검증
                    cycle_counter += 1
                    if cycle_counter % 60 == 0:
                        reconcile_with_broker(engine)

                    logger.info(f"⚡ [Market Active] 종목({config.TARGET_STOCK_CODE}) 현재가: {cur_price:,}원 | 그리드 평가 완료")
                    time.sleep(config.CYCLE_INTERVAL_SEC)

                else:
                    logger.info(f"🌙 [Off Market] 장외/휴장 시간 - CPU Quota 절약을 위해 {config.OFF_MARKET_SLEEP_SEC}초 슬립")
                    time.sleep(config.OFF_MARKET_SLEEP_SEC)

            except KeyboardInterrupt:
                logger.info("사용자에 의해 데몬이 종료되었습니다.")
                break
            except Exception as err:
                err_msg = f"Daemon Loop 예외 발생: {err}"
                logger.error(f"🚨 [Loop Error] {err_msg}")
                notifier.notify_error("Daemon Loop Exception", err_msg)
                time.sleep(5.0)

        notifier.send_message(f"⏹ <b>[MagicTrader Daemon Engine 안전 종료]</b>\n⏱ 시각: {get_kst_now_str()}")
        logger.info("👋 MagicTrader Always-on Daemon이 안전하게 종료되었습니다.")
    finally:
        if real_lock_acquired:
            release_real_trading_lock(config.ACCOUNT_NO)


if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    run_daemon()
