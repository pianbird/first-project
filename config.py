"""
MagicTrader - Global System Configuration & Timezone Standardization (config.py)
Production-Ready Entry Module
"""
from config.settings import (
    Config,
    SystemSettings,
    get_kst_now,
    get_kst_now_str,
    BASE_DIR,
    DEFAULT_CONFIG_PATH,
)

__all__ = [
    "Config",
    "SystemSettings",
    "get_kst_now",
    "get_kst_now_str",
    "BASE_DIR",
    "DEFAULT_CONFIG_PATH",
]
