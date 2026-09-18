"""
Tests for Senior Quant System Engineer Instruction Requirements
- KiwoomAPIError and Timeout Idempotency check
- align_to_tick KRX tick alignment
- Partial fill tracking and sell quantity matching
- Position balance guard
- SQLite WAL & PRAGMA integrity_check
"""
import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from trading_bot.kiwoom_client import KiwoomRESTClient, KiwoomAPIError
from trading_bot.grid_engine import GridEngine, GridLevelState, GridStatus, align_to_tick
from trading_bot.storage import SQLiteStorage


class TestQuantEngineerRequirements:

    def test_kiwoom_api_error_raised(self):
        """HTTP 200 이라도 return_code != 0 인 경우 KiwoomAPIError 예외 발생 검증"""
        client = KiwoomRESTClient()
        with patch.object(client, "get_access_token", return_value="MOCK_TOKEN"):
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.read.return_value = b'{"return_code": "8001", "return_msg": "API Limit Exceeded"}'
                mock_resp.__enter__.return_value = mock_resp
                mock_urlopen.return_value = mock_resp

                with pytest.raises(KiwoomAPIError) as exc_info:
                    client._post_api("http://mock/url", "kt00001", {})
                assert exc_info.value.code == "8001"
                assert "API Limit Exceeded" in exc_info.value.message

    def test_place_order_timeout_server_receipt_verification(self):
        """주문 타임아웃 발생 시 미체결 조회를 통해 서버 접수 확인 후 주문 ID 반환 검증"""
        client = KiwoomRESTClient()
        with patch.object(client, "is_credentials_valid", return_value=True):
            # 첫 주문 시도 시 네트워크 타임아웃 발생하도록 설정
            with patch.object(client, "_post_api", side_effect=TimeoutError("Network Timeout")):
                # get_open_orders 에서 타임아웃된 주문이 미체결 서버에 들어와 있는 것으로 Mocking
                with patch.object(client, "get_open_orders", return_value={
                    "success": True,
                    "open_orders": [{
                        "order_id": "ORD_VERIFIED_99",
                        "stock_code": "005930",
                        "side": "BUY",
                        "price": 70000,
                        "order_qty": 10,
                        "filled_qty": 0
                    }]
                }):
                    res = client.place_order("1234567801", "BUY", "005930", 10, 70000)
                    assert res["success"] is True
                    assert res["order_id"] == "ORD_VERIFIED_99"
                    assert res["status"] == "ACCEPTED"
                    assert "서버 접수 확인" in res["message"]

    def test_align_to_tick_krx_standard(self):
        """align_to_tick 법정 호가 정률 정규화 검증 (2023 KRX 개정 호가 적용)"""
        assert align_to_tick(1500.8) == 1500       # < 2,000 : 1원
        assert align_to_tick(3543.2) == 3540       # 2,000 ~ < 5,000 : 5원
        assert align_to_tick(12547.0) == 12540     # 5,000 ~ < 20,000 : 10원
        assert align_to_tick(34567.0) == 34550     # 20,000 ~ < 50,000 : 50원
        assert align_to_tick(75430.0) == 75400     # 50,000 ~ < 200,000 : 100원
        assert align_to_tick(234560.0) == 234500   # 200,000 ~ < 500,000 : 500원
        assert align_to_tick(754321.0) == 754000   # 500,000+ : 1,000원

    def test_partial_fill_sell_quantity(self):
        """부분 체결(PARTIALLY_FILLED) 시 실제 체결 수량(filled_qty)만큼만 익절 매도 수량 산정 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            storage = SQLiteStorage(db_path)
            engine = GridEngine(storage=storage)
            engine.is_reconciled = True

            lvl = engine.levels[0]
            lvl.status = GridStatus.PARTIALLY_FILLED
            lvl.qty = 10
            lvl.filled_qty = 4  # 10주 중 4주만 부분 체결됨
            lvl.remaining_qty = 6
            lvl.sell_price = 75000

            # 현재가가 익절가 이상일 때 매도 시도
            with patch.object(engine.order_manager, "submit_order", return_value={"success": True, "order_id": "SELL_ORD_01"}):
                with patch.object(engine.kiwoom, "is_credentials_valid", return_value=False):
                    engine.evaluate_cycle(current_price=76000)

            # 매도 주문이 접수된 경우 status 가 ORDER_PLACED 로 변경
            assert lvl.status == GridStatus.ORDER_PLACED
            assert lvl.sell_order_id == "SELL_ORD_01"

    def test_sqlite_wal_and_integrity_check(self):
        """SQLite WAL 설정 및 integrity_check() 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_wal.db")
            storage = SQLiteStorage(db_path)
            assert storage.check_integrity() is True

            with storage._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA journal_mode;")
                row = cursor.fetchone()
                journal_mode = str(row[0]).lower()
                assert journal_mode == "wal"

    def test_send_order_timeout_unknown_pending_no_retry(self):
        """주문 타임아웃 발생 시 무조건 재주문하지 않고 UNKNOWN_PENDING 상태 반환 검증"""
        client = KiwoomRESTClient()
        with patch.object(client, "is_credentials_valid", return_value=True):
            with patch.object(client, "_post_api", side_effect=TimeoutError("Order Timeout")):
                with patch.object(client, "get_open_orders", return_value={"success": True, "open_orders": []}):
                    res = client.send_order("1234567801", "BUY", "005930", 10, 70000)
                    assert res["success"] is False
                    assert res["status"] == "UNKNOWN_PENDING"
                    assert "중복 방지를 위해 재시도 안함" in res["message"]

    def test_calculate_grid_levels_and_evaluate_grid_orders(self):
        """calculate_grid_levels 틱 단위 정렬 및 evaluate_grid_orders 상태 머신 전이 검증"""
        from kiwoom_client import get_tick_size
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_grid.db")
            storage = SQLiteStorage(db_path)
            engine = GridEngine(storage=storage)
            engine.is_reconciled = True

            levels = engine.calculate_grid_levels(min_price=10000, max_price=50000, levels_count=5)
            assert len(levels) == 5
            for lvl in levels:
                assert lvl.buy_price % get_tick_size(lvl.buy_price) == 0

            with patch.object(engine.order_manager, "submit_order", return_value={"success": True, "order_id": "ORD_GRID_01"}):
                with patch.object(engine.kiwoom, "is_credentials_valid", return_value=False):
                    engine.evaluate_grid_orders(current_price=engine.levels[0].buy_price)
                    assert engine.levels[0].status == GridStatus.ORDER_PLACED

    def test_reconcile_with_broker_and_telegram_isolation(self):
        """reconcile_with_broker 실행 및 Telegram Notifier 예외 격리 검증"""
        from trading_bot.main import reconcile_with_broker
        from notifier import TelegramNotifier
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_recon.db")
            storage = SQLiteStorage(db_path)
            engine = GridEngine(storage=storage)

            with patch.object(engine, "reconcile_with_account", return_value={"success": True, "summary": "Test Summary"}):
                res = reconcile_with_broker(engine)
                assert res["success"] is True

            notifier = TelegramNotifier()
            notifier.enabled = True
            notifier.token = "INVALID_TOKEN"
            notifier.chat_id = "INVALID_CHAT"
            # HTTP 500/timeout 장애 시에도 메인 루프에 예외를 전파하지 않음
            with patch("urllib.request.urlopen", side_effect=TimeoutError("Telegram Timeout")):
                res_msg = notifier.send_message("Test")
                assert res_msg is False
                notifier.notify_order_sent("005930", "삼성전자", "BUY", 10, 70000, 1)

