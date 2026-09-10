"""
Pure calculations for grid trading math and price normalization.
Preserves exact original rounding formulas: max(10, int(round(round(price) / 10.0)) * 10).
"""

def get_tick_size(price: int) -> int:
    """
    국내 주식 시장 가격대별 호가 단위(Tick Size) 구하기 (2023년 개정 규칙 반영)
    """
    p = abs(int(price))
    if p < 2000:
        return 1
    elif p < 5000:
        return 5
    elif p < 20000:
        return 10
    elif p < 50000:
        return 50
    elif p < 200000:
        return 100
    elif p < 500000:
        return 500
    else:
        return 1000

def normalize_price(price: float) -> int:
    """
    주문 가격을 십원(10원) 단위 정수로 설정 (1원 단위 절삭 및 소수점 금액 삭제, 예: 2521 -> 2520)
    """
    if not price:
        return 0
    return max(10, int(round(round(float(price)) / 10.0)) * 10)
