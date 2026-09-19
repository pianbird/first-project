"""
MagicTrader - Global System Configuration & Timezone Standardization (config/settings.py)
Production-Ready Environment Configuration Manager
"""
import os
import json
import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, Any, Optional

# Safely load environment variables from .env file using python-dotenv
try:
    from dotenv import load_dotenv
    # Load .env file from project root or working directory
    load_dotenv(override=False)
except ImportError:
    pass

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "web_magictrader.db")
TOKEN_CACHE_PATH = os.path.join(BASE_DIR, "kiwoom_token.json")


def get_kst_now() -> datetime.datetime:
    """
    Linux / Windows / PythonAnywhere 타임존 표준화 - Asia/Seoul KST 현재 시각 반환
    """
    try:
        return datetime.datetime.now(ZoneInfo("Asia/Seoul"))
    except Exception:
        kst_tz = datetime.timezone(datetime.timedelta(hours=9))
        return datetime.datetime.now(kst_tz)


def get_kst_now_str() -> str:
    """KST 시각 포맷팅 문자열 반환 (YYYY-MM-DD HH:MM:SS)"""
    return get_kst_now().strftime("%Y-%m-%d %H:%M:%S")


class Config:
    """
    Production-Ready Central Configuration Manager
    - Safe .env loading via python-dotenv with environment variable priority
    - Kiwoom REST API endpoint, credentials, account & Telegram notification settings
    - Grid trading parameters (min price, max price, levels, amount per level, grid state path)
    """

    # 1. Trading Mode & Endpoint Base URLs
    TRADING_MODE: str = os.getenv("TRADING_MODE", "MOCK").upper()
    IS_MOCK: bool = TRADING_MODE != "REAL"
    MOCK_BASE_URL: str = "https://mockapi.kiwoom.com"
    REAL_BASE_URL: str = "https://api.kiwoom.com"

    # Common BASE_URL resolution (env override -> mode default)
    KIWOOM_BASE_URL: str = (
        os.getenv("KIWOOM_BASE_URL") or os.getenv("BASE_URL") or (MOCK_BASE_URL if IS_MOCK else REAL_BASE_URL)
    ).rstrip("/")
    BASE_URL: str = KIWOOM_BASE_URL  # Alias for backward compatibility

    # 2. Kiwoom REST API Credentials
    MOCK_APP_KEY: str = os.getenv("KIWOOM_MOCK_APP_KEY", "").strip()
    MOCK_APP_SECRET: str = os.getenv("KIWOOM_MOCK_APP_SECRET", "").strip()
    REAL_APP_KEY: str = os.getenv("KIWOOM_REAL_APP_KEY", "").strip()
    REAL_APP_SECRET: str = os.getenv("KIWOOM_REAL_APP_SECRET", "").strip()

    # Active APP_KEY & SECRET_KEY / APP_SECRET
    KIWOOM_APP_KEY: str = (
        os.getenv("KIWOOM_APP_KEY") or os.getenv("APP_KEY") or (MOCK_APP_KEY if IS_MOCK else REAL_APP_KEY)
    ).strip()
    APP_KEY: str = KIWOOM_APP_KEY  # Alias for engine compatibility

    KIWOOM_SECRET_KEY: str = (
        os.getenv("KIWOOM_SECRET_KEY") or os.getenv("SECRET_KEY") or os.getenv("APP_SECRET") or (MOCK_APP_SECRET if IS_MOCK else REAL_APP_SECRET)
    ).strip()
    SECRET_KEY: str = KIWOOM_SECRET_KEY  # Alias for engine compatibility
    APP_SECRET: str = SECRET_KEY         # Alias for backward compatibility

    # Account Number
    ACCOUNT_NO: str = (os.getenv("KIWOOM_ACCOUNT_NO") or os.getenv("ACCOUNT_NO") or "").strip()
    KIWOOM_ACCOUNT_NO: str = ACCOUNT_NO  # Alias for engine compatibility

    # 3. Target Stock Configuration
    STOCK_CODE: str = (os.getenv("STOCK_CODE") or os.getenv("TARGET_STOCK_CODE") or "005930").strip().zfill(6)
    TARGET_STOCK_CODE: str = STOCK_CODE  # Alias for engine compatibility

    STOCK_NAME: str = (os.getenv("STOCK_NAME") or os.getenv("TARGET_STOCK_NAME") or "삼성전자").strip()
    TARGET_STOCK_NAME: str = STOCK_NAME  # Alias for engine compatibility

    # 4. Grid Trading Parameters
    GRID_MIN_PRICE: int = int(os.getenv("GRID_LOWER_PRICE") or os.getenv("GRID_MIN_PRICE", "50000"))  # 하한가
    GRID_LOWER_PRICE: int = GRID_MIN_PRICE                                                            # Alias
    GRID_MAX_PRICE: int = int(os.getenv("GRID_UPPER_PRICE") or os.getenv("GRID_MAX_PRICE", "80000"))  # 상한가
    GRID_UPPER_PRICE: int = GRID_MAX_PRICE                                                            # Alias
    GRID_LEVELS: int = int(os.getenv("GRID_COUNT") or os.getenv("GRID_LEVELS", "10"))                 # 그리드 개수
    GRID_COUNT: int = GRID_LEVELS                                                                     # Alias
    AMOUNT_PER_LEVEL: int = int(os.getenv("ORDER_AMOUNT_KRW") or os.getenv("AMOUNT_PER_LEVEL", "100000")) # 1회 주문 금액
    ORDER_AMOUNT_KRW: int = AMOUNT_PER_LEVEL                                                          # Alias
    GRID_TYPE: str = os.getenv("GRID_TYPE", "ARITHMETIC").upper()

    # 5. System State & Persistence File Paths
    STATE_FILE_PATH: str = os.getenv(
        "STATE_FILE_PATH", os.path.join(BASE_DIR, "grid_state.json")
    )
    DB_PATH: str = os.getenv("DB_PATH", DB_PATH)
    TOKEN_CACHE_PATH: str = os.getenv("TOKEN_CACHE_PATH", TOKEN_CACHE_PATH)

    # 6. Risk Controls
    MAX_DAILY_TRADES: int = int(os.getenv("MAX_DAILY_TRADES", "30"))
    MAX_ACCOUNT_EXPOSURE: int = int(os.getenv("MAX_ACCOUNT_EXPOSURE", "50000000"))
    SAFE_STOP_DROP_THRESHOLD: float = float(os.getenv("SAFE_STOP_DROP_THRESHOLD", "0.85"))

    # 7. Telegram Notification Credentials
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    TELEGRAM_ENABLED: bool = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

    # 8. Execution Timing & Rates
    API_RATE_LIMIT_DELAY: float = 0.25
    CYCLE_INTERVAL_SEC: float = 2.5
    OFF_MARKET_SLEEP_SEC: float = 15.0

    @classmethod
    def load(cls):
        """Reload configuration from environment variables."""
        if 'load_dotenv' in globals():
            load_dotenv(override=True)
        cls.TRADING_MODE = os.getenv("TRADING_MODE", "MOCK").upper()
        cls.IS_MOCK = cls.TRADING_MODE != "REAL"
        cls.KIWOOM_BASE_URL = (
            os.getenv("KIWOOM_BASE_URL") or os.getenv("BASE_URL") or (cls.MOCK_BASE_URL if cls.IS_MOCK else cls.REAL_BASE_URL)
        ).rstrip("/")
        cls.BASE_URL = cls.KIWOOM_BASE_URL
        cls.KIWOOM_APP_KEY = (
            os.getenv("KIWOOM_APP_KEY") or os.getenv("APP_KEY") or (cls.MOCK_APP_KEY if cls.IS_MOCK else cls.REAL_APP_KEY)
        ).strip()
        cls.APP_KEY = cls.KIWOOM_APP_KEY
        cls.KIWOOM_SECRET_KEY = (
            os.getenv("KIWOOM_SECRET_KEY") or os.getenv("SECRET_KEY") or os.getenv("APP_SECRET") or (cls.MOCK_APP_SECRET if cls.IS_MOCK else cls.REAL_APP_SECRET)
        ).strip()
        cls.SECRET_KEY = cls.KIWOOM_SECRET_KEY
        cls.APP_SECRET = cls.SECRET_KEY
        cls.ACCOUNT_NO = (os.getenv("KIWOOM_ACCOUNT_NO") or os.getenv("ACCOUNT_NO") or "").strip()
        cls.KIWOOM_ACCOUNT_NO = cls.ACCOUNT_NO
        cls.STOCK_CODE = (os.getenv("STOCK_CODE") or os.getenv("TARGET_STOCK_CODE") or "005930").strip().zfill(6)
        cls.TARGET_STOCK_CODE = cls.STOCK_CODE
        cls.STOCK_NAME = (os.getenv("STOCK_NAME") or os.getenv("TARGET_STOCK_NAME") or "삼성전자").strip()
        cls.TARGET_STOCK_NAME = cls.STOCK_NAME
        cls.GRID_MIN_PRICE = int(os.getenv("GRID_LOWER_PRICE") or os.getenv("GRID_MIN_PRICE", "50000"))
        cls.GRID_LOWER_PRICE = cls.GRID_MIN_PRICE
        cls.GRID_MAX_PRICE = int(os.getenv("GRID_UPPER_PRICE") or os.getenv("GRID_MAX_PRICE", "80000"))
        cls.GRID_UPPER_PRICE = cls.GRID_MAX_PRICE
        cls.GRID_LEVELS = int(os.getenv("GRID_COUNT") or os.getenv("GRID_LEVELS", "10"))
        cls.GRID_COUNT = cls.GRID_LEVELS
        cls.AMOUNT_PER_LEVEL = int(os.getenv("ORDER_AMOUNT_KRW") or os.getenv("AMOUNT_PER_LEVEL", "100000"))
        cls.ORDER_AMOUNT_KRW = cls.AMOUNT_PER_LEVEL
        cls.STATE_FILE_PATH = os.getenv("STATE_FILE_PATH", os.path.join(BASE_DIR, "grid_state.json"))
        cls.TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        cls.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        cls.TELEGRAM_ENABLED = bool(cls.TELEGRAM_BOT_TOKEN and cls.TELEGRAM_CHAT_ID)

    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """Generic accessor for configuration key with environment fallback."""
        env_val = os.getenv(key.upper())
        if env_val is not None:
            return env_val
        attr_name = key.upper()
        if hasattr(cls, attr_name):
            return getattr(cls, attr_name)
        return default

    @classmethod
    def validate(cls) -> bool:
        """
        Validate essential credentials and grid parameter sanity.
        Returns True if all required checks pass, False otherwise.
        """
        if cls.TRADING_MODE in ["MOCK", "REAL"]:
            if not cls.APP_KEY or not cls.SECRET_KEY:
                print("[Config Validation Error] Kiwoom API APP_KEY or SECRET_KEY is missing.")
                return False

        if cls.GRID_MIN_PRICE <= 0 or cls.GRID_MAX_PRICE <= cls.GRID_MIN_PRICE:
            print(f"[Config Validation Error] Invalid grid range: min={cls.GRID_MIN_PRICE}, max={cls.GRID_MAX_PRICE}")
            return False

        if cls.GRID_LEVELS <= 0 or cls.AMOUNT_PER_LEVEL <= 0:
            print(f"[Config Validation Error] Invalid grid settings: levels={cls.GRID_LEVELS}, amount={cls.AMOUNT_PER_LEVEL}")
            return False

        return True


class SystemSettings:
    """
    Unified settings loader reading from environment variables and config.json.
    """
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = Path(config_path) if config_path else Path(DEFAULT_CONFIG_PATH)
        self._data: Dict[str, Any] = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[SystemSettings] Config load warning: {e}")
        return {}

    def get(self, key: str, default: Any = None) -> Any:
        env_val = os.getenv(key.upper())
        if env_val is not None:
            return env_val
        return self._data.get(key, default)
