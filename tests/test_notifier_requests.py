import os
import sys
import pytest
from unittest.mock import MagicMock, patch

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
trading_bot_dir = os.path.join(base_dir, "trading_bot")
for p in [base_dir, trading_bot_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from notifier import Notifier, TelegramNotifier
import requests


def test_notifier_requests_success():
    mock_config = MagicMock()
    mock_config.TELEGRAM_BOT_TOKEN = "12345:TOKEN"
    mock_config.TELEGRAM_CHAT_ID = "67890"
    mock_config.TELEGRAM_ENABLED = True

    notifier = Notifier(mock_config)

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_resp

        res = notifier.send_message("Test message", parse_mode="HTML")
        assert res is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert "12345:TOKEN" in args[0]
        assert kwargs["json"]["chat_id"] == "67890"
        assert kwargs["json"]["text"] == "Test message"


def test_notifier_error_isolation():
    mock_config = MagicMock()
    mock_config.TELEGRAM_BOT_TOKEN = "12345:TOKEN"
    mock_config.TELEGRAM_CHAT_ID = "67890"
    mock_config.TELEGRAM_ENABLED = True

    notifier = Notifier(mock_config)

    # 1. Test timeout exception isolation
    with patch("requests.post", side_effect=requests.exceptions.Timeout("Timeout connection")):
        res = notifier.send_message("Test message")
        assert res is False

    # 2. Test generic exception isolation in notify_order_sent
    with patch("requests.post", side_effect=Exception("Critical Network Failure")):
        res = notifier.notify_order_sent("005930", "삼성전자", "BUY", 10, 50000, 1)
        assert res is False

    # 3. Test missing credentials isolation
    mock_config.TELEGRAM_BOT_TOKEN = ""
    disabled_notifier = TelegramNotifier(mock_config)
    res = disabled_notifier.send_message("Hello")
    assert res is False
