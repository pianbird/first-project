import os
import sys
import pytest
from unittest.mock import MagicMock, patch

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
trading_bot_dir = os.path.join(base_dir, "trading_bot")
for p in [base_dir, trading_bot_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from kiwoom_client import KiwoomRESTClient


def test_kiwoom_client_authenticate_and_token():
    mock_config = MagicMock()
    mock_config.BASE_URL = "https://mockapi.kiwoom.com"
    mock_config.IS_MOCK = True
    mock_config.APP_KEY = "MOCK_KEY"
    mock_config.APP_SECRET = "MOCK_SECRET"
    mock_config.TOKEN_CACHE_PATH = "/tmp/fake_token_cache.json"

    client = KiwoomRESTClient(mock_config)

    with patch.object(client, "get_access_token", return_value="MOCK_TOKEN_12345") as mock_gat:
        token = client.authenticate(force_refresh=True)
        assert token == "MOCK_TOKEN_12345"
        mock_gat.assert_called_with(force_refresh=True)

        token2 = client._ensure_token()
        assert token2 == "MOCK_TOKEN_12345"
        mock_gat.assert_called_with(force_refresh=False)


def test_get_current_price():
    mock_config = MagicMock()
    mock_config.BASE_URL = "https://mockapi.kiwoom.com"
    mock_config.IS_MOCK = True
    mock_config.APP_KEY = "MOCK_KEY"
    mock_config.APP_SECRET = "MOCK_SECRET"

    client = KiwoomRESTClient(mock_config)

    with patch.object(client, "_post_api") as mock_post:
        mock_post.return_value = {
            "return_code": "0",
            "cur_prc": "-55000"
        }
        price = client.get_current_price("005930")
        assert price == 55000
        mock_post.assert_called_once()


def test_send_order_with_notifier():
    mock_config = MagicMock()
    mock_config.BASE_URL = "https://mockapi.kiwoom.com"
    mock_config.IS_MOCK = True
    mock_config.APP_KEY = "MOCK_KEY"
    mock_config.APP_SECRET = "MOCK_SECRET"
    mock_config.ACCOUNT_NO = "8012345601"
    mock_config.TARGET_STOCK_NAME = "삼성전자"

    client = KiwoomRESTClient(mock_config)
    mock_notifier = MagicMock()

    with patch.object(client, "place_order") as mock_place:
        mock_place.return_value = {
            "success": True,
            "order_id": "ORD999",
            "price": 50000
        }

        res = client.send_order(
            account_no="8012345601",
            order_type="BUY",
            stock_code="005930",
            qty=10,
            price=50000,
            notifier=mock_notifier
        )

        assert res["success"] is True
        mock_notifier.notify_order_sent.assert_called_once_with(
            stock_code="005930",
            stock_name="삼성전자",
            side="BUY",
            qty=10,
            price=50000,
            level_id=1
        )
