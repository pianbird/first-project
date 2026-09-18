"""
Domain Models Layer (src/domain/models.py)
Defines OrderType, TradeAction enums, Position, OrderResult, and TradeSignal models.
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Position:
    """Position tracking model."""
    symbol: str
    quantity: int
    buy_price: int
    current_price: int = 0
    pnl: int = 0

    def update_current_price(self, new_price: int) -> None:
        self.current_price = new_price
        self.pnl = (new_price - self.buy_price) * self.quantity


@dataclass(frozen=True)
class TradeSignal:
    """Pure strategy signal output model."""
    symbol: str
    action: TradeAction
    order_type: OrderType
    price: int
    quantity: int


class GridLevelStatus(str, Enum):
    READY = "READY"
    BUY_ORDERING = "BUY_ORDERING"
    BUY_PENDING = "BUY_PENDING"
    POSITION_HOLD = "POSITION_HOLD"
    SELL_ORDERING = "SELL_ORDERING"
    SELL_PENDING = "SELL_PENDING"


LevelStatus = GridLevelStatus


@dataclass
class GridLevel:
    """Fixed Grid level state tracking model."""
    level_id: int
    buy_price: int
    sell_price: int
    quantity: int
    status: GridLevelStatus = GridLevelStatus.READY
    order_id: Optional[str] = None



@dataclass(frozen=True)
class OrderResult:
    """Order execution result model."""
    order_id: str
    symbol: str
    action: TradeAction
    quantity: int
    price: int
    success: bool
    message: str = ""

