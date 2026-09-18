import os
from typing import Dict, Any, Optional

VALID_TRADING_MODES = {"MOCK", "REAL", "DISABLED"}

MODE_TEXT = {
    "MOCK": "모의투자",
    "REAL": "실전투자",
    "DISABLED": "매매중지"
}

import tempfile
import json
import time

class InvalidTradingModeError(ValueError):
    """트레이딩 모드가 유효하지 않을 때 발생하는 예외"""
    pass

class ModeEndpointMismatchError(RuntimeError):
    """REAL/MOCK 모드와 엔드포인트 URL이 불일치할 때 발생하는 예외"""
    pass

class RealTradingLockError(RuntimeError):
    """동일 계좌로 이중 프로세스가 REAL 모드 동시 기동 시 발생하는 예외"""
    pass

def acquire_real_trading_lock(account_no: str) -> bool:
    """
    동일 계좌번호(account_no)로 이중 프로세스(web_app 백엔드 및 systemd 데몬)가
    동시에 REAL 모드로 기동하는 것을 방지하는 파일 락 획득 함수
    """
    clean_acc = str(account_no or "DEFAULT").strip().replace("-", "")
    lock_file = os.path.join(tempfile.gettempdir(), f"magictrader_real_{clean_acc}.lock")
    current_pid = os.getpid()

    if os.path.exists(lock_file):
        try:
            with open(lock_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing_pid = data.get("pid")

            if existing_pid and existing_pid != current_pid:
                process_alive = False
                try:
                    import psutil
                    process_alive = psutil.pid_exists(existing_pid)
                except ImportError:
                    try:
                        os.kill(existing_pid, 0)
                        process_alive = True
                    except (OSError, OverflowError):
                        process_alive = False

                if process_alive:
                    raise RealTradingLockError(
                        f"CRITICAL LOCK ERROR: 계좌({clean_acc})에 대해 이미 다른 프로세스(PID: {existing_pid})가 REAL 모드로 실행 중입니다! "
                        "이중 실전매매로 인한 포지션 충돌을 방지하기 위해 신규 REAL 모드 기동을 차단합니다."
                    )
        except (json.JSONDecodeError, OSError):
            pass

    try:
        lock_data = {
            "pid": current_pid,
            "account_no": clean_acc,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(lock_file, "w", encoding="utf-8") as f:
            json.dump(lock_data, f)
        return True
    except RealTradingLockError:
        raise
    except Exception as e:
        raise RealTradingLockError(
            f"CRITICAL LOCK ERROR: REAL 모드 락 파일({lock_file}) 작성 실패: {e}. "
            "이중 실전매매 사고를 방지하기 위해 신규 REAL 모드 기동을 차단합니다 (Fail-Closed)."
        )

def release_real_trading_lock(account_no: str):
    clean_acc = str(account_no or "DEFAULT").strip().replace("-", "")
    lock_file = os.path.join(tempfile.gettempdir(), f"magictrader_real_{clean_acc}.lock")
    if os.path.exists(lock_file):
        try:
            with open(lock_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("pid") == os.getpid():
                os.remove(lock_file)
        except Exception:
            pass

class TradingModeManager:
    """
    Trading Mode 중앙 Single Source of Truth 관리 객체
    - MOCK, REAL, DISABLED 세 모드만 허용
    - 각 객체(WebTradingEngine, OrderManager, KiwoomClient)가 동기화되어 동일 상태 공유
    - REAL/MOCK 엔드포인트 수신 강제 점검
    - 주문 직전 최후 안전 Gate 검사
    """

    def __init__(self, initial_mode: str = "MOCK", account_no: str = ""):
        self.account_no = account_no
        self._mode = "MOCK" # 기본값 세팅 후 set_mode로 락 획득 보장
        self.set_mode(initial_mode)

    @staticmethod
    def validate_and_normalize_mode(mode_input: Any) -> str:
        """
        트레이딩 모드 검증 및 정규화
        지원하는 모드: MOCK, REAL, DISABLED
        잘못된 값(TEST, INVALID, None, 빈문자열 등)은 거부
        """
        if not mode_input or not isinstance(mode_input, str):
            raise InvalidTradingModeError(f"유효하지 않은 트레이딩 모드 입력: {mode_input}")

        normalized = mode_input.strip().upper()
        if normalized not in VALID_TRADING_MODES:
            raise InvalidTradingModeError(
                f"지원하지 않는 트레이딩 모드입니다: '{mode_input}'. "
                f"허용된 모드: {sorted(list(VALID_TRADING_MODES))}"
            )

        return normalized

    def set_mode(self, new_mode: str, account_no: str = "") -> str:
        """모드 변경 (검증 포함, 입력값 그대로 정규화 적용)"""
        validated = self.validate_and_normalize_mode(new_mode)
        acc = account_no or self.account_no
        if validated == "REAL" and self._mode != "REAL":
            acquire_real_trading_lock(acc)
        elif self._mode == "REAL" and validated != "REAL":
            release_real_trading_lock(acc)
        self._mode = validated
        return self._mode

    def get_mode(self) -> str:
        return self._mode

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str):
        # [P1-2] .mode = direct 대입 우회를 막기 위해 set_mode()를 직접 호출하도록 통일
        # 이로 인해 mode.setter 이용 시에도 REAL 모드 전환/해제에 대한 lock 및 release가 완벽히 수행됨
        self.set_mode(value)

    def get_mode_text(self) -> str:
        """Dashboard 표시용 한국어 모드 명칭 반환 (NameError 방지)"""
        return MODE_TEXT.get(self._mode, "알 수 없음")

    def validate_endpoint_isolation(self, api_url: str, is_mock_endpoint: bool):
        """
        P0-1: REAL/MOCK/DISABLED 서버 이중 격리 강제 검사
        """
        if self._mode == "DISABLED":
            raise ModeEndpointMismatchError("CRITICAL ERROR: DISABLED 모드에서는 모든 API 주문 호출이 금지됩니다.")

        url_lower = (api_url or "").lower()

        if self._mode == "REAL":
            if is_mock_endpoint or "mock" in url_lower or "simulation" in url_lower:
                raise ModeEndpointMismatchError(
                    f"CRITICAL ERROR: REAL 모드에서 Mock API 엔드포인트({api_url})가 감지되었습니다. "
                    "실전 매매를 즉시 중단합니다."
                )

        if self._mode == "MOCK":
            if not is_mock_endpoint or (("api.kiwoom.com" in url_lower or "openapi.kiwoom.com" in url_lower) and "mock" not in url_lower):
                raise ModeEndpointMismatchError(
                    f"CRITICAL ERROR: MOCK 모드에서 REAL API 엔드포인트({api_url})가 감지되었습니다. "
                    "모의 매매를 즉시 중단합니다."
                )

    def can_place_order(self) -> bool:
        """주문 직전 매매 가능 여부 검사"""
        if self._mode == "DISABLED":
            return False
        return self._mode in ("MOCK", "REAL")
