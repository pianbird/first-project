"""
PythonAnywhere Always-on Task 전용 독립 자동매매 엔진 데몬 (trading_bot_runner.py)
- WSGI Web 대시보드(web_app/backend/main.py)와 완전 분리되어 24시간 독립 상주 구동
- shared SQLite DB(web_magictrader.db)를 통해 Web 대시보드의 제어 상태(RUNNING/STOPPED) 및 종목 그리드 설정을 실시간 동기화
- RealTradingLock: 동일 계좌 REAL 모드 이중 프로세스 기동 방지 파일 락 연동
- MarketCalendar: is_market_open_now() 표준 함수 기반 주말/공휴일 및 정규장 시간(KST 08:55~15:35) 판정
- TelegramNotifier: SAFE_STOP 발동 및 런타임 비상 예외 시 KST 타임스탬프 비상 푸시 알림 (예외 방어 적용)
- 장 운영 시간 외에는 슬립 주기를 15초로 조절하여 일일 CPU Quota 절약
"""
import os
import sys
import time
import json
import datetime
import argparse
from typing import Dict, Any, Optional

# 실행 디렉터리 및 web_app/backend / trading_bot 경로 등록
base_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(base_dir, "web_app", "backend")
bot_dir = os.path.join(base_dir, "trading_bot")

for p in [backend_dir, bot_dir, base_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from db import WebDBManager
    from engine import WebTradingEngine
    from reconciliation import AccountReconciler, ReconciliationError
    from market_calendar import MarketCalendar, is_market_open_now
    from telegram_notifier import TelegramNotifier
    from trading_mode import (
        TradingModeManager,
        acquire_real_trading_lock,
        release_real_trading_lock,
        RealTradingLockError
    )
except (ImportError, ModuleNotFoundError):
    from web_app.backend.db import WebDBManager
    from web_app.backend.engine import WebTradingEngine
    from trading_bot.reconciliation import AccountReconciler, ReconciliationError
    from trading_bot.market_calendar import MarketCalendar, is_market_open_now
    from trading_bot.telegram_notifier import TelegramNotifier
    from trading_bot.trading_mode import (
        TradingModeManager,
        acquire_real_trading_lock,
        release_real_trading_lock,
        RealTradingLockError
    )


def get_kst_now() -> datetime.datetime:
    """KST(Asia/Seoul, UTC+9) 현재 시각 반환 (PythonAnywhere UTC 서버 시차 보정)"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Seoul"))
    except Exception:
        kst_tz = datetime.timezone(datetime.timedelta(hours=9))
        return datetime.datetime.now(kst_tz)


def get_kst_now_str() -> str:
    """KST 현재 시각 문자열 반환 (YYYY-MM-DD HH:MM:SS KST)"""
    return f"{get_kst_now().strftime('%Y-%m-%d %H:%M:%S')} KST"


def is_market_hours(dt: Optional[datetime.datetime] = None) -> bool:
    """MarketCalendar 표준 함수 연동 장운영시간 판단 래퍼 (하위 호환성)"""
    return is_market_open_now(dt, allow_buffer=True)


def load_telegram_notifier() -> TelegramNotifier:
    """trading_bot/config.json 또는 환경변수를 통한 TelegramNotifier 객체 생성을 담당"""
    cfg = {}
    config_path = os.path.join(bot_dir, "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass

    tg_cfg = cfg.get("telegram", {})
    bot_token = tg_cfg.get("bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = tg_cfg.get("chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")
    enabled = tg_cfg.get("enabled", True) if (bot_token and chat_id) else False

    notifier_cfg = {
        "telegram": {
            "enabled": enabled,
            "bot_token": bot_token,
            "chat_id": chat_id
        }
    }
    return TelegramNotifier(notifier_cfg)


def send_emergency_alert(notifier: TelegramNotifier, title: str, reason: str):
    """비상 상황 발생 시 KST 타임스탬프를 포함하여 Telegram 푸시 전송 (전송 실패가 데몬 크래시를 유발하지 않도록 안심 예외 방어)"""
    now_str = get_kst_now_str()
    msg = f"<b>[{title}]</b>\n🕒 <b>시각:</b> {now_str}\n🚨 <b>사유:</b> {reason}"
    try:
        if notifier:
            notifier.send_message(msg)
    except Exception as e:
        print(f"[{now_str}] ⚠️ [Telegram Alert Exception Handled] 알림 전송 예외 발생(무시하고 데몬 계속): {e}")


def interruptible_sleep(db: WebDBManager, duration_sec: float, check_interval: float = 0.5):
    """긴 대기 중에도 DB의 engine_status 변경(RUNNING)을 0.5초 단위로 즉시 감지하여 빠른 반응성 제공"""
    elapsed = 0.0
    while elapsed < duration_sec:
        try:
            status_cfg = db.get_system_config("engine_status")
            if status_cfg and status_cfg.get("status") == "RUNNING":
                break
        except Exception:
            pass
        time.sleep(check_interval)
        elapsed += check_interval


def perform_startup_recovery(engine: WebTradingEngine, db: WebDBManager, notifier: TelegramNotifier):
    """
    데몬 프로세스 시작/재시작 시 포지션 및 주문 상태 복구
    - AccountReconciler를 통해 키움 서버 잔고 및 미체결 주문을 조회하여 DB 상태와 비교 동기화
    - 불일치나 이상 발생 시 SAFE_STOP으로 보호 및 Telegram 비상 알림 전송
    """
    now_str = get_kst_now_str()
    print(f"[{now_str}] 🔍 [Startup Recovery] 프로세스 재시작 잔고 및 미체결 주문 상태 복구 점검 시작...")
    if not engine.kiwoom_client or not engine.kiwoom_client.is_credentials_valid():
        print(f"[{now_str}] ℹ️ [Startup Recovery] 키움 인증키 미설정/가상 모드 상태입니다.")
        return

    try:
        # 1. AccountReconciler를 활용한 계좌 동기화 및 수량 대조 (Source of Truth)
        reconciler = AccountReconciler(db)
        is_ok, recon_info = reconciler.reconcile_account(engine.kiwoom_client, trading_mode=engine.trading_mode)

        if not is_ok:
            status_msg = recon_info.get("status", "RECONCILIATION_REQUIRED")
            mismatches = recon_info.get("mismatches", [])
            if mismatches:
                err_details = ", ".join([f"종목({m['stock_code']}): DB={m['db_qty']}주 vs 계좌={m['kiwoom_qty']}주" for m in mismatches])
                stop_msg = f"RECONCILIATION_REQUIRED: DB 포지션과 Kiwoom 계좌 수량 불일치 감지 ({err_details})"
                print(f"[{now_str}] 🚨 [Startup Recovery Mismatch SAFE_STOP] {stop_msg}")
                engine.trigger_safe_stop(stop_msg)
                send_emergency_alert(notifier, "Startup Recovery SAFE_STOP", stop_msg)
                return
            elif status_msg in ["QUERY_FAILED", "QUERY_EXCEPTION", "API_UNAVAILABLE"]:
                err_msg = recon_info.get("error", "잔고 조회 실패")
                print(f"[{now_str}] ⚠️ [Startup Recovery Warning] {err_msg}")
                db.add_log("WARNING", f"프로세스 재시작 잔고 확인 미완료: {err_msg}")
                return

        # 2. 계좌 잔고 DB 동기화
        kres = engine.get_kiwoom_positions(force_refresh=True)
        held = kres.get("positions") or kres.get("held_positions", []) if (kres and kres.get("success")) else []
        if held:
            db.sync_positions_to_db(held)
            print(f"[{now_str}] ✅ [Startup Recovery] 실제 계좌 잔고({len(held)}개 종목) Source of Truth 동기화 완료")

        # 3. UNKNOWN 주문 및 미체결 주문 상태 복구
        if hasattr(engine, "order_manager") and engine.order_manager and engine.kiwoom_client:
            resolved = engine.order_manager.resolve_unknown_orders(engine.kiwoom_client)
            if resolved > 0:
                print(f"[{now_str}] 🔧 [Startup Recovery] UNKNOWN 주문 {resolved}건 상태 확정 복구 완료")

        pending = db.get_pending_orders()
        if pending:
            print(f"[{now_str}] 📋 [Startup Recovery] 기존 미체결 주문 {len(pending)}건 상태 추적 유지")

        db.add_log("INFO", f"✅ [Startup Recovery] Always-on Task 재시작 후 포지션({len(held)}개) 상태가 복구되었습니다.")
    except Exception as e:
        err = f"프로세스 재시작 복구 중 예외 발생: {e}"
        print(f"[{now_str}] 🚨 [Startup Recovery Error] {err}")
        engine.trigger_safe_stop(err)
        send_emergency_alert(notifier, "Startup Recovery Exception", err)


def run_trading_bot_daemon(interval: float = 2.5, run_once: bool = False, db: Optional[WebDBManager] = None):
    now_str = get_kst_now_str()
    print("=" * 70)
    print(f" 🚀 [PythonAnywhere Always-on Task: MagicTrader 자동매매 엔진 데몬 시작] ({now_str})")
    print("=" * 70)

    if db is None:
        db = WebDBManager()
    engine = WebTradingEngine(db)
    notifier = load_telegram_notifier()

    # 계좌번호 추출 및 RealTradingLock 연동 준비
    account_no = ""
    if engine.kiwoom_client and hasattr(engine.kiwoom_client, "config"):
        account_no = engine.kiwoom_client.config.get("account_no", "")
    if not account_no:
        account_no = os.getenv("KIWOOM_ACCOUNT_NO", "DEFAULT_ACC")

    real_lock_acquired = False

    # REAL 모드인 경우 계좌 기반 파일 락(RealTradingLock) 획득
    if engine.trading_mode == "REAL":
        try:
            acquire_real_trading_lock(account_no)
            real_lock_acquired = True
            print(f"[{now_str}] 🔒 [RealTradingLock] 계좌({account_no}) 실전매매 파일 락 획득 성공")
        except RealTradingLockError as lock_err:
            msg = f"이중 실전매매 기동 차단: {lock_err}"
            print(f"[{now_str}] 🚨 [RealTradingLock Collision] {msg}")
            engine.trigger_safe_stop(msg)
            send_emergency_alert(notifier, "RealTradingLock Collision SAFE_STOP", msg)
            if run_once:
                return
            sys.exit(1)

    try:
        # 프로세스 시작 시 계좌 상태 복구 수행
        perform_startup_recovery(engine, db, notifier)

        is_mock = True
        if engine.kiwoom_client and hasattr(engine.kiwoom_client, "is_mock"):
            is_mock = engine.kiwoom_client.is_mock
        env_name = "모의투자 (Mock)" if is_mock else "실전투자 (Real)"

        print(f"[*] 데이터베이스 경로 : {db.db_path}")
        print(f"[*] 키움 접속 환경    : {env_name}")
        print(f"[*] 초기 구동 상태    : {engine.status}")
        print(f"[*] 장중 평가 주기    : {interval}초 (KST 08:55~15:35 기준)")
        print("-" * 70)

        db.add_log("INFO", f"🚀 [Always-on Task] 백그라운드 자동매매 엔진 데몬이 구동되었습니다. ({now_str})")
        last_hb_time = 0.0
        reconciler = AccountReconciler(db)
        cycle_counter = 0

        while True:
            try:
                now_ts = time.time()
                # 10초마다 Engine Heartbeat DB에 기록
                if now_ts - last_hb_time >= 10.0:
                    db.update_heartbeat(
                        status=engine.status,
                        trading_mode=engine.trading_mode,
                        safe_stop_active=engine.safe_stop_active,
                        safe_stop_reason=engine.safe_stop_reason
                    )
                    last_hb_time = now_ts

                # Web 대시보드(WSGI)와 DB 제어 상태 및 종목 그리드 동기화
                if hasattr(engine, "sync_state_from_db"):
                    engine.sync_state_from_db()
                else:
                    status_cfg = db.get_system_config("engine_status")
                    if status_cfg:
                        engine.status = status_cfg.get("status", engine.status)

                # 구동 중에 모드가 REAL로 갱신된 경우 파일 락 동적 획득
                if engine.trading_mode == "REAL" and not real_lock_acquired:
                    try:
                        acquire_real_trading_lock(account_no)
                        real_lock_acquired = True
                        print(f"[{get_kst_now_str()}] 🔒 [RealTradingLock] REAL 모드 전환으로 인한 파일 락 획득 성공")
                    except RealTradingLockError as lock_err:
                        msg = f"REAL 모드 전환 중 이중 기동 차단: {lock_err}"
                        print(f"[{get_kst_now_str()}] 🚨 [RealTradingLock Collision] {msg}")
                        engine.trigger_safe_stop(msg)
                        send_emergency_alert(notifier, "RealTradingLock Switch Collision", msg)

                cur_now_str = get_kst_now_str()
                # MarketCalendar.is_market_open_now() 표준 함수 직접 호출로 정규 장시간 판정
                in_market = is_market_open_now(allow_buffer=True)

                if engine.status == "RUNNING":
                    if in_market:
                        engine.sync_market_prices()
                        engine.evaluate_grid_cycle()

                        # 60 사이클(약 2.5분)마다 계좌 정합성 주기적 재검증
                        cycle_counter += 1
                        if cycle_counter % 60 == 0 and engine.kiwoom_client and engine.kiwoom_client.is_credentials_valid():
                            is_ok, recon_info = reconciler.reconcile_account(engine.kiwoom_client, trading_mode=engine.trading_mode)
                            if not is_ok and recon_info.get("mismatches"):
                                err_details = ", ".join([f"종목({m['stock_code']}): DB={m['db_qty']}주 vs 계좌={m['kiwoom_qty']}주" for m in recon_info["mismatches"]])
                                stop_msg = f"RECONCILIATION_REQUIRED: 장중 계좌 잔고 수량 불일치 감지 ({err_details})"
                                engine.trigger_safe_stop(stop_msg)
                                send_emergency_alert(notifier, "장중 잔고 불일치 SAFE_STOP", stop_msg)

                        print(f"[{cur_now_str}] ⚡ [Always-on Task] 가동중 (RUNNING) | 장중 그리드 평가 완료")
                        sleep_time = interval
                    else:
                        print(f"[{cur_now_str}] 🌙 [Always-on Task] 가동중 (RUNNING) | 장외/휴장 시간 (CPU Quota 절약 15초 대기)")
                        sleep_time = 15.0
                elif engine.status == "SAFE_STOP":
                    print(f"[{cur_now_str}] 🚨 [Always-on Task] 비상 정지 (SAFE_STOP: {engine.safe_stop_reason}) | 주문이 안전 차단되었습니다.")
                    sleep_time = 10.0
                else:
                    print(f"[{cur_now_str}] ⏸️ [Always-on Task] 대기중 (STOPPED) | 대시보드에서 '시작' 클릭 시 개시됩니다.")
                    sleep_time = 5.0

                if run_once:
                    print(f"[{cur_now_str}] [Always-on Task] --once 옵션 1회 구동 완료.")
                    break

                if sleep_time > 0.5:
                    interruptible_sleep(db, sleep_time, check_interval=0.5)
                else:
                    time.sleep(sleep_time)

            except KeyboardInterrupt:
                print(f"\n[{get_kst_now_str()}] [Always-on Task] 사용자에 의한 데몬 프로세스 종료 요청")
                db.add_log("WARNING", "⏹ [Always-on Task] 사용자에 의해 데몬 프로세스가 종료되었습니다.")
                break
            except Exception as err:
                err_msg = f"Always-on Task 루프 예외 발생: {err}"
                print(f"[{get_kst_now_str()}] 🚨 [Always-on Task Error] {err_msg}")
                send_emergency_alert(notifier, "Always-on Daemon Loop Exception", err_msg)
                try:
                    db.add_log("ERROR", err_msg)
                except Exception:
                    pass
                time.sleep(5.0)

    finally:
        # 프로세스 종료 시 획득했던 RealTradingLock 안전 해제
        if real_lock_acquired:
            release_real_trading_lock(account_no)
            print(f"[{get_kst_now_str()}] 🔓 [RealTradingLock] 계좌({account_no}) 파일 락 안전 해제 완료")


if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="MagicTrader Always-on Task Daemon Runner")
    parser.add_argument("--interval", type=float, default=2.5, help="Grid evaluation loop interval in seconds (default: 2.5)")
    parser.add_argument("--once", action="store_true", help="Run 1 cycle and exit (for verification)")
    args = parser.parse_args()

    run_trading_bot_daemon(interval=args.interval, run_once=args.once)
