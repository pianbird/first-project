# FINAL MODIFICATION LOG

## Strategy Changes
- **종목별 하드 손절매(Hard Stop-Loss) 리스크 제어 기능 추가**:
  - 기존 N차 그리드 분할매수 공식, `clear_price` 전량 청산, 차수별 부분 익절 알고리즘은 100% 동일하게 보존됩니다.
  - 최하단 차수(max_steps) 도달 후 주가 추가 하락 시 발생하던 무기한 물타기/포지션 방치 위험을 방지하기 위해, 각 종목별 하드 손절가(`hard_stop_loss_price`) 이탈 시 시장가 전량 매도 및 해당 종목 신규 매수 차단(`stop_loss_halted=True`) 기능이 추가되었습니다.
  - 하드 손절은 타 종목 매매에 영향을 주지 않으며, 사용자가 대시보드에서 명시적으로 수동 재개할 때까지 유지됩니다. 기본값은 `hard_stop_loss_enabled=False`로 하위 호환성을 100% 보장합니다.

---

## [CRITICAL]

### 1. 단일 주문 실행 경로 강제 및 우회 경로 차단
- **문제**: `web_app/backend/engine.py`, `trading_bot/main.py`, `trading_bot/magic_trade_engine.py` 등 여러 파일에서 `kiwoom_client.place_order(...)`를 직접 호출하여 리스크 검증 및 상태 관리 우회 위험 존재.
- **수정 내용**:
  - `OrderManager.submit_order(...)`를 유일한 실제 주문 실행 입구로 단일화.
  - 모든 주문 요청(그리드, 수동, 강제청산)이 `Signal -> Market Validation -> Account Sync -> Pending Check -> Risk Manager -> Trading Mode Gate -> SAFE_STOP Gate -> Idempotency Check -> OrderManager -> Kiwoom REST Client` 16단계 Gate를 차례로 통과하도록 수정.
- **수정 파일**:
  - [order_manager.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/trading_bot/order_manager.py)
  - [engine.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/web_app/backend/engine.py)
  - [main.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/trading_bot/main.py)
  - [magic_trade_engine.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/trading_bot/magic_trade_engine.py)
- **테스트 결과**: PASS (`test_direct_place_order_scan` 통과 - `order_manager.py` 외부 직접 호출 0건)

### 2. Trading Mode Manager 구축 및 모드/엔드포인트 교차 검증
- **문제**: "TEST", "INVALID", 빈 문자열 등 잘못된 입력이 들어오거나 대시보드 표시용 변수로만 취급될 위험.
- **수정 내용**:
  - `TradingModeManager` 분리 구축 (`MOCK`, `REAL`, `DISABLED`만 허용).
  - REAL 모드에서 Mock endpoint 수신 시 또는 MOCK 모드에서 Real endpoint 수신 시 즉시 `ModeEndpointMismatchError` 예외 발생 및 `SAFE_STOP` 전환.
- **수정 파일**:
  - [trading_mode.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/trading_bot/trading_mode.py)
- **테스트 결과**: PASS (`test_trading_mode_validations`, `test_mode_endpoint_isolation` 통과)

### 3. get_full_dashboard_state() NameError (mode_text) 수정
- **문제**: 대시보드 상태 반환 시 `mode_text` 변수가 정의되지 않아 NameError 발생 위험.
- **수정 내용**: `MODE_TEXT` 사전 맵(`{"MOCK": "모의투자", "REAL": "실전투자", "DISABLED": "매매중지"}`)을 정의하고 미정의 모드는 "알 수 없음"으로 세이프 매핑.
- **수정 파일**:
  - [engine.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/web_app/backend/engine.py)
- **테스트 결과**: PASS

### 4. REAL 모드 시세 / 계좌 실패 시 Naver Fallback 및 DB 추정값 사용 금지
- **문제**: REAL 모드에서 키움 시세나 계좌 조회가 실패했을 때 Naver 시세나 기존 DB 잔고를 사용하여 주문을 계속 진행할 경우 오주문 발생 위험.
- **수정 내용**:
  - REAL 모드에서 키움 시세 실패 시 Naver 시세를 주문 판단에 쓰지 않고 즉시 `SAFE_STOP` 발동.
  - REAL 모드에서 계좌 잔고/포지션 조회 실패 시 즉시 `SAFE_STOP` 발동.
- **수정 파일**:
  - [engine.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/web_app/backend/engine.py)
  - [reconciliation.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/trading_bot/reconciliation.py)
- **테스트 결과**: PASS (`test_account_failure_triggers_safe_stop`, `test_quote_failure_in_real_mode_triggers_safe_stop` 통과)

### 5. API Credentials 보안 조치 및 대시보드 비밀번호 검증
- **문제**: `/api/kiwoom/credentials`에서 raw secrets를 반환하거나 대시보드 비밀번호 미설정 시 기본값("admin")으로 구동될 가능성.
- **수정 내용**:
  - `GET /api/kiwoom/credentials` 응답 시 마스킹된 정보만 반환 (`"app_key": "ABCD****WXYZ"`, secret/token 제외).
  - `DASHBOARD_PASSWORD` 환경변수 검사: 미설정 또는 기본값(`admin`, `password`, `1234`, `default`) 사용 시 기동 즉시 `RuntimeError` 발동.
  - GET/POST `/api/kiwoom/credentials`를 포함한 모든 제어 API에 `HTTPBasicAuth` 적용.
- **수정 파일**:
  - [main.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/web_app/backend/main.py)
  - [kiwoom_client.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/trading_bot/kiwoom_client.py)
  - [models.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/web_app/backend/models.py)
- **테스트 결과**: PASS (`test_credentials_masking` 통과)

---

## [HIGH]

### 1. 계좌번호 하드코딩 완전 제거
- **문제**: 소스 코드 곳곳에 특정 계좌번호(`81330538`)가 기본값으로 하드코딩되어 있던 문제.
- **수정 내용**: 모든 파일에서 하드코딩된 계좌번호 제거, `KiwoomRESTClient.normalize_account_no()` 단일 정규화 메서드 사용.
- **수정 파일**: `kiwoom_client.py`, `engine.py`, `main.py`, `models.py`
- **테스트 결과**: PASS

### 2. UNKNOWN 상태 처리 및 POST 주문 Automatic Retry 금지
- **문제**: 주문 POST 통신 타임아웃 발생 시 무조건적인 재시도로 인한 중복 주문 위험 및 UNKNOWN 상태 해제 미비.
- **수정 내용**:
  - `_post_api` / `_request_with_backoff`에서 POST 주문 요청(`is_order=True`) 시 자동 백오프 재시도를 차단하고 `UNKNOWN` 상태로 저장.
  - UNKNOWN 발생 시 자동 재주문 절대 금지. 스냅샷(`before_qty`, `requested_qty`, `current_qty`) 및 잔고/체결 조회를 통해 최종 상태 확정.
- **수정 파일**: `kiwoom_client.py`, `order_manager.py`
- **테스트 결과**: PASS (`test_unknown_order_resolution` 통과)

---

## [MEDIUM]

### 1. KST 한국 거래소 영업일 캘린더 모듈 구축
- **수정 내용**: `market_calendar.py` 모듈 분리 (2025~2026 KRX 공휴일 및 주말, 정규장 09:00~15:30 KST 관리).
- **수정 파일**: [market_calendar.py](file:///c:/Python_Project/1.%20%EB%A7%A4%EC%A7%81%20%ED%8A%B8%EB%A0%88%EC%9D%B4%EB%8D%94%20%EA%B0%9C%EB%B0%9C/trading_bot/market_calendar.py)
- **테스트 결과**: PASS

---

## 2026-09-10 Production Defects Modification Log (P0 ~ P2)

### P0 (즉시 수정, 보안/자금 손실 직결)
1. **P0-1. `/ws/trading` 웹소켓 인증 누락 보완**:
   - `web_app/backend/main.py`의 `websocket_endpoint` 및 `verify_ws_auth` 구현. WebSocket 연결 시 쿼리 파라미터(`password`/`auth`/`token`) 또는 HTTP Basic Header를 추출하여 `DASHBOARD_PASSWORD`와 `secrets.compare_digest`로 일치 검증. 미인증 접속 시 1008 코드로 즉시 거부.
   - `web_app/frontend/app.js`에서 URL Query String에 자격증명을 동적으로 첨부하도록 수정.
2. **P0-2. 주문 실패 사유 소실 문제 수정 (`error` vs `message`)**:
   - `trading_bot/main.py`, `order_manager.py` 등 실패 사유 파싱 지점에서 `err = order_res.get("message") or order_res.get("error") or "주문 실패"` 듀얼 파싱 및 반환 사전 양쪽에 `message`와 `error` 모두 보장하도록 통일.
3. **P0-3. `trading_bot/db_manager.py` `time` 모듈 미import 해결**:
   - `import time` 추가하여 `order_id` 없는 `record_order` 호출 시 `NameError` 방지.

### P1 (안전장치 신뢰성 문제)
1. **P1-1. `acquire_real_trading_lock` 실패 시 Fail-Closed 변경**:
   - `trading_bot/trading_mode.py`에서 락 파일 쓰기 실패 시 예외를 삼키던 `return True` (Fail-Open) 대신 `RealTradingLockError` 발생 (Fail-Closed).
2. **P1-2. `TradingModeManager.mode` setter 락 로직 통일**:
   - `@mode.setter` 내부에서도 `self.set_mode(value)`를 호출하도록 만들어 `.mode = ` 직접 대입 시에도 락 획득/해제가 항상 구동되도록 통일.
3. **P1-3. 이중 리스크 가드 시스템 (`OrderGuard` vs `RiskManager`) 통합**:
   - `trading_bot/main.py`에서도 `RiskManager` 객체를 생성하고 `submit_order(risk_manager=self.risk_manager)`를 전달하여 두 가드가 함께 동작하도록 수정.
4. **P1-4. 미체결/UNKNOWN/중복주문 검사 SQL 직조회 전환 (100건 선형 스캔 제거)**:
   - `web_app/backend/db.py`에 `get_order_by_id`, `get_active_or_unknown_orders`, `get_unknown_orders` 쿼리 메서드 추가 및 `order_manager.py`의 100건 선형 스캔 방식 대체.
5. **P1-5. `resolve_unknown_orders` 동일 종목 다중 UNKNOWN 이중 계산 방지**:
   - 동일 종목의 UNKNOWN 주문 복수 존재 시 시간순 정렬 후 순차 델타 차감 배분 알고리즘 적용.
6. **P1-6. Idempotency Key 수량/가격 포함 및 구조 개편**:
   - `generate_idempotency_key`에 `qty` 및 `price`를 결합 (`strategy_id:symbol:side:qty:price:cycle_id`)하여 수량/가격 변경 정정 주문이 부당 차단되는 현상 방지.

### P2 (경미, 위생 문제)
1. **P2-1. 시세/검색 엔드포인트 보안 및 정책 명시**:
   - `/api/stocks/search`, `/api/stocks/lookup`, `/api/stocks/quote`에 `verify_dashboard_auth` 적용 및 인트라넷 비공개 대시보드 무단 스캐닝 방지 주석 추가.
2. **P2-2. 배포 산출물 및 `TRADING_MODE` 오버라이드 가드**:
   - `.gitignore` 추가 (`__pycache__/`, `*.pyc`, `test_*.db`, `*.lock` 등).
   - `web_app/backend/engine.py` 기동 시 DB `trading_mode`보다 환경변수 `TRADING_MODE` 우선 적용 및 REAL 모드 기동 경고 로그 추가.

---

### 최종 검증 명령 및 실행 결과
- **검증 명령**:
  `python -m pytest tests/test_final_production.py -v`
- **테스트 결과**:
  ```text
  tests/test_final_production.py::test_trading_mode_validations PASSED     [  2%]
  tests/test_final_production.py::test_mode_endpoint_isolation PASSED      [  5%]
  tests/test_final_production.py::test_disabled_mode_blocks_orders PASSED  [  8%]
  tests/test_final_production.py::test_safe_stop_persistence_and_order_blocking PASSED [ 11%]
  tests/test_final_production.py::test_account_failure_triggers_safe_stop PASSED [ 14%]
  tests/test_final_production.py::test_daily_trade_count_db_persistence PASSED [ 17%]
  tests/test_final_production.py::test_idempotency_db_persistence PASSED   [ 20%]
  tests/test_final_production.py::test_unknown_order_resolution_and_no_autoretry PASSED [ 23%]
  tests/test_final_production.py::test_credentials_masking PASSED          [ 26%]
  tests/test_final_production.py::test_direct_place_order_scan PASSED      [ 29%]
  tests/test_final_production.py::test_p0_1_pre_order_endpoint_isolation PASSED [ 32%]
  tests/test_final_production.py::test_p0_2_real_account_query_failure_no_fallback PASSED [ 35%]
  tests/test_final_production.py::test_p0_3_order_token_error_no_auto_resend PASSED [ 38%]
  tests/test_final_production.py::test_p0_5_daily_limit_atomicity PASSED   [ 41%]
  tests/test_final_production.py::test_p1_4_force_close_real_failure PASSED [ 44%]
  tests/test_final_production.py::test_p1_5_legacy_engine_safety PASSED    [ 47%]
  tests/test_final_production.py::test_problem4_kiwoom_positions_auth_required PASSED [ 50%]
  tests/test_final_production.py::test_hard_stop_loss_validation PASSED    [ 52%]
  tests/test_final_production.py::test_hard_stop_loss_trigger_and_isolation PASSED [ 55%]
  tests/test_final_production.py::test_reset_stop_loss_auth_required PASSED [ 58%]
  tests/test_final_production.py::test_get_full_dashboard_state_integrity PASSED [ 61%]
  tests/test_final_production.py::test_critical_risk_manager_limit_enforcement PASSED [ 64%]
  tests/test_final_production.py::test_sensitive_endpoint_auth_required PASSED [ 67%]
  tests/test_final_production.py::test_invalid_credentials_block_order_fallback PASSED [ 70%]
  tests/test_final_production.py::test_real_trading_file_lock_duplication_prevented PASSED [ 73%]
  tests/test_final_production.py::test_runner_market_calendar_integration PASSED [ 76%]
  tests/test_final_production.py::test_runner_real_trading_lock_and_safe_stop PASSED [ 79%]
  tests/test_final_production.py::test_p0_1_ws_auth_enforcement PASSED     [ 82%]
  tests/test_final_production.py::test_p0_2_order_error_message_preservation PASSED [ 85%]
  tests/test_final_production.py::test_p0_3_db_manager_record_order_no_id PASSED [ 88%]
  tests/test_final_production.py::test_p1_1_real_lock_fail_closed PASSED   [ 91%]
  tests/test_final_production.py::test_p1_2_mode_setter_lock_integration PASSED [ 94%]
  tests/test_final_production.py::test_p1_5_multiple_unknown_orders_resolution PASSED [ 97%]
  tests/test_final_production.py::test_p1_6_idempotency_key_with_qty_and_price PASSED [100%]

  ============================= 34 passed in 5.05s ==============================
  ```

---

## 2026-09-10 구조 리팩터링

1. **`legacy/` 패키지 신설 및 6개 모듈 이동**:
   - `trading_bot/main.py`, `magic_trade_engine.py`, `dashboard.py`, `always_on_daemon.py`, `grid_strategy.py`, `order_guard.py` 본문 변경 없이 `legacy/`로 이동. `trading_bot/`에는 호환성 shim 모듈 재export 배치.
2. **`GridEvaluator` / `TradingSignal` 추출**:
   - `web_app/backend/engine.py` 내부의 그리드 판단 알고리즘을 부작용 없는 순수 판단 모듈(`strategy/grid_evaluator.py`)로 추출.
3. **`normalize_price` / `get_tick_size` 이동**:
   - 십원 단위 정규화 및 호가 단위 계산 공식을 `domain.grid_calculator`로 이동하여 중복 연산 단일화.
4. **[동작 변경 — 사용자 승인 필요]**:
   - 기존 `engine.py` L554 `cycle_id=current_step` 은 정의 전 참조로 `UnboundLocalError`를 유발했습니다. 그 결과 추적 종목이 1개이거나 선행 종목이 차수 평가에 도달하지 못한 사이클에서는 `CLEAR_SELL`(전량 청산) 주문이 전송되지 않았습니다. 리팩터링으로 이 결함이 해소되어 이제 정상 전송됩니다. 이는 매도 실행 여부의 실질적 변경입니다.
5. **[동작 변경] `execute_manual_order()` `total_exp` 호이스팅 및 fail-closed 교정**:
   - `execute_manual_order()` 내 `total_exp` 호이스팅으로 `risk_manager=None` 시 발생하던 fail-open을 명시적 가드(`if self.risk_manager is None: return {"success": False, ...}`)로 차단하고, 엔진 기동 시 `SAFE_STOP` 전환을 강제하였습니다.

