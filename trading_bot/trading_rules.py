import datetime
from typing import Dict, Any, Tuple

def get_kst_now() -> datetime.datetime:
    """KST (Asia/Seoul) 현재 시각 반환 (PythonAnywhere UTC 서버 보정)"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Seoul"))
    except Exception:
        kst_tz = datetime.timezone(datetime.timedelta(hours=9))
        return datetime.datetime.now(kst_tz)

def get_kst_now_str() -> str:
    return get_kst_now().strftime("%Y-%m-%d %H:%M:%S")

def get_kst_date_str() -> str:
    return get_kst_now().strftime("%Y-%m-%d")


from domain.grid_calculator import get_tick_size, normalize_price

def calculate_trade_cost(
    buy_price: int,
    sell_price: int,
    qty: int,
    buy_fee_rate: float = 0.00015,   # 매수 수수료 (0.015%)
    sell_fee_rate: float = 0.00015,  # 매도 수수료 (0.015%)
    tax_rate: float = 0.0018         # 매도 증권거래세+농특세 (0.18%)
) -> Dict[str, Any]:
    """
    거래 제비용(수수료 + 증권거래세)을 감안한 실효 손익 및 수익률 계산
    """
    total_buy_amt = buy_price * qty
    total_sell_amt = sell_price * qty

    buy_fee = int(total_buy_amt * buy_fee_rate)
    sell_fee = int(total_sell_amt * sell_fee_rate)
    tax = int(total_sell_amt * tax_rate)

    total_cost = buy_fee + sell_fee + tax
    gross_pnl = total_sell_amt - total_buy_amt
    net_pnl = gross_pnl - total_cost

    net_rate = (net_pnl / total_buy_amt * 100) if total_buy_amt > 0 else 0.0

    return {
        "buy_amount": total_buy_amt,
        "sell_amount": total_sell_amt,
        "buy_fee": buy_fee,
        "sell_fee": sell_fee,
        "tax": tax,
        "total_cost": total_cost,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
        "net_rate": net_rate
    }

from config.constants import STRICT_MARKET_START, STRICT_MARKET_END


def is_strict_market_hours() -> bool:
    """
    정규장 실시간 연속 매매 가능 시간대 체크 (09:00:05 ~ 15:19:50 KST, 동시호가 예외 제외)
    """
    now = get_kst_now()
    # 주말 (토: 5, 일: 6)
    if now.weekday() >= 5:
        return False

    current_time = now.time()
    start_time = datetime.datetime.strptime(STRICT_MARKET_START, "%H:%M:%S").time()
    end_time = datetime.datetime.strptime(STRICT_MARKET_END, "%H:%M:%S").time()

    return start_time <= current_time <= end_time


