import os
import sys
import time
import json
import datetime
from typing import Dict, Any, List, Optional

try:
    from trading_bot.kiwoom_client import KiwoomRESTClient
    from trading_bot.db_manager import DBManager
    from trading_bot.order_guard import OrderGuard
    from trading_bot.dashboard import DashboardServer
    from trading_bot.trading_rules import is_strict_market_hours, normalize_price, calculate_trade_cost
    from trading_bot.telegram_notifier import TelegramNotifier
    from trading_bot.magic_trade_engine import MagicTradeEngine
    from trading_bot.grid_strategy import generate_auto_grid_config
    from trading_bot.trading_mode import TradingModeManager
    from trading_bot.order_manager import OrderManager
    from trading_bot.risk_manager import RiskManager
except (ImportError, ModuleNotFoundError):
    from kiwoom_client import KiwoomRESTClient
    from db_manager import DBManager
    from order_guard import OrderGuard
    from dashboard import DashboardServer
    from trading_rules import is_strict_market_hours, normalize_price, calculate_trade_cost
    from telegram_notifier import TelegramNotifier
    from magic_trade_engine import MagicTradeEngine
    from grid_strategy import generate_auto_grid_config
    from trading_mode import TradingModeManager
    from order_manager import OrderManager
    from risk_manager import RiskManager

class AutoTradingEngine:
    """
    키움 REST API 자동매매 통합 메인 엔진 및 스케줄러 (MagicTrader 그리드 전략 탑재)
    """

    def __init__(self, config_path: str = "config.json"):
        # 실행 디렉터리 기준 경로 보장
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if not os.path.isabs(config_path):
            config_path = os.path.join(base_dir, config_path)

        self.config_path = config_path
        self.config = self._load_config(config_path)

        # 1. 서브 모듈 초기화
        self.db = DBManager(os.path.join(base_dir, "data", "trading.db"))
        self.kiwoom = KiwoomRESTClient(config_path)
        self.order_guard = OrderGuard(self.config)
        self.notifier = TelegramNotifier(self.config)
        self.magic_engine = MagicTradeEngine()
        self.mode_mgr = TradingModeManager(self.config.get("trading_mode", "MOCK"))
        self.order_manager = OrderManager(self.db, self.mode_mgr)

        risk_cfg = self.config.get("risk_management", {})
        max_daily = risk_cfg.get("max_daily_trades_account", 20)
        max_buy = risk_cfg.get("max_account_exposure", 50000000)
        self.risk_manager = RiskManager(max_daily_trades=max_daily, max_buy_amount=max_buy)

        self.watch_stocks = self.config.get("watch_stocks", [
            {"code": "005930", "name": "삼성전자"},
            {"code": "000660", "name": "SK하이닉스"}
        ])

        # 2. 그리드 전략 자동 세팅 (삼성전자/SK하이닉스 예시 그리드 등록)
        for s in self.watch_stocks:
            code = s.get("code")
            name = s.get("name", code)
            # DB에 기존 저장된 설정이 있으면 로드, 없으면 자동 생성
            existing_cfg = self.db.get_grid_config(code)
            if existing_cfg:
                from grid_strategy import GridConfig
                grid_cfg = GridConfig.from_dict(existing_cfg)
            else:
                base_price = 280000 if code == "005930" else 1700000
                exit_price = 320000 if code == "005930" else 1900000
                grid_cfg = generate_auto_grid_config(code, name, base_price, exit_price, num_steps=20, step_pct=1.5)
            self.magic_engine.register_grid_config(grid_cfg, self.db)

        # 3. 시작 시 계좌 잔고 동기화 (HNS/MTS 수동 주문 정합성 맞춤)
        account_no = self.config.get("account_no", "")
        synced_positions = self.kiwoom.sync_positions(account_no)
        for pos in synced_positions:
            self.db.upsert_position(pos)
        if synced_positions:
            self.db.log_event("INFO", f"시작 시 계좌 포지션 정합성 동기화 완료 ({len(synced_positions)}건)")

        # 4. 대시보드 서버 초기화 및 백그라운드 구동
        dash_cfg = self.config.get("dashboard", {})
        host = dash_cfg.get("host", "0.0.0.0")
        port = dash_cfg.get("port", 8080)
        self.dashboard_server = DashboardServer(self, host=host, port=port)

        self.db.log_event("INFO", "자동매매 메인 엔진 및 MagicTrader 그리드 모듈 초기화 완료")
        self.notifier.send_message("<b>[자동매매 엔진 시작]</b>\n키움 REST API MagicTrader 봇 시스템이 성공적으로 구동되었습니다.")

    def _load_config(self, path: str) -> Dict[str, Any]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def is_market_open(self) -> bool:
        """
        정규장 연속 매매 가능 시간 체크 (09:00:05 ~ 15:19:50, 동시호가 예외 제외)
        """
        return is_strict_market_hours()

    def get_system_status(self) -> Dict[str, Any]:
        """웹 대시보드 API용 시스템 통합 상태 반환"""
        balance = self.kiwoom.get_account_balance()
        positions = self.db.get_active_positions()
        recent_orders = self.db.get_recent_orders(limit=15)
        recent_logs = self.db.get_recent_logs(limit=25)
        today_trades = self.db.get_today_trade_count()

        grid_status = []
        if hasattr(self, "magic_engine"):
            for code, cfg in self.magic_engine.grid_configs.items():
                st = self.magic_engine.get_grid_state(code)
                grid_status.append({
                    "stock_code": code,
                    "stock_name": cfg.stock_name,
                    "current_step": st.current_step,
                    "max_steps": cfg.max_steps,
                    "target_qty": st.target_qty,
                    "actual_qty": st.actual_qty,
                    "exit_price": cfg.exit_price,
                    "is_blackswan_halt": st.is_blackswan_halt,
                    "recycle_mode": st.recycle_mode
                })

        return {
            "is_mock": self.kiwoom.is_mock,
            "is_market_open": self.is_market_open(),
            "kill_switch": self.order_guard.kill_switch,
            "today_trade_count": today_trades,
            "balance": balance,
            "positions": positions,
            "orders": recent_orders,
            "logs": recent_logs,
            "grid_status": grid_status,
            "server_time": time.strftime("%Y-%m-%d %H:%M:%S")
        }

    def _log_event(self, level: str, message: str):
        if hasattr(self.db, "log_event"):
            self.db.log_event(level, message)
        elif hasattr(self.db, "add_log"):
            self.db.add_log(level, message)

    def execute_manual_order(
        self,
        stock_code: str,
        side: str = "BUY",
        qty: int = 1,
        price: int = 0,
        ord_dvsn: str = "03"
    ) -> Dict[str, Any]:
        """수동 주문 전송 처리 및 리스크 가드"""
        clean_code = stock_code.strip()
        side_upper = side.upper()

        # 지정가(00) 주문 단가 호가 단위 보정 (Tick Size Normalization)
        if ord_dvsn == "00" and price > 0:
            price = normalize_price(price)

        # 1. 리스크 가드 검증
        positions = self.db.get_active_positions() if hasattr(self.db, "get_active_positions") else self.db.get_db_positions()
        can_trade, reason = self.order_guard.can_trade(clean_code, side_upper, self.db, positions)
        if not can_trade:
            self._log_event("WARNING", f"주문 거부 [{clean_code} {side_upper}]: {reason}")
            self.notifier.send_message(f"<b>[주문 거부]</b> [{clean_code} {side_upper}]\n사유: {reason}")
            return {"success": False, "reason": reason}

        # 2. 키움 REST API 주문 전송 (OrderManager 단일 경로 통과)
        account_no = self.config.get("account_no", "")
        total_exp = sum(p.get("avg_price", 0) * p.get("qty", 0) for p in positions)
        pending_orders = self.db.get_pending_orders() if hasattr(self.db, "get_pending_orders") else []
        daily_count = self.db.get_daily_trade_count() if hasattr(self.db, "get_daily_trade_count") else 0
        order_res = self.order_manager.submit_order(
            kiwoom_client=self.kiwoom,
            risk_manager=self.risk_manager,
            account_no=account_no,
            order_type=side_upper,
            stock_code=clean_code,
            stock_name=clean_code,
            qty=qty,
            price=price,
            ord_dvsn=ord_dvsn,
            strategy_id="MAIN_MANUAL",
            cycle_id=int(time.time()),
            is_safe_stop=False,
            daily_trade_count=daily_count,
            total_account_exposure=total_exp,
            pending_orders=pending_orders
        )

        if order_res.get("success"):
            # 3. DB 기록 및 쿨다운 시각 저장
            self.db.record_order(order_res)
            self.order_guard.record_trade_execution(clean_code)

            # 포지션 임시 업데이트
            price_info = self.kiwoom.get_stock_price(clean_code)
            cur_price = price_info.get("current_price", price)
            
            if side_upper == "BUY":
                if hasattr(self.db, "upsert_position"):
                    self.db.upsert_position({
                        "stock_code": clean_code,
                        "stock_name": price_info.get("name", clean_code),
                        "qty": qty,
                        "avg_price": cur_price,
                        "current_price": cur_price,
                        "pnl": 0,
                        "pnl_rate": 0.0
                    })
            elif side_upper == "SELL":
                if hasattr(self.db, "remove_position"):
                    self.db.remove_position(clean_code)

            msg = f"주문 전송 성공 [{clean_code} {side_upper} {qty}주] 주문번호: {order_res.get('order_id')}"
            self._log_event("INFO", msg)
            self.notifier.send_message(f"<b>[주문 체결]</b> {clean_code} ({side_upper})\n수량: {qty}주 | 주문번호: {order_res.get('order_id')}")
            return {"success": True, "order_id": order_res.get("order_id"), "message": msg}
        else:
            err = order_res.get("message") or order_res.get("error") or "주문 실패"
            self._log_event("ERROR", f"주문 전송 오류 [{clean_code}]: {err}")
            self.notifier.send_message(f"<b>[주문 오류]</b> {clean_code}\n에러: {err}")
            return {"success": False, "error": err, "message": err}

    def run_cycle(self):
        """1회 모니터링 폴링 사이클 (MagicTrader 그리드 전략 포함)"""
        market_open = self.is_market_open()
        positions = self.db.get_active_positions()
        pos_map = {p["stock_code"]: p.get("qty", 0) for p in positions}
        
        # 감시 종목 시세 폴링 및 그리드 전략 평가
        for item in self.watch_stocks:
            code = item.get("code")
            name = item.get("name", code)
            
            price_info = self.kiwoom.get_stock_price(code)
            if price_info.get("success"):
                cur_price = price_info.get("current_price", 0)
                change = price_info.get("change", 0)
                rate = price_info.get("change_rate", 0.0)
                prev_open = price_info.get("prev_open_price", 0)
                
                log_msg = f"[{name}({code})] 현재가: {cur_price:,}원 (전일비: {change:+,}원, {rate:+.2f}%)"
                print(f"[Polling] {log_msg}")

                # 정규장 시간인 경우 MagicTrader 그리드 전략 평가 및 수량 보정 매매 수행
                if market_open and cur_price > 0:
                    actual_qty = pos_map.get(code, 0)
                    daily_candles = self.kiwoom.get_daily_chart(code)
                    eval_res = self.magic_engine.evaluate_stock(
                        stock_code=code,
                        current_price=cur_price,
                        daily_candles=daily_candles,
                        actual_position_qty=actual_qty,
                        db=self.db,
                        kiwoom=self.kiwoom,
                        order_guard=self.order_guard
                    )
                    action = eval_res.get("action")
                    if action not in ("HOLD", "NONE"):
                        print(f"  └─ [MagicTrader Action] {action}: {eval_res.get('message')}")
            else:
                err = price_info.get("error")
                print(f"[Polling Error] [{name}({code})]: {err}")

    def run(self):
        """메인 스케줄러 실행 루프 (DEPRECATED - 실전 운영은 trading_bot_runner.py + WebTradingEngine 사용)"""
        print("=" * 65)
        print(" [⚠️ DEPRECATED LEGACY ENGINE: AutoTradingEngine - 실전 운영 경로는 trading_bot_runner.py입니다]")
        print("=" * 65)
        if "--force-legacy-run" not in sys.argv:
            print("🛑 [SAFETY GUARD] AutoTradingEngine은 legacy 단독 실행 모드입니다.")
            print("   PythonAnywhere Always-on Task 운영 경로: python trading_bot_runner.py")
            print("   강제 실행이 필요한 경우: python main.py --force-legacy-run")
            return

        env_name = "모의투자 (Mock)" if self.kiwoom.is_mock else "실전투자 (Real)"
        print(f"[*] 접속 환경 : {env_name}")
        print(f"[*] 계좌 번호 : {self.config.get('account_no')}")
        print(f"[*] 대시보드   : http://localhost:{self.config.get('dashboard', {}).get('port', 8080)}")
        print("-" * 65)

        # 웹 대시보드 서버 구동
        self.dashboard_server.start()

        try:
            while True:
                self.run_cycle()
                time.sleep(5) # 5초 폴링 간격
        except KeyboardInterrupt:
            print("\n[메인 엔진] 사용자에 의해 시스템 종료가 요청되었습니다.")
        finally:
            self.dashboard_server.stop()
            self.db.log_event("INFO", "자동매매 시스템 종료 완료")

if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    engine = AutoTradingEngine()
    engine.run()
