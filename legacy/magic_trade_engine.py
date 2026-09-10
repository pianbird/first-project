"""
================================================================================
[LEGACY - DO NOT EXECUTE DIRECTLY]
본 모듈은 구버전 그리드 로직 레거시 코드입니다.
실전 자동매매 및 대시보드 상태 관리는 `web_app/backend/engine.py` 및 `OrderManager`를
단일 실행 경로(Single Order Path)로 사용합니다.
================================================================================
"""
import time
import json
from typing import Dict, Any, List, Optional, Tuple

from grid_strategy import GridConfig, GridStep, GridState, generate_auto_grid_config
from trading_rules import normalize_price
from db_manager import DBManager
from kiwoom_client import KiwoomRESTClient
from order_guard import OrderGuard

class MagicTradeEngine:
    """
    그리드 기반 무한 순환매매(MagicTrader) 전략 핵심 매수/매도 엔진
    
    1. 차수별 그리드 매매 (분할 매수, 차수별 부분 익절, 최종 전량 청산)
    2. 수량 자동 보정 알고리즘 (주문 필요 수량 = 목표 수량 - 실제 보유 수량)
    3. 특수 리스크 방어 (무한 물타기 현금 재순환 모드 & 블랙스완 5일선 2회 안착 필터)
    """

    def __init__(self):
        self.grid_configs: Dict[str, GridConfig] = {}
        self.grid_states: Dict[str, GridState] = {}

    def register_grid_config(self, config: GridConfig, db: Optional[DBManager] = None):
        """종목 그리드 설정 등록"""
        self.grid_configs[config.stock_code] = config
        if config.stock_code not in self.grid_states:
            self.grid_states[config.stock_code] = GridState(stock_code=config.stock_code)
        if db:
            db.save_grid_config(config.to_dict())

    def get_grid_state(self, stock_code: str) -> GridState:
        return self.grid_states.get(stock_code, GridState(stock_code=stock_code))

    def evaluate_blackswan_filter(self, stock_code: str, daily_candles: List[Dict[str, Any]]) -> bool:
        """
        블랙스완 방어: 급락 시 매수 정지 후, 종가가 5일 이동평균선 위에 2회 연속 안착했는지 검사
        :return: 매수 가능 여부 (True: 매수 가능, False: 매수 금지)
        """
        state = self.get_grid_state(stock_code)
        if not state.is_blackswan_halt:
            return True # 블랙스완 상태가 아니면 매수 가능

        if len(daily_candles) < 6:
            return False # 봉 데이터 부족 시 안전하게 매수 금지

        # 최근 5일 이동평균선(MA5) 안착 검사 (최근 2영업일)
        consecutive_cnt = 0
        for i in range(2): # i=0: 최근영업일, i=1: 전영업일
            window = daily_candles[i:i+5]
            if len(window) < 5:
                break
            ma5 = sum(c.get("close_price", 0) for c in window) / 5.0
            close_price = window[0].get("close_price", 0)

            if close_price >= ma5:
                consecutive_cnt += 1
            else:
                consecutive_cnt = 0

        state.consecutive_ma5_above = consecutive_cnt
        if consecutive_cnt >= 2:
            state.is_blackswan_halt = False # 2회 연속 5일선 안착 시 블랙스완 상태 해제
            return True

        return False

    def trigger_blackswan_halt(self, stock_code: str, db: Optional[DBManager] = None):
        """종목 블랙스완 매수 정지 플래그 활성화"""
        state = self.get_grid_state(stock_code)
        state.is_blackswan_halt = True
        state.consecutive_ma5_above = 0
        msg = f"[MagicTrader] 종목 {stock_code} 블랙스완 매수 정지 필터 활성화 (5일선 2회 연속 안착까지 매수 대기)"
        if db:
            db.log_event("WARNING", msg)
        print(msg)

    def evaluate_stock(
        self,
        stock_code: str,
        current_price: int,
        daily_candles: List[Dict[str, Any]],
        actual_position_qty: int,
        db: DBManager,
        kiwoom: KiwoomRESTClient,
        order_guard: OrderGuard
    ) -> Dict[str, Any]:
        """
        단일 종목 실시간 그리드 매매 상태 평가 및 수량 보정 주문 전송
        """
        clean_code = stock_code.strip()
        config = self.grid_configs.get(clean_code)
        if not config:
            return {"action": "NONE", "reason": "그리드 전략 설정이 없습니다."}

        state = self.get_grid_state(clean_code)
        state.actual_qty = actual_position_qty
        state.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")

        # Risk context calculation for OrderManager
        active_pos = db.get_active_positions() if hasattr(db, "get_active_positions") else []
        tot_exp = sum(p.get("avg_price", 0) * p.get("qty", 0) for p in active_pos)
        p_orders = db.get_pending_orders() if hasattr(db, "get_pending_orders") else []
        dt_count = db.get_daily_trade_count() if hasattr(db, "get_daily_trade_count") else 0

        # -------------------------------------------------------------
        # 1. 최종 청산 검사 (현재가가 최종 청산가 이상일 경우 전량 매도)
        # -------------------------------------------------------------
        if current_price >= config.exit_price and actual_position_qty > 0:
            can_trade, reason = order_guard.can_trade(clean_code, "SELL", db, active_pos)
            if can_trade:
                if hasattr(kiwoom, "submit_order"):
                    order_res = kiwoom.submit_order(kiwoom_client=kiwoom, risk_manager=None, account_no="", order_type="SELL", stock_code=clean_code, stock_name=config.stock_name, qty=actual_position_qty, price=0, ord_dvsn="01", daily_trade_count=dt_count, total_account_exposure=tot_exp, pending_orders=p_orders)
                elif hasattr(db, "order_manager") and db.order_manager:
                    order_res = db.order_manager.submit_order(kiwoom_client=kiwoom, risk_manager=None, account_no="", order_type="SELL", stock_code=clean_code, stock_name=config.stock_name, qty=actual_position_qty, price=0, ord_dvsn="01", daily_trade_count=dt_count, total_account_exposure=tot_exp, pending_orders=p_orders)
                else:
                    order_res = {"success": False, "message": "Legacy Engine: place_order direct call blocked. Use OrderManager."}

                if order_res.get("success"):
                    db.record_order(order_res)
                    order_guard.record_trade_execution(clean_code)
                    db.remove_position(clean_code)
                    state.current_step = 0
                    state.target_qty = 0
                    msg = f"🎉 [최종 완벽 청산] {config.stock_name}({clean_code}) 청산가({config.exit_price:,}원) 도달! {actual_position_qty}주 전량 매도 완료"
                    db.log_event("INFO", msg)
                    db.save_grid_state(clean_code, state)
                    return {"action": "FULL_EXIT", "order": order_res, "message": msg}

        # -------------------------------------------------------------
        # 2. 차수 판별 (현재가 기준 도달한 차수 찾기)
        # -------------------------------------------------------------
        target_step = 0
        target_qty = 0

        # 가격이 낮은 차수부터 탐색하여 터치한 차수 결정
        for s in config.grid_steps:
            if current_price <= s.price:
                target_step = s.step
                target_qty = s.target_total_qty

        if config.grid_steps and current_price > config.grid_steps[0].price and current_price < config.exit_price and actual_position_qty > 0:
            target_step = 1
            target_qty = config.grid_steps[0].target_total_qty

        state.current_step = target_step
        state.target_qty = target_qty

        needed_qty = target_qty - actual_position_qty

        # A) 보유 수량이 초과 상태인 경우 (반등 시 차수별 부분 익절 매도)
        if needed_qty < 0:
            sell_qty = abs(needed_qty)
            can_trade, reason = order_guard.can_trade(clean_code, "SELL", db, active_pos)
            if can_trade:
                if hasattr(db, "order_manager") and db.order_manager:
                    order_res = db.order_manager.submit_order(kiwoom_client=kiwoom, risk_manager=None, account_no="", order_type="SELL", stock_code=clean_code, stock_name=config.stock_name, qty=sell_qty, price=0, ord_dvsn="03", daily_trade_count=dt_count, total_account_exposure=tot_exp, pending_orders=p_orders)
                else:
                    order_res = {"success": False, "message": "Legacy Engine: place_order direct call blocked. Use OrderManager."}

                if order_res.get("success"):
                    db.record_order(order_res)
                    order_guard.record_trade_execution(clean_code)
                    msg = f"📈 [차수 익절 매도] {config.stock_name}({clean_code}) {sell_qty}주 부분 매도 체결 (남은 목표: {target_qty}주)"
                    db.log_event("INFO", msg)
                    db.save_grid_state(clean_code, state)
                    return {"action": "PARTIAL_SELL", "order": order_res, "message": msg}

        # B) 보유 수량이 부족한 경우 (하락 시 차수별 분할 매수)
        elif needed_qty > 0:
            can_buy_blackswan = self.evaluate_blackswan_filter(clean_code, daily_candles)
            if not can_buy_blackswan:
                msg = f"🛑 [블랙스완 매수 대기] {config.stock_name}({clean_code}) 종가 5일선 2회 연속 안착 전까지 매수 일시 정지"
                db.save_grid_state(clean_code, state)
                return {"action": "BLACKSWAN_WAIT", "message": msg}

            if target_step >= config.max_steps:
                state.recycle_mode = True
                msg = f"🔄 [무한 물타기 모드] {config.stock_name}({clean_code}) 최대 차수({config.max_steps}차) 도달 - 추가 자금 없이 평단 절감 모드 구동"
                db.log_event("WARNING", msg)

            can_trade, reason = order_guard.can_trade(clean_code, "BUY", db, active_pos)
            if can_trade:
                if hasattr(db, "order_manager") and db.order_manager:
                    order_res = db.order_manager.submit_order(kiwoom_client=kiwoom, risk_manager=None, account_no="", order_type="BUY", stock_code=clean_code, stock_name=config.stock_name, qty=needed_qty, price=0, ord_dvsn="03", daily_trade_count=dt_count, total_account_exposure=tot_exp, pending_orders=p_orders)
                else:
                    order_res = {"success": False, "message": "Legacy Engine: place_order direct call blocked. Use OrderManager."}
                if order_res.get("success"):
                    db.record_order(order_res)
                    order_guard.record_trade_execution(clean_code)
                    msg = f"📉 [차수 분할 매수] {config.stock_name}({clean_code}) {target_step}차 진입, {needed_qty}주 추가 매수 (목표: {target_qty}주)"
                    db.log_event("INFO", msg)
                    db.save_grid_state(clean_code, state)
                    return {"action": "GRID_BUY", "order": order_res, "message": msg}

        db.save_grid_state(clean_code, state)
        return {"action": "HOLD", "current_step": target_step, "target_qty": target_qty, "actual_qty": actual_position_qty}
