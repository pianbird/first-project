import time
from typing import Dict, Any, Tuple, Optional, List

from config.constants import DEFAULT_RISK_COOLDOWN_SEC

class RiskManager:
    """
    실전 자동매매 안전 리스크 관리자 (Risk Guard)
    - 주문 직전 Signal -> RiskManager -> OrderManager -> Kiwoom 흐름 통제
    - 수량/가격 정합성, 일일 최대 매매회수, 최대 매입금액, 미체결 중복 주문, Cooldown 3중 방어
    """

    def __init__(self, max_daily_trades: int = 50, max_buy_amount: int = 100000000):
        self.max_daily_trades = max_daily_trades
        self.max_buy_amount = max_buy_amount
        self.order_cooldowns: Dict[str, float] = {}

    def validate_order(
        self,
        stock_code: str = "",
        side: str = "BUY",
        qty: int = 0,
        price: int = 0,
        daily_trade_count: int = 0,
        total_account_exposure: int = 0,
        pending_orders: Optional[List[Dict[str, Any]]] = None,
        safe_stop_active: bool = False,
        cooldown_sec: float = DEFAULT_RISK_COOLDOWN_SEC,
        symbol: str = "",
        kiwoom_client: Any = None,
        **kwargs
    ) -> Tuple[bool, str]:
        """
        주문 전송 직전 엄격한 리스크 및 중복 주문 검증 수행
        :return: (is_allowed: bool, reason: str)
        """
        code = symbol or stock_code
        clean_code = str(code).strip().zfill(6)

        # 1. SAFE_STOP 발동 중인 경우 주문 완전 금지
        if safe_stop_active:
            return False, "🚨 [SAFE_STOP 발동중] 시스템 비상 정지 상태로 신규 주문이 차단되었습니다."

        # 2. 가격 및 수량 0/음수 검증
        if qty <= 0:
            return False, f"🛑 [주문 거부] 수량이 비정상적입니다 (수량: {qty})"
        if price < 0:
            return False, f"🛑 [주문 거부] 가격이 비정상적입니다 (가격: {price})"

        # 3. 쿨다운(Cooldown) 시간 검증
        last_order_time = self.order_cooldowns.get(clean_code, 0.0)
        now = time.time()
        if now < last_order_time:
            remain = int(last_order_time - now)
            return False, f"⏳ [Cooldown 차단] 종목({clean_code}) 재주문 방지 쿨다운 적용 중 ({remain}초 남음)"

        # 4. 동일 종목 미체결 주문 존재 여부 검사 (중복 주문 방지)
        if pending_orders:
            for order in pending_orders:
                order_code = str(order.get("stock_code", "")).zfill(6)
                if order_code == clean_code:
                    ord_id = order.get("order_id", "N/A")
                    status = order.get("status", "PENDING")
                    return False, f"🛑 [미체결 중복 주문 차단] 종목({clean_code})에 이미 미체결 주문({ord_id}, 상태: {status})이 존재합니다."

        # 5. 매수 주문 시 리스크 가드 (일일 최대 매매 횟수 및 총 매입 한도)
        if side.upper() == "BUY":
            if daily_trade_count >= self.max_daily_trades and self.max_daily_trades > 0:
                return False, f"🛑 [일일 매매 횟수 초과] 오늘 매매 횟수({daily_trade_count}회)가 일일 한도({self.max_daily_trades}회)에 도달하였습니다."

            est_cost = price * qty if price > 0 else 0
            if (total_account_exposure + est_cost) > self.max_buy_amount and self.max_buy_amount > 0:
                return False, f"🛑 [매입 한도 초과] 주문 총액이 계좌 최대 매입 한도({self.max_buy_amount:,}원)를 초과합니다."

        return True, "OK"

    def set_cooldown(self, stock_code: str, cooldown_sec: float = DEFAULT_RISK_COOLDOWN_SEC):
        clean_code = str(stock_code).strip().zfill(6)
        self.order_cooldowns[clean_code] = time.time() + cooldown_sec
