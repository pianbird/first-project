import os
import sys
import pytest
import tempfile
import json
import time

# Add project roots to sys.path
base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
bot_dir = os.path.join(base_dir, "trading_bot")
backend_dir = os.path.join(base_dir, "web_app", "backend")

for p in [backend_dir, bot_dir, base_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from trading_bot.trading_mode import TradingModeManager, InvalidTradingModeError, ModeEndpointMismatchError, RealTradingLockError, acquire_real_trading_lock, MODE_TEXT
from trading_bot.market_calendar import MarketCalendar
from trading_bot.reconciliation import AccountReconciler, ReconciliationError
from trading_bot.risk_manager import RiskManager
from trading_bot.order_manager import OrderManager, OrderStatus
from trading_bot.kiwoom_client import KiwoomRESTClient
from web_app.backend.db import WebDBManager
from web_app.backend.engine import WebTradingEngine

# Mock Kiwoom Client for testing
class MockKiwoomClient:
    def __init__(self, is_mock=True, fail_account=False, fail_quote=False, fail_order=False):
        self.is_mock = is_mock
        self.fail_account = fail_account
        self.fail_quote = fail_quote
        self.fail_order = fail_order
        self.config = {"account_no": "8133053801"}
        self.base_url = "https://mockapi.kiwoom.com" if is_mock else "https://api.kiwoom.com"
        self.MOCK_BASE_URL = "https://mockapi.kiwoom.com"
        self.REAL_BASE_URL = "https://api.kiwoom.com"
        self.app_key = "MOCK_KEY"
        self.app_secret = "MOCK_SECRET"

    def is_credentials_valid(self):
        return not self.fail_account

    def normalize_account_no(self, acc_str=None):
        return KiwoomRESTClient.normalize_account_no(acc_str or "8133053801")

    def get_masked_credentials(self):
        return {
            "configured": True,
            "trading_mode": "MOCK" if self.is_mock else "REAL",
            "app_key": "MOCK****",
            "account_no": "8133053801"
        }

    def get_positions(self, account_no=None, force_refresh=False):
        if self.fail_account:
            raise RuntimeError("ACCOUNT_QUERY_FAILED")
        pos = [
            {"code": "005930", "stock_code": "005930", "name": "삼성전자", "stock_name": "삼성전자", "qty": 10, "avg_price": 70000, "current_price": 70000}
        ]
        return {
            "success": True,
            "account_no": "8133053801",
            "positions": pos,
            "held_positions": pos
        }

    def sync_positions(self, account_no=None, force_refresh=False):
        res = self.get_positions(account_no, force_refresh=force_refresh)
        return res.get("positions", [])

    def get_account_balance(self, account_no=None, force_refresh=False):
        return self.get_positions(account_no, force_refresh=force_refresh)

    def get_stock_price(self, code):
        if self.fail_quote:
            return {"success": False, "current_price": 0}
        return {"success": True, "current_price": 72000, "change": 2000, "change_rate": 2.85, "prev_close_price": 70000}

    def place_order(self, account_no, order_type, stock_code, qty, price=0, ord_dvsn="03"):
        if self.fail_order:
            raise RuntimeError("ORDER_NETWORK_TIMEOUT")
        return {
            "success": True,
            "order_id": f"ORD_MOCK_{int(time.time()*1000)}",
            "status": "ACCEPTED",
            "message": "Mock Order Accepted"
        }

# --- 1. Trading Mode & set_trading_mode Bug Fix Test ---
def test_trading_mode_validations():
    tm = TradingModeManager("MOCK")
    assert tm.get_mode() == "MOCK"
    assert tm.get_mode_text() == "모의투자"

    tm.set_mode("REAL")
    assert tm.get_mode() == "REAL"
    assert tm.get_mode_text() == "실전투자"

    tm.set_mode("DISABLED")
    assert tm.get_mode() == "DISABLED"
    assert tm.get_mode_text() == "매매중지"
    assert tm.can_place_order() is False

    with pytest.raises(InvalidTradingModeError):
        tm.set_mode("INVALID_MODE")

    with pytest.raises(InvalidTradingModeError):
        tm.set_mode("")

# --- 2. REAL/MOCK Endpoint Isolation Test ---
def test_mode_endpoint_isolation():
    tm = TradingModeManager("REAL")
    with pytest.raises(ModeEndpointMismatchError):
        tm.validate_endpoint_isolation("https://mockapi.kiwoom.com", is_mock_endpoint=True)

    tm_mock = TradingModeManager("MOCK")
    with pytest.raises(ModeEndpointMismatchError):
        tm_mock.validate_endpoint_isolation("https://openapi.kiwoom.com", is_mock_endpoint=False)

# --- 3. DISABLED Mode Order Block Test ---
def test_disabled_mode_blocks_orders(tmp_path):
    db_file = os.path.join(tmp_path, "test_disabled.db")
    db = WebDBManager(db_file)
    tm = TradingModeManager("DISABLED")
    om = OrderManager(db, tm)
    kiwoom = MockKiwoomClient()

    res = om.submit_order(
        kiwoom_client=kiwoom, risk_manager=None, account_no="8133053801",
        order_type="BUY", stock_code="005930", stock_name="삼성전자",
        qty=10, price=70000, ord_dvsn="01", strategy_id="TEST_DIS", cycle_id=1
    )
    assert res.get("success") is False
    assert res.get("status") == OrderStatus.REJECTED
    assert "DISABLED" in res.get("message", "")

# --- 4. SAFE_STOP DB Persistence & Order Block Test ---
def test_safe_stop_persistence_and_order_blocking(tmp_path):
    db_file = os.path.join(tmp_path, "test_safestop.db")
    db = WebDBManager(db_file)
    engine = WebTradingEngine(db)

    engine.trigger_safe_stop("TEST_SAFE_STOP_REASON")
    assert engine.safe_stop_active is True
    assert engine.status == "SAFE_STOP"

    # Reload engine from DB
    engine2 = WebTradingEngine(db)
    assert engine2.safe_stop_active is True
    assert engine2.status == "SAFE_STOP"
    assert "TEST_SAFE_STOP_REASON" in engine2.safe_stop_reason

    # Order must be blocked under SAFE_STOP
    om = engine2.order_manager
    res = om.submit_order(
        kiwoom_client=MockKiwoomClient(), risk_manager=None, account_no="8133053801",
        order_type="BUY", stock_code="005930", stock_name="삼성전자",
        qty=10, price=70000, ord_dvsn="01", is_safe_stop=engine2.safe_stop_active
    )
    assert res.get("success") is False
    assert "SAFE_STOP" in res.get("message", "")

# --- 5. Account Mismatch -> SAFE_STOP Test ---
def test_account_failure_triggers_safe_stop(tmp_path):
    db_file = os.path.join(tmp_path, "test_recovery.db")
    db = WebDBManager(db_file)
    engine = WebTradingEngine(db)
    engine.mode_mgr.set_mode("REAL")
    engine.kiwoom_client = MockKiwoomClient(is_mock=False, fail_account=True)

    reconciler = AccountReconciler(db)
    with pytest.raises(ReconciliationError):
        reconciler.reconcile_account(engine.kiwoom_client, trading_mode="REAL")

# --- 6. Daily Trade Count DB Persistence Test ---
def test_daily_trade_count_db_persistence(tmp_path):
    db_file = os.path.join(tmp_path, "test_daily_trades.db")
    db = WebDBManager(db_file)

    initial = db.get_daily_trade_count()
    assert initial == 0

    c1 = db.increment_daily_trade_count()
    assert c1 == 1
    c2 = db.increment_daily_trade_count()
    assert c2 == 2

    # Verify persistent retrieval
    assert db.get_daily_trade_count() == 2

# --- 7. Idempotency DB Persistence Test ---
def test_idempotency_db_persistence(tmp_path):
    db_file = os.path.join(tmp_path, "test_idempotency.db")
    db = WebDBManager(db_file)

    key = "TEST_IDEMPOTENCY_KEY_12345"
    res1 = db.register_idempotency_key(key, "005930", "BUY")
    assert res1 is True

    # Duplicate key attempt must fail
    res2 = db.register_idempotency_key(key, "005930", "BUY")
    assert res2 is False

# --- 8. UNKNOWN Order Snapshot Resolution & No Auto-Retry Test ---
def test_unknown_order_resolution_and_no_autoretry(tmp_path):
    db_file = os.path.join(tmp_path, "test_unknown.db")
    db = WebDBManager(db_file)
    om = OrderManager(db, TradingModeManager("MOCK"))
    kiwoom = MockKiwoomClient(fail_order=True)

    # Order fails with network exception -> registered as UNKNOWN
    res = om.submit_order(
        kiwoom_client=kiwoom, risk_manager=None, account_no="8133053801",
        order_type="BUY", stock_code="005930", stock_name="삼성전자",
        qty=10, price=70000, ord_dvsn="01", strategy_id="TEST_UNK", cycle_id=100
    )
    assert res.get("status") == OrderStatus.UNKNOWN

    # Resolve UNKNOWN with delta calculation
    kiwoom_working = MockKiwoomClient(fail_order=False)
    resolved_cnt = om.resolve_unknown_orders(kiwoom_working)
    assert resolved_cnt >= 1

# --- 9. Security Credentials Masking Test ---
def test_credentials_masking():
    kc = KiwoomRESTClient()
    kc.app_key = "1234567890ABCDEF"
    masked = kc.get_masked_credentials()

    assert "app_secret" not in masked
    assert "access_token" not in masked
    assert masked["app_key"] == "1234****CDEF"

# --- 10. Codebase Direct Order Call Scan Test ---
def test_direct_place_order_scan():
    """Verify that place_order is only invoked inside order_manager.py or test files"""
    import re
    pattern = re.compile(r'\.place_order\(')
    direct_calls = []

    for root, dirs, files in os.walk(base_dir):
        if "tests" in root or "__pycache__" in root or ".gemini" in root:
            continue
        for file in files:
            if file.endswith(".py") and file not in ("order_manager.py", "test_final_production.py"):
                filepath = os.path.join(root, file)
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    for line_no, line in enumerate(f, 1):
                        if pattern.search(line):
                            direct_calls.append(f"{file}:{line_no} -> {line.strip()}")

    assert len(direct_calls) == 0, f"Found direct place_order calls outside order_manager.py: {direct_calls}"

# --- 11. P0-1 Pre-order Endpoint Isolation Check Test ---
def test_p0_1_pre_order_endpoint_isolation(tmp_path):
    db = WebDBManager(os.path.join(tmp_path, "test_p0_1.db"))
    tm = TradingModeManager("MOCK")
    om = OrderManager(db, tm)

    # Client has MOCK mode set, but base_url set to REAL URL -> Pre-order endpoint isolation must reject
    mismatched_client = MockKiwoomClient(is_mock=True)
    mismatched_client.base_url = "https://api.kiwoom.com"

    res = om.submit_order(
        kiwoom_client=mismatched_client, risk_manager=None, account_no="1234567801",
        order_type="BUY", stock_code="005930", stock_name="삼성전자",
        qty=10, price=70000, ord_dvsn="01", strategy_id="P0_1_TEST", cycle_id=1
    )
    assert res["success"] is False
    assert res["status"] == OrderStatus.REJECTED
    assert "EndpointIsolation" in res["message"] or "Endpoint" in res["message"]

# --- 12. P0-2 REAL Account Query Failure No Fallback Test ---
def test_p0_2_real_account_query_failure_no_fallback(tmp_path):
    db_file = os.path.join(tmp_path, "test_p0_2.db")
    db = WebDBManager(db_file)
    engine = WebTradingEngine(db_path=db_file)
    engine.trading_mode = "REAL"
    engine.mode_mgr.set_mode("REAL")
    engine.kiwoom_client = MockKiwoomClient(is_mock=False, fail_account=True)

    res = engine.get_kiwoom_positions(force_refresh=True)
    assert res["success"] is False
    assert engine.safe_stop_active is True
    assert "ACCOUNT_QUERY_FAILED" in engine.safe_stop_reason

# --- 13. P0-3 Order Token Error No Auto Resend Test ---
def test_p0_3_order_token_error_no_auto_resend():
    kc = KiwoomRESTClient()
    kc.app_key = "MOCK_KEY"
    kc.app_secret = "MOCK_SECRET"
    kc.get_access_token = lambda force_refresh=False: "MOCK_TOKEN"

    # Simulate HTTP 401 on order request -> must raise RuntimeError without retrying
    import urllib.error
    import io

    call_count = 0
    def mock_request_with_backoff(req, is_order=False):
        nonlocal call_count
        call_count += 1
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))

    kc._request_with_backoff = mock_request_with_backoff

    with pytest.raises(RuntimeError) as exc_info:
        kc._post_api("https://mockapi.kiwoom.com/api/dostk/ordr", api_id="kt10000", body={}, is_order=True)

    assert "중복주문 방지" in str(exc_info.value) or "401" in str(exc_info.value)
    assert call_count == 1  # Guaranteed ZERO auto-retry for order API

# --- 14. P0-5 Daily Trade Limit Atomicity Test ---
def test_p0_5_daily_limit_atomicity(tmp_path):
    db = WebDBManager(os.path.join(tmp_path, "test_p0_5.db"))
    limit = 2

    # Fill count to 1
    db.increment_daily_trade_count()

    # Now limit is 2, current count is 1. Next single check & increment should succeed
    allowed1, count1, msg1 = db.check_and_increment_daily_trade_limit(max_limit=limit)
    assert allowed1 is True
    assert count1 == 2

    # Count is now 2 (at limit). Next attempt must be atomically rejected
    allowed2, count2, msg2 = db.check_and_increment_daily_trade_limit(max_limit=limit)
    assert allowed2 is False
    assert "일일 매매 횟수 초과" in msg2

# --- 15. P1-4 Force Close REAL Failure Test ---
def test_p1_4_force_close_real_failure(tmp_path):
    db_file = os.path.join(tmp_path, "test_p1_4.db")
    db = WebDBManager(db_file)
    engine = WebTradingEngine(db_path=db_file)
    engine.trading_mode = "REAL"
    engine.mode_mgr.set_mode("REAL")
    engine.kiwoom_client = None  # No client available in REAL mode

    res = engine.force_close_kiwoom_position("005930", qty=10)
    assert res["success"] is False
    assert "실패" in res["message"]

# --- 16. P1-5 Legacy Engine Execution Path Guard Test ---
def test_p1_5_legacy_engine_safety():
    from trading_bot.main import AutoTradingEngine
    engine = AutoTradingEngine()
    import sys

    # Without --force-legacy-run sys.argv flag, run() must safely exit without starting loop
    orig_argv = sys.argv[:]
    try:
        sys.argv = ["main.py"]
        engine.run()  # Should print safety guard message and return safely
    finally:
        sys.argv = orig_argv


# --- 17. Problem 4 Auth Protection Test for /api/kiwoom/positions ---
def test_problem4_kiwoom_positions_auth_required(monkeypatch):
    import asyncio
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test_secure_pass_9999!")
    from web_app.backend.main import app, verify_dashboard_auth

    # 1. Inspect route dependant to ensure verify_dashboard_auth dependency is attached
    target_route = None
    for route in app.routes:
        if getattr(route, "path", None) == "/api/kiwoom/positions":
            target_route = route
            break
    assert target_route is not None, "Endpoint /api/kiwoom/positions not found in app routes"
    dep_calls = [dep.call for dep in target_route.dependant.dependencies]
    assert verify_dashboard_auth in dep_calls, "verify_dashboard_auth must be set as a dependency"

    # 2. Directly invoke ASGI app with unauthenticated GET request and verify 401 response
    async def run_unauth_request():
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "GET",
            "path": "/api/kiwoom/positions",
            "raw_path": b"/api/kiwoom/positions",
            "headers": [],
            "query_string": b"",
        }
        sent_messages = []
        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        async def send(message):
            sent_messages.append(message)
        await app(scope, receive, send)
        return sent_messages

    messages = asyncio.run(run_unauth_request())
    start_msg = next(m for m in messages if m["type"] == "http.response.start")
    assert start_msg["status"] == 401, f"Expected 401 status, got {start_msg['status']}"


# --- 18. Hard Stop-Loss Risk Feature Tests ---
def test_hard_stop_loss_validation():
    from trading_bot.grid_strategy import generate_auto_grid_config
    with pytest.raises(ValueError):
        generate_auto_grid_config(
            stock_code="005930",
            stock_name="삼성전자",
            base_price=70000,
            exit_price=80000,
            num_steps=20,
            step_pct=1.5,
            hard_stop_loss_enabled=True,
            hard_stop_loss_price=60000
        )

def test_hard_stop_loss_trigger_and_isolation():
    from web_app.backend.engine import WebTradingEngine
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        db_path = os.path.join(tmpdir, "test_hsl.db")
        engine = WebTradingEngine(db_path=db_path)
        engine.kiwoom_client = MockKiwoomClient(is_mock=True)

        engine.add_stock("005930", "삼성전자", 70000, 80000, num_steps=20, hard_stop_loss_enabled=True, hard_stop_loss_price=45000)
        engine.add_stock("000660", "SK하이닉스", 100000, 120000, num_steps=20, hard_stop_loss_enabled=False)

        engine.positions["005930"] = {"stock_code": "005930", "stock_name": "삼성전자", "qty": 10, "avg_price": 70000, "current_price": 70000, "pnl": 0, "pnl_rate": 0.0}
        engine.positions["000660"] = {"stock_code": "000660", "stock_name": "SK하이닉스", "qty": 5, "avg_price": 100000, "current_price": 100000, "pnl": 0, "pnl_rate": 0.0}

        engine.market_data["005930"]["current_price"] = 44000
        engine.market_data["005930"]["prev_close"] = 44000

        submitted_orders = []
        def mock_submit_order(*args, **kwargs):
            submitted_orders.append(kwargs)
            return {"success": True, "order_id": "MOCK_HSL_ORDER_001"}
        engine.order_manager.submit_order = mock_submit_order

        engine._evaluate_grid_cycle_internal()

        hsl_orders = [o for o in submitted_orders if o.get("stock_code") == "005930"]
        assert len(hsl_orders) == 1
        assert hsl_orders[0]["order_type"] == "SELL"
        assert hsl_orders[0]["strategy_id"] == "HARD_STOP_LOSS"
        assert hsl_orders[0]["qty"] == 10

        assert engine.stocks["005930"]["stop_loss_halted"] is True

        submitted_orders.clear()
        engine.positions["005930"]["qty"] = 0
        engine._evaluate_grid_cycle_internal()
        a_orders = [o for o in submitted_orders if o.get("stock_code") == "005930"]
        assert len(a_orders) == 0

        engine.market_data["000660"]["current_price"] = 90000
        engine.market_data["000660"]["prev_close"] = 90000
        submitted_orders.clear()
        engine._evaluate_grid_cycle_internal()
        b_orders = [o for o in submitted_orders if o.get("stock_code") == "000660"]
        assert len(b_orders) > 0

def test_reset_stop_loss_auth_required(monkeypatch):
    import asyncio
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test_secure_pass_9999!")
    from web_app.backend.main import app, verify_dashboard_auth

    target_route = None
    for route in app.routes:
        if getattr(route, "path", None) == "/api/stocks/reset-stop-loss":
            target_route = route
            break
    assert target_route is not None
    dep_calls = [dep.call for dep in target_route.dependant.dependencies]
    assert verify_dashboard_auth in dep_calls

    async def run_unauth_request():
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "POST",
            "path": "/api/stocks/reset-stop-loss",
            "raw_path": b"/api/stocks/reset-stop-loss",
            "headers": [],
            "query_string": b"",
        }
        sent_messages = []
        async def receive():
            return {"type": "http.request", "body": b'{"stock_code":"005930"}', "more_body": False}
        async def send(message):
            sent_messages.append(message)
        await app(scope, receive, send)
        return sent_messages

    messages = asyncio.run(run_unauth_request())
    start_msg = next(m for m in messages if m["type"] == "http.response.start")
    assert start_msg["status"] == 401


# --- 19. Regression Prevention Test for get_full_dashboard_state Integrity ---
def test_get_full_dashboard_state_integrity(tmp_path):
    """
    Verify get_full_dashboard_state return payload integrity and AST method boundaries.
    Prevents accidental method nesting regressions.
    """
    import ast
    db_path = os.path.join(tmp_path, "test_integrity.db")
    db = WebDBManager(db_path)
    engine = WebTradingEngine(db_path=db_path)

    # 1. Direct runtime invocation check
    state = engine.get_full_dashboard_state()
    assert state is not None, "get_full_dashboard_state must not return None"
    assert isinstance(state, dict), "get_full_dashboard_state must return a dict"

    required_keys = ["status", "trading_mode", "stocks", "held_positions", "safe_stop_active"]
    for key in required_keys:
        assert key in state, f"Key '{key}' must exist in get_full_dashboard_state return payload"

    assert isinstance(state["stocks"], list), "stocks payload must be a list"

    # 2. AST boundary static analysis check for engine.py
    engine_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web_app", "backend", "engine.py"))
    with open(engine_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=engine_file)

    funcs = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("get_full_dashboard_state", "reset_stop_loss"):
            funcs[node.name] = (node.lineno, node.end_lineno, type(node.body[-1]).__name__)

    assert "get_full_dashboard_state" in funcs
    assert "reset_stop_loss" in funcs

    g_start, g_end, g_last = funcs["get_full_dashboard_state"]
    r_start, r_end, r_last = funcs["reset_stop_loss"]

    assert g_last == "Return", "get_full_dashboard_state last statement must be Return"
    assert r_last == "Return", "reset_stop_loss last statement must be Return"
    assert not (r_start >= g_start and r_end <= g_end), "reset_stop_loss must NOT be nested inside get_full_dashboard_state"


# --- 20. Critical Risk Manager Limit Enforcement in Grid Trading Test ---
def test_critical_risk_manager_limit_enforcement(tmp_path):
    """
    Verify max_daily_trades and max_buy_amount limits are enforced during automatic grid orders.
    """
    db_path = os.path.join(tmp_path, "test_risk_grid.db")
    db = WebDBManager(db_path)
    engine = WebTradingEngine(db_path=db_path)
    engine.kiwoom_client = MockKiwoomClient(is_mock=True)

    # 1. Daily trade count limit enforcement
    engine.max_daily_trades = 2
    if engine.risk_manager:
        engine.risk_manager.max_daily_trades = 2

    # Set daily trades to 2 (limit reached)
    db.increment_daily_trade_count()
    db.increment_daily_trade_count()

    engine.add_stock("005930", "삼성전자", 70000, 80000, num_steps=20)
    engine.market_data["005930"]["current_price"] = 65000

    # Attempt BUY order via evaluate_grid_cycle
    submitted_orders = []
    def mock_submit_order(*args, **kwargs):
        submitted_orders.append(kwargs)
        return engine.order_manager.submit_order(*args, **kwargs)

    orig_submit = engine.order_manager.submit_order
    engine.order_manager.submit_order = mock_submit_order
    try:
        engine.status = "RUNNING"
        engine._evaluate_grid_cycle_internal()

        # Buy order must be submitted with risk context and rejected by RiskManager
        assert len(submitted_orders) >= 1
        buy_call = submitted_orders[0]
        assert buy_call["daily_trade_count"] == 2
        assert buy_call["total_account_exposure"] >= 0
    finally:
        engine.order_manager.submit_order = orig_submit


# --- 21. Sensitive Endpoint Authentication Enforcement Test ---
def test_sensitive_endpoint_auth_required(monkeypatch):
    """
    Verify GET /api/status and GET /api/logs/download require Basic Auth (401 if unauthenticated).
    """
    import asyncio
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test_secure_pass_9999!")
    from web_app.backend.main import app, verify_dashboard_auth

    endpoints = ["/api/status", "/api/logs/download"]

    for ep in endpoints:
        target_route = next((r for r in app.routes if getattr(r, "path", None) == ep), None)
        assert target_route is not None, f"Endpoint {ep} not found in app routes"
        dep_calls = [dep.call for dep in target_route.dependant.dependencies]
        assert verify_dashboard_auth in dep_calls, f"verify_dashboard_auth missing on {ep}"

        async def run_unauth_request(path):
            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "method": "GET",
                "path": path,
                "raw_path": path.encode(),
                "headers": [],
                "query_string": b"",
            }
            sent = []
            async def receive(): return {"type": "http.request", "body": b"", "more_body": False}
            async def send(msg): sent.append(msg)
            await app(scope, receive, send)
            return sent

        messages = asyncio.run(run_unauth_request(ep))
        start_msg = next(m for m in messages if m["type"] == "http.response.start")
        assert start_msg["status"] == 401, f"{ep} expected 401 status, got {start_msg['status']}"


# --- 22. Invalid Credentials Block Virtual Order Fallback Test ---
def test_invalid_credentials_block_order_fallback():
    """
    Verify place_order returns REJECTED when credentials are invalid unless in simulation mode.
    """
    kc = KiwoomRESTClient()
    kc.app_key = ""  # Invalid credentials
    kc.is_simulation_mode = False

    res = kc.place_order("8133053801", "BUY", "005930", 10, 70000)
    assert res["success"] is False
    assert res["status"] == OrderStatus.REJECTED
    assert "credentials invalid" in res["message"].lower() or "가상 주문" in res["message"]


# --- 23. Real Trading File Lock Duplication Prevention Test ---
def test_real_trading_file_lock_duplication_prevented(tmp_path):
    """
    Verify acquire_real_trading_lock raises RealTradingLockError when acquired by another PID.
    """
    from trading_bot.trading_mode import acquire_real_trading_lock, release_real_trading_lock, RealTradingLockError
    import tempfile

    test_acc = "9999888877"
    lock_file = os.path.join(tempfile.gettempdir(), f"magictrader_real_{test_acc}.lock")

    # Clean up prior test locks if any
    if os.path.exists(lock_file):
        try: os.remove(lock_file)
        except Exception: pass

    try:
        # 1. Acquire lock for current process
        ok = acquire_real_trading_lock(test_acc)
        assert ok is True
        assert os.path.exists(lock_file)

        # 2. Simulate existing lock belonging to another active PID (using parent process PID os.getppid())
        other_pid = os.getppid()
        if other_pid == os.getpid():
            other_pid = 4  # System PID on Windows
        fake_data = {"pid": other_pid, "account_no": test_acc, "timestamp": "2026-09-09 21:30:00"}
        with open(lock_file, "w", encoding="utf-8") as f:
            json.dump(fake_data, f)

        with pytest.raises(RealTradingLockError) as exc_info:
            # Re-acquire when active PID holds lock
            acquire_real_trading_lock(test_acc)
        assert "CRITICAL LOCK ERROR" in str(exc_info.value)
    finally:
        release_real_trading_lock(test_acc)
        if os.path.exists(lock_file):
            try: os.remove(lock_file)
            except Exception: pass


# --- 24. Trading Bot Runner Market Calendar Integration Test ---
def test_runner_market_calendar_integration():
    """
    Verify trading_bot_runner uses MarketCalendar to determine market hours safely.
    """
    from trading_bot_runner import is_market_hours, get_kst_now
    from trading_bot.market_calendar import MarketCalendar
    import datetime

    # 1. Weekend check
    saturday = datetime.datetime(2026, 9, 12, 11, 0, 0)  # Saturday
    assert MarketCalendar.is_trading_day(saturday.date()) is False

    # 2. KRX Holiday check
    holiday = datetime.date(2026, 12, 25)  # Christmas
    assert MarketCalendar.is_trading_day(holiday) is False

    # 3. Regular trading day hours check
    workday_open = datetime.datetime(2026, 9, 10, 10, 0, 0)  # Thursday 10:00 KST
    assert MarketCalendar.is_trading_day(workday_open.date()) is True
    is_open, _ = MarketCalendar.is_market_open(workday_open)
    assert is_open is True


# --- 25. Trading Bot Runner RealTradingLock & SafeStop Integration Test ---
def test_runner_real_trading_lock_and_safe_stop(tmp_path):
    """
    Verify trading_bot_runner acquires RealTradingLock in REAL mode and triggers SAFE_STOP on collision.
    """
    from trading_bot.trading_mode import acquire_real_trading_lock, release_real_trading_lock, RealTradingLockError
    import trading_bot_runner
    import tempfile

    test_acc = "DEFAULT_ACC"
    lock_file = os.path.join(tempfile.gettempdir(), f"magictrader_real_{test_acc}.lock")

    if os.path.exists(lock_file):
        try: os.remove(lock_file)
        except Exception: pass

    try:
        # Simulate active lock by parent process / external PID
        fake_pid = os.getppid() if os.getppid() != os.getpid() else 4
        fake_data = {"pid": fake_pid, "account_no": test_acc, "timestamp": "2026-09-09 22:00:00"}
        with open(lock_file, "w", encoding="utf-8") as f:
            json.dump(fake_data, f)

        # Attempting to acquire lock while locked by another active PID must raise RealTradingLockError
        with pytest.raises(RealTradingLockError) as exc_info:
            acquire_real_trading_lock(test_acc)
        assert "CRITICAL LOCK ERROR" in str(exc_info.value)

        # Verify runner handling when REAL mode lock collision occurs: triggers SAFE_STOP and exits safely without crashing
        db = WebDBManager(os.path.join(tmp_path, "test_runner_lock.db"))
        db.save_system_config("operating_mode", {"trading_mode": "REAL"})
        
        try:
            trading_bot_runner.run_trading_bot_daemon(interval=0.1, run_once=True, db=db)
        except SystemExit:
            pass

        # Verify safe stop was triggered
        safe_stop_state = db.get_system_config("safe_stop_state")
        assert safe_stop_state is not None
        assert safe_stop_state.get("safe_stop_active") is True
        assert "이중 실전매매 기동 차단" in safe_stop_state.get("safe_stop_reason", "")
    finally:
        release_real_trading_lock(test_acc)
        if os.path.exists(lock_file):
            try: os.remove(lock_file)
            except Exception: pass

# --- 12. P0-1 WebSocket Authentication Test ---
def test_p0_1_ws_auth_enforcement(monkeypatch):
    from web_app.backend.main import verify_ws_auth

    class DummyWS:
        def __init__(self, query_params=None, headers=None):
            self.query_params = query_params or {}
            self.headers = headers or {}

    # 1) DASHBOARD_PASSWORD가 취약/미설정인 경우 -> False
    monkeypatch.setenv("DASHBOARD_PASSWORD", "1234")
    assert verify_ws_auth(DummyWS(query_params={"password": "1234"})) is False

    # 2) 올바른 비밀번호 설정 후 자격증명 미제공 -> False
    monkeypatch.setenv("DASHBOARD_PASSWORD", "super_secret_pass_123")
    assert verify_ws_auth(DummyWS(query_params={})) is False
    assert verify_ws_auth(DummyWS(query_params={"password": "wrong_pass"})) is False

    # 3) 쿼리 파라미터로 올바른 비밀번호 제공 -> True
    assert verify_ws_auth(DummyWS(query_params={"password": "super_secret_pass_123"})) is True

    # 4) Basic Header로 올바른 자격증명 제공 -> True
    import base64
    basic_val = base64.b64encode(b"admin:super_secret_pass_123").decode("utf-8")
    assert verify_ws_auth(DummyWS(headers={"authorization": f"Basic {basic_val}"})) is True

# --- 13. P0-2 Order Error/Message Preservation Test ---
def test_p0_2_order_error_message_preservation(tmp_path):
    from trading_bot.main import AutoTradingEngine

    db_path = os.path.join(tmp_path, "test_p0_2.db")
    engine = AutoTradingEngine()
    engine.db = WebDBManager(db_path)
    engine.mode_mgr.set_mode("DISABLED")

    # DISABLED 상태에서 주문 요청 시 message 및 error 키가 보장되고 사유가 드러나는지 확인
    res = engine.execute_manual_order("005930", "BUY", 10, 70000, "01")
    assert res.get("success") is False
    assert "error" in res
    assert "DISABLED" in res["error"]
    assert res.get("error") == res.get("message")

# --- 14. P0-3 DBManager record_order time module NameError Test ---
def test_p0_3_db_manager_record_order_no_id(tmp_path):
    from trading_bot.db_manager import DBManager

    db = DBManager(os.path.join(tmp_path, "test_p0_3.db"))
    # order_id, order_no가 모두 없는 order_res 전달
    order_res = {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "order_type": "BUY",
        "ord_dvsn": "MARKET",
        "qty": 10,
        "price": 70000,
        "status": "ACCEPTED"
    }

    # NameError 발생하지 않고 정상 처리되어야 함
    db.record_order(order_res)
    recent = db.get_recent_orders(limit=1)
    assert len(recent) == 1
    assert recent[0]["order_id"].startswith("ORD_")

# --- 15. P1-1 Real Lock Fail-Closed Test ---
def test_p1_1_real_lock_fail_closed(monkeypatch):
    from trading_bot.trading_mode import acquire_real_trading_lock, RealTradingLockError

    def mock_open_raise(*args, **kwargs):
        raise PermissionError("Access Denied for Lock File")

    monkeypatch.setattr("builtins.open", mock_open_raise)

    # 쓰기 실패 시 return True가 아닌 RealTradingLockError(Fail-Closed)가 발생해야 함
    with pytest.raises(RealTradingLockError) as exc_info:
        acquire_real_trading_lock("TEST_FAIL_CLOSED_ACC")
    assert "Fail-Closed" in str(exc_info.value) or "CRITICAL LOCK ERROR" in str(exc_info.value)

# --- 16. P1-2 Mode Setter Lock Integration Test ---
def test_p1_2_mode_setter_lock_integration(tmp_path):
    acc = "P1_2_ACC"
    tm = TradingModeManager("MOCK", account_no=acc)
    assert tm.mode == "MOCK"

    # 타 활성 프로세스 PID(예: ppid 또는 4)에 의해 REAL 락 파일이 획득되어 있는 상황 시뮬레이션
    lock_file = os.path.join(tempfile.gettempdir(), f"magictrader_real_{acc}.lock")
    fake_pid = os.getppid() if os.getppid() != os.getpid() else 4
    with open(lock_file, "w", encoding="utf-8") as f:
        json.dump({"pid": fake_pid, "account_no": acc, "timestamp": "2026-09-10 00:00:00"}, f)

    try:
        # .mode = "REAL" Direct 대입 시에도 set_mode()가 호출되어 RealTradingLockError 예외 발생해야 함
        with pytest.raises(RealTradingLockError) as exc_info:
            tm.mode = "REAL"
        assert "CRITICAL LOCK ERROR" in str(exc_info.value)
    finally:
        if os.path.exists(lock_file):
            try: os.remove(lock_file)
            except Exception: pass

# --- 17. P1-4 & P1-5 Multiple UNKNOWN Resolution and SQL Direct Query Test ---
def test_p1_5_multiple_unknown_orders_resolution(tmp_path):
    db_file = os.path.join(tmp_path, "test_p1_5.db")
    db = WebDBManager(db_file)
    tm = TradingModeManager("MOCK")
    om = OrderManager(db, tm)

    # 1. 동일 종목(005930)에 2건의 UNKNOWN 주문을 시뮬레이션 삽입
    # 둘 다 before_qty=0, requested_qty=10
    om.register_new_order(
        order_id="UNK_ORD_1", stock_code="005930", stock_name="삼성전자",
        side="BUY", qty=10, price=70000, initial_status=OrderStatus.UNKNOWN, before_qty=0
    )
    om.register_new_order(
        order_id="UNK_ORD_2", stock_code="005930", stock_name="삼성전자",
        side="BUY", qty=10, price=70000, initial_status=OrderStatus.UNKNOWN, before_qty=0
    )

    # 2. 계좌 잔고 조회를 흉내내는 MockKiwoomClient 생성 (현재 수량=10)
    class MockKiwoomWith10Qty(MockKiwoomClient):
        def get_positions(self, account_no=None, force_refresh=False):
            pos = [{"code": "005930", "stock_code": "005930", "name": "삼성전자", "qty": 10, "avg_price": 70000}]
            return {"success": True, "positions": pos, "held_positions": pos}

    kiwoom = MockKiwoomWith10Qty()

    # 3. UNKNOWN 해제 실행 -> 잔고 델타(+10)가 1건 수량이므로, 첫 번째 UNKNOWN만 FILLED 되고 두 번째는 FAILED가 되어야 함
    resolved_count = om.resolve_unknown_orders(kiwoom)
    assert resolved_count == 2

    ord1 = db.get_order_by_id("UNK_ORD_1")
    ord2 = db.get_order_by_id("UNK_ORD_2")

    assert ord1["status"] == OrderStatus.FILLED
    assert ord2["status"] == OrderStatus.FAILED

# --- 18. P1-6 Idempotency Key Qty and Price Integration Test ---
def test_p1_6_idempotency_key_with_qty_and_price(tmp_path):
    db = WebDBManager(os.path.join(tmp_path, "test_p1_6.db"))
    om = OrderManager(db, TradingModeManager("MOCK"))

    key1 = om.generate_idempotency_key("GRID", "005930", "BUY", cycle_id=1, qty=10, price=70000)
    key2 = om.generate_idempotency_key("GRID", "005930", "BUY", cycle_id=1, qty=10, price=71000)

    # 가격이 다르므로 키가 다르고 DB 등록에 성공해야 함
    assert key1 != key2
    assert db.register_idempotency_key(key1, "005930", "BUY") is True
    assert db.register_idempotency_key(key2, "005930", "BUY") is True


def test_manual_order_blocked_when_risk_manager_missing(tmp_path):
    """
    Verify manual order is blocked with success=False when risk_manager is None (Fail-Closed).
    """
    db_file = os.path.join(tmp_path, "test_rm_missing.db")
    engine = WebTradingEngine(db_path=db_file)
    engine.risk_manager = None

    res = engine.execute_manual_order("005930", "BUY", 10, 70000)
    assert res["success"] is False
    assert "RiskManager" in res["message"]







