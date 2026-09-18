"""
Grid Level Generator (src/strategy/grid_generator.py)
Generates static GridLevel instances with tick size alignment and 1:1 adjacent level mapping.
"""
from typing import List
from src.domain.models import GridLevel, LevelStatus, GridLevelStatus
from domain.grid_calculator import adjust_to_tick_size, get_tick_size


class GridGenerator:
    """
    Grid Level Generator.
    Calculates grid interval levels between lower and upper bounds,
    aligns buy/sell prices to KRX tick size rules, and initializes levels to READY.
    """

    @staticmethod
    def generate(lower_price: float, upper_price: float, grid_count: int, quantity: int) -> List[GridLevel]:
        """
        1. lower_price부터 upper_price 사이를 grid_count 단계로 균등 분할.
        2. 국내 주식 호가 단위(Tick Size)로 반올림/내림 보정.
        3. 인접 상위 레벨의 가격을 각 레벨의 sell_price로 1:1 매핑.
        4. 모든 레벨의 초기 상태를 LevelStatus.READY로 설정하여 반환.
        """
        if lower_price >= upper_price:
            raise ValueError("lower_price must be less than upper_price")
        if grid_count <= 0:
            raise ValueError("grid_count must be greater than 0")
        if quantity <= 0:
            raise ValueError("quantity must be greater than 0")

        step = (upper_price - lower_price) / grid_count
        raw_points = [lower_price + (i * step) for i in range(grid_count + 1)]
        points = [adjust_to_tick_size(p, round_up=False) for p in raw_points]

        levels: List[GridLevel] = []
        for i in range(grid_count):
            buy_p = points[i]
            sell_p = points[i + 1]

            # Ensure sell_p is at least 1 tick higher than buy_p
            if sell_p <= buy_p:
                tick = get_tick_size(buy_p)
                sell_p = buy_p + tick

            levels.append(
                GridLevel(
                    level_id=i + 1,
                    buy_price=int(buy_p),
                    sell_price=int(sell_p),
                    quantity=quantity,
                    status=LevelStatus.READY,
                    order_id=None
                )
            )

        return levels


def generate_grid_levels(lower_price: float, upper_price: float, grid_count: int, quantity: int) -> List[GridLevel]:
    """
    [Requirement 1 Specification Wrapper]
    generate_grid_levels(lower_price: float, upper_price: float, grid_count: int, quantity: int) -> List[GridLevel]
    """
    return GridGenerator.generate(lower_price=lower_price, upper_price=upper_price, grid_count=grid_count, quantity=quantity)
