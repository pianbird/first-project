import json
import sqlite3
import time
from typing import Dict, Any, List, Optional, Tuple
from web_app.backend.repositories.db_core import DBCore

class SystemRepository:
    """
    system_config, backups, web_trade_logs, engine_heartbeat, idempotency_keys, daily_trades 테이블 전용 Repository
    """

    def __init__(self, db_core: DBCore):
        self.db_core = db_core

    def register_idempotency_key(self, client_key: str, stock_code: str = "", side: str = "") -> bool:
        """Idempotency Key DB 등록 (이미 존재 시 False 반환으로 중복 차단)"""
        if not client_key:
            return True
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        def _op():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                    INSERT INTO idempotency_keys (client_order_key, stock_code, side, created_at)
                    VALUES (?, ?, ?, ?)
                    """, (client_key, stock_code, side, now_str))
                    return True
                except sqlite3.IntegrityError:
                    return False
        return self.db_core._execute_with_retry(_op)

    def get_kst_today_str(self) -> str:
        try:
            from zoneinfo import ZoneInfo
            from datetime import datetime
            return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
        except Exception:
            return time.strftime("%Y-%m-%d")

    def get_daily_trade_count(self) -> int:
        """KST 오늘 일자 기준 누적 매매 횟수 반환"""
        today_str = self.get_kst_today_str()
        def _op():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT trade_count FROM daily_trades WHERE trade_date = ?", (today_str,))
                row = cursor.fetchone()
                return int(row["trade_count"]) if row else 0
        return self.db_core._execute_with_retry(_op)

    def increment_daily_trade_count(self) -> int:
        """KST 오늘 일자 기준 매매 횟수 1 증가 및 갱신된 값 반환"""
        today_str = self.get_kst_today_str()
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        def _op():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO daily_trades (trade_date, trade_count, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    trade_count = trade_count + 1,
                    updated_at = excluded.updated_at
                """, (today_str, now_str))
                cursor.execute("SELECT trade_count FROM daily_trades WHERE trade_date = ?", (today_str,))
                row = cursor.fetchone()
                return int(row["trade_count"]) if row else 1
        return self.db_core._execute_with_retry(_op)

    def check_and_increment_daily_trade_limit(self, max_limit: int) -> Tuple[bool, int, str]:
        """
        P0-5: KST 오늘 일자 기준 매매 한도 원자적(Atomic) 검사 및 선증가 reservation
        SQLite 트랜잭션 내에서 count 조회 및 limit 비교, 증가를 원자적으로 수행
        Returns: (allowed: bool, current_count: int, message: str)
        """
        if max_limit <= 0:
            new_c = self.increment_daily_trade_count()
            return True, new_c, "OK"

        today_str = self.get_kst_today_str()
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        def _op():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                cursor.execute("SELECT trade_count FROM daily_trades WHERE trade_date = ?", (today_str,))
                row = cursor.fetchone()
                curr = int(row["trade_count"]) if row else 0

                if curr >= max_limit:
                    return False, curr, f"🛑 [일일 매매 횟수 초과] 오늘 매매 횟수({curr}회)가 일일 한도({max_limit}회)에 도달하여 주문이 원자적으로 차단되었습니다."

                cursor.execute("""
                INSERT INTO daily_trades (trade_date, trade_count, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    trade_count = trade_count + 1,
                    updated_at = excluded.updated_at
                """, (today_str, now_str))
                cursor.execute("SELECT trade_count FROM daily_trades WHERE trade_date = ?", (today_str,))
                row2 = cursor.fetchone()
                new_c = int(row2["trade_count"]) if row2 else curr + 1
                return True, new_c, "OK"

        return self.db_core._execute_with_retry(_op)

    def save_system_config(self, key: str, value_dict: Dict[str, Any]):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        val_json = json.dumps(value_dict, ensure_ascii=False)

        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT OR REPLACE INTO system_config (key, value_json, updated_at)
                VALUES (?, ?, ?)
                """, (key, val_json, now_str))
                conn.commit()

        self.db_core._execute_with_retry(_action)

    def get_system_config(self, key: str) -> Optional[Dict[str, Any]]:
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value_json FROM system_config WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    return json.loads(row["value_json"])
                return None

        return self.db_core._execute_with_retry(_action)

    def update_heartbeat(self, status: str, trading_mode: str, safe_stop_active: bool = False, safe_stop_reason: str = ""):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        safe_stop_int = 1 if safe_stop_active else 0

        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT OR REPLACE INTO engine_heartbeat (id, status, trading_mode, safe_stop_active, safe_stop_reason, last_heartbeat)
                VALUES (1, ?, ?, ?, ?, ?)
                """, (status, trading_mode, safe_stop_int, safe_stop_reason, now_str))
                conn.commit()

        self.db_core._execute_with_retry(_action)

    def get_heartbeat(self) -> Dict[str, Any]:
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM engine_heartbeat WHERE id = 1")
                row = cursor.fetchone()
                if row:
                    res = dict(row)
                    res["safe_stop_active"] = bool(res.get("safe_stop_active", 0))
                    return res
                return {
                    "status": "STOPPED",
                    "trading_mode": "MOCK",
                    "safe_stop_active": False,
                    "safe_stop_reason": "",
                    "last_heartbeat": "N/A"
                }

        return self.db_core._execute_with_retry(_action)

    def create_backup(self, backup_name: str, backup_dict: Dict[str, Any]):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        data_json = json.dumps(backup_dict, ensure_ascii=False)

        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO backups (backup_name, backup_data, created_at)
                VALUES (?, ?, ?)
                """, (backup_name, data_json, now_str))
                conn.commit()

        self.db_core._execute_with_retry(_action)

    def get_latest_backup(self) -> Optional[Dict[str, Any]]:
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT backup_data FROM backups ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                if row:
                    return json.loads(row["backup_data"])
                return None

        return self.db_core._execute_with_retry(_action)

    def add_log(self, level: str, message: str):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO web_trade_logs (timestamp, level, message)
                VALUES (?, ?, ?)
                """, (now_str, level, message))
                conn.commit()

        self.db_core._execute_with_retry(_action)

    def get_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM web_trade_logs ORDER BY id DESC LIMIT ?", (limit,))
                rows = cursor.fetchall()
                return [{"id": r["id"], "timestamp": r["timestamp"], "level": r["level"], "message": r["message"]} for r in rows]

        return self.db_core._execute_with_retry(_action)

    def reset_system_config(self):
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM system_config")
                conn.commit()

        self.db_core._execute_with_retry(_action)

    def reset_web_trade_logs(self):
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM web_trade_logs")
                conn.commit()

        self.db_core._execute_with_retry(_action)

    def reset_engine_heartbeat(self):
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM engine_heartbeat")
                conn.commit()

        self.db_core._execute_with_retry(_action)
