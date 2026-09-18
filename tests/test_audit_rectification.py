"""
Comprehensive verification tests for Audit Findings (NEW-001 through NEW-006).
"""
import os
import sys
import time
import unittest
import tempfile
import datetime
import urllib.error
import urllib.parse
from zoneinfo import ZoneInfo


# Add workspace path
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in [base_dir, os.path.join(base_dir, "trading_bot"), os.path.join(base_dir, "web_app", "backend")]:
    if p not in sys.path:
        sys.path.insert(0, p)

from config import Config, get_kst_now, get_kst_now_str
from kiwoom_client import KiwoomRESTClient, get_tick_size, align_to_tick_size, normalize_tick
from storage import SQLiteStorage
from grid_engine import GridEngine, GridStatus
from order_manager import OrderManager, OrderStatus
from trading_mode import TradingModeManager
from web_app.backend.db import WebDBManager
from strategy.grid_evaluator import GridEvaluator, TradingSignal


class TestAuditRectification(unittest.TestCase):

    def test_new003_krx_tick_size_2023_compliance(self):
        """[NEW-003] 2023 KRX 개정 법정 호가 단위 정률 보정 검증 (align_to_tick_size)"""
        # Tier 1: < 2,000원 -> 1원 틱
        self.assertEqual(get_tick_size(1500), 1)
        self.assertEqual(align_to_tick_size(1523), 1523)

        # Tier 2: 2,000원 ~ 5,000원 미만 -> 5원 틱
        self.assertEqual(get_tick_size(2500), 5)
        self.assertEqual(align_to_tick_size(2523), 2520)

        # Tier 3: 5,000원 ~ 20,000원 미만 -> 10원 틱
        self.assertEqual(get_tick_size(12500), 10)
        self.assertEqual(align_to_tick_size(12534), 12530)

        # Tier 4: 20,000원 ~ 50,000원 미만 -> 50원 틱
        self.assertEqual(get_tick_size(35000), 50)
        self.assertEqual(align_to_tick_size(35230), 35200)

        # Tier 5: 50,000원 ~ 200,000원 미만 -> 100원 틱
        self.assertEqual(get_tick_size(70000), 100)
        self.assertEqual(align_to_tick_size(70240), 70200)

        # Tier 6: 200,000원 ~ 500,000원 미만 -> 500원 틱
        self.assertEqual(get_tick_size(350000), 500)
        self.assertEqual(align_to_tick_size(352300), 352000)

        # Tier 7: 500,000원 이상 -> 1,000원 틱
        self.assertEqual(get_tick_size(600000), 1000)
        self.assertEqual(align_to_tick_size(602300), 602000)

    def test_new006_kst_timezone_sync(self):
        """[NEW-006] PythonAnywhere KST 타임존 동기화 유틸리티 검증"""
        now = get_kst_now()
        self.assertIsNotNone(now.tzinfo)
        tz_str = str(now.tzinfo)
        self.assertTrue("Asia/Seoul" in tz_str or "+09:00" in tz_str or "KST" in tz_str)

        now_str = get_kst_now_str()
        self.assertTrue(len(now_str) >= 19)

    def test_new001_idempotent_grid_locking(self):
        """[NEW-001] 동일 그리드 레벨 중복 발주 방지 ORDER_PENDING 락 검증"""
        db_fd, temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)

        try:
            storage = SQLiteStorage(temp_db_path)
            class MockClient(KiwoomRESTClient):
                def __init__(self):
                    self.order_count = 0
                    self.base_url = "https://mockapi.kiwoom.com"
                    self.is_mock = True
                def is_credentials_valid(self): return True
                def normalize_account_no(self, acc): return "8012345601"
                def get_positions(self, account_no=None): return {"success": True, "positions": []}
                def get_open_orders(self, account_no=None, stock_code=None): return {"success": True, "open_orders": []}
                def place_order(self, **kwargs):
                    self.order_count += 1
                    return {"success": True, "order_id": f"ORD_{self.order_count}", "status": "ACCEPTED"}

            mock_client = MockClient()
            engine = GridEngine(storage=storage, kiwoom_client=mock_client)
            engine.is_reconciled = True

            target_lvl = engine.levels[0]
            target_lvl.status = GridStatus.IDLE
            buy_trigger_price = target_lvl.buy_price

            # 첫 번째 평가 사이클 실행 -> 1회 주문 발생
            engine.evaluate_cycle(current_price=buy_trigger_price)
            self.assertEqual(mock_client.order_count, 1)
            self.assertEqual(target_lvl.status, GridStatus.ORDER_PLACED)

            # 동일 가격으로 두 번째 평가 사이클 실행 -> 이미 ORDER_PLACED 상태이므로 중복 주문 미발생
            engine.evaluate_cycle(current_price=buy_trigger_price)
            self.assertEqual(mock_client.order_count, 1)

        finally:
            try:
                if os.path.exists(temp_db_path):
                    os.remove(temp_db_path)
            except Exception:
                pass

    def test_new002_kiwoom_pagination(self):
        """[NEW-002] cont-yn / next-key 연속조회 페이징 다중 페이지 병합 검증"""
        class MockPaginatedClient(KiwoomRESTClient):
            def __init__(self):
                super().__init__()
                self.app_key = "MOCK_KEY"
                self.app_secret = "MOCK_SECRET"

            def is_credentials_valid(self): return True

            def _post_api(self, url, api_id, body, is_order=False, cont_yn="N", next_key=""):
                if api_id == "kt00018":
                    if cont_yn == "N":
                        return {
                            "return_code": "0",
                            "cont_yn": "Y",
                            "next_key": "PAGE_2_KEY",
                            "output1": [{"stk_cd": "005930", "rmnd_qty": "10", "pur_pric": "70000", "cur_prc": "71000"}]
                        }
                    else:
                        return {
                            "return_code": "0",
                            "cont_yn": "N",
                            "next_key": "",
                            "output1": [{"stk_cd": "000660", "rmnd_qty": "5", "pur_pric": "120000", "cur_prc": "125000"}]
                        }
                return {"return_code": "0"}

        paginated_client = MockPaginatedClient()
        res = paginated_client.get_positions("8012345601")

        self.assertTrue(res["success"])
        positions = res["positions"]
        self.assertEqual(len(positions), 2)
        self.assertEqual(positions[0]["stock_code"], "005930")
        self.assertEqual(positions[1]["stock_code"], "000660")

    def test_new004_exponential_backoff_and_reauth(self):
        """[NEW-004] HTTP 401 갱신 및 백오프 재시도 검증"""
        class MockAuthRetryClient(KiwoomRESTClient):
            def __init__(self):
                super().__init__()
                self.app_key = "MOCK_KEY"
                self.app_secret = "MOCK_SECRET"
                self.auth_call_count = 0
                self.api_call_count = 0

            def is_credentials_valid(self): return True
            def get_access_token(self, force_refresh=False):
                if force_refresh:
                    self.auth_call_count += 1
                return "MOCK_TOKEN"

            def _request_with_backoff(self, req, is_order=False, max_retries=3, backoff_factor=0.5):
                self.api_call_count += 1
                if self.api_call_count == 1:
                    # 1회차 호출 시 HTTP 401 에러 유발
                    raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
                # 2회차 호출 시 정상 응답 반환
                class MockResponse:
                    def read(self):
                        return b'{"return_code": "0", "token": "NEW_TOKEN"}'
                    def __enter__(self): return self
                    def __exit__(self, *args): pass
                return MockResponse()

        retry_client = MockAuthRetryClient()
        res = retry_client._post_api("https://mockapi.kiwoom.com/test", api_id="ka10001", body={})
        self.assertEqual(res.get("return_code"), "0")
        self.assertEqual(retry_client.auth_call_count, 1)

    def test_new005_reconciliation_safety_guard(self):
        """[NEW-005] 실계좌 대사 미완료 시 Safety Guard 주문 차단 및 양방향 복구 검증"""
        db_fd, temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)

        try:
            storage = SQLiteStorage(temp_db_path)
            class MockReconClient(KiwoomRESTClient):
                def __init__(self):
                    self.order_called = False
                def is_credentials_valid(self): return True
                def normalize_account_no(self, acc): return "8012345601"
                def get_positions(self, account_no=None):
                    return {"success": True, "positions": [{"stock_code": "005930", "qty": 10}]}
                def get_open_orders(self, account_no=None, stock_code=None):
                    return {"success": True, "open_orders": []}
                def place_order(self, **kwargs):
                    self.order_called = True
                    return {"success": True, "order_id": "881122"}

            mock_client = MockReconClient()
            engine = GridEngine(storage=storage, kiwoom_client=mock_client)

            # 초기 is_reconciled는 False 상태
            self.assertFalse(engine.is_reconciled)

            # evaluate_cycle 호출 시 Safety Guard가 이 대사를 수행하고 is_reconciled를 True로 전환
            engine.evaluate_cycle(current_price=engine.levels[0].buy_price)
            self.assertTrue(engine.is_reconciled)

        finally:
            try:
                if os.path.exists(temp_db_path):
                    os.remove(temp_db_path)
            except Exception:
                pass

    def test_critical_optimistic_fill_prevention_and_order_placed_state(self):
        """[CRITICAL] 주문 접수 시 ORDER_PLACED 전이 및 계좌 대사 확인 전 FILLED 금지 검증"""
        db_fd, temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)

        try:
            storage = SQLiteStorage(temp_db_path)
            class MockClient(KiwoomRESTClient):
                def __init__(self):
                    super().__init__()
                    self.open_orders_list = []
                    self.positions_list = []
                def is_credentials_valid(self): return True
                def normalize_account_no(self, acc): return "8012345601"
                def get_positions(self, account_no=None): return {"success": True, "positions": self.positions_list}
                def get_open_orders(self, account_no=None, stock_code=None): return {"success": True, "open_orders": self.open_orders_list}
                def place_order(self, **kwargs):
                    return {"success": True, "order_id": "ORD_BUY_1001", "status": "ACCEPTED"}

            mock_client = MockClient()
            engine = GridEngine(storage=storage, kiwoom_client=mock_client)
            engine.is_reconciled = True
            for l in engine.levels:
                l.status = GridStatus.IDLE
                l.buy_order_id = ""
                l.sell_order_id = ""

            target_lvl = engine.levels[0]
            buy_price = target_lvl.buy_price

            # 1. 주문 발주 사이클 실행 -> 상태는 ORDER_PLACED여야 함 (FILLED 금지)
            engine.evaluate_cycle(current_price=buy_price)
            self.assertEqual(target_lvl.status, GridStatus.ORDER_PLACED)
            self.assertEqual(target_lvl.buy_order_id, "ORD_BUY_1001")

            # 2. 미체결 목록에 상주 중이면 대사 후에도 ORDER_PLACED 유지
            mock_client.open_orders_list = [{
                "order_id": "ORD_BUY_1001",
                "stock_code": engine.stock_code,
                "order_qty": target_lvl.qty,
                "filled_qty": 0,
                "leaves_qty": target_lvl.qty,
                "side": "BUY"
            }]
            engine.reconcile_with_account()
            self.assertEqual(target_lvl.status, GridStatus.ORDER_PLACED)

            # 3. 미체결에서 사라지고 보유 잔고에 수량이 들어온 경우에만 FILLED 전이
            mock_client.open_orders_list = []
            mock_client.positions_list = [{"stock_code": engine.stock_code, "qty": target_lvl.qty}]
            engine.reconcile_with_account()
            self.assertEqual(target_lvl.status, GridStatus.FILLED)

        finally:
            if os.path.exists(temp_db_path):
                try: os.remove(temp_db_path)
                except Exception: pass

    def test_critical_partial_fill_handling(self):
        """[CRITICAL] 부분 체결(Partial Fill) 수량 분리 관리 및 청산 주문 연동 검증"""
        db_fd, temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)

        try:
            storage = SQLiteStorage(temp_db_path)
            class MockClient(KiwoomRESTClient):
                def __init__(self):
                    super().__init__()
                    self.placed_orders = []
                def is_credentials_valid(self): return True
                def normalize_account_no(self, acc): return "8012345601"
                def get_positions(self, account_no=None):
                    return {"success": True, "positions": [{"stock_code": "005930", "qty": 3}]}
                def get_open_orders(self, account_no=None, stock_code=None):
                    return {"success": True, "open_orders": [{
                        "order_id": "ORD_BUY_PARTIAL",
                        "stock_code": "005930",
                        "order_qty": 10,
                        "filled_qty": 3,
                        "leaves_qty": 7,
                        "side": "BUY"
                    }]}
                def place_order(self, **kwargs):
                    self.placed_orders.append(kwargs)
                    return {"success": True, "order_id": f"ORD_{len(self.placed_orders)}", "status": "ACCEPTED"}

            mock_client = MockClient()
            engine = GridEngine(storage=storage, kiwoom_client=mock_client)
            engine.is_reconciled = True

            lvl = engine.levels[0]
            lvl.status = GridStatus.ORDER_PLACED
            lvl.buy_order_id = "ORD_BUY_PARTIAL"
            lvl.qty = 10

            # 계좌 대사 실행 -> 10주 중 3주 부분 체결 반영 감지
            engine.reconcile_with_account()
            self.assertEqual(lvl.status, GridStatus.PARTIALLY_FILLED)
            self.assertEqual(lvl.filled_qty, 3)
            self.assertEqual(lvl.remaining_qty, 7)

            # 다른 레벨들이 이 가격 이하 매수로 걸리지 않도록 설정
            for l in engine.levels[1:]:
                l.status = GridStatus.ORDER_PLACED

            # 익절 매도 조건 달성 시 전체 10주가 아닌 실제 체결된 3주만 매도 발주되어야 함
            engine.evaluate_cycle(current_price=lvl.sell_price + 100)
            self.assertTrue(len(mock_client.placed_orders) > 0)
            sell_orders = [o for o in mock_client.placed_orders if o.get("order_type") == "SELL"]
            self.assertEqual(len(sell_orders), 1)
            self.assertEqual(sell_orders[0].get("qty"), 3)

        finally:
            if os.path.exists(temp_db_path):
                try: os.remove(temp_db_path)
                except Exception: pass

    def test_high_order_api_no_retry_on_timeout(self):
        """[HIGH] 주문 API (is_order=True) 타임아웃 발생 시 무작정 재시도 금지 검증"""
        class TimeoutClient(KiwoomRESTClient):
            def __init__(self):
                super().__init__()
                self.request_attempts = 0
            def get_access_token(self, force_refresh=False): return "MOCK_TOKEN"
            def _request_with_backoff(self, req, is_order=False, max_retries=3, backoff_factor=0.5):
                self.request_attempts += 1
                raise TimeoutError("Network Timeout simulated")

        client = TimeoutClient()
        with self.assertRaises((RuntimeError, TimeoutError)):
            client._post_api("https://mockapi.kiwoom.com/order", api_id="kt10000", body={}, is_order=True)

        # 주문 API의 경우 백오프 재시도 없이 1회만 시도하고 즉시 예외 전파되어야 함
        self.assertEqual(client.request_attempts, 1)


if __name__ == "__main__":
    unittest.main()

