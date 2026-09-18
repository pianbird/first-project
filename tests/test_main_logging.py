import os
import sys
import datetime
import pytest
from unittest.mock import MagicMock, patch
from logging.handlers import RotatingFileHandler

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
trading_bot_dir = os.path.join(base_dir, "trading_bot")
for p in [base_dir, trading_bot_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from main import is_market_open, is_market_hours, setup_logger


def test_setup_logger_rotating_file_handler(tmp_path):
    log_file = str(tmp_path / "trading_bot.log")
    logger = setup_logger(log_file)

    handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) > 0
    rf_handler = handlers[0]
    assert rf_handler.maxBytes == 5 * 1024 * 1024
    assert rf_handler.backupCount == 5


def test_is_market_open():
    # Mon 10:00 KST -> Market Open
    mon_10am = datetime.datetime(2026, 9, 7, 10, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    assert is_market_open(mon_10am) is True
    assert is_market_hours(mon_10am) is True

    # Sat 10:00 KST -> Off Market
    sat_10am = datetime.datetime(2026, 9, 12, 10, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    assert is_market_open(sat_10am) is False

    # Mon 03:00 KST -> Off Market
    mon_3am = datetime.datetime(2026, 9, 7, 3, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    assert is_market_open(mon_3am) is False
