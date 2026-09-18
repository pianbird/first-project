"""
PythonAnywhere Always-on Task 전용 자동매매 엔진 데몬 (trading_bot/always_on_daemon.py)
- WSGI Web 프로세스(web_app/backend/main.py)와 완전 분리되어 24시간 독립 상주 구동
- shared SQLite DB(web_magictrader.db)를 통해 Web 대시보드의 제어 상태(RUNNING/STOPPED) 및 종목 그리드 설정을 실시간 수신
- 비정상 종료 시 PythonAnywhere가 자동으로 프로세스를 재시작함
"""
import os
import sys
import time
import argparse

# 실행 디렉터리 및 web_app/backend 경로 등록
base_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.abspath(os.path.join(base_dir, "..", "web_app", "backend"))

if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
if base_dir not in sys.path:
    sys.path.append(base_dir)

from db import WebDBManager
from engine import WebTradingEngine
from trading_rules import get_kst_now_str

def run_always_on_engine(run_once: bool = False):
    print("=" * 70)
    print(" [PythonAnywhere Always-on Task: MagicTrader 자동매매 엔진 데몬 시작]")
    print("=" * 70)

    db = WebDBManager()
    engine = WebTradingEngine(db)

    is_mock = True
    if engine.kiwoom_client and hasattr(engine.kiwoom_client, "is_mock"):
        is_mock = engine.kiwoom_client.is_mock
    env_name = "모의투자 (Mock)" if is_mock else "실전투자 (Real)"

    print(f"[*] 데이터베이스 경로 : {db.db_path}")
    print(f"[*] 키움 접속 환경    : {env_name}")
    print(f"[*] 초기 구동 상태    : {engine.status}")
    print("[*] 5초 주기로 DB 동기화 및 그리드 자동 매매 주문 평가를 수행합니다.")
    print("-" * 70)

    db.add_log("INFO", "🚀 [Always-on Task] 백그라운드 자동매매 엔진 데몬 프로세스가 구동되었습니다.")

    while True:
        try:
            # 1. Web 대시보드(WSGI)에서 전달된 최신 제어 명령 및 등록 종목 동기화
            engine.sync_state_from_db()

            now_str = get_kst_now_str()

            if engine.status == "RUNNING":
                active_count = sum(1 for s in engine.stocks.values() if s.get("is_active", True))
                print(f"[{now_str}] [Always-on Task] 구동 중 (RUNNING) | 활성 감시 종목: {active_count}개")
                engine.evaluate_grid_cycle()
            else:
                print(f"[{now_str}] [Always-on Task] 대기 중 (STOPPED) | 웹 대시보드에서 '시작' 조치 시 개시됩니다.")

            if run_once:
                print("[Always-on Task] --once 모드 1회 구동 완료.")
                break

        except KeyboardInterrupt:
            print("\n[Always-on Task] 사용자에 의한 프로세스 종료 요청")
            db.add_log("WARNING", "⏹ [Always-on Task] 사용자에 의해 데몬 프로세스가 종료되었습니다.")
            break
        except Exception as err:
            err_msg = f"Always-on Task 루프 예외 발생: {err}"
            print(f"[Always-on Task Exception] {err_msg}")
            try:
                db.add_log("ERROR", err_msg)
            except Exception:
                pass

        time.sleep(5)

if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="MagicTrader Always-on Task Daemon")
    parser.add_argument("--once", action="store_true", help="Run 1 cycle and exit (for verification test)")
    args = parser.parse_args()

    run_always_on_engine(run_once=args.once)
