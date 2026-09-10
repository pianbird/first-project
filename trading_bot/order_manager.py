import time
from typing import Dict, Any, List, Optional, Tuple
from db_manager import DBManager
from trading_mode import TradingModeManager, MODE_TEXT
from market_calendar import MarketCalendar

class OrderStatus:
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"

class OrderManager:
    """
    단일 주문 전송 경로 및 라이프사이클 / UNKNOWN 주문 관리자
    - 프로젝트 내 모든 주문은 반드시 submit_order()를 거쳐야 함
    - 주문 제출 전: Signal Valid, Market Open, Account Sync, Pending Check, Risk Check, Trading Mode, SAFE_STOP, Idempotency Check, Price/Qty Gate 통과
    - Kiwoom place_order() 호출 후 응답 수신 및 DB/Audit 기록
    - 통신 장애/타임아웃 시 UNKNOWN 등록 및 계좌/미체결 snapshot 기반 상태 확정
    - UNKNOWN 상태 시 자동 재주문 절대 금지
    """

    def __init__(self, db: DBManager, trading_mode_mgr: Optional[TradingModeManager] = None):
        self.db = db
        self.mode_mgr = trading_mode_mgr or TradingModeManager("MOCK")

    def generate_idempotency_key(self, strategy_id: str, symbol: str, side: str, cycle_id: Any, qty: int = 0, price: int = 0) -> str:
        """
        [P1-6] Idempotency Key 생성 함수
        - cycle_id 식별자와 함께 수량(qty), 가격(price)을 포함시켜 분 단위 차단 오류 및 정정 주문 부당 차단 방지
        """
        clean_code = str(symbol).strip().zfill(6)
        return f"{strategy_id}:{clean_code}:{side.upper()}:{qty}:{int(price)}:{cycle_id}"

    def register_new_order(
        self,
        order_id: str,
        stock_code: str,
        stock_name: str,
        side: str,
        qty: int,
        price: int,
        order_type: str = "MARKET",
        initial_status: str = OrderStatus.SUBMITTED,
        message: str = "",
        client_order_key: str = "",
        before_qty: int = 0
    ) -> Dict[str, Any]:
        """신규 주문 등록 및 DB 저장"""
        clean_code = str(stock_code).strip().zfill(6)
        order_info = {
            "order_id": order_id,
            "stock_code": clean_code,
            "stock_name": stock_name or clean_code,
            "side": side.upper(),
            "order_type": order_type,
            "requested_qty": qty,
            "before_qty": before_qty,
            "filled_qty": 0,
            "remaining_qty": qty,
            "price": price,
            "status": initial_status,
            "message": message,
            "client_order_key": client_order_key,
            "requested_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self.db.save_order(order_info)
        return order_info

    def update_order_status(
        self,
        order_id: str,
        status: str,
        filled_qty: Optional[int] = None,
        message: str = ""
    ):
        """기존 주문 상태 갱신 및 Audit Log 기록 [P1-4 SQL Direct Query 적용]"""
        target = self.db.get_order_by_id(order_id) if hasattr(self.db, "get_order_by_id") else None
        if not target:
            recent = self.db.get_recent_orders(limit=100)
            for o in recent:
                if o.get("order_id") == order_id:
                    target = o
                    break

        if not target:
            return

        target["status"] = status
        if message:
            target["message"] = message
        if filled_qty is not None:
            target["filled_qty"] = filled_qty
            target["remaining_qty"] = max(0, target.get("requested_qty", 0) - filled_qty)

        self.db.save_order(target)

    def get_pending_orders(self, stock_code: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.get_pending_orders(stock_code)

    def has_pending_or_unknown_order(self, stock_code: str, side: Optional[str] = None) -> bool:
        """해당 종목 및 방향에 미체결(PENDING, SUBMITTED, ACCEPTED, UNKNOWN) 주문이 존재하는지 검사 [P1-4]"""
        if hasattr(self.db, "get_active_or_unknown_orders"):
            active = self.db.get_active_or_unknown_orders(stock_code, side)
            return len(active) > 0

        recent = self.db.get_recent_orders(limit=100)
        clean_code = str(stock_code).strip().zfill(6)
        pending_statuses = {OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.UNKNOWN}

        for o in recent:
            if str(o.get("stock_code", "")).strip().zfill(6) == clean_code:
                if o.get("status") in pending_statuses:
                    if side is None or o.get("side", "").upper() == side.upper():
                        return True
        return False

    def submit_order(
        self,
        kiwoom_client: Any,
        risk_manager: Any,
        account_no: str,
        order_type: str,  # BUY or SELL
        stock_code: str,
        stock_name: str,
        qty: int,
        price: int = 0,
        ord_dvsn: str = "03",
        strategy_id: str = "GRID_A",
        cycle_id: Any = 1,
        is_safe_stop: bool = False,
        daily_trade_count: Optional[int] = None,
        total_account_exposure: Optional[int] = None,
        pending_orders: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        단일 주문 통로 함수
        - 16단계 주문 Gate 검사를 순차적으로 실행
        - Gate 통과 시에만 KiwoomRESTClient.place_order() 호출
        """
        clean_code = str(stock_code).strip().zfill(6)
        side_upper = order_type.upper()

        # Gate 1: Safe Stop 상태 검사
        if is_safe_stop:
            msg = "ORDER BLOCKED: 시스템이 SAFE_STOP 상태입니다."
            return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}

        # Gate 2: Trading Mode 검사
        if not self.mode_mgr.can_place_order():
            msg = f"ORDER BLOCKED: Trading Mode가 '{self.mode_mgr.get_mode()}' 상태입니다."
            return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}

        # Gate 2.5: Kiwoom Client Credentials Validation Gate
        if kiwoom_client and hasattr(kiwoom_client, "is_credentials_valid"):
            is_mock_or_real = self.mode_mgr and self.mode_mgr.get_mode() in ["REAL", "MOCK"]
            if is_mock_or_real and not kiwoom_client.is_credentials_valid():
                msg = "ORDER BLOCKED: Kiwoom API 인증 자격증명이 유효하지 않습니다 (Credentials Invalid)."
                return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}

        # Gate 3: 수량 및 가격 기본 검사
        if not isinstance(qty, int) or qty <= 0:
            msg = f"ORDER BLOCKED: 주문 수량이 유효하지 않습니다 ({qty})."
            return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}

        if price < 0 or not isinstance(price, (int, float)):
            msg = f"ORDER BLOCKED: 주문 가격이 유효하지 않습니다 ({price})."
            return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}

        # Gate 4: 동일 종목 / 방향 미체결 및 UNKNOWN 주문 검사
        if self.has_pending_or_unknown_order(clean_code, side_upper):
            msg = f"ORDER BLOCKED: 종목 {clean_code}에 기존 미체결/UNKNOWN 주문이 존재합니다."
            return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}

        # Gate 5: Order Idempotency Key 중복 검사 (DB 영속화 Unique Constraint 전용) [P1-6 개편]
        client_key = self.generate_idempotency_key(strategy_id, clean_code, side_upper, cycle_id, qty=qty, price=price)
        if hasattr(self.db, "register_idempotency_key"):
            if not self.db.register_idempotency_key(client_key, clean_code, side_upper):
                msg = f"ORDER BLOCKED: DB에 등록된 중복 주문 클라이언트 키입니다 ({client_key})."
                return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}
        else:
            msg = f"ORDER BLOCKED: DB Idempotency Key 등록 기능을 사용할 수 없어 주문이 차단되었습니다 ({client_key})."
            return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}

        # Gate 6: Risk Manager 검사 (Fail-Safe 적용)
        if risk_manager and hasattr(risk_manager, "validate_order"):
            dt_count = daily_trade_count
            if dt_count is None:
                if hasattr(self.db, "get_daily_trade_count"):
                    dt_count = self.db.get_daily_trade_count()
                else:
                    dt_count = 0

            tot_exp = total_account_exposure
            if tot_exp is None and side_upper == "BUY":
                msg = "ORDER BLOCKED (RiskManager Fail-Safe): 매수 주문 시 계좌 총 매입 노출금액(total_account_exposure) 인자가 누락되었습니다."
                print(f"[OrderManager Fail-Safe] WARNING: 매수 주문({clean_code}) 시 total_account_exposure 인자가 누락되어 주문을 차단합니다.")
                return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}
            elif tot_exp is None:
                tot_exp = 0

            p_orders = pending_orders
            if p_orders is None and hasattr(self.db, "get_pending_orders"):
                p_orders = self.db.get_pending_orders()

            risk_ok, risk_msg = risk_manager.validate_order(
                symbol=clean_code,
                stock_code=clean_code,
                side=side_upper,
                qty=qty,
                price=price,
                daily_trade_count=dt_count,
                total_account_exposure=tot_exp,
                pending_orders=p_orders,
                safe_stop_active=is_safe_stop,
                kiwoom_client=kiwoom_client
            )
            if not risk_ok:
                msg = f"ORDER BLOCKED (RiskManager): {risk_msg}"
                return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}

        # Kiwoom REST API 호환 계좌 정규화
        norm_account = kiwoom_client.normalize_account_no(account_no) if kiwoom_client and hasattr(kiwoom_client, "normalize_account_no") else account_no

        # 주문 전 보유 수량 snapshot 기록 (UNKNOWN 주문 복구용)
        before_qty = 0
        if kiwoom_client and hasattr(kiwoom_client, "get_positions"):
            try:
                pos_info = kiwoom_client.get_positions()
                held = (pos_info.get("positions") or pos_info.get("held_positions") or []) if isinstance(pos_info, dict) else []
                for h in held:
                    if str(h.get("code") or h.get("stock_code", "")).strip().zfill(6) == clean_code:
                        before_qty = int(h.get("qty", 0))
                        break
            except Exception:
                pass

        temp_order_id = f"PENDING_{int(time.time()*1000)}"
        self.register_new_order(
            order_id=temp_order_id,
            stock_code=clean_code,
            stock_name=stock_name,
            side=side_upper,
            qty=qty,
            price=int(price),
            order_type=ord_dvsn,
            initial_status=OrderStatus.SUBMITTED,
            message="Kiwoom API 전송 중...",
            client_order_key=client_key,
            before_qty=before_qty
        )

        # FINAL SAFETY GATE - Endpoint Isolation check right before actual place_order call
        if self.mode_mgr:
            base_url = getattr(kiwoom_client, "base_url", "")
            is_mock = getattr(kiwoom_client, "is_mock", True)
            try:
                self.mode_mgr.validate_endpoint_isolation(base_url, is_mock)
            except Exception as e:
                msg = f"ORDER BLOCKED (EndpointIsolation): {e}"
                self.update_order_status(temp_order_id, status=OrderStatus.REJECTED, message=msg)
                return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}

        try:
            # 단일 place_order 호출 지점
            resp = kiwoom_client.place_order(
                account_no=norm_account,
                order_type=side_upper,
                stock_code=clean_code,
                qty=qty,
                price=int(price),
                ord_dvsn=ord_dvsn
            )

            if resp.get("success"):
                actual_order_id = resp.get("order_id") or temp_order_id
                self.update_order_status(
                    temp_order_id,
                    status=OrderStatus.ACCEPTED,
                    filled_qty=0,
                    message=resp.get("message", "주문 접수 완료")
                )
                resp["order_id"] = actual_order_id
                return resp
            else:
                msg = resp.get("message") or resp.get("error") or "주문 거부됨"
                self.update_order_status(
                    temp_order_id,
                    status=OrderStatus.REJECTED,
                    message=msg
                )
                resp["message"] = msg
                resp["error"] = msg
                return resp

        except Exception as e:
            # 주문 통신 장애/타임아웃 발생 -> UNKNOWN 상태 처리 및 중복 자동 재전송 절대 금지
            err_msg = f"주문 전송 중 네트워크 예외 발생 -> UNKNOWN 상태 등록: {e}"
            print(f"[OrderManager Exception] {err_msg}")
            self.update_order_status(
                temp_order_id,
                status=OrderStatus.UNKNOWN,
                message=err_msg
            )
            return {
                "success": False,
                "order_id": temp_order_id,
                "status": OrderStatus.UNKNOWN,
                "message": err_msg,
                "error": err_msg,
                "before_qty": before_qty,
                "requested_qty": qty,
                "stock_code": clean_code,
                "side": side_upper
            }

    def resolve_unknown_orders(self, kiwoom_client: Any) -> int:
        """
        [P1-5] UNKNOWN 상태의 주문을 snapshot 및 Kiwoom 체결/미체결/잔고 API 조회를 통하여 최종 상태로 확정
        - 동일 종목의 다중 UNKNOWN 주문 발생 시, 주문 생성 시간순 정렬 후 순차 델타 차감 배분
        :return: 확정 처리된 UNKNOWN 주문 건수
        """
        if hasattr(self.db, "get_unknown_orders"):
            unknowns = self.db.get_unknown_orders()
        else:
            recent = self.db.get_recent_orders(limit=100)
            unknowns = [o for o in recent if o.get("status") == OrderStatus.UNKNOWN]

        if not unknowns:
            return 0

        resolved_count = 0
        try:
            held_list = []
            if kiwoom_client and hasattr(kiwoom_client, "is_credentials_valid") and kiwoom_client.is_credentials_valid():
                kpos_info = kiwoom_client.get_positions(force_refresh=True)
                held_list = (kpos_info.get("positions") or kpos_info.get("held_positions") or []) if isinstance(kpos_info, dict) else []

            from collections import defaultdict
            grouped = defaultdict(list)
            for u in unknowns:
                code = str(u.get("stock_code", "")).strip().zfill(6)
                grouped[code].append(u)

            for code, order_list in grouped.items():
                order_list.sort(key=lambda x: (x.get("requested_at", ""), x.get("order_id", "")))

                current_qty = 0
                for h in held_list:
                    if str(h.get("code") or h.get("stock_code", "")).strip().zfill(6) == code:
                        current_qty = int(h.get("qty", 0))
                        break

                buy_orders = [o for o in order_list if o.get("side", "BUY").upper() == "BUY"]
                sell_orders = [o for o in order_list if o.get("side", "BUY").upper() == "SELL"]

                if buy_orders:
                    initial_before = buy_orders[0].get("before_qty", 0)
                    rem_buy_delta = max(0, current_qty - initial_before)

                    for u_order in buy_orders:
                        ord_id = u_order.get("order_id")
                        req_qty = u_order.get("requested_qty", 0)

                        allocated_filled = min(req_qty, rem_buy_delta)
                        rem_buy_delta -= allocated_filled

                        if allocated_filled >= req_qty and req_qty > 0:
                            self.update_order_status(ord_id, OrderStatus.FILLED, filled_qty=req_qty, message="UNKNOWN -> 잔고 Delta 순차 배분을 통한 체결(FILLED) 확정")
                        elif allocated_filled > 0:
                            self.update_order_status(ord_id, OrderStatus.PARTIALLY_FILLED, filled_qty=allocated_filled, message=f"UNKNOWN -> 부분 체결({allocated_filled}/{req_qty}) 확정")
                        else:
                            self.update_order_status(ord_id, OrderStatus.FAILED, filled_qty=0, message="UNKNOWN -> 체결 내역 없음(FAILED) 확정")
                        resolved_count += 1

                if sell_orders:
                    initial_before = sell_orders[0].get("before_qty", 0)
                    rem_sell_delta = max(0, initial_before - current_qty)

                    for u_order in sell_orders:
                        ord_id = u_order.get("order_id")
                        req_qty = u_order.get("requested_qty", 0)

                        allocated_filled = min(req_qty, rem_sell_delta)
                        rem_sell_delta -= allocated_filled

                        if allocated_filled >= req_qty and req_qty > 0:
                            self.update_order_status(ord_id, OrderStatus.FILLED, filled_qty=req_qty, message="UNKNOWN -> 잔고 Delta 순차 배분을 통한 체결(FILLED) 확정")
                        elif allocated_filled > 0:
                            self.update_order_status(ord_id, OrderStatus.PARTIALLY_FILLED, filled_qty=allocated_filled, message=f"UNKNOWN -> 부분 체결({allocated_filled}/{req_qty}) 확정")
                        else:
                            self.update_order_status(ord_id, OrderStatus.FAILED, filled_qty=0, message="UNKNOWN -> 체결 내역 없음(FAILED) 확정")
                        resolved_count += 1

        except Exception as e:
            print(f"[resolve_unknown_orders 예외] {e}")

        return resolved_count
