import time
from typing import Dict, Any, Tuple, Optional, List
from db_manager import DBManager

class OrderGuard:
    """
    과도 매매 방지, 쿨다운, 포지션 락 및 서킷브레이커/VI 시장 급변 방어 리스크 가드
    """

    def __init__(self, config: Dict[str, Any]):
        risk_cfg = config.get("risk_management", {})
        self.cooldown_seconds = risk_cfg.get("cooldown_minutes", 5) * 60
        self.max_daily_trades_per_stock = risk_cfg.get("max_daily_trades_per_stock", 3)
        self.max_daily_trades_account = risk_cfg.get("max_daily_trades_account", 10)
        self.max_account_exposure = risk_cfg.get("max_account_exposure", 50000000) # 기본 5,000만원

        self.kill_switch = risk_cfg.get("kill_switch", False)
        self.emergency_freeze_until = 0.0

        # 종목별 마지막 체결 완료 시각 (쿨다운 관리)
        self.last_trade_time: Dict[str, float] = {}

    def can_trade(
        self,
        stock_code: str,
        side: str,                  # "BUY" or "SELL"
        db: DBManager,
        active_positions: List[Dict[str, Any]]
    ) -> Tuple[bool, str]:
        """
        주문 전송 가능 여부 및 차단 사유 다중 검증
        """
        # 1. 수동 비상 정지(Kill-Switch) 활성화 여부
        if self.kill_switch:
            return False, "비상 정지(Kill-Switch)가 활성화되어 있어 거래가 중단되었습니다."

        # 2. 시장 급변/서킷브레이커 동결 락 (30분)
        if time.time() < self.emergency_freeze_until:
            remaining_mins = int((self.emergency_freeze_until - time.time()) / 60)
            return False, f"서킷브레이커/VI 방어로 인해 시스템 동결 상태입니다. (남은 시간: {remaining_mins}분)"

        clean_code = stock_code.strip()

        # 3. 종목별 쿨다운 (Cooldown) 검사
        last_time = self.last_trade_time.get(clean_code, 0.0)
        elapsed = time.time() - last_time
        if elapsed < self.cooldown_seconds:
            rem_secs = int(self.cooldown_seconds - elapsed)
            return False, f"종목 쿨다운 적용 중입니다. ({clean_code}, 남은시간: {rem_secs}초)"

        # 4. 매수 포지션 및 계좌 총 매입금액 한도 검사
        if side.upper() == "BUY":
            if self.max_account_exposure > 0:
                total_exposure = sum(p.get("avg_price", 0) * p.get("qty", 0) for p in active_positions)
                if total_exposure >= self.max_account_exposure:
                    return False, f"계좌 총 매입금액 한도({self.max_account_exposure:,}원) 초과 (현재: {total_exposure:,}원)"

        # 5. 종목별 당일 최대 매매 횟수 상한 검사
        stock_daily_trades = db.get_today_trade_count(clean_code) if hasattr(db, "get_today_trade_count") else db.get_daily_trade_count()
        if stock_daily_trades >= self.max_daily_trades_per_stock:
            return False, f"종목별 당일 최대 매매 횟수({self.max_daily_trades_per_stock}회) 초과: {clean_code}"

        # 6. 계좌 전체 당일 최대 매매 횟수 상한 검사
        total_daily_trades = db.get_today_trade_count() if hasattr(db, "get_today_trade_count") else db.get_daily_trade_count()
        if total_daily_trades >= self.max_daily_trades_account:
            return False, f"계좌 전체 당일 최대 매매 횟수({self.max_daily_trades_account}회) 초과"

        return True, "정상"

    def record_trade_execution(self, stock_code: str):
        """체결 시 종목 쿨다운 시각 기록"""
        self.last_trade_time[stock_code.strip()] = time.time()

    def check_market_anomaly(self, status_code: str, db: Optional[DBManager] = None) -> bool:
        """
        키움 API 상태 코드 검사 (55: VI 발동, 57: 서킷브레이커, 58: 거래정지)
        """
        code_str = str(status_code).strip()
        anomaly_codes = {
            "55": "VI 발동",
            "57": "서킷브레이커 발동",
            "58": "종목 거래정지"
        }

        if code_str in anomaly_codes:
            reason = anomaly_codes[code_str]
            self.trigger_emergency_halt(reason, db)
            return True
        return False

    def trigger_emergency_halt(self, reason: str = "시장 급변 감지", db: Optional[DBManager] = None):
        """비상 정지 및 30분 시스템 동결 락 활성화"""
        self.emergency_freeze_until = time.time() + 1800 # 30분 동결
        self.kill_switch = True
        msg = f"[비상방어 발동] {reason}! 30분간 신규 매매 동결 및 Kill-Switch 활성화"
        if db:
            db.log_event("WARNING", msg)
        print(f"[OrderGuard] {msg}")

    def reset_kill_switch(self):
        """비상 정지 해제"""
        self.kill_switch = False
        self.emergency_freeze_until = 0.0
