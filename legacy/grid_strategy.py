import math
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from trading_rules import normalize_price

@dataclass
class GridStep:
    """단일 차수 그리드 정보"""
    step: int             # 차수 (1차, 2차, ...)
    price: int            # 차수 매수 기준가
    step_qty: int         # 해당 차수 신규 매수 수량
    target_total_qty: int # 해당 차수 누적 목표 수량

@dataclass
class GridConfig:
    """종목별 그리드 전략 설정"""
    stock_code: str
    stock_name: str
    exit_price: int                 # 최종 청산 가격 (전량 매도)
    max_steps: int                  # 최대 차수 (예: 20차 또는 50차)
    grid_steps: List[GridStep] = field(default_factory=list)
    max_capital: int = 0            # 종목별 최대 할당 자금
    hard_stop_loss_enabled: bool = False  # 하드 손절 활성화 여부
    hard_stop_loss_price: int = 0         # 하드 손절가 (0이면 미설정)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stock_code": self.stock_code,
            "stock_name": self.stock_name,
            "exit_price": self.exit_price,
            "max_steps": self.max_steps,
            "max_capital": self.max_capital,
            "hard_stop_loss_enabled": self.hard_stop_loss_enabled,
            "hard_stop_loss_price": self.hard_stop_loss_price,
            "grid_steps": [asdict(s) for s in self.grid_steps]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GridConfig":
        steps_data = data.get("grid_steps", [])
        steps = [GridStep(**s) for s in steps_data]
        return cls(
            stock_code=data["stock_code"],
            stock_name=data.get("stock_name", data["stock_code"]),
            exit_price=data.get("exit_price", 0),
            max_steps=data.get("max_steps", len(steps)),
            grid_steps=steps,
            max_capital=data.get("max_capital", 0),
            hard_stop_loss_enabled=data.get("hard_stop_loss_enabled", False),
            hard_stop_loss_price=data.get("hard_stop_loss_price", 0)
        )

@dataclass
class GridState:
    """종목별 런타임 그리드 상태"""
    stock_code: str
    current_step: int = 0             # 현재 위치 차수 (0: 미보유)
    target_qty: int = 0               # 목표 보유 수량
    actual_qty: int = 0               # 실제 보유 수량
    is_blackswan_halt: bool = False   # 블랙스완 매수 정지 상태 여부
    consecutive_ma5_above: int = 0    # 종가 > 5일선 연속 달성 횟수
    recycle_mode: bool = False        # 무한 물타기(Cash Re-cycle) 모드 여부
    stop_loss_halted: bool = False    # 하드 손절 발동 후 해당 종목 매수 정지 여부
    stop_loss_triggered_at: str = "" # 하드 손절 발동 KST 시각
    updated_at: str = ""

def generate_auto_grid_config(
    stock_code: str,
    stock_name: str,
    base_price: int,
    exit_price: int,
    num_steps: int = 20,
    start_step: int = 1,
    step_pct: float = 1.5,
    step_qty: int = 1,
    hard_stop_loss_enabled: bool = False,
    hard_stop_loss_price: int = 0,
    hard_stop_loss_pct: float = 5.0
) -> GridConfig:
    """
    기준가 및 하락 비율(%)을 바탕으로 N차 그리드 수량/가격 자동 생성 (start_step 차수가 base_price가 됨)
    """
    steps: List[GridStep] = []
    accum_qty = 0

    for k in range(1, num_steps + 1):
        raw_price = base_price * (1.0 - ((k - start_step) * step_pct / 100.0))
        norm_price = normalize_price(int(raw_price))
        accum_qty += step_qty
        steps.append(GridStep(
            step=k,
            price=norm_price,
            step_qty=step_qty,
            target_total_qty=accum_qty
        ))

    norm_exit = normalize_price(exit_price)
    max_cap = sum(s.price * s.step_qty for s in steps)

    final_stop_loss_price = hard_stop_loss_price
    if steps and hard_stop_loss_enabled:
        lowest_grid_price = steps[-1].price
        if hard_stop_loss_price > 0 and hard_stop_loss_price >= lowest_grid_price:
            raise ValueError(f"하드 손절가({hard_stop_loss_price:,}원)는 최하단 그리드 가격({lowest_grid_price:,}원)보다 작아야 합니다.")
        elif hard_stop_loss_price == 0:
            calc_price = int(lowest_grid_price * (1.0 - (hard_stop_loss_pct / 100.0)))
            final_stop_loss_price = normalize_price(calc_price)

    return GridConfig(
        stock_code=stock_code,
        stock_name=stock_name,
        exit_price=norm_exit,
        max_steps=num_steps,
        grid_steps=steps,
        max_capital=max_cap,
        hard_stop_loss_enabled=hard_stop_loss_enabled,
        hard_stop_loss_price=final_stop_loss_price
    )

