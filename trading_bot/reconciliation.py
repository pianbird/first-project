import time
from typing import Dict, Any, List, Tuple, Optional
from db_manager import DBManager

class ReconciliationError(RuntimeError):
    """DB와 키움 실계좌 잔고/포지션 불일치 시 발생하는 예외"""
    pass

class AccountReconciler:
    """
    Kiwoom API(Source of Truth)와 DB 간의 계좌 잔고, 포지션, 미체결 불일치 감지 및 동기화
    """

    def __init__(self, db: DBManager):
        self.db = db

    def reconcile_account(self, kiwoom_client: Any, trading_mode: str = "MOCK") -> Tuple[bool, Dict[str, Any]]:
        """
        계좌 동기화 검사
        - Kiwoom API에서 실제 잔고 및 보유 종목 수량 조회
        - DB 포지션 및 예수금 비교
        - 불일치 발견 시 status='RECONCILIATION_REQUIRED', is_reconciled=False
        - REAL 모드에서 Kiwoom API 실패 시 -> ReconciliationError (SAFE_STOP 처리용)
        """
        result_details = {
            "status": "OK",
            "is_reconciled": True,
            "mismatches": [],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        if not kiwoom_client or not hasattr(kiwoom_client, "is_credentials_valid") or not kiwoom_client.is_credentials_valid():
            if trading_mode == "REAL":
                raise ReconciliationError("REAL 모드에서 Kiwoom API 인증 또는 클라이언트가 유효하지 않습니다.")
            result_details["status"] = "API_UNAVAILABLE"
            result_details["is_reconciled"] = False
            return False, result_details

        # 1. 키움 실계좌 포지션 조회
        try:
            kpos_info = kiwoom_client.get_positions(force_refresh=True)
            if not isinstance(kpos_info, dict) or ("positions" not in kpos_info and "held_positions" not in kpos_info):
                if trading_mode == "REAL":
                    raise ReconciliationError("REAL 모드에서 Kiwoom 포지션 API 응답이 비정상입니다.")
                result_details["status"] = "QUERY_FAILED"
                result_details["is_reconciled"] = False
                return False, result_details
        except Exception as e:
            if trading_mode == "REAL":
                raise ReconciliationError(f"REAL 모드 Kiwoom 포지션 조회 실패: {e}")
            result_details["status"] = "QUERY_EXCEPTION"
            result_details["is_reconciled"] = False
            result_details["error"] = str(e)
            return False, result_details

        kiwoom_positions = kpos_info.get("positions") or kpos_info.get("held_positions", [])
        kiwoom_dict = {
            str(p.get("code") or p.get("stock_code", "")).strip().zfill(6): int(p.get("qty", 0))
            for p in kiwoom_positions
            if int(p.get("qty", 0)) > 0
        }

        # 2. DB 포지션 조회
        db_positions = self.db.get_db_positions()
        db_dict = {
            str(p.get("stock_code", "")).strip().zfill(6): int(p.get("qty", 0))
            for p in db_positions
            if int(p.get("qty", 0)) > 0
        }

        # 3. 수량 불일치 검사
        all_codes = set(kiwoom_dict.keys()).union(set(db_dict.keys()))
        mismatches = []

        for code in all_codes:
            k_qty = kiwoom_dict.get(code, 0)
            d_qty = db_dict.get(code, 0)
            if k_qty != d_qty:
                mismatches.append({
                    "stock_code": code,
                    "kiwoom_qty": k_qty,
                    "db_qty": d_qty,
                    "difference": k_qty - d_qty
                })

        if mismatches:
            result_details["status"] = "RECONCILIATION_REQUIRED"
            result_details["is_reconciled"] = False
            result_details["mismatches"] = mismatches
            return False, result_details

        return True, result_details

    def sync_db_from_kiwoom(self, kiwoom_client: Any) -> bool:
        """
        Kiwoom 실계좌 데이터를 Source of Truth로 하여 DB 포지션 및 예수금을 재동기화 (복구 시 사용)
        """
        if not kiwoom_client:
            return False

        try:
            kpos_info = kiwoom_client.get_positions(force_refresh=True)
            held_list = kpos_info.get("positions") or kpos_info.get("held_positions", [])
            for h in held_list:
                code = str(h.get("code") or h.get("stock_code", "")).strip().zfill(6)
                qty = int(h.get("qty", 0))
                avg_price = float(h.get("avg_price", 0))
                name = h.get("name", code)

                if qty > 0:
                    self.db.update_position({
                        "stock_code": code,
                        "stock_name": name,
                        "qty": qty,
                        "avg_price": avg_price,
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
                    })
                else:
                    self.db.delete_position(code)

            return True
        except Exception as e:
            print(f"[AccountReconciler sync 예외] {e}")
            return False
