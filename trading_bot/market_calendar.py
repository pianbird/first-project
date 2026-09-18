from datetime import datetime, date, time as dt_time
from zoneinfo import ZoneInfo
from typing import Tuple, Dict, Any, Optional

KST = ZoneInfo("Asia/Seoul")

# 한국 거래소(KRX) 정기/특수 휴장일 목록 (YYYY-MM-DD)
KRX_HOLIDAYS_2025_2026 = {
    # 2025
    "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30",
    "2025-03-03", "2025-05-05", "2025-05-06", "2025-06-06",
    "2025-08-15", "2025-10-03", "2025-10-06", "2025-10-07",
    "2025-10-08", "2025-10-09", "2025-12-25", "2025-12-31",
    # 2026
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18",
    "2026-03-02", "2026-05-05", "2026-05-25", "2026-06-03",
    "2026-08-17", "2026-09-24", "2026-09-25", "2026-09-28",
    "2026-10-05", "2026-10-09", "2026-12-25", "2026-12-31"
}

class MarketCalendar:
    """
    KST 기준 거래일 및 장 운영 시간 관리자
    - 주말(토/일) 및 KRX 공휴일 판별
    - 정규장 시간 (09:00:00 ~ 15:30:00 KST) 판별
    """

    @staticmethod
    def get_kst_now() -> datetime:
        return datetime.now(KST)

    @classmethod
    def is_trading_day(cls, target_date: Optional[date] = None) -> bool:
        """해당 날짜가 거래일(주말 제외, 공휴일 제외)인지 확인"""
        if target_date is None:
            target_date = cls.get_kst_now().date()

        # 주말 검사 (5: 토요일, 6: 일요일)
        if target_date.weekday() in (5, 6):
            return False

        # KRX 휴장일 검사
        date_str = target_date.strftime("%Y-%m-%d")
        if date_str in KRX_HOLIDAYS_2025_2026:
            return False

        return True

    @classmethod
    def is_market_open(cls, dt: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        현재 시각이 정규장 운영 시간인지 판별
        :return: (is_open: bool, reason: str)
        """
        if dt is None:
            dt = cls.get_kst_now()
        else:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KST)

        current_date = dt.date()
        current_time = dt.time()

        if not cls.is_trading_day(current_date):
            return False, f"휴장일입니다 ({current_date.strftime('%Y-%m-%d')})"

        market_start = dt_time(9, 0, 0)
        market_end = dt_time(15, 30, 0)

        if current_time < market_start:
            return False, f"장 시작 전입니다 ({current_time.strftime('%H:%M:%S')} < 09:00:00)"

        if current_time > market_end:
            return False, f"장 마감 후입니다 ({current_time.strftime('%H:%M:%S')} > 15:30:00)"

        return True, "정규장 진행 중"

    @classmethod
    def is_market_open_now(cls, allow_buffer: bool = True) -> bool:
        """
        현재 시각이 KRX 정규장 운영 시간(또는 여유 5분 buffer 포함 KST 08:55~15:35)인지 여부를 bool로 반환
        """
        now_kst = cls.get_kst_now()
        if not cls.is_trading_day(now_kst.date()):
            return False

        current_time = now_kst.time()
        start_time = dt_time(8, 55, 0) if allow_buffer else dt_time(9, 0, 0)
        end_time = dt_time(15, 35, 0) if allow_buffer else dt_time(15, 30, 0)
        return start_time <= current_time <= end_time


def is_market_open_now(allow_buffer: bool = True) -> bool:
    """모듈 직호출 지원용 is_market_open_now 도우미 함수"""
    return MarketCalendar.is_market_open_now(allow_buffer=allow_buffer)
