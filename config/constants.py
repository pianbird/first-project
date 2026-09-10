"""
Centralized domain and operational constants for MagicTrader.
All numeric values preserve exact original definitions.
"""

# Market Risk / Safe Stop Thresholds
SAFE_STOP_DROP_THRESHOLD: float = 0.85  # Overnight/daily drop > 15% triggers SAFE_STOP

# Timing & Delays
ORDER_INTER_SLEEP_SEC: float = 0.35      # Inter-order sleep delay (seconds)
DEFAULT_RISK_COOLDOWN_SEC: float = 15.0  # Cooldown applied on order rejection/error (seconds)
DEFAULT_ORDER_GUARD_COOLDOWN_MIN: int = 5 # Legacy OrderGuard cooldown (minutes)

# Deposit & Exposure Defaults
DEFAULT_VIRTUAL_DEPOSIT: int = 100000000 # 100,000,000 KRW virtual starting capital
DEFAULT_MAX_ACCOUNT_EXPOSURE: int = 50000000 # 50,000,000 KRW exposure limit

# Trading Hours Defaults
DEFAULT_MARKET_OPEN_TIME: str = "09:00:00"
DEFAULT_MARKET_CLOSE_TIME: str = "15:30:00"
STRICT_MARKET_START: str = "09:00:05"
STRICT_MARKET_END: str = "15:19:50"
