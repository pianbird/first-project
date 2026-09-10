// MagicTrader Web Dashboard Real-time WebSocket Client Engine

let ws = null;
let currentPendingAlert = null;
const NUM_STEPS_DISPLAY = 20;
let wsRetryCount = 0;
let isPollingMode = false;
let pollingTimer = null;

// Floating UI Toast Notification System (Bypasses browser alert suppression)
function showToast(msg, type = "info") {
    let toast = document.getElementById("ui-toast-banner");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "ui-toast-banner";
        document.body.appendChild(toast);
    }

    let bg = "bg-slate-900/95 border-indigo-500 text-indigo-100 shadow-indigo-500/20";
    let icon = "fa-solid fa-circle-info text-indigo-400";
    if (type === "success") {
        bg = "bg-slate-900/95 border-emerald-500 text-emerald-100 shadow-emerald-500/20";
        icon = "fa-solid fa-circle-check text-emerald-400";
    } else if (type === "error") {
        bg = "bg-slate-900/95 border-rose-500 text-rose-100 shadow-rose-500/20";
        icon = "fa-solid fa-triangle-exclamation text-rose-400";
    } else if (type === "warning") {
        bg = "bg-slate-900/95 border-amber-500 text-amber-100 shadow-amber-500/20";
        icon = "fa-solid fa-triangle-exclamation text-amber-400";
    }

    toast.className = `fixed top-5 right-5 z-[9999] max-w-lg p-4 rounded-xl shadow-2xl text-xs font-semibold transition-all duration-300 transform translate-y-0 opacity-100 flex items-start space-x-3 border backdrop-blur-md ${bg}`;
    toast.innerHTML = `<i class="${icon} text-base mt-0.5"></i><div class="flex-1 whitespace-pre-line leading-relaxed">${msg}</div><button onclick="this.parentElement.remove()" class="ml-2 text-slate-400 hover:text-white p-1"><i class="fa-solid fa-xmark text-sm"></i></button>`;

    setTimeout(() => {
        if (toast && toast.parentElement) {
            toast.style.opacity = "0";
            setTimeout(() => { if (toast && toast.parentElement) toast.remove(); }, 300);
        }
    }, 8000);
}


// Initialize Page
document.addEventListener("DOMContentLoaded", () => {
    initStepHeaders();
    connectWebSocket();
    setupGridWheelScroll();
});

// Render Step Headers in Table Header (1차 ~ 20차)
function initStepHeaders() {
    const container = document.getElementById("step-headers-container");
    if (!container) return;
    let html = "";
    for (let k = 1; k <= NUM_STEPS_DISPLAY; k++) {
        html += `<th id="step-header-${k}" class="p-2 text-center text-slate-300 font-semibold border-l border-borderbg min-w-[110px]">${k}차 (단가/목표)</th>`;
    }
    container.outerHTML = html;
}

// Connect to WebSocket Server (/ws/trading) with HTTP Polling Fallback
function connectWebSocket() {
    if (isPollingMode) return;

    try {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const urlParams = new URLSearchParams(window.location.search);
        const pwd = urlParams.get("password") || urlParams.get("auth") || "";
        const authQuery = pwd ? `?password=${encodeURIComponent(pwd)}` : "";
        const wsUrl = `${protocol}//${window.location.host}/ws/trading${authQuery}`;

        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            console.log("[WebSocket] MagicTrader Server Connected.");
            wsRetryCount = 0;
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                updateDashboardUI(data);
            } catch (err) {
                console.error("[WebSocket Parsing Error]", err);
            }
        };

        ws.onclose = () => {
            if (isPollingMode) return;
            wsRetryCount++;
            if (wsRetryCount >= 2) {
                console.warn("[WebSocket] Connection closed/failed (PythonAnywhere WSGI constraint). Switching to HTTP REST Polling Mode.");
                startPollingMode();
            } else {
                console.warn(`[WebSocket] Connection closed. Retry ${wsRetryCount}/2 in 2 seconds...`);
                setTimeout(connectWebSocket, 2000);
            }
        };

        ws.onerror = (err) => {
            console.error("[WebSocket Error]", err);
            if (!isPollingMode) {
                try { ws.close(); } catch(e) {}
            }
        };
    } catch (e) {
        console.warn("[WebSocket Exception] Switching to HTTP REST Polling Mode immediately.", e);
        startPollingMode();
    }
}

// Out-of-order REST response guard ID
let latestStatusRequestId = 0;

// Global HTTP REST Status Fetcher
async function fetchSystemStatus() {
    const reqId = ++latestStatusRequestId;
    try {
        const res = await fetch('/api/status');
        if (res.ok) {
            const data = await res.json();
            // 요청 시점 이후 새로운 요청이 발송되었으면 과거 패킷 폐기 (Race condition / Out-of-order 방어)
            if (reqId < latestStatusRequestId) {
                console.log(`[HTTP Polling] Dropped out-of-order status response (#${reqId} < #${latestStatusRequestId})`);
                return null;
            }
            updateDashboardUI(data);
            return data;
        }
    } catch (err) {
        console.error("[HTTP Polling Error]", err);
    }
    return null;
}

// Start HTTP REST Polling Mode (Fallback for WSGI / PythonAnywhere)
function startPollingMode() {
    if (isPollingMode) return;
    isPollingMode = true;

    if (ws) {
        try {
            ws.onopen = null;
            ws.onmessage = null;
            ws.onclose = null;
            ws.onerror = null;
            ws.close();
        } catch (e) {}
        ws = null;
    }

    console.log("[HTTP Polling] Real-time HTTP Status Polling Started (2.5s interval).");

    fetchSystemStatus();
    if (!pollingTimer) {
        pollingTimer = setInterval(fetchSystemStatus, 2500);
    }
}


// User Toggle Lock Map (stockCode -> { targetState: bool, lockUntil: number }) - uWSGI multi-worker sync
const userActiveLocks = new Map();

// Update Entire UI from Backend Broadcast Data Payload
function updateDashboardUI(data) {
    if (data.status) {
        currentEngineStatus = data.status;
    }
    // 1. Top System Bar Status Badge
    const badge = document.getElementById("status-badge");
    const dot = document.getElementById("status-dot");
    const text = document.getElementById("status-text");

    if (data.status === "RUNNING") {
        badge.className = "px-3 py-1 rounded-full text-xs font-semibold bg-emerald-950 text-emerald-400 border border-emerald-500 flex items-center space-x-1.5 animate-pulse";
        dot.className = "w-2 h-2 rounded-full bg-emerald-400";
        text.innerText = "자동매매 진행중 (RUNNING)";
    } else if (data.status === "EMERGENCY_SUSPENDED") {
        badge.className = "px-3 py-1 rounded-full text-xs font-semibold bg-rose-950 text-rose-400 border border-rose-500 flex items-center space-x-1.5 animate-bounce";
        dot.className = "w-2 h-2 rounded-full bg-rose-400";
        text.innerText = "🚨 권리락 긴급정지";
    } else if (data.status === "STOPPED") {
        badge.className = "px-3 py-1 rounded-full text-xs font-semibold bg-amber-950 text-amber-400 border border-amber-500 flex items-center space-x-1.5";
        dot.className = "w-2 h-2 rounded-full bg-amber-400";
        text.innerText = "일시 정지됨 (STOPPED)";
    } else {
        badge.className = "px-3 py-1 rounded-full text-xs font-semibold bg-slate-800 text-slate-400 flex items-center space-x-1.5";
        dot.className = "w-2 h-2 rounded-full bg-slate-500";
        text.innerText = "대기중 (IDLE)";
    }



    // 1.5 Server Mode Badge (Mock vs Real)
    const serverBadge = document.getElementById("server-mode-badge");
    const serverText = document.getElementById("server-mode-text");
    if (serverBadge && serverText) {
        const isMock = data.is_mock !== undefined ? data.is_mock : true;
        if (isMock) {
            serverBadge.className = "px-3 py-1 rounded-md text-xs font-bold bg-amber-950/90 text-amber-300 border border-amber-500/60 shadow flex items-center space-x-1.5";
            serverText.innerText = "🟡 키움 모의투자 서버";
        } else {
            serverBadge.className = "px-3 py-1 rounded-md text-xs font-bold bg-rose-950/90 text-rose-300 border border-rose-500/80 shadow flex items-center space-x-1.5 animate-pulse";
            serverText.innerText = "🔴 키움 실전매매 서버";
        }
    }

    // 2. Counters
    document.getElementById("counter-daily").innerText = `${data.daily_trade_count} / ${data.max_daily_trades} 회`;
    document.getElementById("counter-exposure").innerText = `${formatNumber(data.total_purchase)} / ${formatNumber(data.max_buy_amount)} 원`;
    const guardBadge = document.getElementById("current-guard-daily-badge");
    if (guardBadge) guardBadge.innerText = `현재: ${data.max_daily_trades}회`;
    const buyBadge = document.getElementById("current-guard-buy-badge");
    if (buyBadge) {
        buyBadge.innerText = `현재: ${formatNumber(data.max_buy_amount)}원`;
    }

    // 3. Grid Matrix Table Rows
    const tbody = document.getElementById("grid-table-body");
    const container = document.getElementById("grid-scroll-container");
    if (tbody && data.stocks) {
        const now = Date.now();
        // uWSGI 다중 워커 환경 상태 정합성 및 5초 강제 락 보장 (체크박스 깜빡임 완전 차단)
        data.stocks.forEach(s => {
            s.code = String(s.code).trim().padStart(6, '0');
            const cleanCode = s.code;
            if (userActiveLocks.has(cleanCode)) {
                const lock = userActiveLocks.get(cleanCode);
                const targetState = Boolean(lock.targetState);

                if (now < lock.lockUntil) {
                    // 클릭 후 5초간: 서버 응답과 무관하게 s.is_active = targetState를 강제 유지 (체크박스 핑퐁 깜빡임 완전 차단)
                    s.is_active = targetState;
                } else if (s.is_active === targetState || now > (lock.lockUntil + 2000)) {
                    // 5초 이후 서버 상태가 일치하거나 7초 경과 시 안전하게 락 해제
                    userActiveLocks.delete(cleanCode);
                } else {
                    s.is_active = targetState;
                }
            }
        });

        const savedScrollLeft = container ? container.scrollLeft : 0;
        const savedScrollTop = container ? container.scrollTop : 0;

        tbody.innerHTML = data.stocks.map(s => renderStockRow(s)).join("");

        if (container) {
            container.scrollLeft = savedScrollLeft;
            container.scrollTop = savedScrollTop;
        }
    }

    // 3.4 Update Held Stocks Portfolio Panel
    if (data.account_no) {
        const accBadge = document.getElementById("held-account-badge");
        if (accBadge) accBadge.innerText = `계좌: ${data.account_no}`;
    }

    if (data.held_positions && data.held_positions.length > 0) {
        const posList = data.held_positions.map(p => ({
            code: p.stock_code,
            name: p.stock_name,
            qty: p.qty,
            avg_price: p.avg_price,
            current_price: p.current_price,
            pnl: p.pnl,
            pnl_rate: p.pnl_rate
        }));
        renderHeldStocksPanel(posList);
    } else if (data.held_positions && data.held_positions.length === 0) {
        renderHeldStocksPanel([]);
    } else {
        const activePositions = (data.stocks || []).filter(s => s.qty && s.qty > 0);
        renderHeldStocksPanel(activePositions);
    }

    // 3.5 Update Stock Delete Select Options
    const deleteSelect = document.getElementById("delete-stock-select");
    if (deleteSelect && data.stocks) {
        const currentVal = deleteSelect.value;
        deleteSelect.innerHTML = `<option value="">등록된 종목 선택 (${data.stocks.length}개)...</option>` +
            data.stocks.map(s => `<option value="${s.code}">${s.name} (${s.code})</option>`).join("");
        deleteSelect.value = currentVal;
    }

    // 4. Log Console
    const consoleBody = document.getElementById("log-console-body");
    if (consoleBody && data.logs) {
        consoleBody.innerHTML = data.logs.map(l => {
            let color = "text-slate-300";
            if (l.level === "ERROR") color = "text-rose-400 font-bold";
            else if (l.level === "WARNING") color = "text-amber-400";
            else if (l.level === "INFO") color = "text-emerald-400";
            return `<div class="${color}">[${l.timestamp}] [${l.level}] ${l.message}</div>`;
        }).join("");
    }

    document.getElementById("last-updated-time").innerText = data.timestamp || "";

    // 5. Bottom Account Bar
    document.getElementById("footer-purchase").innerText = `${formatNumber(data.total_purchase)} 원`;
    document.getElementById("footer-valuation").innerText = `${formatNumber(data.total_valuation)} 원`;
    document.getElementById("footer-net-assets").innerText = `${formatNumber(data.net_assets)} 원`;

    const pnlEl = document.getElementById("footer-pnl");
    const pnlRate = data.total_purchase > 0 ? ((data.unrealized_pnl / data.total_purchase) * 100).toFixed(2) : "0.00";
    if (data.unrealized_pnl > 0) {
        pnlEl.className = "font-bold text-emerald-400 text-sm";
        pnlEl.innerText = `+${formatNumber(data.unrealized_pnl)} 원 (+${pnlRate}%)`;
    } else if (data.unrealized_pnl < 0) {
        pnlEl.className = "font-bold text-rose-400 text-sm";
        pnlEl.innerText = `${formatNumber(data.unrealized_pnl)} 원 (${pnlRate}%)`;
    } else {
        pnlEl.className = "font-bold text-slate-300 text-sm";
        pnlEl.innerText = `0 원 (0.00%)`;
    }

    // 6. Emergency Alert Modal Check
    if (data.pending_alert) {
        currentPendingAlert = data.pending_alert;
        document.getElementById("alert-stock-info").innerText = data.pending_alert.message;
        document.getElementById("alert-modal").classList.remove("hidden");
    } else {
        document.getElementById("alert-modal").classList.add("hidden");
    }
}

let currentEngineStatus = "IDLE";
const selectedStockCodes = new Set();

function handleSelectStockRow(code, isChecked) {
    if (isChecked) {
        selectedStockCodes.add(code);
    } else {
        selectedStockCodes.delete(code);
    }
    updateSelectAllHeaderState();
}

function updateSelectAllHeaderState() {
    const allRowBoxes = document.querySelectorAll(".stock-row-select");
    const headerCheck = document.getElementById("select-all-stocks");
    if (headerCheck && allRowBoxes.length > 0) {
        const checkedCount = document.querySelectorAll(".stock-row-select:checked").length;
        headerCheck.checked = (checkedCount === allRowBoxes.length);
    }
}

// Render Single Stock Row in Table
function renderStockRow(stock) {
    let statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] bg-slate-700 text-slate-300">추적중</span>`;
    if (stock.status === "SUSPENDED_PRICE_ADJUSTMENT") {
        statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] bg-rose-900 text-rose-200 border border-rose-500 animate-pulse">🚨 권리락 정지</span>`;
    } else if (stock.status === "LIQUIDATED") {
        statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] bg-amber-900 text-amber-200">🎉 청산 완료</span>`;
    } else if (stock.status === "PAUSED_ALLOCATION") {
        statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] bg-indigo-900 text-indigo-200">신주배정 대기</span>`;
    }

    const isInactive = stock.is_active === false;
    const rowBg = isInactive ? "bg-slate-900/60 opacity-60" : "hover:bg-slate-800/50";
    if (isInactive) {
        statusBadge += ` <span class="px-1.5 py-0.5 rounded text-[10px] bg-slate-800 text-slate-400 border border-slate-600">OFF</span>`;
    }

    const isRowChecked = selectedStockCodes.has(stock.code);
    const steps = (stock.grid && stock.grid.steps) ? stock.grid.steps : [];
    
    // Build 1~20 step cells with color highlights
    let stepCells = "";
    for (let k = 1; k <= NUM_STEPS_DISPLAY; k++) {
        const stepData = steps.find(s => s.step === k);
        if (stepData) {
            let cellClass = "p-2 text-center border-l border-borderbg text-[11px]";
            if (stock.current_step === k) {
                cellClass += " grid-cell-active";
            } else if (stock.current_step > 0 && k < stock.current_step) {
                cellClass += " grid-cell-tp";
            }
            if (stock.status === "SUSPENDED_PRICE_ADJUSTMENT") {
                cellClass += " grid-cell-alert";
            }

            stepCells += `<td class="${cellClass}">
                <div class="font-medium">${formatNumber(stepData.price)}원</div>
                <div class="text-[10px] opacity-75">${stepData.target_total_qty}주</div>
            </td>`;
        } else {
            stepCells += `<td class="p-2 text-center border-l border-borderbg text-slate-600 text-[11px]">-</td>`;
        }
    }

    let hslBadge = '';
    if (!stock.hard_stop_loss_enabled) {
        hslBadge = `<span class="text-[10px] block mt-0.5 bg-amber-950/80 text-amber-400 border border-amber-700/60 px-1 py-0.2 rounded" title="하드 손절 미설정 상태">⚠️ 손절 미설정</span>`;
    } else if (stock.stop_loss_halted) {
        hslBadge = `<span class="text-[10px] block mt-0.5 bg-rose-950 text-rose-300 border border-rose-600 px-1 py-0.2 rounded font-bold animate-pulse" title="${stock.stop_loss_triggered_at || ''}">🛑 손절 발동됨</span><button onclick="resetStopLoss('${stock.code}')" class="mt-0.5 px-1.5 py-0.5 bg-indigo-600 hover:bg-indigo-500 text-white text-[10px] rounded transition shadow">재개</button>`;
    } else {
        hslBadge = `<span class="text-[10px] block mt-0.5 text-rose-400 font-normal">🛡️ 손절: ${formatNumber(stock.hard_stop_loss_price)}원</span>`;
    }

    const changeClass = stock.change > 0 ? "text-emerald-400" : (stock.change < 0 ? "text-rose-400" : "text-slate-400");
    const pnlClass = stock.pnl > 0 ? "text-emerald-400" : (stock.pnl < 0 ? "text-rose-400" : "text-slate-400");

    const stickyCellBg = isInactive ? "bg-slate-950" : "bg-slate-900 group-hover:bg-slate-800";

    return `
        <tr id="stock-row-${stock.code}" class="${rowBg} group transition">
            <td class="p-3 text-center sticky left-0 z-10 min-w-[40px] ${stickyCellBg} transition-colors">
                <input type="checkbox" value="${stock.code}" ${isRowChecked ? 'checked' : ''} onchange="handleSelectStockRow('${stock.code}', this.checked)" class="stock-row-select w-4 h-4 accent-rose-500 rounded bg-slate-900 border-slate-700 cursor-pointer" title="종목 선택">
            </td>
            <td class="p-3 text-center sticky left-[40px] z-10 min-w-[50px] ${stickyCellBg} transition-colors">
                <input type="checkbox" id="chk-active-${stock.code}" ${stock.is_active !== false ? 'checked' : ''} onchange="handleToggleStockActive('${stock.code}', this.checked)" title="자동매매 스위치 ON/OFF" class="w-4 h-4 accent-emerald-500 rounded bg-slate-900 border-slate-700 cursor-pointer">
            </td>
            <td class="p-3 font-mono font-bold text-slate-300 sticky left-[90px] z-10 min-w-[80px] ${stickyCellBg} transition-colors">${stock.code}</td>
            <td class="p-3 font-semibold text-slate-100 sticky left-[170px] z-10 min-w-[100px] ${stickyCellBg} transition-colors">${stock.name}</td>
            <td class="p-3 font-bold ${changeClass} sticky left-[270px] z-10 min-w-[95px] border-r-2 border-indigo-500/50 shadow-md ${stickyCellBg} transition-colors">
                ${formatNumber(stock.current_price)}원
                <span class="text-[10px] block font-normal">${stock.change_rate > 0 ? '+' : ''}${stock.change_rate}%</span>
            </td>
            <td class="p-3 font-bold text-center">
                <span class="px-2.5 py-1 rounded-md bg-indigo-950 text-indigo-300 border border-indigo-500 font-mono text-xs">${stock.current_step} 차</span>
            </td>
            <td class="p-3 font-mono">${stock.qty} 주</td>
            <td class="p-3 ${pnlClass} font-semibold">
                ${formatNumber(stock.pnl)}원
                <span class="text-[10px] block font-normal">(${stock.pnl_rate}%)</span>
            </td>
            <td class="p-3">${statusBadge}</td>
            <td class="p-3 font-bold text-cyan-400 font-mono">${formatNumber(stock.clear_price)}원 ${hslBadge}</td>
            ${stepCells}
            <td class="p-3 text-slate-400 italic">${stock.memo || '-'}</td>
            <td class="p-3 text-center">
                <button onclick="handleDeleteStock('${stock.code}', '${stock.name}')" title="종목 삭제" class="px-2 py-1 bg-rose-950/80 hover:bg-rose-800 text-rose-300 border border-rose-700/60 rounded text-[11px] font-medium transition flex items-center space-x-1 mx-auto shadow">
                    <i class="fa-solid fa-trash"></i>
                    <span>삭제</span>
                </button>
            </td>
        </tr>
    `;
}

async function handleToggleStockActive(stockCode, isChecked) {
    const cleanCode = String(stockCode).trim().padStart(6, '0');
    const targetState = Boolean(isChecked);
    // 클릭 즉시 5초 강제 보존 락 부여
    userActiveLocks.set(cleanCode, { targetState: targetState, lockUntil: Date.now() + 5000 });

    // 즉시 해당 종목 행의 DOM 스타일을 토글하여 우수한 UX 반응성 제공 (전체 재렌더링 방지)
    const rowEl = document.getElementById("stock-row-" + cleanCode);
    if (rowEl) {
        if (!targetState) {
            rowEl.classList.add("bg-slate-900/60", "opacity-60");
            rowEl.classList.remove("hover:bg-slate-800/50");
        } else {
            rowEl.classList.remove("bg-slate-900/60", "opacity-60");
            rowEl.classList.add("hover:bg-slate-800/50");
        }
    }

    try {
        const res = await fetch("/api/stocks/toggle-active", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ stock_code: cleanCode, is_active: targetState })
        });

        if (!res.ok) {
            const errText = await res.text();
            console.error("[Toggle Error Response]", errText);
            alert(`자동매매 스위치 변경 서버 오류 (${res.status})`);
            userActiveLocks.delete(cleanCode);
            fetchSystemStatus();
            return;
        }

        const data = await res.json();
        console.log("[Toggle Stock Active]", data);
        if (!data.success) {
            alert("자동매매 스위치 변경 실패: " + (data.detail || data.message || "서버 응답 오류"));
            userActiveLocks.delete(cleanCode);
            fetchSystemStatus();
        }
    } catch (err) {
        alert("자동매매 스위치 변경 예외: " + err);
        userActiveLocks.delete(cleanCode);
        fetchSystemStatus();
    }
}

async function handleDeleteStock(stockCode, stockName) {
    if (currentEngineStatus === "RUNNING") {
        alert("🛑 자동매매가 진행 중(RUNNING)입니다. 먼저 상단 [일시 정지] 버튼을 눌러 자동매매를 정지하신 후 종목을 삭제해주세요.");
        return;
    }

    if (!confirm(`정말로 종목 [${stockName}(${stockCode})]을 삭제하시겠습니까?\n(※ 미결제 주문 취소 및 보유 포지션 정리 후 삭제됩니다)`)) {
        return;
    }
    try {
        const res = await fetch("/api/stocks/delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ stock_code: stockCode })
        });
        const data = await res.json();
        if (data.success) {
            selectedStockCodes.delete(stockCode);
            updateSelectAllHeaderState();
            console.log("[Delete Stock]", data.message);
        } else {
            alert("종목 삭제 오류: " + (data.detail || data.message));
        }
    } catch (err) {
        alert("종목 삭제 실패: " + err);
    }
}

// Check All / Uncheck All Table Rows
function toggleSelectAllStocks(isChecked) {
    const checkboxes = document.querySelectorAll(".stock-row-select");
    checkboxes.forEach(cb => {
        cb.checked = isChecked;
        if (isChecked) {
            selectedStockCodes.add(cb.value);
        } else {
            selectedStockCodes.delete(cb.value);
        }
    });
}

// Batch Delete Selected Stocks from Table (Supports simultaneous multi-deletion)
async function handleDeleteSelectedStocks() {
    if (currentEngineStatus === "RUNNING") {
        alert("🛑 자동매매가 진행 중(RUNNING)입니다. 먼저 상단 [일시 정지] 버튼을 눌러 자동매매를 정지하신 후 종목을 삭제해주세요.");
        return;
    }

    if (selectedStockCodes.size === 0) {
        alert("삭제할 종목을 체크박스로 선택해주세요.");
        return;
    }

    const codes = Array.from(selectedStockCodes);
    if (!confirm(`선택한 ${codes.length}개 종목을 정말로 모두 동시 삭제하시겠습니까?\n(※ 미결제 주문 취소 및 포지션 정리 후 삭제됩니다)`)) {
        return;
    }

    let successCount = 0;
    for (const code of codes) {
        try {
            const res = await fetch("/api/stocks/delete", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ stock_code: code })
            });
            const data = await res.json();
            if (data.success) {
                selectedStockCodes.delete(code);
                successCount++;
            } else {
                alert(`종목(${code}) 삭제 불가: ` + (data.detail || data.message));
            }
        } catch (err) {
            console.error(`[Delete Stock Failed] ${code}`, err);
        }
    }
    const headerCheck = document.getElementById("select-all-stocks");
    if (headerCheck) headerCheck.checked = false;
    alert(`선택한 ${successCount}개 종목 동시 삭제 완료!`);
}

// Manual Delete Stock from Left Control Panel Dropdown
async function handleManualDeleteFromPanel() {
    const select = document.getElementById("delete-stock-select");
    const stockCode = select ? select.value : "";
    if (!stockCode) {
        alert("삭제할 종목을 드롭다운 목록에서 선택해주세요.");
        return;
    }
    const selectedText = select.options[select.selectedIndex].text;
    await handleDeleteStock(stockCode, selectedText);
}

// Number Formatting Helper (1원 단위 정수 표출, 소수점 금액 삭제)
function formatNumber(num) {
    if (num === null || num === undefined || isNaN(num)) return "0";
    return Math.round(Number(num)).toLocaleString("ko-KR");
}

// User Action Event Handlers
async function handleControl(action) {
    try {
        const res = await fetch("/api/control", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action })
        });

        if (!res.ok) {
            const errText = await res.text();
            console.error("[Control Error Response]", errText);
            alert(`제어 요청 서버 오류 (${res.status})`);
            return;
        }

        const data = await res.json();
        console.log("[Control Action]", data);
        if (data.success) {
            fetchSystemStatus();
        } else {
            alert("제어 요청 실패: " + (data.message || data.detail || "오류"));
        }
    } catch (err) {
        alert("제어 요청 통신 예외: " + err);
    }
}

function toggleStopLossInput(isChecked) {
    const wrapper = document.getElementById("stop-loss-input-wrapper");
    if (wrapper) {
        if (isChecked) {
            wrapper.classList.remove("hidden");
        } else {
            wrapper.classList.add("hidden");
        }
    }
}

async function resetStopLoss(stockCode) {
    if (!confirm(`종목(${stockCode})의 손절 정지 상태를 해제하고 매매를 재개하시겠습니까?`)) {
        return;
    }
    try {
        const res = await fetch("/api/stocks/reset-stop-loss", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ stock_code: stockCode })
        });
        const data = await res.json();
        if (data.success) {
            alert(data.message);
            fetchSystemStatus();
        } else {
            alert("손절 해제 실패: " + (data.message || "오류"));
        }
    } catch (err) {
        alert("손절 해제 통신 예외: " + err);
    }
}

async function handleAddStock(event) {
    event.preventDefault();
    const hslEnableEl = document.getElementById("add-stop-loss-enable");
    const hslPriceEl = document.getElementById("add-stop-loss-price");
    const payload = {
        code: document.getElementById("add-code").value.trim(),
        name: document.getElementById("add-name").value.trim(),
        base_price: parseInt(document.getElementById("add-base-price").value),
        clear_price: parseInt(document.getElementById("add-clear-price").value),
        num_steps: parseInt(document.getElementById("add-steps").value),
        start_step: parseInt(document.getElementById("add-start-step").value) || 1,
        step_pct: parseFloat(document.getElementById("add-step-pct").value),
        budget_per_step: parseInt(document.getElementById("add-budget").value),
        memo: document.getElementById("add-memo").value.trim(),
        hard_stop_loss_enabled: hslEnableEl ? hslEnableEl.checked : false,
        hard_stop_loss_price: hslPriceEl ? (parseInt(hslPriceEl.value) || 0) : 0
    };

    try {
        const res = await fetch("/api/stocks", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const errText = await res.text();
            console.error("[Add Stock Error Response]", errText);
            alert(`종목 추가 서버 오류 (${res.status})`);
            return;
        }

        const data = await res.json();
        if (data.success) {
            alert(data.message || "종목이 성공적으로 등록되었습니다.");
            document.getElementById("add-stock-form").reset();
            document.getElementById("add-start-step").value = 1;
            fetchSystemStatus();
        } else {
            alert("종목 추가 실패: " + (data.message || data.detail || "오류"));
        }
    } catch (err) {
        alert("종목 추가 통신 예외: " + err);
    }
}

async function handleUpdateGuard() {
    const payload = {
        max_daily_trades: intVal(document.getElementById("guard-max-daily").value),
        max_buy_amount: intVal(document.getElementById("guard-max-buy").value)
    };

    try {
        const res = await fetch("/api/system/guard", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        alert(data.message);
    } catch (err) {
        alert("보호 설정 업데이트 실패: " + err);
    }
}

function setDailyTradePreset(val) {
    const input = document.getElementById("guard-max-daily");
    if (input) {
        input.value = val;
    }
    handleUpdateGuard();
}

function setMaxBuyPreset(val) {
    const input = document.getElementById("guard-max-buy");
    if (input) {
        input.value = val;
    }
    handleUpdateGuard();
}

async function resetDailyTradesToDefault() {
    const input = document.getElementById("guard-max-daily");
    if (input) {
        input.value = 20;
    }
    await handleUpdateGuard();
    alert("일일 최대 매매 횟수가 디폴트값(20회)으로 초기화 및 적용되었습니다.");
}

async function resetSystemGuardToDefault() {
    const dailyInput = document.getElementById("guard-max-daily");
    const buyInput = document.getElementById("guard-max-buy");
    if (dailyInput) dailyInput.value = 20;
    if (buyInput) buyInput.value = 50000000;
    await handleUpdateGuard();
    alert("시스템 보호 설정이 모두 기본 디폴트값(20회 / 50,000,000원)으로 초기화 및 적용되었습니다.");
}

async function handleSaveDB() {
    const res = await fetch("/api/db/save", { method: "POST" });
    const data = await res.json();
    alert(data.message);
}

async function handleLoadDB() {
    const res = await fetch("/api/db/load", { method: "POST" });
    const data = await res.json();
    alert(data.message);
}

async function handleLoadBackup() {
    const res = await fetch("/api/db/backup", { method: "POST" });
    const data = await res.json();
    showToast(data.message, data.success ? "success" : "warning");
}

async function handleResetDB() {
    if (!confirm("⚠️ [DB 데이터 초기화 경고]\n\n등록된 모든 종목 그리드 데이터, 보유 포지션 및 매매 이력 로그가 초기화됩니다.\n\n정말로 데이터베이스를 초기화하시겠습니까?")) {
        return;
    }

    try {
        const res = await fetch("/api/db/reset", { method: "POST" });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast("🗑️ 데이터베이스가 성공적으로 초기화되었습니다.", "success");
            fetchSystemStatus();
            fetchKiwoomPositions();
        } else {
            showToast("DB 초기화 실패: " + (data.detail || data.message || "오류"), "error");
        }
    } catch (err) {
        showToast("DB 초기화 중 통신 오류 발생: " + err, "error");
    }
}

function downloadTradeLogs() {
    const link = document.createElement("a");
    link.href = "/api/logs/download";
    link.download = `magictrader_trade_logs.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    showToast("📥 매매 이력 로그 파일 다운로드가 시작되었습니다.", "success");
}





async function handleResolveEmergency(actionChoice) {
    if (!currentPendingAlert) return;
    try {
        const res = await fetch("/api/emergency/resolve", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                stock_code: currentPendingAlert.stock_code,
                action_choice: actionChoice
            })
        });
        const data = await res.json();
        document.getElementById("alert-modal").classList.add("hidden");
        console.log("[Resolve Emergency]", data);
    } catch (err) {
        alert("긴급 정지 해제 조치 실패: " + err);
    }
}

async function handleTriggerEmergencyTest() {
    try {
        const res = await fetch("/api/emergency/test-trigger", { method: "POST" });
        const data = await res.json();
        console.log("[Test Emergency Trigger]", data);
    } catch (err) {
        alert("권리락 테스트 요청 실패: " + err);
    }
}

// Modal Toggle
function toggleGridCalculatorModal() {
    const modal = document.getElementById("calculator-modal");
    modal.classList.toggle("hidden");
}

// Apply Grid Calculator Values to Add Stock Form
function applyCalculatorToForm() {
    const basePrice = intVal(document.getElementById("calc-start-price").value);
    const startStep = intVal(document.getElementById("calc-start-step").value) || 1;
    const dropPct = parseFloat(document.getElementById("calc-drop-pct").value);
    const budget = intVal(document.getElementById("calc-budget").value);
    const maxSteps = intVal(document.getElementById("calc-max-steps").value);

    document.getElementById("add-base-price").value = basePrice;
    document.getElementById("add-clear-price").value = Math.floor(basePrice * 1.15); // +15% 기본 청산가 설정
    document.getElementById("add-steps").value = maxSteps;
    document.getElementById("add-start-step").value = startStep;
    document.getElementById("add-step-pct").value = dropPct;
    document.getElementById("add-budget").value = budget;

    toggleGridCalculatorModal();
    alert(`계산기 설정값(${startStep}차 진입 ~ ${maxSteps}차)이 종목 등록 폼에 반영되었습니다!`);
}

// Run Interactive Grid Calculation Preview
function runGridCalculation() {
    const basePrice = intVal(document.getElementById("calc-start-price").value);
    const startStep = intVal(document.getElementById("calc-start-step").value) || 1;
    const dropPct = parseFloat(document.getElementById("calc-drop-pct").value);
    const budget = intVal(document.getElementById("calc-budget").value);
    const maxSteps = intVal(document.getElementById("calc-max-steps").value);

    let html = "";
    let accumQty = 0;
    let totalSpent = 0;

    for (let k = 1; k <= maxSteps; k++) {
        let rawPrice = basePrice * (1.0 - ((k - startStep) * dropPct / 100.0));
        let price = Math.max(10, Math.round(Math.round(rawPrice) / 10) * 10);
        let qty = Math.max(1, Math.floor(budget / price));
        accumQty += qty;
        totalSpent += price * qty;

        const isStart = k === startStep;
        const style = isStart ? "text-amber-300 font-bold bg-amber-950/40 p-1 rounded" : "";
        const tag = isStart ? " ⭐ [시작 진입차수]" : "";

        html += `<div class="${style}">[${k}차${tag}] 매수가: ${formatNumber(price)}원 | 필요수량: ${formatNumber(qty)}주 | 누적목표: ${formatNumber(accumQty)}주 | 총소요금: ${formatNumber(totalSpent)}원</div>`;
    }

    document.getElementById("calc-results-preview").innerHTML = html;
}

function intVal(str) {
    return parseInt(str, 10) || 0;
}

// Real-time Bidirectional Base Price & Parameter Synchronization
function calculateSuggestedClearPrice() {
    const basePrice = parseFloat(document.getElementById("add-base-price").value) || 0;
    const startStep = parseInt(document.getElementById("add-start-step").value, 10) || 1;
    const stepPct = parseFloat(document.getElementById("add-step-pct").value) || 1.5;

    if (basePrice <= 0) return;

    // 1차 가격 계산 = basePrice * (1.0 - (1 - startStep) * stepPct / 100.0)
    const price1 = Math.round(Math.round(basePrice * (1.0 - ((1 - startStep) * stepPct / 100.0))) / 10) * 10;
    
    // 청산가격 = 1차 가격에 하락률(stepPct%) 반영 (10원 단위 정조정)
    let clearPrice = Math.round(Math.round(price1 * (1.0 + (stepPct / 100.0))) / 10) * 10;

    const clearInput = document.getElementById("add-clear-price");
    if (clearInput) {
        clearInput.value = clearPrice;
    }
}

function syncBasePriceFromForm(val) {
    const calcInput = document.getElementById("calc-start-price");
    if (calcInput && val !== "") calcInput.value = val;
    calculateSuggestedClearPrice();
}

function syncBasePriceFromCalc(val) {
    const formInput = document.getElementById("add-base-price");
    if (formInput && val !== "") formInput.value = val;
    calculateSuggestedClearPrice();
}

function syncStartStepFromForm(val) {
    const calcInput = document.getElementById("calc-start-step");
    if (calcInput && val !== "") calcInput.value = val;
    calculateSuggestedClearPrice();
}

function syncStartStepFromCalc(val) {
    const formInput = document.getElementById("add-start-step");
    if (formInput && val !== "") formInput.value = val;
    calculateSuggestedClearPrice();
}

function syncTotalStepsFromForm(val) {
    const calcInput = document.getElementById("calc-max-steps");
    if (calcInput && val !== "") calcInput.value = val;
}

function syncTotalStepsFromCalc(val) {
    const formInput = document.getElementById("add-steps");
    if (formInput && val !== "") formInput.value = val;
}

function syncDropPctFromForm(val) {
    const calcInput = document.getElementById("calc-drop-pct");
    if (calcInput && val !== "") calcInput.value = val;
    calculateSuggestedClearPrice();
}

function syncDropPctFromCalc(val) {
    const formInput = document.getElementById("add-step-pct");
    if (formInput && val !== "") formInput.value = val;
    calculateSuggestedClearPrice();
}

function syncBudgetFromForm(val) {
    const calcInput = document.getElementById("calc-budget");
    if (calcInput && val !== "") calcInput.value = val;
}

function syncBudgetFromCalc(val) {
    const formInput = document.getElementById("add-budget");
    if (formInput && val !== "") formInput.value = val;
}

// Stock Code -> Name & Name -> Code Bidirectional Auto-Lookup Engine & Price Quote
let currentFetchedPrice = 0;

async function fetchStockPriceQuote(stockCode) {
    if (!stockCode || stockCode.length !== 6) return;
    try {
        const res = await fetch(`/api/stocks/quote?code=${stockCode}`);
        const data = await res.json();
        if (data.success && data.current_price) {
            currentFetchedPrice = data.current_price;
            const badge = document.getElementById("current-price-badge");
            if (badge) {
                badge.innerText = `📊 현재가: ${formatNumber(data.current_price)}원`;
                badge.classList.remove("hidden");
            }
            const basePriceInput = document.getElementById("add-base-price");
            const calcPriceInput = document.getElementById("calc-start-price");
            
            // 현재가를 기준가/청산가에 즉시 반영
            if (basePriceInput) {
                basePriceInput.value = data.current_price;
                basePriceInput.dataset.autoFilled = "true";
                syncBasePriceFromForm(data.current_price);
            }
            if (calcPriceInput) {
                calcPriceInput.value = data.current_price;
            }
            calculateSuggestedClearPrice();
        }
    } catch (err) {
        console.error("[Quote Fetch Error]", err);
    }
}

function applyCurrentPriceToForm() {
    if (!currentFetchedPrice) return;
    const basePriceInput = document.getElementById("add-base-price");
    const calcPriceInput = document.getElementById("calc-start-price");
    if (basePriceInput) {
        basePriceInput.value = currentFetchedPrice;
        syncBasePriceFromForm(currentFetchedPrice);
    }
    if (calcPriceInput) calcPriceInput.value = currentFetchedPrice;
    calculateSuggestedClearPrice();
    alert(`현재가(${formatNumber(currentFetchedPrice)}원) 및 1차 가격 기준 청산가가 신규 등록 폼에 반영되었습니다!`);
}

async function handleCodeInput(codeVal) {
    const cleanCode = codeVal.trim().toUpperCase();
    const statusEl = document.getElementById("code-lookup-status");
    const nameEl = document.getElementById("add-name");

    if (!cleanCode) {
        if (statusEl) statusEl.innerText = "";
        const badge = document.getElementById("current-price-badge");
        if (badge) badge.classList.add("hidden");
        return;
    }

    try {
        const res = await fetch(`/api/stocks/search?q=${encodeURIComponent(cleanCode)}`);
        const data = await res.json();
        const datalist = document.getElementById("stock-code-datalist");
        if (datalist && data.matches) {
            datalist.innerHTML = data.matches.map(m => `<option value="${m.code}">${m.name} (${m.code})</option>`).join("");
        }

        const match = data.matches.find(m => m.code === cleanCode);
        if (match) {
            if (nameEl) nameEl.value = match.name;
            if (statusEl) statusEl.innerText = `✓ ${match.name}`;
            fetchStockPriceQuote(cleanCode);
        } else if (cleanCode.length === 6) {
            const lookupRes = await fetch(`/api/stocks/lookup?code=${cleanCode}`);
            const lookupData = await lookupRes.json();
            if (lookupData.success) {
                if (nameEl) nameEl.value = lookupData.name;
                if (statusEl) statusEl.innerText = `✓ ${lookupData.name}`;
            } else {
                if (statusEl) statusEl.innerText = "";
            }
            fetchStockPriceQuote(cleanCode);
        } else {
            if (statusEl) statusEl.innerText = "";
        }
    } catch (err) {
        console.error("[Code Lookup Error]", err);
    }
}

async function handleNameInput(nameVal) {
    const cleanName = nameVal.trim();
    const statusEl = document.getElementById("name-lookup-status");
    const codeEl = document.getElementById("add-code");

    if (!cleanName) {
        if (statusEl) statusEl.innerText = "";
        return;
    }

    try {
        const res = await fetch(`/api/stocks/search?q=${encodeURIComponent(cleanName)}`);
        const data = await res.json();
        const datalist = document.getElementById("stock-name-datalist");
        if (datalist && data.matches) {
            datalist.innerHTML = data.matches.map(m => `<option value="${m.name}">${m.code} - ${m.name}</option>`).join("");
        }

        const match = data.matches.find(m => m.name === cleanName || m.name.toUpperCase() === cleanName.toUpperCase());
        if (match) {
            if (codeEl) codeEl.value = match.code;
            if (statusEl) statusEl.innerText = `✓ ${match.code}`;
            fetchStockPriceQuote(match.code);
        } else if (data.matches.length > 0) {
            const topMatch = data.matches[0];
            if (codeEl && !codeEl.value) codeEl.value = topMatch.code;
            if (statusEl) statusEl.innerText = `✓ ${topMatch.code}`;
            fetchStockPriceQuote(topMatch.code);
        } else {
            if (statusEl) statusEl.innerText = "";
        }
    } catch (err) {
        console.error("[Name Lookup Error]", err);
    }
}

function handleCodeBlur(codeVal) {
    handleCodeInput(codeVal);
}

function handleNameBlur(nameVal) {
    handleNameInput(nameVal);
}

// Quick Scroll Helpers for Real-time Grid Matrix
function scrollToGridStep(stepNum) {
    const container = document.getElementById("grid-scroll-container");
    const targetHeader = document.getElementById(`step-header-${stepNum}`);
    if (container && targetHeader) {
        const frozenWidth = 365;
        const scrollPos = targetHeader.offsetLeft - frozenWidth;
        container.scrollTo({ left: Math.max(0, scrollPos), behavior: "smooth" });
    }
}

function scrollToGridColumn(dir) {
    const container = document.getElementById("grid-scroll-container");
    if (!container) return;
    if (dir === 'left') {
        container.scrollTo({ left: 0, behavior: "smooth" });
    } else if (dir === 'right') {
        container.scrollTo({ left: container.scrollWidth, behavior: "smooth" });
    }
}

function setupGridWheelScroll() {
    const container = document.getElementById("grid-scroll-container");
    if (!container) return;
    container.addEventListener("wheel", (evt) => {
        if (evt.deltaX !== 0) return;
        if (evt.shiftKey) {
            evt.preventDefault();
            container.scrollLeft += evt.deltaY;
        }
    }, { passive: false });
}

// Render Held Stocks Portfolio Panel (Only stocks with qty > 0)
function renderHeldStocksPanel(stocks) {
    const tbody = document.getElementById("held-stocks-tbody");
    const emptyEl = document.getElementById("held-stocks-empty");
    const badgeEl = document.getElementById("held-count-badge");
    const purchaseEl = document.getElementById("held-summary-purchase");
    const valuationEl = document.getElementById("held-summary-valuation");
    const pnlEl = document.getElementById("held-summary-pnl");

    if (!tbody) return;

    // Filter stocks with qty > 0
    const heldStocks = stocks.filter(s => s.qty && s.qty > 0);

    if (badgeEl) {
        badgeEl.innerText = `${heldStocks.length}개 종목 보유`;
    }

    if (heldStocks.length === 0) {
        tbody.innerHTML = "";
        if (emptyEl) emptyEl.classList.remove("hidden");
        if (purchaseEl) purchaseEl.innerText = "0 원";
        if (valuationEl) valuationEl.innerText = "0 원";
        if (pnlEl) {
            pnlEl.className = "font-bold text-slate-400";
            pnlEl.innerText = "0 원 (0.00%)";
        }
        return;
    }

    if (emptyEl) emptyEl.classList.add("hidden");

    let totalPurchase = 0;
    let totalValuation = 0;
    let totalPnl = 0;

    heldStocks.forEach(s => {
        const purchaseAmt = (s.avg_price || s.current_price) * s.qty;
        const valAmt = s.current_price * s.qty;
        const pnlAmt = s.pnl !== undefined ? s.pnl : (valAmt - purchaseAmt);
        totalPurchase += purchaseAmt;
        totalValuation += valAmt;
        totalPnl += pnlAmt;
    });

    if (purchaseEl) purchaseEl.innerText = `${formatNumber(totalPurchase)} 원`;
    if (valuationEl) valuationEl.innerText = `${formatNumber(totalValuation)} 원`;
    if (pnlEl) {
        const totalPnlRate = totalPurchase > 0 ? ((totalPnl / totalPurchase) * 100).toFixed(2) : "0.00";
        if (totalPnl > 0) {
            pnlEl.className = "font-bold text-emerald-400";
            pnlEl.innerText = `+${formatNumber(totalPnl)} 원 (+${totalPnlRate}%)`;
        } else if (totalPnl < 0) {
            pnlEl.className = "font-bold text-rose-400";
            pnlEl.innerText = `${formatNumber(totalPnl)} 원 (${totalPnlRate}%)`;
        } else {
            pnlEl.className = "font-bold text-slate-300";
            pnlEl.innerText = `0 원 (0.00%)`;
        }
    }

    // 1초 자동 갱신 시 사용자가 선택한 체크박스 상태 보존
    const checkedCodes = new Set(
        Array.from(document.querySelectorAll(".held-stock-checkbox:checked")).map(cb => cb.dataset.code)
    );

    tbody.innerHTML = heldStocks.map(s => {
        const code = s.code || s.stock_code;
        const name = s.name || s.stock_name;
        const purchaseAmt = (s.avg_price || s.current_price) * s.qty;
        const valAmt = s.current_price * s.qty;
        const pnlAmt = s.pnl !== undefined ? s.pnl : (valAmt - purchaseAmt);
        const pnlRate = s.pnl_rate !== undefined ? s.pnl_rate : (purchaseAmt > 0 ? ((pnlAmt / purchaseAmt) * 100).toFixed(2) : "0.00");
        const weight = totalValuation > 0 ? ((valAmt / totalValuation) * 100).toFixed(1) : "0.0";

        const changeClass = s.change > 0 ? "text-emerald-400" : (s.change < 0 ? "text-rose-400" : "text-slate-400");
        const pnlClass = pnlAmt > 0 ? "text-emerald-400 font-semibold" : (pnlAmt < 0 ? "text-rose-400 font-semibold" : "text-slate-400");
        const isChecked = checkedCodes.has(code);

        return `
            <tr class="hover:bg-slate-800/60 transition">
                <td class="p-2 text-center"><input type="checkbox" class="held-stock-checkbox w-3.5 h-3.5 accent-rose-500 rounded cursor-pointer" data-code="${code}" data-name="${name}" data-qty="${s.qty}" ${isChecked ? 'checked' : ''}></td>
                <td class="p-2 font-bold text-slate-300">${code}</td>
                <td class="p-2 font-semibold text-slate-100 font-sans">${name}</td>
                <td class="p-2 text-right font-bold text-amber-400">${formatNumber(s.qty)} 주</td>
                <td class="p-2 text-right text-slate-300">${formatNumber(s.avg_price || s.current_price)} 원</td>
                <td class="p-2 text-right font-bold ${changeClass}">${formatNumber(s.current_price)} 원</td>
                <td class="p-2 text-right text-slate-300">${formatNumber(purchaseAmt)} 원</td>
                <td class="p-2 text-right font-bold text-slate-100">${formatNumber(valAmt)} 원</td>
                <td class="p-2 text-right ${pnlClass}">
                    ${pnlAmt > 0 ? '+' : ''}${formatNumber(pnlAmt)} 원 (${pnlAmt > 0 ? '+' : ''}${pnlRate}%)
                </td>
                <td class="p-2 text-right text-cyan-400 font-bold">${weight}%</td>
                <td class="p-2 text-center">
                    <button onclick="forceClosePosition('${code}', ${s.qty}, '${name}')" class="px-2 py-0.5 bg-rose-900/80 hover:bg-rose-800 text-rose-200 border border-rose-600 rounded text-[11px] font-semibold transition inline-flex items-center space-x-1 shadow" title="${name} ${s.qty}주 시장가 전량 강제청산">
                        <i class="fa-solid fa-bolt text-amber-300"></i>
                        <span>시장가 청산</span>
                    </button>
                </td>
            </tr>
        `;
    }).join("");
}

function toggleSelectAllHeldStocks(checked) {
    const checkboxes = document.querySelectorAll(".held-stock-checkbox");
    checkboxes.forEach(cb => cb.checked = checked);
}

async function forceClosePosition(code, qty, name) {
    if (!confirm(`[⚡ 시장가 강제청산 경고]\n\n종목: ${name} (${code})\n수량: ${qty}주\n\n해당 보유 주식을 키움증권 시장가(kt10003 매도)로 전량 강제청산 주문을 전송하시겠습니까?`)) {
        return;
    }

    try {
        const res = await fetch("/api/kiwoom/force-close", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                stock_code: code,
                qty: qty,
                stock_name: name
            })
        });
        const data = await res.json();
        if (data.success) {
            alert(`[⚡ 시장가 강제청산 완료]\n\n${data.message}`);
            fetchKiwoomPositions();
        } else {
            alert(`[강제청산 실패] ${data.detail || data.message || "오류"}`);
        }
    } catch (err) {
        alert("강제청산 주문 통신 예외 발생: " + err);
    }
}

async function forceCloseSelectedHeldPositions() {
    const checkedCbs = Array.from(document.querySelectorAll(".held-stock-checkbox:checked"));
    if (checkedCbs.length === 0) {
        alert("시장가 강제청산할 보유 종목을 선택해 주세요.");
        return;
    }

    const items = checkedCbs.map(cb => ({
        code: cb.dataset.code,
        name: cb.dataset.name,
        qty: parseInt(cb.dataset.qty) || 0
    }));

    const stockSummary = items.map(i => `- ${i.name} (${i.code}): ${i.qty}주`).join("\n");
    if (!confirm(`[⚡ 선택 종목 시장가 강제청산 경고]\n\n다음 선택한 ${items.length}개 보유 종목을 키움증권 시장가(kt10003 매도)로 전량 강제청산하시겠습니까?\n\n${stockSummary}`)) {
        return;
    }

    let successCount = 0;
    for (const item of items) {
        try {
            const res = await fetch("/api/kiwoom/force-close", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    stock_code: item.code,
                    qty: item.qty,
                    stock_name: item.name
                })
            });
            const data = await res.json();
            if (data.success) successCount++;
        } catch (err) {
            console.error(`[forceCloseSelectedHeldPositions] ${item.code} 청산 오류:`, err);
        }
    }

    alert(`[⚡ 선택 종목 강제청산 완료]\n총 ${items.length}개 중 ${successCount}개 종목의 시장가 매도 주문이 접수되었습니다.`);
    fetchKiwoomPositions();
}

async function openKiwoomCredModal() {
    const modal = document.getElementById("kiwoom-cred-modal");
    if (modal) modal.classList.remove("hidden");

    try {
        const res = await fetch("/api/kiwoom/credentials");
        const data = await res.json();
        if (data.success) {
            const accInput = document.getElementById("cred-account-no");
            const mockSelect = document.getElementById("cred-is-mock");
            const keyInput = document.getElementById("cred-app-key");
            const secretInput = document.getElementById("cred-app-secret");

            if (accInput) accInput.value = data.account_no || "";
            if (mockSelect) mockSelect.value = data.is_mock ? "true" : "false";
            if (keyInput) keyInput.value = data.app_key || "";
            if (secretInput) secretInput.value = data.app_secret || "";
        }
    } catch (err) {
        console.warn("[openKiwoomCredModal] 기존 설정 조회 예외:", err);
    }
}

function closeKiwoomCredModal() {
    const modal = document.getElementById("kiwoom-cred-modal");
    if (modal) modal.classList.add("hidden");
}

async function saveKiwoomCredentials() {
    const appKey = document.getElementById("cred-app-key")?.value.trim();
    const appSecret = document.getElementById("cred-app-secret")?.value.trim();
    const accountNo = document.getElementById("cred-account-no")?.value.trim() || "";
    const isMock = document.getElementById("cred-is-mock")?.value === "true";

    if (!appKey) {
        showToast("키움 Open API App Key를 입력해 주세요.", "warning");
        return;
    }

    try {
        const res = await fetch("/api/kiwoom/credentials", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                app_key: appKey,
                app_secret: appSecret,
                account_no: accountNo,
                is_mock: isMock
            })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            closeKiwoomCredModal();
            let msg = data.message || "키움 Open API 인증키가 성공적으로 업데이트되었습니다.";
            if (data.balance && data.balance.message) {
                msg += `\n[키움 잔고 응답]: ${data.balance.message}`;
            }
            showToast(msg, "success");
            fetchKiwoomPositions();
        } else {
            showToast("키움 API 인증키 저장 실패: " + (data.detail || data.message || "오류"), "error");
        }
    } catch (err) {
        showToast("키움 API 인증키 저장 통신 예외: " + err, "error");
    }
}


async function fetchKiwoomPositions() {
    try {
        const res = await fetch("/api/kiwoom/positions");
        if (res.status === 401) {
            showToast("대시보드 HTTP Basic Auth 인증이 필요합니다. (401 Unauthorized)", "error");
            return;
        }
        const data = await res.json();

        if (data.success === false && data.source === "CREDENTIALS_REQUIRED") {
            showToast("키움 Open API 인증키(App Key / App Secret) 설정이 필요합니다.", "warning");
            openKiwoomCredModal();
            return;
        }

        if (data.success) {
            const accBadge = document.getElementById("held-account-badge");
            if (accBadge && data.account_no) {
                accBadge.innerText = `계좌: ${data.account_no}`;
            }

            const posList = (data.positions || []).map(p => {
                const gridStock = (typeof currentStocksData !== 'undefined' && currentStocksData) ? currentStocksData.find(s => s.code === p.stock_code) : null;
                return {
                    code: p.stock_code,
                    name: p.stock_name,
                    qty: p.qty,
                    avg_price: p.avg_price,
                    current_price: p.current_price,
                    pnl: p.pnl,
                    pnl_rate: p.pnl_rate,
                    current_step: p.current_step || (gridStock ? gridStock.current_step : 0)
                };
            });
            renderHeldStocksPanel(posList);
            const sourceText = data.source === "KIWOOM_REAL_SERVER" ? "키움증권 실서버 연동" : (data.source === "MOCK_SYNC" ? "키움 모의투자 연동" : "매매 엔진 연동");

            if (posList.length > 0) {
                showToast(`[${sourceText}] 계좌번호: ${data.account_no || '미지정'}\n보유 주식 잔고 조회가 완료되었습니다! (총 ${posList.length}개 종목)`, "success");
            } else {
                showToast(`[${sourceText}] 계좌번호: ${data.account_no || '미지정'}\n보유 주식 0개 조회됨.\n수신 메시지: ${data.message || '조회 성공 (보유 종목 없음)'}`, "info");
            }
        } else {
            showToast("키움증권 보유 주식 정보 조회 안내: " + (data.message || "알 수 없는 오류"), "error");
        }
    } catch (err) {
        showToast("키움증권 잔고 조회 중 통신 오류가 발생했습니다: " + err, "error");
    }
}



async function setTradingMode(mode) {
    // 키움 실시간 연동 고정
    return;
}
