"""
MagicTrader - Database & Persistence Management Facade (storage.py)
Production-Ready SQLite Persistence Module
"""
from trading_bot.storage import (
    Storage,
    SQLiteStorage,
    atomic_write_json,
    load_json,
)

__all__ = [
    "Storage",
    "SQLiteStorage",
    "atomic_write_json",
    "load_json",
]
