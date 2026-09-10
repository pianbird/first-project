from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class StockCreateRequest(BaseModel):
    code: str = Field(..., json_schema_extra={"example": "005930"})
    name: str = Field(..., json_schema_extra={"example": "삼성전자"})
    base_price: int = Field(..., json_schema_extra={"example": 70000})
    clear_price: int = Field(..., json_schema_extra={"example": 80000})
    num_steps: int = Field(default=20, json_schema_extra={"example": 20})
    start_step: int = Field(default=1, json_schema_extra={"example": 1}) # 진입 차수 (시작 차수)
    step_pct: float = Field(default=1.5, json_schema_extra={"example": 1.5})
    budget_per_step: int = Field(default=100000, json_schema_extra={"example": 100000})
    memo: Optional[str] = Field(default="", json_schema_extra={"example": "전략 메모"})
    hard_stop_loss_enabled: bool = Field(default=False, json_schema_extra={"example": False})
    hard_stop_loss_price: int = Field(default=0, json_schema_extra={"example": 0})

class SystemGuardConfig(BaseModel):
    max_daily_trades: int = Field(default=20, json_schema_extra={"example": 20})
    max_buy_amount: int = Field(default=50000000, json_schema_extra={"example": 50000000})

class ControlRequest(BaseModel):
    action: str = Field(..., json_schema_extra={"example": "START"}) # "START", "STOP", "CANCEL_ORDERS"

class EmergencyResolutionRequest(BaseModel):
    stock_code: str
    action_choice: int = Field(..., json_schema_extra={"example": 0}) # 0: Resume trading, 1: Pause until new allocation

class ManualOrderRequest(BaseModel):
    stock_code: str
    order_type: str # "BUY" or "SELL"
    qty: int
    price: int = 0
    ord_dvsn: str = "03"

class StockToggleActiveRequest(BaseModel):
    stock_code: Optional[str] = None
    code: Optional[str] = None
    is_active: bool = True

class StockDeleteRequest(BaseModel):
    stock_code: Optional[str] = None
    code: Optional[str] = None

class ResetStopLossRequest(BaseModel):
    stock_code: Optional[str] = None
    code: Optional[str] = None

class KiwoomCredentialsRequest(BaseModel):
    app_key: str
    app_secret: str
    account_no: Optional[str] = ""
    is_mock: Optional[bool] = True

class ForceCloseRequest(BaseModel):
    stock_code: str
    qty: Optional[int] = None
    stock_name: Optional[str] = ""

class TradingModeRequest(BaseModel):
    trading_mode: str = Field(..., json_schema_extra={"example": "SIMULATION"}) # "SIMULATION" or "REAL"
