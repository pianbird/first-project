import math
from typing import Union

def get_tick_size(price: Union[int, float]) -> int:
    """
    국내 주식 시장 가격대별 호가 단위(Tick Size) 구하기 (2023년 개정 규칙 준수 - FIX-02)
    - 2,000원 미만: 1원
    - 2,000원 이상 ~ 5,000원 미만: 5원
    - 5,000원 이상 ~ 20,000원 미만: 10원
    - 20,000원 이상 ~ 50,000원 미만: 50원
    - 50,000원 이상 ~ 200,000원 미만: 100원
    - 200,000원 이상 ~ 500,000원 미만: 500원
    - 500,000원 이상: 1,000원
    """
    p = abs(float(price))
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


def adjust_to_tick_size(price: float, round_up: bool = False) -> int:
    """
    2023 KRX 개정 호가 단위 정밀 변환 함수 (FIX-02)
    :param price: 지정가/계산된 가격
    :param round_up: False 시 보수적 내림(math.floor, 매수용), True 시 올림(math.ceil, 매도용)
    :return: 틱 단위 보정 정수 가격
    """
    if not price or price <= 0:
        return 0
    p = float(price)
    tick = get_tick_size(p)
    if round_up:
        adjusted = math.ceil(p / tick) * tick
    else:
        adjusted = math.floor(p / tick) * tick
    return int(adjusted)


def normalize_price(price: float, round_up: bool = False) -> int:
    """
    호가 단위 정밀 변환 래퍼 함수 (하위 호환성 준수 및 2023 KRX 틱 규정 적용)
    """
    return adjust_to_tick_size(price, round_up=round_up)

