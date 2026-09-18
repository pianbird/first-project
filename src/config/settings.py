"""
Configuration Layer (src/config/settings.py)
Defines AppConfig and TradingConfig models.
"""
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AppConfig:
    """Application authentication and environment settings."""
    app_key: str = field(default_factory=lambda: os.getenv("KIWOOM_APP_KEY", os.getenv("APP_KEY", "")).strip())
    app_secret: str = field(default_factory=lambda: os.getenv("KIWOOM_SECRET_KEY", os.getenv("SECRET_KEY", "")).strip())
    account_no: str = field(default_factory=lambda: os.getenv("KIWOOM_ACCOUNT_NO", os.getenv("ACCOUNT_NO", "")).strip())
    is_mock: bool = field(default_factory=lambda: os.getenv("TRADING_MODE", "MOCK").upper() != "REAL")
    base_url: str = field(default_factory=lambda: "https://mockapi.kiwoom.com" if os.getenv("TRADING_MODE", "MOCK").upper() != "REAL" else "https://api.kiwoom.com")


@dataclass
class TradingConfig:
    """Trading strategy parameters: profit target, stop loss, slippage tolerance, and polling interval."""
    target_profit_rate: float = 0.03       # 3% profit target
    stop_loss_rate: float = 0.02          # 2% stop loss threshold
    max_slippage_rate: float = 0.005       # 0.5% max slippage
    polling_interval_sec: float = 2.5      # 2.5s polling loop interval
    off_market_sleep_sec: float = 15.0     # 15s off-market sleep interval
