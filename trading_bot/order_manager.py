import time
from typing import Dict, Any, List, Optional, Tuple
try:
    from trading_bot.db_manager import DBManager
    from trading_bot.trading_mode import TradingModeManager, MODE_TEXT
    from trading_bot.market_calendar import MarketCalendar
except (ImportError, ModuleNotFoundError):
    from db_manager import DBManager
    from trading_mode import TradingModeManager, MODE_TEXT
    from market_calendar import MarketCalendar


class OrderStatus:
    PENDING_SUBMIT = "PENDING_SUBMIT"
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    OPEN = "OPEN"
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
        pending_statuses = {OrderStatus.PENDING_SUBMIT, OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.UNKNOWN}

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

        # Gate 6: Risk Manager 검사 (Fail-Closed 적용)
        if not risk_manager or not hasattr(risk_manager, "validate_order"):
            msg = "ORDER BLOCKED (RiskManager Fail-Closed): risk_manager 가 주입되지 않았습니다."
            return {"success": False, "status": OrderStatus.REJECTED, "message": msg, "error": msg}

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
            initial_status=OrderStatus.PENDING_SUBMIT,
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
            # 주문 통신 장애/타임아웃 발생 -> UNKNOWN 상태 처리 및 중복 자동 재전송 절대 금지 (FIX-01)
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
        [P1-5 & FIX-01] UNKNOWN 상태의 주문을 Exchange 미체결 내역(get_open_orders) 및 잔고 Delta 조회를 통해 최종 상태 확정
        - 1단계: kiwoom_client.get_open_orders() 미체결 내역 TR(ka10005) 양방향 대조
        - 2단계: 미체결 목록에 없을 시 계좌 잔고 Delta 분석을 통해 FILLED vs FAILED 확정
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
            # 1. 거래소 실체결/미체결 조회 (ka10005 TR)
            open_orders_list = []
            if kiwoom_client and hasattr(kiwoom_client, "get_open_orders"):
                try:
                    oo_res = kiwoom_client.get_open_orders()
                    if isinstance(oo_res, dict) and oo_res.get("success"):
                        open_orders_list = oo_res.get("open_orders", [])
                except Exception as oo_err:
                    print(f"[resolve_unknown_orders] get_open_orders 예외: {oo_err}")

            # 2. 거래소 잔고 조회 (kt00018 TR)
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

                # 종목별 미체결 내역 매칭 확인
                code_open_orders = [oo for oo in open_orders_list if str(oo.get("stock_code", "")).zfill(6) == code]

                remaining_unknowns = []
                for u_order in order_list:
                    ord_id = u_order.get("order_id")
                    u_side = u_order.get("side", "BUY").upper()
                    u_qty = u_order.get("requested_qty", 0)
                    u_price = u_order.get("price", 0)

                    matched_oo = None
                    for oo in code_open_orders:
                        oo_side = oo.get("side", "").upper()
                        oo_qty = oo.get("order_qty", 0)
                        if oo_side == u_side and (oo_qty == u_qty or oo.get("price", 0) == u_price):
                            matched_oo = oo
                            break

                    if matched_oo:
                        actual_id = matched_oo.get("order_id") or ord_id
                        filled = matched_oo.get("filled_qty", 0)
                        status_to_set = OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.ACCEPTED
                        self.update_order_status(
                            ord_id,
                            status=status_to_set,
                            filled_qty=filled,
                            message=f"UNKNOWN -> 거래소 미체결 내역 대조 확정 (원주문번호: {actual_id})"
                        )
                        code_open_orders.remove(matched_oo)
                        resolved_count += 1
                    else:
                        remaining_unknowns.append(u_order)

                if not remaining_unknowns:
                    continue

                # 미체결에 없는 잔여 UNKNOWN 주문은 계좌 잔고 Delta로 2차 검증
                current_qty = 0
                for h in held_list:
                    if str(h.get("code") or h.get("stock_code", "")).strip().zfill(6) == code:
                        current_qty = int(h.get("qty", 0))
                        break

                buy_orders = [o for o in remaining_unknowns if o.get("side", "BUY").upper() == "BUY"]
                sell_orders = [o for o in remaining_unknowns if o.get("side", "BUY").upper() == "SELL"]

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
                            self.update_order_status(ord_id, OrderStatus.FAILED, filled_qty=0, message="UNKNOWN -> 미체결/잔고 대조 실패(FAILED) 확정")
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
                            self.update_order_status(ord_id, OrderStatus.FAILED, filled_qty=0, message="UNKNOWN -> 미체결/잔고 대조 실패(FAILED) 확정")
                        resolved_count += 1

        except Exception as e:
            print(f"[resolve_unknown_orders 예외] {e}")

        return resolved_count

    def poll_open_order_status(self, kiwoom_client: Any, stock_code: Optional[str] = None) -> int:
        """
        [Task 4-2] ACCEPTED / OPEN / SUBMITTED 상태인 로컬 미체결 주문의 거래소 실제 체결/취소 상태를 폴링하여 확정
        - 1. kiwoom_client.get_open_orders(stock_code=stock_code) 로 거래소 미체결 조회
        - 2. self.db.get_active_or_unknown_orders(stock_code) 로 로컬 활성 주문 조회
        - 3. 로컬 ACCEPTED/SUBMITTED 주문 중 거래소 미체결 목록에 없는 것은 kiwoom_client.get_positions() 잔고 대비로 FILLED / CANCELLED 확정
        - 4. 거래소 미체결에 있고 filled_qty > 0 이면 PARTIALLY_FILLED 로 갱신
        - 5. 확정 건수 반환
        """
        if not kiwoom_client:
            return 0

        if hasattr(self.db, "get_active_or_unknown_orders"):
            active_orders = self.db.get_active_or_unknown_orders(stock_code)
        else:
            recent = self.db.get_recent_orders(limit=100)
            clean_code = str(stock_code).strip().zfill(6) if stock_code else None
            active_statuses = {OrderStatus.PENDING_SUBMIT, OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED}
            active_orders = [
                o for o in recent 
                if o.get("status") in active_statuses and (clean_code is None or str(o.get("stock_code", "")).strip().zfill(6) == clean_code)
            ]

        target_statuses = {OrderStatus.ACCEPTED, OrderStatus.OPEN, OrderStatus.SUBMITTED, OrderStatus.PENDING_SUBMIT, OrderStatus.PARTIALLY_FILLED}
        target_orders = [o for o in active_orders if o.get("status") in target_statuses]

        if not target_orders:
            return 0

        updated_count = 0

        open_orders_list = []
        if hasattr(kiwoom_client, "get_open_orders"):
            try:
                oo_res = kiwoom_client.get_open_orders(stock_code=stock_code)
                if isinstance(oo_res, dict) and oo_res.get("success"):
                    open_orders_list = oo_res.get("open_orders", [])
            except Exception as oo_err:
                print(f"[poll_open_order_status] get_open_orders 예외: {oo_err}")
                return 0

        held_list = []
        if hasattr(kiwoom_client, "is_credentials_valid") and kiwoom_client.is_credentials_valid():
            try:
                kpos_info = kiwoom_client.get_positions(force_refresh=True)
                held_list = (kpos_info.get("positions") or kpos_info.get("held_positions") or []) if isinstance(kpos_info, dict) else []
            except Exception as pos_err:
                print(f"[poll_open_order_status] get_positions 예외: {pos_err}")

        from collections import defaultdict
        grouped = defaultdict(list)
        for t in target_orders:
            code = str(t.get("stock_code", "")).strip().zfill(6)
            grouped[code].append(t)

        for code, order_list in grouped.items():
            order_list.sort(key=lambda x: (x.get("requested_at", ""), x.get("order_id", "")))
            code_open_orders = [oo for oo in open_orders_list if str(oo.get("stock_code", "")).zfill(6) == code]

            not_in_open = []
            for t_order in order_list:
                ord_id = t_order.get("order_id")
                t_side = t_order.get("side", "BUY").upper()
                t_qty = t_order.get("requested_qty", 0)
                t_price = t_order.get("price", 0)

                matched_oo = None
                for oo in code_open_orders:
                    oo_side = oo.get("side", "").upper()
                    oo_qty = oo.get("order_qty", 0)
                    oo_id = oo.get("order_id")
                    if (oo_id and (oo_id == ord_id or oo_id in ord_id)) or (oo_side == t_side and (oo_qty == t_qty or oo.get("price", 0) == t_price)):
                        matched_oo = oo
                        break

                if matched_oo:
                    filled = matched_oo.get("filled_qty", 0)
                    current_status = t_order.get("status")
                    if filled > 0 and current_status != OrderStatus.PARTIALLY_FILLED:
                        self.update_order_status(ord_id, OrderStatus.PARTIALLY_FILLED, filled_qty=filled, message=f"미체결 폴링 -> 부분 체결({filled}/{t_qty}) 갱신")
                        updated_count += 1
                    code_open_orders.remove(matched_oo)
                else:
                    not_in_open.append(t_order)

            if not not_in_open:
                continue

            current_qty = 0
            for h in held_list:
                if str(h.get("code") or h.get("stock_code", "")).strip().zfill(6) == code:
                    current_qty = int(h.get("qty", 0))
                    break

            buy_orders = [o for o in not_in_open if o.get("side", "BUY").upper() == "BUY"]
            sell_orders = [o for o in not_in_open if o.get("side", "BUY").upper() == "SELL"]

            if buy_orders:
                initial_before = buy_orders[0].get("before_qty", 0)
                rem_buy_delta = max(0, current_qty - initial_before)

                for b_order in buy_orders:
                    ord_id = b_order.get("order_id")
                    req_qty = b_order.get("requested_qty", 0)

                    allocated_filled = min(req_qty, rem_buy_delta)
                    rem_buy_delta -= allocated_filled

                    if allocated_filled >= req_qty and req_qty > 0:
                        self.update_order_status(ord_id, OrderStatus.FILLED, filled_qty=req_qty, message="미체결 폴링 -> 체결(FILLED) 확정")
                    elif allocated_filled > 0:
                        self.update_order_status(ord_id, OrderStatus.PARTIALLY_FILLED, filled_qty=allocated_filled, message=f"미체결 폴링 -> 부분 체결({allocated_filled}/{req_qty}) 확정")
                    else:
                        self.update_order_status(ord_id, OrderStatus.CANCELLED, filled_qty=0, message="미체결 폴링 -> 미체결 및 잔고 대조 결과 취소/체결 없음 확정")
                    updated_count += 1

            if sell_orders:
                initial_before = sell_orders[0].get("before_qty", 0)
                rem_sell_delta = max(0, initial_before - current_qty)

                for s_order in sell_orders:
                    ord_id = s_order.get("order_id")
                    req_qty = s_order.get("requested_qty", 0)

                    allocated_filled = min(req_qty, rem_sell_delta)
                    rem_sell_delta -= allocated_filled

                    if allocated_filled >= req_qty and req_qty > 0:
                        self.update_order_status(ord_id, OrderStatus.FILLED, filled_qty=req_qty, message="미체결 폴링 -> 체결(FILLED) 확정")
                    elif allocated_filled > 0:
                        self.update_order_status(ord_id, OrderStatus.PARTIALLY_FILLED, filled_qty=allocated_filled, message=f"미체결 폴링 -> 부분 체결({allocated_filled}/{req_qty}) 확정")
                    else:
                        self.update_order_status(ord_id, OrderStatus.CANCELLED, filled_qty=0, message="미체결 폴링 -> 미체결 및 잔고 대조 결과 취소/체결 없음 확정")
                    updated_count += 1

        return updated_count

