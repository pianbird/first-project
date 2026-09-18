"""
Core Domain Models for Algorithmic Trading System
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class TickData:
    """Immutable market tick data model."""
    code: str
    price: int
    volume: int
    timestamp: str


@dataclass(frozen=True)
class OrderSignal:
    """Pure strategy order signal model."""
    code: str
    action: str  # "BUY" or "SELL"
    order_type: str  # "LIMIT" or "MARKET"
    price: int
    quantity: int


@dataclass
class Position:
    """Portfolio position model."""
    code: str
    quantity: int
    avg_price: int
    current_price: int = 0
    unrealized_pnl: int = 0

    def update_price(self, new_price: int) -> None:
        """Update current price and recalculate unrealized PnL."""
        self.current_price = new_price
        self.unrealized_pnl = (new_price - self.avg_price) * self.quantity


@dataclass(frozen=True)
class OrderResult:
    """Order execution result model."""
    order_id: str
    code: str
    status: str  # "PENDING", "FILLED", "REJECTED"
    msg: str = ""
