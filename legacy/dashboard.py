import os
import json
import time
import threading


from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from typing import Dict, Any, Optional

class DashboardRequestHandler(BaseHTTPRequestHandler):
    """
    내장 http.server 기반 모니터링 & 수동 제어 REST API / HTML 웹 핸들러
    """

    # main_engine 참조 객체
    engine = None

    def _send_json(self, data: Dict[str, Any], status_code: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_content: str, status_code: int = 200):
        body = html_content.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        """HTTP Basic Authentication 헤더 검사"""
        if not self.engine:
            return True

        dash_cfg = self.engine.config.get("dashboard", {})
        username = dash_cfg.get("username")
        password = dash_cfg.get("password")

        if not username or not password:
            return True

        auth_header = self.headers.get("Authorization")
        if auth_header and auth_header.startswith("Basic "):
            import base64
            try:
                auth_decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                if ":" in auth_decoded:
                    u, p = auth_decoded.split(":", 1)
                    if u == username and p == password:
                        return True
            except Exception:
                pass

        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Kiwoom Dashboard Restricted Access"')
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write("401 Unauthorized: 웹 대시보드 접근 권한이 없습니다.".encode("utf-8"))
        return False

    def do_GET(self):
        if not self._check_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._send_html(self._get_dashboard_html())
        elif path == "/api/status":
            if self.engine:
                status_data = self.engine.get_system_status()
                self._send_json(status_data)
            else:
                self._send_json({"error": "엔진이 초기화되지 않았습니다."}, status_code=500)
        else:
            self._send_json({"error": "요청한 리소스를 찾을 수 없습니다."}, status_code=404)

    def do_POST(self):
        if not self._check_auth():
            return

        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        
        try:
            body = json.loads(raw_body)
        except Exception:
            body = {}

        if path == "/api/emergency_stop":
            if self.engine:
                action = body.get("action", "toggle")
                if action == "enable":
                    self.engine.order_guard.trigger_emergency_halt("웹 대시보드 수동 비상 정지", self.engine.db)
                elif action == "disable":
                    self.engine.order_guard.reset_kill_switch()
                    self.engine.db.log_event("INFO", "웹 대시보드에서 비상 정지가 해제되었습니다.")
                else:
                    if self.engine.order_guard.kill_switch:
                        self.engine.order_guard.reset_kill_switch()
                        self.engine.db.log_event("INFO", "웹 대시보드에서 비상 정지가 해제되었습니다.")
                    else:
                        self.engine.order_guard.trigger_emergency_halt("웹 대시보드 수동 비상 정지", self.engine.db)

                self._send_json({
                    "success": True,
                    "kill_switch": self.engine.order_guard.kill_switch,
                    "message": f"Kill-Switch 상태가 변경되었습니다: {self.engine.order_guard.kill_switch}"
                })
            else:
                self._send_json({"error": "엔진 불능"}, status_code=500)

        elif path == "/api/order":
            if self.engine:
                res = self.engine.execute_manual_order(
                    stock_code=body.get("stock_code", ""),
                    side=body.get("side", "BUY"),
                    qty=int(body.get("qty", 1)),
                    price=int(body.get("price", 0)),
                    ord_dvsn=body.get("ord_dvsn", "03")
                )
                self._send_json(res)
            else:
                self._send_json({"error": "엔진 불능"}, status_code=500)
        else:
            self._send_json({"error": "Not Found"}, status_code=404)

    def log_message(self, format, *args):
        # 콘솔 HTTP 서빙 로그 간소화
        pass

    def _get_dashboard_html(self) -> str:
        return """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>키움 REST API 자동매매 실시간 대시보드</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-main: #f8fafc;
            --text-sub: #94a3b8;
            --accent-green: #10b981;
            --accent-red: #ef4444;
            --accent-blue: #3b82f6;
            --border-color: #334155;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }
        body { background-color: var(--bg-color); color: var(--text-main); padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 20px; border-bottom: 1px solid var(--border-color); margin-bottom: 20px; }
        .title-group { display: flex; align-items: center; gap: 12px; }
        .badge { padding: 4px 10px; border-radius: 20px; font-size: 0.85rem; font-weight: bold; background: #334155; }
        .badge.real { background: #065f46; color: #34d399; }
        .badge.mock { background: #92400e; color: #fcd34d; }
        .btn-kill { background: var(--accent-red); color: white; border: none; padding: 10px 20px; border-radius: 8px; font-weight: bold; cursor: pointer; transition: 0.2s; }
        .btn-kill:hover { opacity: 0.9; transform: scale(1.02); }
        .btn-kill.active { background: var(--accent-green); }
        
        .grid-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: var(--card-bg); padding: 20px; border-radius: 12px; border: 1px solid var(--border-color); }
        .card .label { color: var(--text-sub); font-size: 0.9rem; margin-bottom: 8px; }
        .card .val { font-size: 1.6rem; font-weight: bold; }
        
        .section-title { font-size: 1.2rem; font-weight: bold; margin-bottom: 12px; color: #e2e8f0; }
        .table-container { background: var(--card-bg); border-radius: 12px; border: 1px solid var(--border-color); overflow: hidden; margin-bottom: 24px; }
        table { width: 100%; border-collapse: collapse; text-align: left; }
        th, td { padding: 12px 16px; border-bottom: 1px solid var(--border-color); font-size: 0.95rem; }
        th { background: #0f172a; color: var(--text-sub); }
        tr:last-child td { border-bottom: none; }
        .text-up { color: var(--accent-red); font-weight: bold; }
        .text-down { color: var(--accent-blue); font-weight: bold; }
        
        .grid-forms { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 24px; }
        @media(max-width: 768px) { .grid-forms { grid-template-columns: 1fr; } }
        
        .form-group { margin-bottom: 12px; }
        .form-group label { display: block; color: var(--text-sub); font-size: 0.85rem; margin-bottom: 4px; }
        .form-group input, .form-group select { width: 100%; padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border-color); background: #0f172a; color: white; }
        .btn-submit { width: 100%; padding: 10px; background: var(--accent-blue); color: white; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; }
        
        .log-box { background: #090d16; border: 1px solid var(--border-color); border-radius: 12px; padding: 16px; height: 180px; overflow-y: auto; font-family: monospace; font-size: 0.85rem; color: #a7f3d0; }
        .log-line { margin-bottom: 4px; border-bottom: 1px dashed #1e293b; padding-bottom: 2px; }
    </style>
</head>
<body>
    <div class="header">
        <div class="title-group">
            <h2>Kiwoom REST 자동매매 대시보드</h2>
            <span id="env-badge" class="badge">연결 중...</span>
            <span id="status-badge" class="badge">상태 대기</span>
        </div>
        <button id="btn-kill" class="btn-kill" onclick="toggleKillSwitch()">비상 정지 (Kill-Switch)</button>
    </div>

    <!-- 요약 카드 -->
    <div class="grid-cards">
        <div class="card">
            <div class="label">예수금</div>
            <div class="val" id="val-deposit">0 원</div>
        </div>
        <div class="card">
            <div class="label">총 평가 손익</div>
            <div class="val" id="val-pnl">0 원</div>
        </div>
        <div class="card">
            <div class="label">총 수익률</div>
            <div class="val" id="val-rate">0.00 %</div>
        </div>
        <div class="card">
            <div class="label">당일 총 매매 횟수</div>
            <div class="val" id="val-trades">0 회</div>
        </div>
    </div>

    <!-- MagicTrader 그리드 전략 현황 -->
    <div class="section-title">🎯 MagicTrader 그리드 전략 현황 (Grid Matrix Status)</div>
    <div class="table-container">
        <table>
            <thead>
                <tr>
                    <th>종목명(코드)</th>
                    <th>현재 차수</th>
                    <th>누적 목표 수량</th>
                    <th>실제 보유 수량</th>
                    <th>최종 청산가</th>
                    <th>전략 상태</th>
                </tr>
            </thead>
            <tbody id="tbl-grid-status">
                <tr><td colspan="6" style="text-align: center; color: var(--text-sub);">등록된 그리드 전략이 없습니다.</td></tr>
            </tbody>
        </table>
    </div>

    <!-- 보유 잔고 포지션 -->
    <div class="section-title">📊 보유 포지션 (Active Positions)</div>
    <div class="table-container">
        <table>
            <thead>
                <tr>
                    <th>종목코드</th>
                    <th>종목명</th>
                    <th>수량</th>
                    <th>평균단가</th>
                    <th>현재가</th>
                    <th>평가손익</th>
                    <th>수익률</th>
                </tr>
            </thead>
            <tbody id="tbl-positions">
                <tr><td colspan="7" style="text-align: center; color: var(--text-sub);">보유 포지션이 없습니다.</td></tr>
            </tbody>
        </table>
    </div>

    <div class="grid-forms">
        <!-- 수동 주문 인터페이스 -->
        <div>
            <div class="section-title">⚡ 수동 주문 인터페이스</div>
            <div class="card">
                <div class="form-group">
                    <label>종목 코드</label>
                    <input type="text" id="ord-code" placeholder="예: 005930" value="005930">
                </div>
                <div class="form-group">
                    <label>구분</label>
                    <select id="ord-side">
                        <option value="BUY">매수 (BUY)</option>
                        <option value="SELL">매도 (SELL)</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>주문 유형 (ORD_DVSN)</label>
                    <select id="ord-dvsn">
                        <option value="03">최유리 지정가 (03)</option>
                        <option value="01">시장가 (01)</option>
                        <option value="00">지정가 (00)</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>수량 (주)</label>
                    <input type="number" id="ord-qty" value="1">
                </div>
                <div class="form-group">
                    <label>주문 단가 (지정가 00일 때만 적용)</label>
                    <input type="number" id="ord-price" value="0">
                </div>
                <button class="btn-submit" onclick="sendManualOrder()">주문 전송</button>
            </div>
        </div>

        <!-- 실시간 시스템 로그 -->
        <div>
            <div class="section-title">📝 시스템 실시간 로그</div>
            <div class="log-box" id="log-container">
                <div class="log-line">[시스템] 대시보드 로딩 완료. 데이터를 불러오는 중...</div>
            </div>
        </div>
    </div>

    <script>
        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                if(!res.ok) return;
                const data = await res.json();
                
                // 1. 배지 및 상태 갱신
                const badge = document.getElementById('env-badge');
                if(data.is_mock) {
                    badge.innerText = '모의투자 (Mock)';
                    badge.className = 'badge mock';
                } else {
                    badge.innerText = '실전투자 (Real)';
                    badge.className = 'badge real';
                }
                
                const killBtn = document.getElementById('btn-kill');
                if(data.kill_switch) {
                    killBtn.innerText = '비상 정지 해제';
                    killBtn.className = 'btn-kill active';
                } else {
                    killBtn.innerText = '비상 정지 (Kill-Switch)';
                    killBtn.className = 'btn-kill';
                }

                // 2. 카드 갱신
                const balance = data.balance || {};
                document.getElementById('val-deposit').innerText = (balance.deposit || 0).toLocaleString() + ' 원';
                document.getElementById('val-pnl').innerText = (balance.total_eval_pnl || 0).toLocaleString() + ' 원';
                document.getElementById('val-rate').innerText = (balance.total_eval_rate || 0).toFixed(2) + ' %';
                document.getElementById('val-trades').innerText = (data.today_trade_count || 0) + ' 회';

                // 2-1. 매직트레이더 그리드 현황 갱신
                const gridTbody = document.getElementById('tbl-grid-status');
                const gridList = data.grid_status || [];
                if(gridList.length === 0) {
                    gridTbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-sub);">등록된 그리드 전략이 없습니다.</td></tr>';
                } else {
                    gridTbody.innerHTML = gridList.map(g => {
                        let stBadge = '<span style="color:#34d399; font-weight:bold;">🟢 정상 추적</span>';
                        if(g.is_blackswan_halt) stBadge = '<span style="color:#f87171; font-weight:bold;">🛑 블랙스완 매수정지</span>';
                        else if(g.recycle_mode) stBadge = '<span style="color:#fde047; font-weight:bold;">🔄 무한물타기</span>';
                        return `<tr>
                            <td><b>${g.stock_name}</b> (${g.stock_code})</td>
                            <td><b style="color:#38bdf8;">${g.current_step}</b> / ${g.max_steps} 차</td>
                            <td>${g.target_qty} 주</td>
                            <td>${g.actual_qty} 주</td>
                            <td><b style="color:#22d3ee;">${(g.exit_price||0).toLocaleString()} 원</b></td>
                            <td>${stBadge}</td>
                        </tr>`;
                    }).join('');
                }

                // 3. 포지션 테이블 갱신
                const posTbody = document.getElementById('tbl-positions');
                const positions = data.positions || [];
                if(positions.length === 0) {
                    posTbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-sub);">보유 포지션이 없습니다.</td></tr>';
                } else {
                    posTbody.innerHTML = positions.map(p => {
                        const pnlClass = p.pnl > 0 ? 'text-up' : (p.pnl < 0 ? 'text-down' : '');
                        return `<tr>
                            <td>${p.stock_code}</td>
                            <td>${p.stock_name}</td>
                            <td>${p.qty} 주</td>
                            <td>${(p.avg_price||0).toLocaleString()} 원</td>
                            <td>${(p.current_price||0).toLocaleString()} 원</td>
                            <td class="${pnlClass}">${(p.pnl||0).toLocaleString()} 원</td>
                            <td class="${pnlClass}">${(p.pnl_rate||0).toFixed(2)} %</td>
                        </tr>`;
                    }).join('');
                }

                // 4. 로그 갱신
                const logBox = document.getElementById('log-container');
                const logs = data.logs || [];
                if(logs.length > 0) {
                    logBox.innerHTML = logs.map(l => `<div class="log-line">[${l.timestamp}] [${l.level}] ${l.message}</div>`).join('');
                }
            } catch(e) {
                console.error("Status fetch error:", e);
            }
        }

        async function toggleKillSwitch() {
            if(!confirm("Kill-Switch 상태를 변경하시겠습니까?")) return;
            const res = await fetch('/api/emergency_stop', { method: 'POST', body: JSON.stringify({ action: 'toggle' }) });
            const data = await res.json();
            alert(data.message || '처리되었습니다.');
            fetchStatus();
        }

        async function sendManualOrder() {
            const code = document.getElementById('ord-code').value.trim();
            const side = document.getElementById('ord-side').value;
            const dvsn = document.getElementById('ord-dvsn').value;
            const qty = parseInt(document.getElementById('ord-qty').value);
            const price = parseInt(document.getElementById('ord-price').value);

            if(!code) { alert('종목 코드를 입력하세요.'); return; }
            if(!confirm(`${code} ${side} ${qty}주 주문을 전송하시겠습니까?`)) return;

            const res = await fetch('/api/order', {
                method: 'POST',
                body: JSON.stringify({ stock_code: code, side: side, ord_dvsn: dvsn, qty: qty, price: price })
            });
            const data = await res.json();
            if(data.success) {
                alert('주문이 전송되었습니다: ' + data.order_id);
            } else {
                alert('주문 전송 실패: ' + (data.error || data.reason));
            }
            fetchStatus();
        }

        // 3초 마다 자동 비동기 갱신
        setInterval(fetchStatus, 3000);
        fetchStatus();
    </script>
</body>
</html>"""


class DashboardServer:
    """
    데몬 스레드로 비동기 작동하는 내장 웹 대시보드 서버 (기본 포트: 8080)
    """

    def __init__(self, main_engine, host: str = "0.0.0.0", port: int = 8080):
        self.main_engine = main_engine
        self.host = host
        self.port = port
        DashboardRequestHandler.engine = main_engine
        self.server = HTTPServer((host, port), DashboardRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        print(f"[DashboardServer] 내장 웹 대시보드가 구동되었습니다: http://localhost:{self.port}")

    def stop(self):
        self.server.shutdown()
        print("[DashboardServer] 대시보드 서버가 종료되었습니다.")
