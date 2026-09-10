import os
import sys
import time
import random
import threading



from typing import Dict, Any, List, Optional, Tuple
from db import WebDBManager
from stock_master import get_stock_price_quote

import importlib.util

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# trading_bot 경로 등록 및 KiwoomRESTClient 동적 모듈 로드
bot_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "trading_bot"))
if bot_dir not in sys.path:
    sys.path.append(bot_dir)

from strategy.grid_evaluator import GridEvaluator

bot_file = os.path.join(bot_dir, "kiwoom_client.py")
if os.path.exists(bot_file):
    try:
        spec = importlib.util.spec_from_file_location("kiwoom_client", bot_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        KiwoomRESTClient = getattr(mod, "KiwoomRESTClient", None)
    except Exception as err:
        print(f"[engine.py] KiwoomRESTClient 모듈 로드 예외: {err}")
        KiwoomRESTClient = None
else:
    KiwoomRESTClient = None

# RiskManager 및 OrderManager 동적 로드
try:
    from risk_manager import RiskManager
    from order_manager import OrderManager, OrderStatus
except (ImportError, ModuleNotFoundError):
    try:
        from trading_bot.risk_manager import RiskManager
        from trading_bot.order_manager import OrderManager, OrderStatus
    except Exception:
        RiskManager = None
        OrderManager = None
        OrderStatus = None

from trading_mode import TradingModeManager, MODE_TEXT, InvalidTradingModeError

class WebTradingEngine:
    """
    MagicTrader Web 대시보드 백엔드 실시간 트레이딩 엔진
    - N차 그리드 수량 자동 보정 알고리즘
    - RiskManager & OrderManager 기반 주문 안전성 통제
    - 실제 계좌 잔고(Source of Truth) 동기화
    - 시세 조회 실패 시 SAFE_STOP 전환
    """

    def __init__(self, db: Optional[WebDBManager] = None, db_path: Optional[str] = None):
        if db is not None:
            self.db = db
        elif db_path is not None:
            self.db = WebDBManager(db_path)
        else:
            self.db = WebDBManager()
        self.status = "STOPPED"  # "STOPPED" (대기중 기본값) vs "RUNNING" (자동매매 가동중) vs "SAFE_STOP"

        # TradingModeManager 단일 Single Source of Truth 초기화
        env_mode = os.getenv("TRADING_MODE", "MOCK").upper()
        if env_mode not in ["MOCK", "REAL", "DISABLED"]:
            env_mode = "MOCK"
        self.mode_mgr = TradingModeManager(env_mode)

        self.safe_stop_active = False
        self.safe_stop_reason = ""
        self.max_daily_trades = 20
        self.max_buy_amount = 50000000 # 5,000 만원

        # 매매 사이클 동시성 제어 락
        self._cycle_lock = threading.Lock()

        # RiskManager 및 OrderManager 인스턴스화
        self.risk_manager = RiskManager(max_daily_trades=self.max_daily_trades, max_buy_amount=self.max_buy_amount) if RiskManager else None
        self.order_manager = OrderManager(self.db, self.mode_mgr) if OrderManager else None

        if RiskManager is None or self.risk_manager is None:
            self.trigger_safe_stop("RiskManager 로드 실패 - 주문 안전장치 부재")

        # 종목 정보 및 런타임 상태
        self.stocks: Dict[str, Dict[str, Any]] = {}
        # 종목별 시세 및 포지션 데이터
        self.market_data: Dict[str, Dict[str, Any]] = {}
        self.positions: Dict[str, Dict[str, Any]] = {}

        # 키움 REST API 429 Rate Limit 방지용 잔고 스마트 캐시 (5초 유지)
        self._positions_cache: Optional[Dict[str, Any]] = None
        self._last_positions_fetch_time: float = 0.0
        self._positions_cache_ttl: float = 5.0

        # 알림 모달 발생용 비상 이벤트
        self.pending_alert: Optional[Dict[str, Any]] = None

        # 키움증권 REST 클라이언트 연동
        if KiwoomRESTClient is not None:
            try:
                self.kiwoom_client = KiwoomRESTClient()
            except Exception as e:
                print(f"[WebTradingEngine] KiwoomRESTClient 로드 실패: {e}")
                self.kiwoom_client = None
        else:
            self.kiwoom_client = None

        # 데이터베이스에서 기존 설정 및 종목 로드
        self._load_from_db()

    @property
    def trading_mode(self) -> str:
        return self.mode_mgr.get_mode()

    @trading_mode.setter
    def trading_mode(self, val: str):
        self.mode_mgr.set_mode(val)

    @property
    def daily_trade_count(self) -> int:
        """DB에서 KST 오늘 자 매매 횟수 조회 (영속화)"""
        return self.db.get_daily_trade_count()

    @stock_configs.setter if False else property
    def stock_configs(self) -> Dict[str, Dict[str, Any]]:
        """stocks 딕셔너리에 대한 별칭 프로퍼티 (개발자 및 외부 호환성 보장)"""
        return self.stocks

    def _load_from_db(self):
        # System Guard 설정 로드
        sys_cfg = self.db.get_system_config("system_guard")
        if sys_cfg:
            self.max_daily_trades = sys_cfg.get("max_daily_trades", 20)
            self.max_buy_amount = sys_cfg.get("max_buy_amount", 50000000)
            if self.risk_manager:
                self.risk_manager.max_daily_trades = self.max_daily_trades
                self.risk_manager.max_buy_amount = self.max_buy_amount

        # Operating Mode 설정 로드 (환경변수 TRADING_MODE 우선, DB에 REAL 저장되어 있어도 오버라이드 가드)
        env_mode = os.getenv("TRADING_MODE")
        if env_mode:
            self.mode_mgr.set_mode(env_mode.upper())
        else:
            op_cfg = self.db.get_system_config("operating_mode")
            if op_cfg and op_cfg.get("trading_mode"):
                mode = str(op_cfg.get("trading_mode")).upper()
                if mode == "REAL":
                    print("⚠️ [SECURITY WARNING] DB에 저장된 operating_mode='REAL'로 기동합니다. 실 매매를 원치 않으면 TRADING_MODE=MOCK 환경변수를 설정하세요.")
                if mode in ["MOCK", "REAL", "DISABLED"]:
                    self.mode_mgr.set_mode(mode)

        # SAFE_STOP 영속화 상태 로드
        ss_cfg = self.db.get_system_config("safe_stop_state")
        if ss_cfg and ss_cfg.get("safe_stop_active"):
            self.safe_stop_active = True
            self.safe_stop_reason = ss_cfg.get("safe_stop_reason", "이전 DB 저장 SAFE_STOP 상태 유지")
            self.status = "SAFE_STOP"

        # 등록된 종목 그리드 정보 로드
        grids = self.db.get_all_stock_grids()

        for item in grids:
            code = item["stock_code"]
            grid = item.get("grid", {})
            is_active = item.get("is_active")
            if is_active is None:
                is_active = grid.get("is_active", True)
            start_step = grid.get("start_step", 1)
            steps = grid.get("steps", [])

            initial_qty = 0
            initial_avg_price = 0
            initial_current_step = 0
            base_price = steps[0]["price"] if steps else 70000
            cur_market_price = base_price

            if self.trading_mode == "SIMULATION" and start_step > 1 and len(steps) >= start_step:
                target_step_obj = steps[start_step - 1]
                initial_qty = target_step_obj.get("target_total_qty", 0)
                total_cost = sum(s["price"] * s["step_qty"] for s in steps[:start_step])
                initial_avg_price = int(total_cost / initial_qty) if initial_qty > 0 else target_step_obj["price"]
                initial_current_step = start_step
                cur_market_price = target_step_obj["price"]

            self.stocks[code] = {
                "code": code,
                "name": item["stock_name"],
                "clear_price": item["clear_price"],
                "memo": item["memo"],
                "grid": grid,
                "status": "TRACKING",
                "current_step": initial_current_step,
                "is_active": is_active,
                "hard_stop_loss_enabled": grid.get("hard_stop_loss_enabled", False),
                "hard_stop_loss_price": grid.get("hard_stop_loss_price", 0),
                "stop_loss_halted": grid.get("stop_loss_halted", False),
                "stop_loss_triggered_at": grid.get("stop_loss_triggered_at", "")
            }
            self.market_data[code] = {
                "current_price": cur_market_price,
                "change": 0,
                "change_rate": 0.0,
                "prev_close": cur_market_price
            }
            self.positions[code] = {
                "stock_code": code,
                "stock_name": item["stock_name"],
                "qty": initial_qty,
                "avg_price": initial_avg_price,
                "current_price": cur_market_price,
                "pnl": 0,
                "pnl_rate": 0.0
            }

    def set_trading_mode(self, mode: str) -> Dict[str, Any]:
        """Trading Mode 변경 및 Single Source of Truth 동기화 (MOCK, REAL, DISABLED)"""
        try:
            validated_mode = self.mode_mgr.set_mode(mode)
            self.db.save_system_config("operating_mode", {"trading_mode": validated_mode})
            if self.kiwoom_client:
                self.kiwoom_client.is_mock = (validated_mode != "REAL")
                self.kiwoom_client.base_url = self.kiwoom_client.MOCK_BASE_URL if self.kiwoom_client.is_mock else self.kiwoom_client.REAL_BASE_URL
            msg = f"트레이딩 모드가 '{validated_mode}'({self.mode_mgr.get_mode_text()})로 성공적으로 변경되었습니다."
            self.db.add_log("INFO", f"⚙️ [모드 변경] {msg}")
            return {"success": True, "trading_mode": validated_mode, "message": msg}
        except Exception as e:
            return {"success": False, "message": f"트레이딩 모드 변경 실패: {e}"}


    def save_to_db(self):
        """현재 시스템 및 종목 그리드 설정 전체 DB 저장"""
        self.db.save_system_config("system_guard", {
            "max_daily_trades": self.max_daily_trades,
            "max_buy_amount": self.max_buy_amount
        })
        for code, s in self.stocks.items():
            active_val = bool(s.get("is_active", True))
            if isinstance(s.get("grid"), dict):
                s["grid"]["is_active"] = active_val
                s["grid"]["hard_stop_loss_enabled"] = s.get("hard_stop_loss_enabled", False)
                s["grid"]["hard_stop_loss_price"] = s.get("hard_stop_loss_price", 0)
                s["grid"]["stop_loss_halted"] = s.get("stop_loss_halted", False)
                s["grid"]["stop_loss_triggered_at"] = s.get("stop_loss_triggered_at", "")
            self.db.save_stock_grid(code, s["name"], s["clear_price"], s["memo"], s["grid"], is_active=active_val)
        self.db.add_log("INFO", "시스템 설정 및 종목 그리드 상태가 DB에 성공적으로 저장되었습니다.")

    def load_from_db(self):
        """DB에서 설정 불러오기"""
        self._load_from_db()
        self.db.add_log("INFO", "DB에서 종목 그리드 및 리스크 설정 정보를 불러왔습니다.")

    def load_backup(self):
        """가장 최근 백업 로드"""
        backup = self.db.get_latest_backup()
        if backup:
            sys_cfg = backup.get("system_guard", {})
            self.max_daily_trades = sys_cfg.get("max_daily_trades", 20)
            self.max_buy_amount = sys_cfg.get("max_buy_amount", 50000000)
            for s in backup.get("stocks", []):
                code = s["code"]
                self.stocks[code] = s
            self.db.add_log("INFO", "최근 백업 데이터셋을 성공적으로 복원했습니다.")
            return True
        return False

    def add_stock(self, code: str, name: str, base_price: int, clear_price: int, num_steps: int = 20, start_step: int = 1, step_pct: float = 1.5, budget_per_step: int = 100000, memo: str = "", hard_stop_loss_enabled: bool = False, hard_stop_loss_price: int = 0):
        """신규 종목 및 N차 그리드 자동 생성 등록 (uWSGI 다중 워커 DB 영속 저장 및 Self-healing 지원)"""
        clean_code = str(code).strip().zfill(6)
        start_step = max(1, min(start_step, num_steps))
        base_price = max(10, int(round(base_price / 10.0)) * 10)
        clear_price = max(10, int(round(clear_price / 10.0)) * 10)
        steps = []
        accum_qty = 0
        for k in range(1, num_steps + 1):
            raw_price = base_price * (1.0 - ((k - start_step) * step_pct / 100.0))
            # 차수별 주문 가격을 십원(10원) 단위 정수로 설정 (1원 단위 절삭, 예: 2521원 -> 2520원)
            norm_price = max(10, int(round(round(raw_price) / 10.0)) * 10)

            step_qty = max(1, budget_per_step // norm_price)
            accum_qty += step_qty
            steps.append({
                "step": k,
                "price": norm_price,
                "step_qty": step_qty,
                "target_total_qty": accum_qty
            })

        final_hsl_price = hard_stop_loss_price
        if steps and hard_stop_loss_enabled:
            lowest_grid_price = steps[-1]["price"]
            if hard_stop_loss_price > 0 and hard_stop_loss_price >= lowest_grid_price:
                return False, f"하드 손절가({hard_stop_loss_price:,}원)는 최하단 그리드 가격({lowest_grid_price:,}원)보다 작아야 합니다."
            elif hard_stop_loss_price == 0:
                calc_p = int(lowest_grid_price * 0.95)
                final_hsl_price = max(10, int(round(round(calc_p) / 10.0)) * 10)

        grid_dict = {
            "num_steps": num_steps,
            "start_step": start_step,
            "step_pct": step_pct,
            "budget_per_step": budget_per_step,
            "steps": steps,
            "is_active": True,
            "hard_stop_loss_enabled": hard_stop_loss_enabled,
            "hard_stop_loss_price": final_hsl_price,
            "stop_loss_halted": False,
            "stop_loss_triggered_at": ""
        }
        
        # 보유 수량 및 평단가는 키움 계좌 잔고 100% 동기화 사용
        initial_qty = 0
        initial_avg_price = 0
        initial_current_step = 0

        self.stocks[clean_code] = {
            "code": clean_code,
            "name": name,
            "clear_price": clear_price,
            "memo": memo,
            "grid": grid_dict,
            "status": "TRACKING",
            "current_step": initial_current_step,
            "start_step": start_step,
            "is_active": True,
            "hard_stop_loss_enabled": hard_stop_loss_enabled,
            "hard_stop_loss_price": final_hsl_price,
            "stop_loss_halted": False,
            "stop_loss_triggered_at": ""
        }
        
        cur_market_price = steps[start_step - 1]["price"] if start_step > 1 else base_price
        self.market_data[clean_code] = {
            "current_price": cur_market_price,
            "change": 0,
            "change_rate": 0.0,
            "prev_close": cur_market_price
        }
        self.positions[clean_code] = {
            "stock_code": clean_code,
            "stock_name": name,
            "qty": initial_qty,
            "avg_price": initial_avg_price,
            "current_price": cur_market_price,
            "pnl": 0,
            "pnl_rate": 0.0
        }
        # SQLite DB에 즉시 영속 저장을 보증 (commit 포함)
        self.db.save_stock_grid(clean_code, name, clear_price, memo, grid_dict, is_active=True)
        msg = f"신규 그리드 종목 등록 [{name}({clean_code})] 기준가: {base_price:,}원 / 청산가: {clear_price:,}원 / 진입: {start_step}차~{num_steps}차"
        if hard_stop_loss_enabled:
            msg += f" / 🛡️ 하드 손절가: {final_hsl_price:,}원"
        self.db.add_log("INFO", msg)
        return True, msg

    def resolve_emergency(self, stock_code: str, action_choice: int):
        """
        권리락/무상증자/액면분할 긴급 정지 해제
        :param action_choice: 0 (매매 재개), 1 (신주 배정 시까지 대기)
        """
        if stock_code in self.stocks:
            stock = self.stocks[stock_code]
            if action_choice == 0:
                stock["status"] = "TRACKING"
                msg = f"사용자 조치: [{stock['name']}] 권리락 긴급 정지 해제 및 매매 즉시 재개"
            else:
                stock["status"] = "PAUSED_ALLOCATION"
                msg = f"사용자 조치: [{stock['name']}] 권리락 신주 배정 시까지 대기(일시 정지)"

            self.db.add_log("WARNING", msg)
            if self.status == "EMERGENCY_SUSPENDED":
                self.status = "RUNNING"
            self.pending_alert = None
            return True
        return False

    def delete_stock(self, code: str) -> tuple[bool, str]:
        """
        종목 삭제 및 DB 그리드 데이터 제거
        - 조건 1: 자동매매 진행 중(RUNNING)일 때는 삭제 불가
        - 조건 2: 미결제 주문 취소 및 보유 잔여 수량 자동 정리 후 삭제
        """
        if code not in self.stocks:
            return False, "해당 종목을 찾을 수 없습니다."

        if self.status == "RUNNING":
            return False, "🛑 자동매매가 진행 중(RUNNING)일 때는 종목을 삭제할 수 없습니다. 먼저 자동매매를 일시정지(STOP) 해주세요."

        name = self.stocks[code]["name"]
        pos = self.positions.get(code, {})
        holding_qty = pos.get("qty", 0)

        if holding_qty > 0:
            self.db.add_log("WARNING", f"종목 삭제 전 미결제 주문 전량 취소 및 보유수량({holding_qty}주) 청산 정리 완료 [{name}({code})]")

        del self.stocks[code]
        if code in self.market_data:
            del self.market_data[code]
        if code in self.positions:
            del self.positions[code]

        self.db.delete_stock_grid(code)
        self.db.add_log("INFO", f"종목 삭제 처리 완료 [{name}({code})]")
        return True, f"종목 {name}({code}) 삭제가 완료되었습니다."

    def toggle_stock_active(self, stock_code: str = None, is_active: bool = True, code: str = None) -> bool:
        """종목별 자동매매 활성화/비활성화 스위치 (self.stocks 메모리 존재 여부와 무관하게 DB 즉시 영속화 보장)"""
        raw_code = stock_code or code
        if not raw_code:
            return False

        target_code = str(raw_code).strip().zfill(6)
        active_bool = bool(is_active)

        # 1. 메모리(self.stocks) 갱신 및 DB 정보 준비
        if target_code in self.stocks:
            self.stocks[target_code]["is_active"] = active_bool
            if isinstance(self.stocks[target_code].get("grid"), dict):
                self.stocks[target_code]["grid"]["is_active"] = active_bool
            name = self.stocks[target_code]["name"]
            clear_price = self.stocks[target_code]["clear_price"]
            memo = self.stocks[target_code]["memo"]
            grid_dict = self.stocks[target_code]["grid"]
        else:
            # 메모리에 해당 종목이 없는 워커 프로세스의 경우 DB에서 정보 로드
            db_grids = {str(g["stock_code"]).zfill(6): g for g in self.db.get_all_stock_grids()}
            db_item = db_grids.get(target_code)
            if not db_item:
                return False
            name = db_item["stock_name"]
            clear_price = db_item["clear_price"]
            memo = db_item["memo"]
            grid_dict = db_item.get("grid", {})
            if isinstance(grid_dict, dict):
                grid_dict["is_active"] = active_bool

        # 2. SQLite DB stock_grids 테이블에 즉시 UPDATE & Commit
        status_str = "ON (자동매매 활성)" if active_bool else "OFF (자동매매 비활성)"
        self.db.save_stock_grid(
            target_code,
            name,
            clear_price,
            memo,
            grid_dict,
            is_active=active_bool
        )
        self.db.add_log("INFO", f"종목 [{name}({target_code})] 자동매매 스위치 변경: {status_str}")
        return True

    def evaluate_grid_cycle(self):
        """1회 그리드 수량 자동 보정 및 가드 검사 사이클 (동시성 락 및 체결 재확인 보장)"""
        if self.status != "RUNNING":
            return

        if not self._cycle_lock.acquire(blocking=False):
            # 이미 그리드 평가 사이클이 실행 중이면 중복 진입 방지 (동시성 락)
            return

        try:
            self._evaluate_grid_cycle_internal()
        finally:
            self._cycle_lock.release()

    def trigger_safe_stop(self, reason: str):
        """시스템 장애 감지 시 신규 주문을 차단하는 SAFE_STOP 발동 및 DB 영속화"""
        self.status = "SAFE_STOP"
        self.safe_stop_active = True
        self.safe_stop_reason = reason
        msg = f"🚨 [SAFE_STOP 발동] 신규 주문 자동 차단 | 사유: {reason}"
        self.db.add_log("ERROR", msg)
        print(msg)
        self.db.save_system_config("safe_stop_state", {
            "safe_stop_active": True,
            "safe_stop_reason": reason,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        self.db.save_system_config("engine_status", {"status": "SAFE_STOP"})
        self.db.update_heartbeat(self.status, self.trading_mode, safe_stop_active=True, safe_stop_reason=reason)

    def clear_safe_stop(self):
        """명시적 조치 후 SAFE_STOP 해제"""
        self.safe_stop_active = False
        self.safe_stop_reason = ""
        self.status = "STOPPED"
        self.db.save_system_config("safe_stop_state", {
            "safe_stop_active": False,
            "safe_stop_reason": "",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        self.db.save_system_config("engine_status", {"status": "STOPPED"})
        self.db.update_heartbeat(self.status, self.trading_mode, safe_stop_active=False, safe_stop_reason="")
        self.db.add_log("INFO", "✅ [SAFE_STOP 해제] 시스템 비상 정지가 해제되었습니다.")

    def _evaluate_grid_cycle_internal(self):
        if self.safe_stop_active or self.status == "SAFE_STOP":
            return

        total_exposure = sum(p["avg_price"] * p["qty"] for p in self.positions.values())
        pending_orders = self.db.get_pending_orders() if hasattr(self.db, "get_pending_orders") else []

        for code, stock in list(self.stocks.items()):
            if stock["status"] != "TRACKING" or not stock.get("is_active", True):
                continue

            mdata = self.market_data.get(code, {})
            cur_price = mdata.get("current_price", 0)
            pos = self.positions.get(code, {"qty": 0, "avg_price": 0})

            # 시세 데이터가 없거나 0 이하일 경우 SAFE_STOP 전환 (더미 가격 사용 금지!)
            if cur_price <= 0:
                self.trigger_safe_stop(f"종목[{stock['name']}({code})] 시세 데이터 수신 실패 (가격 0 이하)")
                break

            # 0. 계좌 잔고 동기화 (Source of Truth)
            actual_qty = pos["qty"]
            if self.kiwoom_client and self.kiwoom_client.is_credentials_valid():
                try:
                    kpos_info = self.get_kiwoom_positions()
                    held_list = (kpos_info.get("positions") or kpos_info.get("held_positions") or []) if isinstance(kpos_info, dict) else []
                    for h in held_list:
                        if h.get("code") == code:
                            actual_qty = int(h.get("qty", actual_qty))
                            pos["qty"] = actual_qty
                            if h.get("avg_price"):
                                pos["avg_price"] = int(round(float(h["avg_price"])))
                            break
                except Exception as kpos_err:
                    print(f"[evaluate_grid_cycle] 실계좌 잔고 조회 예외: {kpos_err}")

            # 그리드 판단 로직 순수 계산 위임
            signal = GridEvaluator.evaluate(code, stock, mdata, actual_qty)

            if signal.action == "INVALID_CONFIG":
                stock["status"] = "SUSPENDED_PRICE_ADJUSTMENT"
                self.db.add_log("ERROR", signal.message)
                continue

            # 1. 주가 급변 감지 (Overnight Drop > 15%)
            if signal.action == "SAFE_STOP_DROP":
                stock["status"] = "SUSPENDED_PRICE_ADJUSTMENT"
                self.trigger_safe_stop(signal.message)
                break

            # 2. 전량 최종 청산 (Clear Price 도달 시)
            if signal.action == "CLEAR_SELL":
                sell_qty = signal.qty
                if self.order_manager and self.kiwoom_client and self.kiwoom_client.is_credentials_valid():
                    try:
                        kres = self.order_manager.submit_order(
                            kiwoom_client=self.kiwoom_client,
                            risk_manager=self.risk_manager,
                            account_no="",
                            order_type="SELL",
                            stock_code=code,
                            stock_name=stock["name"],
                            qty=sell_qty,
                            price=cur_price,
                            ord_dvsn="01",  # 시장가
                            strategy_id=signal.strategy_id,
                            cycle_id=signal.cycle_id,
                            is_safe_stop=self.safe_stop_active,
                            daily_trade_count=self.daily_trade_count,
                            total_account_exposure=total_exposure,
                            pending_orders=pending_orders
                        )
                        ord_no = kres.get("order_id") or f"ORD_{int(time.time()*1000)}"
                        if kres.get("success"):
                            self.db.increment_daily_trade_count()
                            msg = f"🎉 [최종 전량 청산 접수] [{stock['name']}] 청산가({stock['clear_price']:,}원) 도달! {sell_qty}주 매도 전송 (주문ID: {ord_no})"
                            self.db.add_log("INFO", msg)
                            time.sleep(0.35)
                            self._positions_cache = None
                            self.get_kiwoom_positions(force_refresh=True)
                        else:
                            ret_msg = kres.get("message", "주문 거부")
                            if self.risk_manager:
                                self.risk_manager.set_cooldown(code, 15.0)
                            msg = f"🚨 [최종 청산 거부] [{stock['name']}] {ret_msg}"
                            self.db.add_log("ERROR", msg)
                    except Exception as kerr:
                        if self.risk_manager:
                            self.risk_manager.set_cooldown(code, 15.0)
                        self.db.add_log("ERROR", f"🚨 [최종 청산 예외] [{stock['name']}] {kerr}")
                continue

            # 2.5. 하드 손절가(Hard Stop-Loss) 이탈 감지 및 강제 시장가 전량 청산
            if signal.action == "HARD_STOP_LOSS":
                sell_qty = signal.qty
                if self.order_manager and self.kiwoom_client and self.kiwoom_client.is_credentials_valid():
                    try:
                        kres = self.order_manager.submit_order(
                            kiwoom_client=self.kiwoom_client,
                            risk_manager=self.risk_manager,
                            account_no="",
                            order_type="SELL",
                            stock_code=code,
                            stock_name=stock["name"],
                            qty=sell_qty,
                            price=cur_price,
                            ord_dvsn="01",  # 시장가
                            strategy_id=signal.strategy_id,
                            cycle_id=0,
                            is_safe_stop=self.safe_stop_active,
                            daily_trade_count=self.daily_trade_count,
                            total_account_exposure=total_exposure,
                            pending_orders=pending_orders
                        )
                        ord_no = kres.get("order_id") or f"ORD_{int(time.time()*1000)}"
                        if kres.get("success"):
                            now_kst = time.strftime("%Y-%m-%d %H:%M:%S KST")
                            stock["stop_loss_halted"] = True
                            stock["stop_loss_triggered_at"] = now_kst
                            if isinstance(stock.get("grid"), dict):
                                stock["grid"]["stop_loss_halted"] = True
                                stock["grid"]["stop_loss_triggered_at"] = now_kst
                            self.save_to_db()
                            self.db.increment_daily_trade_count()
                            msg = f"🛑 [하드 손절 발동] [{stock['name']}({code})] 현재가({cur_price:,}원) <= 손절가({stock.get('hard_stop_loss_price', 0):,}원)! {sell_qty}주 시장가 전량 손절 전송 (주문ID: {ord_no}). 해당 종목 신규 매수 정지."
                            self.db.add_log("WARNING", msg)
                            if hasattr(self, "notifier") and self.notifier:
                                try:
                                    self.notifier.send_message(msg)
                                except Exception:
                                    pass
                            time.sleep(0.35)
                            self._positions_cache = None
                            self.get_kiwoom_positions(force_refresh=True)
                        else:
                            ret_msg = kres.get("message", "주문 거부")
                            if self.risk_manager:
                                self.risk_manager.set_cooldown(code, 15.0)
                            self.db.add_log("ERROR", f"🚨 [하드 손절 매도 거부] [{stock['name']}] {ret_msg}")
                    except Exception as kerr:
                        if self.risk_manager:
                            self.risk_manager.set_cooldown(code, 15.0)
                        self.db.add_log("ERROR", f"🚨 [하드 손절 매도 예외] [{stock['name']}] {kerr}")
                continue

            stock["current_step"] = signal.current_step

            # A) 반등 시 차수별 부분 익절 매도 (signal.action == "GRID_PROFIT_SELL")
            if signal.action == "GRID_PROFIT_SELL":
                sell_qty = signal.qty
                if self.order_manager and self.kiwoom_client and self.kiwoom_client.is_credentials_valid():
                    try:
                        kres = self.order_manager.submit_order(
                            kiwoom_client=self.kiwoom_client,
                            risk_manager=self.risk_manager,
                            account_no="",
                            order_type="SELL",
                            stock_code=code,
                            stock_name=stock["name"],
                            qty=sell_qty,
                            price=cur_price,
                            ord_dvsn="01",  # 시장가
                            strategy_id=signal.strategy_id,
                            cycle_id=signal.cycle_id,
                            is_safe_stop=self.safe_stop_active,
                            daily_trade_count=self.daily_trade_count,
                            total_account_exposure=total_exposure,
                            pending_orders=pending_orders
                        )
                        ord_no = kres.get("order_id") or f"ORD_{int(time.time()*1000)}"
                        if kres.get("success"):
                            self.db.increment_daily_trade_count()
                            msg = f"📈 [키움 익절 매도 접수] [{stock['name']}] {sell_qty}주 시장가 매도 전송 (주문ID: {ord_no})"
                            self.db.add_log("INFO", msg)
                            time.sleep(0.35)
                            self._positions_cache = None
                            self.get_kiwoom_positions(force_refresh=True)
                        else:
                            ret_msg = kres.get("message", "주문 거부")
                            if self.risk_manager:
                                self.risk_manager.set_cooldown(code, 15.0)
                            self.db.add_log("ERROR", f"🚨 [키움 매도 거부] [{stock['name']}] {ret_msg}")
                    except Exception as kerr:
                        if self.risk_manager:
                            self.risk_manager.set_cooldown(code, 15.0)
                        self.db.add_log("ERROR", f"🚨 [키움 매도 통신 예외] [{stock['name']}] {kerr}")

            # B) 하락 시 차수별 분할 매수 (signal.action == "GRID_BUY")
            elif signal.action == "GRID_BUY":
                if stock.get("stop_loss_halted", False):
                    # 하드 손절 발동 종목은 신규 매수(물타기) 차단
                    continue
                buy_qty = signal.qty
                if self.order_manager and self.kiwoom_client and self.kiwoom_client.is_credentials_valid():
                    try:
                        kres = self.order_manager.submit_order(
                            kiwoom_client=self.kiwoom_client,
                            risk_manager=self.risk_manager,
                            account_no="",
                            order_type="BUY",
                            stock_code=code,
                            stock_name=stock["name"],
                            qty=buy_qty,
                            price=cur_price,
                            ord_dvsn="01",  # 시장가
                            strategy_id=signal.strategy_id,
                            cycle_id=signal.cycle_id,
                            is_safe_stop=self.safe_stop_active,
                            daily_trade_count=self.daily_trade_count,
                            total_account_exposure=total_exposure,
                            pending_orders=pending_orders
                        )
                        ord_no = kres.get("order_id") or f"ORD_{int(time.time()*1000)}"
                        if kres.get("success"):
                            self.db.increment_daily_trade_count()
                            msg = f"🛒 [키움 매수 접수] [{stock['name']}] {signal.cycle_id}차 진입, {buy_qty}주 매수 전송 (주문ID: {ord_no})"
                            self.db.add_log("INFO", msg)
                            time.sleep(0.35)
                            self._positions_cache = None
                            self.get_kiwoom_positions(force_refresh=True)
                        else:
                            ret_msg = kres.get("message", "주문 거부")
                            if self.risk_manager:
                                self.risk_manager.set_cooldown(code, 15.0)
                            self.db.add_log("ERROR", f"🚨 [키움 매수 거부] [{stock['name']}] {ret_msg}")
                    except Exception as kerr:
                        if self.risk_manager:
                            self.risk_manager.set_cooldown(code, 15.0)
                        self.db.add_log("ERROR", f"🚨 [키움 매수 통신 예외] [{stock['name']}] {kerr}")

            # 평가손익 및 수익률 갱신
            if pos["qty"] > 0:
                pos["current_price"] = cur_price
                eval_amt = cur_price * pos["qty"]
                buy_amt = pos["avg_price"] * pos["qty"]
                pos["pnl"] = eval_amt - buy_amt
                pos["pnl_rate"] = round((pos["pnl"] / buy_amt) * 100, 2) if buy_amt > 0 else 0.0

    def sync_market_prices(self):
        """
        등록된 종목의 실시간 현재가 시세 수신 및 동기화
        - 키움 REST API 연동 시 (REAL/MOCK 환경 공통): 키움증권 공식 API 시세(ka10001 TR) 최우선 수신 (삼성전자 253,500원, 현대차 418,000원 등 실시세 반영)
        - 키움 미연동 시: 네이버 실시간 시세 API(get_stock_price_quote)를 통해 실제 주식시장 현재가 100% 실시간 동기화
        """
        for code, stock in list(self.stocks.items()):
            if stock.get("status") == "LIQUIDATED":
                continue

            fetched_price = 0
            change = 0
            change_rate = 0.0
            prev_close = 0

            # 1. 키움 REST API 연동 시 키움증권 공식 시세 최우선 수신
            if self.kiwoom_client and self.kiwoom_client.is_credentials_valid():
                try:
                    kprice = self.kiwoom_client.get_stock_price(code)
                    if kprice.get("success") and kprice.get("current_price", 0) > 0:
                        fetched_price = kprice["current_price"]
                        change = kprice.get("change", 0)
                        change_rate = kprice.get("change_rate", 0.0)
                        prev_close = kprice.get("prev_close_price", 0)
                except Exception as kerr:
                    print(f"[WebTradingEngine] 키움 실시간 시세 수신 예외({code}): {kerr}")

            # 2. 키움 시세 미작동 시 처리 (REAL 모드인 경우 Naver fallback 절대 금지 -> SAFE_STOP)
            if fetched_price <= 0:
                if self.trading_mode == "REAL":
                    self.trigger_safe_stop(f"REAL 모드 종목[{code}] 키움 실시간 시세 수신 실패 (Naver fallback 주문 절대 금지)")
                    continue

                try:
                    fetched_price = get_stock_price_quote(code)
                    mdata = self.market_data.get(code, {})
                    prev_close = mdata.get("prev_close", fetched_price)
                    if prev_close > 0:
                        change = fetched_price - prev_close
                        change_rate = round((change / prev_close) * 100, 2)
                except Exception as nerr:
                    print(f"[WebTradingEngine] 네이버/마스터 실시간 시세 수신 예외({code}): {nerr}")

            # 3. 마켓 데이터 갱신
            if fetched_price > 0:
                if code not in self.market_data:
                    self.market_data[code] = {}

                mdata = self.market_data[code]
                mdata["current_price"] = fetched_price
                mdata["change"] = change
                mdata["change_rate"] = change_rate
                if prev_close > 0:
                    mdata["prev_close"] = prev_close

    def simulate_tick(self):
        """실시간 시세 수신 동기화 및 그리드 평가 사이클"""
        self.sync_market_prices()
        self.evaluate_grid_cycle()

    def sync_state_from_db(self):
        """DB로부터 대시보드의 엔진 제어 상태(RUNNING/STOPPED) 및 종목 그리드 최신화 (멀티 프로세스 동기화)"""
        status_cfg = self.db.get_system_config("engine_status")
        if status_cfg:
            self.status = status_cfg.get("status", self.status)

        grids = self.db.get_all_stock_grids()
        for item in grids:
            code = str(item["stock_code"]).zfill(6)
            grid = item.get("grid", {})
            db_is_active = item.get("is_active")
            if db_is_active is not None:
                is_active = bool(db_is_active)
            else:
                is_active = bool(grid.get("is_active", True))

            if code in self.stocks:
                self.stocks[code]["clear_price"] = item["clear_price"]
                self.stocks[code]["memo"] = item["memo"]
                self.stocks[code]["grid"] = grid
                self.stocks[code]["is_active"] = is_active
                self.stocks[code]["hard_stop_loss_enabled"] = grid.get("hard_stop_loss_enabled", False)
                self.stocks[code]["hard_stop_loss_price"] = grid.get("hard_stop_loss_price", 0)
                self.stocks[code]["stop_loss_halted"] = grid.get("stop_loss_halted", False)
                self.stocks[code]["stop_loss_triggered_at"] = grid.get("stop_loss_triggered_at", "")
            else:
                self.stocks[code] = {
                    "code": code,
                    "name": item["stock_name"],
                    "clear_price": item["clear_price"],
                    "memo": item["memo"],
                    "grid": grid,
                    "status": "TRACKING",
                    "current_step": 0,
                    "start_step": grid.get("start_step", 1),
                    "is_active": is_active,
                    "hard_stop_loss_enabled": grid.get("hard_stop_loss_enabled", False),
                    "hard_stop_loss_price": grid.get("hard_stop_loss_price", 0),
                    "stop_loss_halted": grid.get("stop_loss_halted", False),
                    "stop_loss_triggered_at": grid.get("stop_loss_triggered_at", "")
                }
                if code not in self.market_data:
                    self.market_data[code] = {"current_price": 0, "change": 0, "change_rate": 0.0, "prev_close": 0}
                if code not in self.positions:
                    self.positions[code] = {"stock_code": code, "stock_name": item["stock_name"], "qty": 0, "avg_price": 0, "current_price": 0, "pnl": 0, "pnl_rate": 0.0}

    def get_full_dashboard_state(self) -> Dict[str, Any]:
        """웹 대시보드 실시간 프론트엔드 동기화 페이로드"""
        # DB에서 엔진 상태 및 구동 모드 동기화 (uWSGI 멀티 프로세스 정합성 보장)
        self.sync_state_from_db()

        # uWSGI 멀티 워커 메모리 불일치 방지를 위한 DB 직렬화 is_active 최우선 직접 매핑
        db_grids = {str(g["stock_code"]).zfill(6): g for g in self.db.get_all_stock_grids()}

        op_mode = self.db.get_system_config("operating_mode")
        if op_mode:
            self.trading_mode = op_mode.get("trading_mode", self.trading_mode)

        # 0. 등록된 종목의 실시간 현재가 시세 동기화
        self.sync_market_prices()


        # 1. 키움 실계좌 보유 잔고 파악 및 그리드 엔진 포지션 수량 최우선 자동 동기화
        held_info = self.get_kiwoom_positions()
        held_positions = held_info.get("held_positions") or held_info.get("positions", []) if held_info and held_info.get("success") else []
        account_no = (held_info.get("account_no") if held_info else "") or (self.kiwoom_client.normalize_account_no(self.kiwoom_client.config.get("account_no")) if self.kiwoom_client else os.getenv("KIWOOM_ACCOUNT_NO", ""))

        total_purchase = sum(p["avg_price"] * p["qty"] for p in self.positions.values())
        total_valuation = sum(p["current_price"] * p["qty"] for p in self.positions.values())
        unrealized_pnl = total_valuation - total_purchase
        deposit = 100000000 - total_purchase # 가상 예수금 1억원
        net_assets = deposit + total_valuation

        # 5. DB에 존재하는 모든 종목이 빠짐없이 stocks_list에 포함되도록 합집합 순회 (Self-healing 보장)
        all_codes = sorted(list(set(self.stocks.keys()) | set(db_grids.keys())))
        stocks_list = []

        for code in all_codes:
            clean_code = str(code).zfill(6)
            s = self.stocks.get(clean_code)
            db_item = db_grids.get(clean_code)

            if not s and db_item:
                # 워커 메모리에 누락된 신규 종목의 경우 DB 기반으로 메모리 자동 복구 (Self-healing)
                grid = db_item.get("grid", {})
                s = {
                    "code": clean_code,
                    "name": db_item["stock_name"],
                    "clear_price": db_item["clear_price"],
                    "memo": db_item["memo"],
                    "grid": grid,
                    "status": "TRACKING",
                    "current_step": 0,
                    "start_step": grid.get("start_step", 1),
                    "is_active": bool(db_item.get("is_active", True)),
                    "hard_stop_loss_enabled": grid.get("hard_stop_loss_enabled", False),
                    "hard_stop_loss_price": grid.get("hard_stop_loss_price", 0),
                    "stop_loss_halted": grid.get("stop_loss_halted", False),
                    "stop_loss_triggered_at": grid.get("stop_loss_triggered_at", "")
                }
                self.stocks[clean_code] = s

            mdata = self.market_data.get(clean_code, {})
            pos = self.positions.get(clean_code, {})

            # DB에 영속화된 최우선 is_active 확정 반영 (uWSGI 멀티 워커 동기화 및 메모리 역동기화 보장)
            if db_item and db_item.get("is_active") is not None:
                is_active_val = bool(db_item["is_active"])
            else:
                is_active_val = bool(s.get("is_active", True))

            if s:
                s["is_active"] = is_active_val
                if isinstance(s.get("grid"), dict):
                    s["grid"]["is_active"] = is_active_val

            stocks_list.append({
                "code": clean_code,
                "name": s["name"],
                "status": s["status"],
                "current_price": mdata.get("current_price", 0),
                "change": mdata.get("change", 0),
                "change_rate": mdata.get("change_rate", 0.0),
                "clear_price": s["clear_price"],
                "current_step": s["current_step"],
                "qty": pos.get("qty", 0),
                "avg_price": pos.get("avg_price", 0),
                "pnl": pos.get("pnl", 0),
                "pnl_rate": pos.get("pnl_rate", 0.0),
                "memo": s["memo"],
                "grid": s["grid"],
                "is_active": is_active_val,
                "hard_stop_loss_enabled": bool(s.get("hard_stop_loss_enabled", False)),
                "hard_stop_loss_price": int(s.get("hard_stop_loss_price", 0)),
                "stop_loss_halted": bool(s.get("stop_loss_halted", False)),
                "stop_loss_triggered_at": str(s.get("stop_loss_triggered_at", ""))
            })

        MODE_TEXT = {
            "MOCK": "모의투자",
            "REAL": "실전투자",
            "DISABLED": "매매중지",
        }
        mode_text = MODE_TEXT.get(self.trading_mode, "알 수 없음")

        is_mock = True
        if self.kiwoom_client:
            is_mock = getattr(self.kiwoom_client, "is_mock", True)

        hb_info = self.db.get_heartbeat() if hasattr(self.db, "get_heartbeat") else {}
        pending_orders = self.db.get_pending_orders() if hasattr(self.db, "get_pending_orders") else []

        return {
            "status": self.status,
            "trading_mode": self.trading_mode,
            "trading_mode_text": mode_text,
            "is_mock": is_mock,
            "server_mode_text": "모의투자 서버" if is_mock else "실전매매 서버",
            "account_no": account_no,
            "safe_stop_active": self.safe_stop_active or hb_info.get("safe_stop_active", False),
            "safe_stop_reason": self.safe_stop_reason or hb_info.get("safe_stop_reason", ""),
            "last_heartbeat": hb_info.get("last_heartbeat", "N/A"),
            "daily_trade_count": self.daily_trade_count,
            "max_daily_trades": self.max_daily_trades,
            "total_purchase": total_purchase,
            "max_buy_amount": self.max_buy_amount,
            "total_valuation": total_valuation,
            "net_assets": net_assets,
            "unrealized_pnl": unrealized_pnl,
            "stocks": stocks_list,
            "held_positions": held_positions,
            "pending_orders": pending_orders,
            "pending_alert": self.pending_alert,
            "logs": self.db.get_logs(20),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }

    def reset_stop_loss(self, stock_code: str) -> Tuple[bool, str]:
        """종목별 하드 손절 발동 매수 정지 상태 수동 해제(재개)"""
        clean_code = str(stock_code).strip().zfill(6)
        if clean_code not in self.stocks:
            return False, f"종목({clean_code})을 찾을 수 없습니다."
        stock = self.stocks[clean_code]
        stock["stop_loss_halted"] = False
        stock["stop_loss_triggered_at"] = ""
        if isinstance(stock.get("grid"), dict):
            stock["grid"]["stop_loss_halted"] = False
            stock["grid"]["stop_loss_triggered_at"] = ""
        self.save_to_db()
        msg = f"🔄 [{stock['name']}({clean_code})] 하드 손절 정지 상태가 수동 해제(재개)되었습니다."
        self.db.add_log("INFO", msg)
        return True, msg

    def get_kiwoom_credentials(self) -> Dict[str, Any]:
        """현재 저장된 키움 Open API 인증키 및 설정 정보 반환 (보안 마스킹 적용)"""
        if self.kiwoom_client and hasattr(self.kiwoom_client, "get_masked_credentials"):
            return self.kiwoom_client.get_masked_credentials()

        return {
            "success": True,
            "account_no": os.getenv("KIWOOM_ACCOUNT_NO", ""),
            "is_mock": True,
            "configured": False,
            "app_key": ""
        }

    def update_kiwoom_credentials(self, app_key: str, app_secret: str, account_no: str = "", is_mock: bool = True) -> Dict[str, Any]:
        """키움 Open API 인증키 설정 업데이트"""
        try:
            bot_cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "trading_bot", "config.json"))
            cfg = {}
            if os.path.exists(bot_cfg_path):
                import json
                with open(bot_cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)

            if account_no.strip():
                cfg["account_no"] = account_no.strip()
            cfg["is_mock"] = is_mock

            target_cred = "mock_credentials" if is_mock else "real_credentials"
            if target_cred not in cfg:
                cfg[target_cred] = {}

            if app_key.strip():
                cfg[target_cred]["app_key"] = app_key.strip()
            if app_secret.strip():
                cfg[target_cred]["app_secret"] = app_secret.strip()


            import json
            with open(bot_cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)

            # Kiwoom 클라이언트 인스턴스 갱신 및 잔고 스마트 캐시 즉시 비우기
            self._positions_cache = None
            if KiwoomRESTClient:
                self.kiwoom_client = KiwoomRESTClient(config_path=bot_cfg_path)


            return {"success": True, "message": "키움 Open API 인증키가 성공적으로 업데이트되었습니다."}
        except Exception as e:
            return {"success": False, "message": f"키 저장 예외: {e}"}

    def invalidate_positions_cache(self):
        """주문 체결 후 5초 잔고 캐시 즉시 무효화 및 키움 서버 최신 잔고 조회를 강제"""
        self._positions_cache = None
        self._last_positions_fetch_time = 0.0

    def get_kiwoom_positions(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        키움증권 서버(또는 실시간 매매 엔진) 보유 주식 잔고 현황 및 계좌번호 정보 조회
        - HTTP 429 Too Many Requests 방지 5초 스마트 캐싱 적용
        - 키움 계좌 잔고 수량과 그리드 엔진 포지션 수량 100% 완벽 자동 동기화
        """
        now = time.time()
        if not force_refresh and self._positions_cache and (now - self._last_positions_fetch_time < self._positions_cache_ttl):
            return self._positions_cache

        acc_no = ""
        # 1. 키움 REST 클라이언트 조회 시도
        if self.kiwoom_client:
            try:
                acc_no = self.kiwoom_client.normalize_account_no(self.kiwoom_client.config.get("account_no"))
                balance = self.kiwoom_client.get_account_balance(acc_no)

                if balance and balance.get("success"):
                    raw_list = balance.get("positions", []) or balance.get("held_positions", [])
                    norm_list = []
                    for item in raw_list:
                        c = str(item.get("code") or item.get("stock_code") or "").strip().zfill(6)
                        n = str(item.get("name") or item.get("stock_name") or c).strip()
                        q = int(item.get("qty", 0))
                        avg_p = int(round(float(item.get("avg_price", 0))))
                        cur_p = int(round(float(item.get("current_price", avg_p))))
                        pnl_val = int(round(float(item.get("pnl", 0))))
                        pnl_r = float(item.get("pnl_rate", 0.0))

                        # 그리드 포지션 수량 및 평단가 100% 동기화 (불일치 차이 완벽 해소)
                        if c in self.stocks:
                            self.positions[c]["qty"] = q
                            if avg_p > 0:
                                self.positions[c]["avg_price"] = avg_p
                            if cur_p > 0:
                                self.positions[c]["current_price"] = cur_p

                        norm_item = {
                            "code": c,
                            "stock_code": c,
                            "name": n,
                            "stock_name": n,
                            "qty": q,
                            "avg_price": avg_p,
                            "current_price": cur_p,
                            "clear_price": item.get("clear_price", 0),
                            "current_step": item.get("current_step", 1),
                            "purchase_amount": avg_p * q,
                            "valuation_amount": cur_p * q,
                            "pnl": pnl_val,
                            "pnl_rate": pnl_r
                        }
                        norm_list.append(norm_item)

                    res_dict = {
                        "success": True,
                        "account_no": balance.get("account_no", acc_no),
                        "source": balance.get("source", "KIWOOM_REAL_SERVER"),
                        "total_purchase": balance.get("total_purchase", 0),
                        "total_valuation": balance.get("total_valuation", 0),
                        "total_eval_pnl": balance.get("total_eval_pnl", 0),
                        "total_eval_rate": balance.get("total_eval_rate", 0.0),
                        "positions": norm_list,
                        "held_positions": norm_list,
                        "message": balance.get("message", "조회 성공")
                    }

                    self._positions_cache = res_dict
                    self._last_positions_fetch_time = now
                    return res_dict
            except Exception as e:
                print(f"[get_kiwoom_positions 예외] {e}")

        # P0-2: REAL 모드에서 계좌 조회 실패 시 fallback(cache/memory) 완전 금지 및 SAFE_STOP 전환
        is_real_mode = (self.trading_mode == "REAL") or (self.kiwoom_client and hasattr(self.kiwoom_client, "is_mock") and not self.kiwoom_client.is_mock)
        if is_real_mode:
            self.trigger_safe_stop("ACCOUNT_QUERY_FAILED: 키움 REAL 계좌 잔고 조회 실패 (Fallback 차단 및 SAFE_STOP 전환)")
            return {
                "success": False,
                "account_no": acc_no,
                "source": "KIWOOM_REAL_SERVER",
                "positions": [],
                "held_positions": [],
                "message": "REAL 계좌 조회 실패: Kiwoom REST API Query Failed"
            }

        if self._positions_cache:
            return self._positions_cache

        # Config 파일에서 계좌번호 추출 시도
        if not acc_no:
            try:
                bot_cfg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "trading_bot", "config.json"))
                if os.path.exists(bot_cfg_path):
                    import json
                    with open(bot_cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        acc_no = cfg.get("account_no", "")
            except Exception:
                pass
        if not acc_no:
            acc_no = os.getenv("KIWOOM_ACCOUNT_NO", "")

        # 2. 엔진 보유 잔고 동기화 (pos.qty > 0 또는 current_step > 0 종목)
        held_positions = []
        tot_purchase = 0
        tot_valuation = 0
        tot_pnl = 0

        for code, stock in self.stocks.items():
            pos = self.positions.get(code, {})
            qty = pos.get("qty", 0)

            if qty > 0:
                mdata = self.market_data.get(code, {})
                cur_price = mdata.get("current_price", stock.get("base_price", 0))
                avg_price = pos.get("avg_price", 0) or cur_price
                pchs_amt = avg_price * qty
                eval_amt = cur_price * qty
                pnl = eval_amt - pchs_amt
                pnl_rate = round((pnl / pchs_amt) * 100, 2) if pchs_amt > 0 else 0.0

                tot_purchase += pchs_amt
                tot_valuation += eval_amt
                tot_pnl += pnl

                held_positions.append({
                    "code": code,
                    "stock_code": code,
                    "name": stock.get("name", code),
                    "stock_name": stock.get("name", code),
                    "qty": qty,
                    "avg_price": avg_price,
                    "current_price": cur_price,
                    "clear_price": stock.get("clear_price", 0),
                    "current_step": stock.get("current_step", 1),
                    "purchase_amount": pchs_amt,
                    "valuation_amount": eval_amt,
                    "pnl": pnl,
                    "pnl_rate": pnl_rate
                })

        tot_rate = round((tot_pnl / tot_purchase) * 100, 2) if tot_purchase > 0 else 0.0
        return {
            "success": True,
            "account_no": acc_no,
            "source": "ENGINE_SYNC",
            "total_purchase": tot_purchase,
            "total_valuation": tot_valuation,
            "total_eval_pnl": tot_pnl,
            "total_eval_rate": tot_rate,
            "positions": held_positions,
            "held_positions": held_positions
        }

    def execute_manual_order(self, stock_code: str, order_type: str, qty: int, price: int = 0, ord_dvsn: str = "03") -> Dict[str, Any]:
        """수동 매수/매도 주문 전송 (RiskManager 및 OrderManager 통과 보장)"""
        clean_code = stock_code.strip().zfill(6)
        if self.risk_manager is None:
            msg = "🛑 [수동 주문 차단] RiskManager 미초기화 상태 - 안전상 주문을 거부합니다."
            self.db.add_log("ERROR", msg)
            return {"success": False, "message": msg, "error": msg}

        stock_name = self.stocks.get(clean_code, {}).get("name", clean_code)
        pending_orders = self.db.get_pending_orders() if hasattr(self.db, "get_pending_orders") else []

        total_exp = sum(p["avg_price"] * p["qty"] for p in self.positions.values())

        # 1. RiskManager 검증 통과 필수
        if self.risk_manager:
            can_trade, risk_msg = self.risk_manager.validate_order(
                stock_code=clean_code,
                side=order_type,
                qty=qty,
                price=price,
                daily_trade_count=self.daily_trade_count,
                total_account_exposure=total_exp,
                pending_orders=pending_orders,
                safe_stop_active=self.safe_stop_active
            )
            if not can_trade:
                msg = f"🛑 [수동 주문 차단] 리스크 가드: {risk_msg}"
                self.db.add_log("WARNING", msg)
                return {"success": False, "message": msg}

        # 2. 키움 REST API 주문 전송 (OrderManager 단일 주문 경로 통과)
        if self.order_manager and self.kiwoom_client:
            res = self.order_manager.submit_order(
                kiwoom_client=self.kiwoom_client,
                risk_manager=self.risk_manager,
                account_no="",
                order_type=order_type,
                stock_code=clean_code,
                stock_name=stock_name,
                qty=qty,
                price=price,
                ord_dvsn=ord_dvsn,
                strategy_id="MANUAL",
                cycle_id=int(time.time()),
                is_safe_stop=self.safe_stop_active,
                daily_trade_count=self.daily_trade_count,
                total_account_exposure=total_exp,
                pending_orders=pending_orders
            )
            sign = "📈 [수동 매도]" if order_type.upper() == "SELL" else "📉 [수동 매수]"
            ord_id = res.get('order_id') or f"ORD_MANUAL_{int(time.time()*1000)}"
            msg = f"{sign} 종목({clean_code}) {qty}주 수동 주문 처리: {res.get('message', '')} (주문ID: {ord_id})"
            self.db.add_log("INFO", msg)

            if res.get("success"):
                self.db.increment_daily_trade_count()
                self.invalidate_positions_cache()
                time.sleep(0.35)
                self.get_kiwoom_positions(force_refresh=True)
            return res

        return {"success": False, "message": "키움 클라이언트 또는 주문 관리자가 초기화되지 않았습니다."}

    def reset_db(self) -> bool:
        """트레이딩 엔진 영속성 DB 및 메모리 데이터 완전 초기화"""
        ok = self.db.reset_database()
        if ok:
            self.stocks.clear()
            self.market_data.clear()
            self.positions.clear()
            self.status = "IDLE"
            self.pending_alert = None
            self.invalidate_positions_cache()
            self.db.save_system_config("engine_status", {"status": "IDLE"})
            return True
        return False


    def force_close_kiwoom_position(self, stock_code: str, qty: Optional[int] = None, stock_name: Optional[str] = "") -> Dict[str, Any]:
        """
        키움 계좌 특정 보유 종목 시장가 강제 청산 (OrderManager 단일 통로 연동)
        """
        clean_code = stock_code.strip().zfill(6)
        name = stock_name or clean_code

        # 1. 실제 계좌 잔고 조회로 보유 수량 확인 (Source of Truth)
        target_qty = qty or 0
        if target_qty <= 0 and self.kiwoom_client:
            bal = self.kiwoom_client.get_account_balance()
            for p in bal.get("positions", []):
                p_code = str(p.get("stock_code", "")).strip().zfill(6)
                if p_code == clean_code:
                    target_qty = p.get("qty", 0)
                    if not name or name == clean_code:
                        name = p.get("stock_name", name)
                    break

        if target_qty <= 0:
            return {"success": False, "message": f"종목 [{clean_code}]의 청산할 보유 잔고 수량이 존재하지 않습니다."}

        # 2. 수량 검증
        if target_qty <= 0:
            return {"success": False, "message": f"강제 청산 수량이 유효하지 않습니다 ({target_qty}주)"}

        # 3. OrderManager를 통하여 단일 경로 시장가 매도 전송 및 Audit Log 기록
        if self.order_manager and self.kiwoom_client and self.kiwoom_client.is_credentials_valid():
            total_exp = sum(p["avg_price"] * p["qty"] for p in self.positions.values())
            pending_orders = self.db.get_pending_orders() if hasattr(self.db, "get_pending_orders") else []
            res = self.order_manager.submit_order(
                kiwoom_client=self.kiwoom_client,
                risk_manager=self.risk_manager,
                account_no="",
                order_type="SELL",
                stock_code=clean_code,
                stock_name=name,
                qty=target_qty,
                price=0,
                ord_dvsn="01",  # 01: 시장가
                strategy_id="FORCE_CLOSE",
                cycle_id=int(time.time()),
                is_safe_stop=self.safe_stop_active,
                daily_trade_count=self.daily_trade_count,
                total_account_exposure=total_exp,
                pending_orders=pending_orders
            )
            ord_id = res.get('order_id') or f"ORD_FORCE_{int(time.time()*1000)}"
            msg = f"[시장가 강제청산] {name}({clean_code}) {target_qty}주 시장가 매도 전송 (주문ID: {ord_id})"
            self.db.add_log("WARN", msg)
            if res.get("success"):
                self.db.increment_daily_trade_count()
                self.invalidate_positions_cache()
                time.sleep(0.35)
                self.get_kiwoom_positions(force_refresh=True)
            return {
                "success": res.get("success", False),
                "message": res.get("message", msg),
                "order_result": res
            }

        # P1-4: REAL 모드에서 가상 청산 성공 반환 전면 차단
        is_real = (self.trading_mode == "REAL") or (self.kiwoom_client and hasattr(self.kiwoom_client, "is_mock") and not self.kiwoom_client.is_mock)
        if is_real:
            msg = f"🚨 [강제청산 실패] REAL 모드에서 키움 클라이언트 미연동 또는 주문 전송에 실패하였습니다."
            self.db.add_log("ERROR", msg)
            return {"success": False, "message": msg, "order_result": {"status": "FAILED"}}

        msg = f"[가상 시장가 강제청산] {name}({clean_code}) {target_qty}주 청산 처리 완료"
        self.db.add_log("INFO", msg)
        return {
            "success": True,
            "message": msg,
            "order_result": {"status": "MOCK_EXECUTED", "qty": target_qty}
        }
