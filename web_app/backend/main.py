import os
import sys
import time
import secrets

from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse

# backend 경로 모듈 등록
base_dir = os.path.dirname(os.path.abspath(__file__))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from db import WebDBManager
from engine import WebTradingEngine
from models import (
    StockCreateRequest,
    SystemGuardConfig,
    ControlRequest,
    EmergencyResolutionRequest,
    ManualOrderRequest,
    StockToggleActiveRequest,
    StockDeleteRequest,
    ResetStopLossRequest,
    KiwoomCredentialsRequest,
    ForceCloseRequest,
    TradingModeRequest
)

from stock_master import (
    lookup_stock_by_code,
    lookup_stock_by_name,
    search_stock_master,
    get_stock_price_quote
)

# HTTP Basic Security setup
security = HTTPBasic(auto_error=False)

def verify_dashboard_auth(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    """FastAPI HTTP Basic Auth 검증 함수 (제어/주문/삭제/초기화 API 수호)"""
    expected_username = os.getenv("DASHBOARD_USERNAME", "admin")
    expected_password = (os.getenv("DASHBOARD_PASSWORD") or "").strip()

    forbidden_passwords = {"", "admin", "password", "1234", "default"}
    if expected_password.lower() in forbidden_passwords:
        raise RuntimeError(
            "CRITICAL SECURITY FAILURE: DASHBOARD_PASSWORD 환경변수가 설정되지 않았거나 "
            "취약한 기본값('admin', 'password', '1234', 'default')이 사용되었습니다. "
            "실전 투입 전 안전한 DASHBOARD_PASSWORD를 환경변수에 반드시 설정하십시오."
        )

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dashboard HTTP Basic Authentication Required",
            headers={"WWW-Authenticate": "Basic"},
        )

    is_user_correct = secrets.compare_digest(credentials.username, expected_username)
    is_pass_correct = secrets.compare_digest(credentials.password, expected_password)

    if not (is_user_correct and is_pass_correct):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Username or Password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# 데이터베이스 및 트레이딩 엔진 지연 로딩 (Lazy Initialization)
_db_instance: Optional[WebDBManager] = None
_engine_instance: Optional[WebTradingEngine] = None

def get_db() -> WebDBManager:
    global _db_instance
    if _db_instance is None:
        _db_instance = WebDBManager()
    return _db_instance

def get_engine() -> WebTradingEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = WebTradingEngine(get_db())
    return _engine_instance

class LazyEngineProxy:
    """웹 서버 스타트업 시점의 블로킹을 방지하기 위한 트레이딩 엔진 지연 프록시"""
    def __getattr__(self, name):
        return getattr(get_engine(), name)

engine = LazyEngineProxy()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    PythonAnywhere WSGI Reload 타임아웃 방지를 위한 경량 lifespan 컨텍스트 매니저
    - 웹 워커 시작 시 무한 폴링 루프나 외부 네트워크 I/O 전면 배제
    - 실시간 시세 수신 및 매매는 Always-on 데몬(trading_bot_runner.py)이 담당
    """
    print("[MagicTrader Web] FastAPI 대시보드 백엔드가 준비되었습니다 (Lightweight Lifespan).")
    yield
    print("[MagicTrader Web] FastAPI 대시보드 백엔드 종료.")

app = FastAPI(
    title="MagicTrader Web Dashboard Server",
    version="2.0.0",
    lifespan=lifespan
)

# 프론트엔드 정적 파일 마운트
frontend_dir = os.path.join(os.path.dirname(base_dir), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

# 연결된 WebSocket 클라이언트 관리자
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(data)
            except Exception:
                self.disconnect(connection)

ws_manager = ConnectionManager()

@app.get("/", response_class=FileResponse)
@app.get("/index.html", response_class=FileResponse)
def serve_dashboard():
    """웹 대시보드 메인 페이지 서빙 (a2wsgi 스레드풀 블로킹 방지를 위해 동기 FileResponse 처리)"""
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return JSONResponse({"error": f"frontend/index.html 경로를 찾을 수 없습니다. (확인 경로: {index_path})"}, status_code=404)

@app.get("/api/status")
def get_system_status(username: str = Depends(verify_dashboard_auth)):
    """시스템 실시간 대시보드 상태 조회 (무거운 시뮬레이션 제거 및 동기 처리로 즉시 응답)"""
    return engine.get_full_dashboard_state()

# [P2-1 보안 정책] 시세 조회 및 종목 검색 API
# 계좌 잔고 직접 조회가 아니더라도 대시보드 전체를 비공개 전용으로 보호하고
# 외부 무단 스캐닝을 차단하기 위해 HTTP Basic Auth(verify_dashboard_auth)를 적용함.
@app.get("/api/stocks/search")
def search_stocks(q: str = "", username: str = Depends(verify_dashboard_auth)):
    matches = search_stock_master(q)
    return {"matches": matches}

@app.get("/api/stocks/lookup")
def lookup_stock(code: str = None, name: str = None, username: str = Depends(verify_dashboard_auth)):
    if code:
        found_name = lookup_stock_by_code(code)
        if found_name:
            return {"success": True, "code": code.zfill(6), "name": found_name}
    if name:
        found_code = lookup_stock_by_name(name)
        if found_code:
            found_name = lookup_stock_by_code(found_code) or name
            return {"success": True, "code": found_code, "name": found_name}
    return {"success": False, "message": "종목 정보를 찾을 수 없습니다."}

@app.get("/api/stocks/quote")
def get_stock_price_quote_api(code: str, username: str = Depends(verify_dashboard_auth)):
    clean_code = code.zfill(6)
    name = lookup_stock_by_code(clean_code) or clean_code
    price = get_stock_price_quote(clean_code)
    if clean_code in engine.market_data:
        price = engine.market_data[clean_code]["current_price"]
    suggested_clear = int(price * 1.015)
    suggested_clear = (suggested_clear // 100) * 100 if price >= 50000 else (suggested_clear // 10) * 10
    return {
        "success": True,
        "code": clean_code,
        "name": name,
        "current_price": price,
        "clear_price_suggested": suggested_clear
    }

@app.post("/api/stocks")
@app.post("/api/stocks/add")
def add_stock(req: StockCreateRequest, username: str = Depends(verify_dashboard_auth)):
    try:
        ok, msg = engine.add_stock(
            code=req.code,
            name=req.name,
            base_price=req.base_price,
            clear_price=req.clear_price,
            num_steps=req.num_steps,
            start_step=req.start_step,
            step_pct=req.step_pct,
            budget_per_step=req.budget_per_step,
            memo=req.memo or "",
            hard_stop_loss_enabled=getattr(req, "hard_stop_loss_enabled", False),
            hard_stop_loss_price=getattr(req, "hard_stop_loss_price", 0)
        )
        if ok:
            return {"success": True, "message": msg}
        return JSONResponse({"success": False, "message": msg}, status_code=400)
    except Exception as e:
        print(f"[/api/stocks 추가 예외] {e}")
        return JSONResponse({"success": False, "message": f"종목 추가 내부 오류: {str(e)}"}, status_code=500)

@app.post("/api/stocks/reset-stop-loss")
def reset_stop_loss_api(req: ResetStopLossRequest, username: str = Depends(verify_dashboard_auth)):
    try:
        target_code = getattr(req, "stock_code", None) or getattr(req, "code", None)
        if not target_code:
            return JSONResponse({"success": False, "message": "종목 코드가 누락되었습니다."}, status_code=400)
        ok, msg = engine.reset_stop_loss(target_code)
        if ok:
            return {"success": True, "message": msg}
        return JSONResponse({"success": False, "message": msg}, status_code=400)
    except Exception as e:
        print(f"[/api/stocks/reset-stop-loss 예외] {e}")
        return JSONResponse({"success": False, "message": f"손절 해제 내부 오류: {str(e)}"}, status_code=500)

@app.post("/api/stocks/toggle-active")
def toggle_stock_active(req: StockToggleActiveRequest, username: str = Depends(verify_dashboard_auth)):
    try:
        target_code = getattr(req, "stock_code", None) or getattr(req, "code", None)
        if not target_code:
            return JSONResponse({"success": False, "message": "종목 코드가 누락되었습니다."}, status_code=400)
        ok = engine.toggle_stock_active(stock_code=target_code, is_active=req.is_active)
        if ok:
            status_text = "활성화" if req.is_active else "비활성화"
            return {"success": True, "message": f"종목({target_code}) 자동매매 {status_text} 처리 완료"}
        return {"success": False, "message": f"종목({target_code})을 찾을 수 없습니다."}
    except Exception as e:
        print(f"[/api/stocks/toggle-active 예외] {e}")
        return JSONResponse({"success": False, "message": f"내부 처리 오류: {str(e)}"}, status_code=500)

@app.post("/api/stocks/delete")
def delete_stock(req: StockDeleteRequest, username: str = Depends(verify_dashboard_auth)):
    try:
        target_code = getattr(req, "stock_code", None) or getattr(req, "code", None)
        if not target_code:
            return JSONResponse({"success": False, "message": "종목 코드가 누락되었습니다."}, status_code=400)
        ok, msg = engine.delete_stock(target_code)
        if ok:
            return {"success": True, "message": msg}
        return JSONResponse({"success": False, "message": msg}, status_code=400)
    except Exception as e:
        print(f"[/api/stocks/delete 예외] {e}")
        return JSONResponse({"success": False, "message": f"삭제 내부 오류: {str(e)}"}, status_code=500)

@app.post("/api/system/guard")
def update_system_guard(cfg: SystemGuardConfig, username: str = Depends(verify_dashboard_auth)):
    engine.max_daily_trades = cfg.max_daily_trades
    engine.max_buy_amount = cfg.max_buy_amount
    engine.save_to_db()
    return {"success": True, "message": "리스크 가드 설정이 업데이트되었습니다."}

@app.get("/api/logs/download")
def download_logs_api(username: str = Depends(verify_dashboard_auth)):
    """SQLite DB에 영속 저장된 실시간 매매 및 시스템 이력 로그 다운로드"""
    logs = engine.db.get_logs(limit=500)
    lines = [f"[{l['timestamp']}] [{l['level']}] {l['message']}" for l in reversed(logs)]
    content = "\n".join(lines)
    filename = f"magictrader_trade_logs_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.post("/api/mode/set")
def set_trading_mode_api(req: TradingModeRequest, username: str = Depends(verify_dashboard_auth)):
    mode = str(req.mode or "MOCK").upper()
    res = engine.set_trading_mode(mode)
    return res

@app.post("/api/mode/toggle")
def toggle_trading_mode_api(username: str = Depends(verify_dashboard_auth)):
    new_mode = "REAL" if engine.trading_mode == "MOCK" else "MOCK"
    res = engine.set_trading_mode(new_mode)
    return res

@app.post("/api/control")
def control_trading(req: ControlRequest, username: str = Depends(verify_dashboard_auth)):
    try:
        act = req.action.upper()
        if act == "START":
            engine.status = "RUNNING"
            engine.safe_stop_active = False
            engine.safe_stop_reason = ""
            engine.db.save_system_config("engine_status", {"status": "RUNNING"})
            engine.db.add_log("INFO", "▶ [자동매매 시작] 그리드 순환매매 엔진 구동을 개시합니다.")
        elif act == "STOP":
            engine.status = "STOPPED"
            engine.db.save_system_config("engine_status", {"status": "STOPPED"})
            engine.db.add_log("WARNING", "⏹ [자동매매 일시정지] 매매 엔진 구동이 중단되었습니다.")
        elif act == "CANCEL_ORDERS":
            if hasattr(engine, "cancel_all_pending_orders"):
                engine.cancel_all_pending_orders()
            engine.db.add_log("WARNING", "🛑 [주문 취소] 모든 미체결 주문 취소 요청.")
        else:
            return JSONResponse({"success": False, "message": "유효하지 않은 동작입니다."}, status_code=400)
        return {"success": True, "status": engine.status, "message": f"{act} 제어 성공"}
    except Exception as e:
        print(f"[/api/control 예외] {e}")
        return JSONResponse({"success": False, "message": f"제어 처리 오류: {str(e)}"}, status_code=500)

@app.post("/api/db/save")
def save_db(username: str = Depends(verify_dashboard_auth)):
    engine.save_to_db()
    state = engine.get_full_dashboard_state()
    engine.db.create_backup(f"backup_{int(time.time())}", state)
    return {"success": True, "message": "데이터베이스 저장 및 백업 완결"}

@app.post("/api/db/load")
def load_db(username: str = Depends(verify_dashboard_auth)):
    engine.load_from_db()
    return {"success": True, "message": "DB 데이터 로드 완결"}

@app.post("/api/db/backup")
def load_backup(username: str = Depends(verify_dashboard_auth)):
    ok = engine.load_backup()
    if ok:
        return {"success": True, "message": "백업 복원 완료"}
    return {"success": False, "message": "복원할 백업 데이터가 존재하지 않습니다."}

@app.post("/api/db/reset")
def reset_db_route(username: str = Depends(verify_dashboard_auth)):
    """데이터베이스 및 종목 그리드 설정 데이터 완전 초기화"""
    ok = engine.reset_db()
    if ok:
        return {"success": True, "message": "데이터베이스 및 그리드 설정 데이터가 성공적으로 초기화되었습니다."}
    raise HTTPException(status_code=500, detail="데이터베이스 초기화 처리 중 오류가 발생했습니다.")

@app.post("/api/emergency/resolve")
def resolve_emergency(req: EmergencyResolutionRequest, username: str = Depends(verify_dashboard_auth)):
    ok = engine.resolve_emergency(req.stock_code, req.action_choice)
    if ok:
        return {"success": True, "message": "긴급 정지가 조치되었습니다."}
    raise HTTPException(status_code=404, detail="해당 종목을 찾을 수 없습니다.")

@app.post("/api/emergency/test-trigger")
def trigger_emergency_test(stock_code: str = None, username: str = Depends(verify_dashboard_auth)):
    ok = engine.trigger_test_price_adjustment(stock_code)
    if ok:
        return {"success": True, "message": "권리락/무상증자 급락 테스트가 발령되었습니다."}
    raise HTTPException(status_code=400, detail="권리락 테스트 발령 실패")

@app.get("/api/kiwoom/positions")
def get_kiwoom_positions_api(username: str = Depends(verify_dashboard_auth)):
    """키움증권 서버(또는 실시간 계좌) 보유 주식 정보 및 잔고 현황 조회 (HTTP Basic Auth 인증 적용)"""
    data = engine.get_kiwoom_positions(force_refresh=True)
    return data

@app.get("/api/kiwoom/credentials")
def get_kiwoom_credentials_api(username: str = Depends(verify_dashboard_auth)):
    """현재 저장된 키움 Open API 인증키 및 접속 설정 정보 조회 (보안 마스킹 적용)"""
    return engine.get_kiwoom_credentials()

@app.post("/api/kiwoom/credentials")
def update_kiwoom_credentials_api(req: KiwoomCredentialsRequest, username: str = Depends(verify_dashboard_auth)):
    """키움증권 Open API 인증키 (App Key / App Secret) 설정 업데이트 및 잔고 재조회"""
    raw_acc = (req.account_no or os.getenv("KIWOOM_ACCOUNT_NO", "")).strip()

    res = engine.update_kiwoom_credentials(
        app_key=req.app_key,
        app_secret=req.app_secret,
        account_no=raw_acc,
        is_mock=req.is_mock if req.is_mock is not None else True
    )

    if res.get("success"):
        balance_res = engine.get_kiwoom_positions()
        return {
            "success": True,
            "message": res.get("message"),
            "balance": balance_res
        }
    raise HTTPException(status_code=400, detail=res.get("message", "인증키 설정 실패"))

@app.post("/api/orders/manual")
def place_manual_order_api(req: ManualOrderRequest, username: str = Depends(verify_dashboard_auth)):
    """키움증권 서버 수동 매수/매도 주문 전송"""
    res = engine.execute_manual_order(
        stock_code=req.stock_code,
        order_type=req.order_type,
        qty=req.qty,
        price=req.price,
        ord_dvsn=req.ord_dvsn
    )
    if res.get("success"):
        return res
    raise HTTPException(status_code=400, detail=res.get("message", "주문 전송 실패"))

@app.post("/api/kiwoom/force-close")
def force_close_api(req: ForceCloseRequest, username: str = Depends(verify_dashboard_auth)):
    """키움 계좌 보유 종목 시장가 강제청산 매도 주문 전송"""
    res = engine.force_close_kiwoom_position(
        stock_code=req.stock_code,
        qty=req.qty,
        stock_name=req.stock_name
    )
    if res.get("success"):
        return res
    raise HTTPException(status_code=400, detail=res.get("message", "강제청산 실패"))

def verify_ws_auth(websocket: WebSocket) -> bool:
    """WebSocket 자격증명 검증 함수 (DASHBOARD_PASSWORD 기반 secrets.compare_digest 검증)"""
    expected_password = (os.getenv("DASHBOARD_PASSWORD") or "").strip()
    forbidden_passwords = {"", "admin", "password", "1234", "default"}
    if expected_password.lower() in forbidden_passwords:
        return False

    query_params = websocket.query_params
    provided_pass = query_params.get("password") or query_params.get("auth") or query_params.get("token") or query_params.get("key")
    
    if not provided_pass:
        auth_header = websocket.headers.get("authorization")
        if auth_header:
            if auth_header.startswith("Basic "):
                try:
                    import base64
                    decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                    if ":" in decoded:
                        provided_pass = decoded.split(":", 1)[1]
                except Exception:
                    pass
            elif auth_header.startswith("Bearer "):
                provided_pass = auth_header[7:]

    if not provided_pass:
        return False

    return secrets.compare_digest(provided_pass, expected_password)

@app.websocket("/ws/trading")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket 실시간 상태 수신 엔드포인트 (/ws/trading)
    - DASHBOARD_PASSWORD 기반 자격증명 검증 강제 (미인증 시 즉시 1008 코드로 차단)
    - PythonAnywhere WSGI (uWSGI / a2wsgi) 환경의 WebSocket 업그레이드 미지원으로 인한 502/크래시 방어 가드
    """
    if not verify_ws_auth(websocket):
        print(f"[WebSocket Security Guard] 미인증 WebSocket 연결 시도 차단 (code 1008).")
        try:
            await websocket.close(code=1008, reason="WebSocket Dashboard Authentication Failed")
        except Exception:
            pass
        return

    try:
        await ws_manager.connect(websocket)
    except Exception as e:
        print(f"[WebSocket Guard] WSGI/ASGI 핸드셰이크 미지원 또는 접속 연결 거부: {e}")
        try:
            await websocket.close(code=1001, reason="WSGI WebSocket Not Supported - Use HTTP Polling Fallback")
        except Exception:
            pass
        return

    try:
        await websocket.send_json(engine.get_full_dashboard_state())
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as err:
        print(f"[WebSocket Loop Guard] 예외 발생: {err}")
        ws_manager.disconnect(websocket)
        try:
            await websocket.close(code=1000)
        except Exception:
            pass

@app.get("/{full_path:path}", response_class=FileResponse)
def catch_all_frontend_routes(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="요청하신 API 경로를 찾을 수 없습니다.")
    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return JSONResponse({"error": "frontend/index.html 경로를 찾을 수 없습니다."}, status_code=404)

if __name__ == "__main__":
    try:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)
    except ImportError:
        print("Error: uvicorn 패키지가 설치되어 있지 않습니다.")
        print("로컬 테스트: pip install uvicorn fastapi a2wsgi 실행 후 다시 시도하세요.")
        print("PythonAnywhere: Web 탭의 WSGI 설정 파일에서 wsgi.py:application을 통해 웹 앱을 구동하세요.")
