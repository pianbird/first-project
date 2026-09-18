"""
MagicTrader - Storage Manager & Atomic File Persistence (storage.py)
"""
import os
import json
import sqlite3
import time
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import sys
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import get_kst_now_str


def atomic_write_json(file_path: str, data: Dict[str, Any]) -> None:
    """
    Atomic File Write implementation.
    Writes JSON data to a temporary file in the same directory, flushes, fsyncs,
    and atomically replaces the target file via os.replace() to prevent 0-byte corruption.
    Includes Windows file lock retry fallback.
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_fd, temp_path = tempfile.mkstemp(dir=str(path.parent), prefix="tmp_state_", suffix=".json")
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        for attempt in range(5):
            try:
                os.replace(temp_path, str(path))
                break
            except (PermissionError, OSError):
                time.sleep(0.05)
                if attempt == 4:
                    try:
                        shutil.move(temp_path, str(path))
                    except Exception:
                        pass
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise IOError(f"Atomic write failed for {file_path}: {e}") from e


def load_json(file_path: str) -> Dict[str, Any]:
    """Safely load JSON state file"""
    path = Path(file_path)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Storage Warning] Load JSON exception ({file_path}): {e}")
        return {}


class Storage:
    """
    JSON 및 DB 기반 그리드 매매 상태 및 미체결 주문 복구 영속성 관리자 (Storage Class with Static Methods)
    - 프로세스 재시작 시 미체결 주문, 포지션, 그리드 레벨 상태 복구
    - 동시성 이슈 및 파일 손상(0-byte crash) 방지를 위한 원자적 파일 쓰기(Atomic Write) 지원
    - 타입 힌팅 및 철저한 예외 처리 포함 (Production-Ready)
    """

    @staticmethod
    def save_state(file_path: str, state_data: Dict[str, Any]) -> bool:
        """
        그리드 매매 상태, 활성 주문, 포지션 정보 등을 JSON 파일로 원자적 저장 (Atomic Write)
        :param file_path: 저장 대상 JSON 파일 경로
        :param state_data: 저장할 직렬화 가능 딕셔너리 데이터
        :return: 저장 성공 여부 (bool)
        """
        if not file_path:
            return False
        try:
            atomic_write_json(file_path, state_data)
            return True
        except Exception as e:
            print(f"🚨 [Storage.save_state Error] 상태 파일 저장 실패 ({file_path}): {e}")
            return False

    @staticmethod
    def load_state(file_path: str) -> Dict[str, Any]:
        """
        복구용 JSON 상태 파일 안전하게 로드
        :param file_path: 로드할 JSON 파일 경로
        :return: 상태 딕셔너리 (파일 미존재 또는 로드 실패 시 빈 딕셔너리 반환)
        """
        if not file_path:
            return {}
        try:
            return load_json(file_path)
        except Exception as e:
            print(f"🚨 [Storage.load_state Error] 상태 파일 로드 실패 ({file_path}): {e}")
            return {}

    @staticmethod
    def save_grid_state(
        file_path: str,
        stock_code: str,
        levels: List[Dict[str, Any]],
        active_orders: Optional[List[Dict[str, Any]]] = None,
        positions: Optional[List[Dict[str, Any]]] = None,
        is_reconciled: bool = True
    ) -> bool:
        """
        종목 코드, 그리드 레벨 목록, 미체결 주문 목록, 계좌 포지션을 번들링하여 JSON 파일에 저장
        """
        now_str = get_kst_now_str()
        payload = {
            "stock_code": str(stock_code).zfill(6),
            "updated_at": now_str,
            "is_reconciled": is_reconciled,
            "levels": levels or [],
            "active_orders": active_orders or [],
            "positions": positions or []
        }
        return Storage.save_state(file_path, payload)

    @staticmethod
    def load_grid_state(file_path: str) -> Dict[str, Any]:
        """
        프로세스 재시작 시 그리드 상태 및 미체결 주문 복구를 위한 JSON 파일 로드 static 메서드
        """
        return Storage.load_state(file_path)


class SQLiteStorage:
    """
    SQLite3 persistence manager with WAL journal mode, thread safety, and busy timeout.
    Manages bot state (bot_state), active orders (active_orders), grid level states,
    order history, and account reconciliation history.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=30000;")
            conn.execute("PRAGMA read_uncommitted=1;")
            with conn:
                yield conn
        except Exception as e:
            print(f"[SQLiteStorage PRAGMA Warning] {e}")
            yield conn
        finally:
            conn.close()

    def check_integrity(self) -> bool:
        """SQLite PRAGMA integrity_check 실행하여 DB 테이블 손상 여부 무결성 검증"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA integrity_check;")
                res = cursor.fetchone()
                is_ok = bool(res and str(res[0]).lower() == "ok")
                if not is_ok:
                    print(f"🚨 [SQLiteStorage Warning] integrity_check 결과 이상: {res}")
                return is_ok
        except Exception as e:
            print(f"🚨 [SQLiteStorage Error] DB integrity check 예외: {e}")
            return False

    def close(self):
        """Close DB connections / clear WAL if needed"""
        pass

    def _execute_with_retry(self, func, max_retries: int = 5, delay: float = 0.2):
        """Execute SQLite operation with thread lock and retry backoff for lock contention"""
        last_err = None
        with self._lock:
            for attempt in range(max_retries):
                try:
                    return func()
                except sqlite3.OperationalError as e:
                    last_err = e
                    if "locked" in str(e).lower() or "busy" in str(e).lower():
                        time.sleep(delay * (1.5 ** attempt))
                    else:
                        raise e
                except Exception as e:
                    raise e
            raise last_err

    def _init_db(self):
        if not self.check_integrity():
            print("⚠️ [SQLiteStorage] DB 무결성 검사 실패 또는 신규 DB 생성 중...")

        def _create():
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 1. Grid Levels State Machine Table
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS grid_levels (
                    level_id INTEGER PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    buy_price INTEGER NOT NULL,
                    sell_price INTEGER NOT NULL,
                    qty INTEGER NOT NULL,
                    filled_qty INTEGER DEFAULT 0,
                    remaining_qty INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    buy_order_id TEXT DEFAULT '',
                    sell_order_id TEXT DEFAULT '',
                    last_order_time TEXT DEFAULT '',
                    updated_at TEXT NOT NULL
                )
                """)

                cursor.execute("PRAGMA table_info(grid_levels)")
                cols = [r["name"] for r in cursor.fetchall()]
                if "filled_qty" not in cols:
                    cursor.execute("ALTER TABLE grid_levels ADD COLUMN filled_qty INTEGER DEFAULT 0")
                if "remaining_qty" not in cols:
                    cursor.execute("ALTER TABLE grid_levels ADD COLUMN remaining_qty INTEGER DEFAULT 0")

                # 2. Order History Table
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS order_history (
                    order_id TEXT PRIMARY KEY,
                    client_order_key TEXT DEFAULT '',
                    stock_code TEXT NOT NULL,
                    side TEXT NOT NULL,
                    requested_qty INTEGER NOT NULL,
                    filled_qty INTEGER DEFAULT 0,
                    remaining_qty INTEGER NOT NULL,
                    before_qty INTEGER DEFAULT 0,
                    price INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

                cursor.execute("PRAGMA table_info(order_history)")
                ord_cols = [r["name"] for r in cursor.fetchall()]
                if "before_qty" not in ord_cols:
                    cursor.execute("ALTER TABLE order_history ADD COLUMN before_qty INTEGER DEFAULT 0")

                # 3. Idempotency Keys Table
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    client_order_key TEXT PRIMARY KEY,
                    stock_code TEXT DEFAULT '',
                    side TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """)

                # 4. System Config / Status Table
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

                # 5. Key-Value Bot State Persistence Table
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

                # 6. Active Orders Tracking Table
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS active_orders (
                    order_id TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price INTEGER NOT NULL,
                    qty INTEGER NOT NULL,
                    level_id INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'OPEN',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

                conn.commit()

        self._execute_with_retry(_create)

    def save_state(self, key_or_data: Any, value: Any = None) -> bool:
        """
        봇 키-값 상태 저장 (SQLite bot_state 테이블 및 dict 오버로드 지원)
        """
        now_str = get_kst_now_str()
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if isinstance(key_or_data, dict):
                    for k, v in key_or_data.items():
                        v_json = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
                        cursor.execute("""
                        INSERT INTO bot_state (key, value_json, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                        """, (str(k), v_json, now_str))
                else:
                    k = str(key_or_data)
                    v_json = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else str(value)
                    cursor.execute("""
                    INSERT INTO bot_state (key, value_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                    """, (k, v_json, now_str))
                return True
        return self._execute_with_retry(_op)

    def load_state(self, key: Optional[str] = None, default: Any = None) -> Any:
        """
        봇 상태 로드 (특정 key 지정 시 해당 값 반환, 미지정 시 전체 딕셔너리 반환)
        """
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if key is not None:
                    cursor.execute("SELECT value_json FROM bot_state WHERE key = ?", (str(key),))
                    row = cursor.fetchone()
                    if row and row["value_json"]:
                        try:
                            return json.loads(row["value_json"])
                        except Exception:
                            return row["value_json"]
                    return default
                else:
                    cursor.execute("SELECT key, value_json FROM bot_state")
                    rows = cursor.fetchall()
                    res = {}
                    for r in rows:
                        try:
                            res[r["key"]] = json.loads(r["value_json"])
                        except Exception:
                            res[r["key"]] = r["value_json"]
                    return res
        return self._execute_with_retry(_op)

    def add_order(self, order_data: Dict[str, Any]) -> bool:
        """
        활성 미체결 주문 추가 (active_orders 및 order_history 내역에 기록)
        """
        now_str = get_kst_now_str()
        order_id = str(order_data.get("order_id", ""))
        if not order_id:
            return False
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO active_orders (order_id, stock_code, side, price, qty, level_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    stock_code = excluded.stock_code,
                    side = excluded.side,
                    price = excluded.price,
                    qty = excluded.qty,
                    level_id = excluded.level_id,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """, (
                    order_id,
                    str(order_data.get("stock_code", "")).zfill(6),
                    str(order_data.get("side", "")).upper(),
                    int(order_data.get("price", 0)),
                    int(order_data.get("qty", order_data.get("requested_qty", 0))),
                    int(order_data.get("level_id", 0)),
                    str(order_data.get("status", "OPEN")),
                    str(order_data.get("created_at", now_str)),
                    now_str
                ))
                return True
        self.save_order(order_data)
        return self._execute_with_retry(_op)

    def remove_order(self, order_id: str) -> bool:
        """
        체결/취소 처리된 주문을 active_orders 테이블에서 제거
        """
        if not order_id:
            return False
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM active_orders WHERE order_id = ?", (str(order_id),))
                return True
        return self._execute_with_retry(_op)

    def get_active_orders(self, stock_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        현재 활성화된 미체결/진행 중인 주문 내역 조회
        """
        clean_code = str(stock_code).strip().zfill(6) if stock_code else ""
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if clean_code:
                    cursor.execute("SELECT * FROM active_orders WHERE stock_code = ? OR stock_code = ? ORDER BY updated_at DESC", (clean_code, clean_code.lstrip("0")))
                else:
                    cursor.execute("SELECT * FROM active_orders ORDER BY updated_at DESC")
                rows = cursor.fetchall()
                results = [dict(r) for r in rows]
                if not results:
                    return self.get_active_or_unknown_orders(stock_code=stock_code)
                return results
        return self._execute_with_retry(_op)

    def register_idempotency_key(self, client_key: str, stock_code: str = "", side: str = "") -> bool:
        """Idempotency Key DB 등록 (이미 존재 시 False 반환으로 중복 차단)"""
        if not client_key:
            return True
        now_str = get_kst_now_str()
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                    INSERT INTO idempotency_keys (client_order_key, stock_code, side, created_at)
                    VALUES (?, ?, ?, ?)
                    """, (client_key, stock_code, side, now_str))
                    return True
                except sqlite3.IntegrityError:
                    return False
        return self._execute_with_retry(_op)

    def save_grid_level(self, level_id: int, stock_code: str, buy_price: int, sell_price: int, qty: int, status: str, buy_order_id: str = "", sell_order_id: str = "", last_order_time: str = "", filled_qty: int = 0, remaining_qty: int = 0):
        """Save or update grid level state with filled_qty and remaining_qty"""
        now_str = get_kst_now_str()
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO grid_levels (level_id, stock_code, buy_price, sell_price, qty, filled_qty, remaining_qty, status, buy_order_id, sell_order_id, last_order_time, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(level_id) DO UPDATE SET
                    stock_code = excluded.stock_code,
                    buy_price = excluded.buy_price,
                    sell_price = excluded.sell_price,
                    qty = excluded.qty,
                    filled_qty = excluded.filled_qty,
                    remaining_qty = excluded.remaining_qty,
                    status = excluded.status,
                    buy_order_id = excluded.buy_order_id,
                    sell_order_id = excluded.sell_order_id,
                    last_order_time = excluded.last_order_time,
                    updated_at = excluded.updated_at
                """, (level_id, stock_code, buy_price, sell_price, qty, filled_qty, remaining_qty, status, buy_order_id, sell_order_id, last_order_time, now_str))
        self._execute_with_retry(_op)

    def get_grid_levels(self) -> List[Dict[str, Any]]:
        """Retrieve all grid level states"""
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM grid_levels ORDER BY level_id ASC")
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
        return self._execute_with_retry(_op)

    def save_order(self, order_info: Dict[str, Any]):
        """Save or update order lifecycle info with before_qty snapshot"""
        now_str = get_kst_now_str()
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO order_history (order_id, client_order_key, stock_code, side, requested_qty, filled_qty, remaining_qty, before_qty, price, status, message, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    status = excluded.status,
                    filled_qty = excluded.filled_qty,
                    remaining_qty = excluded.remaining_qty,
                    message = excluded.message,
                    updated_at = excluded.updated_at
                """, (
                    order_info.get("order_id", ""),
                    order_info.get("client_order_key", ""),
                    order_info.get("stock_code", ""),
                    order_info.get("side", ""),
                    order_info.get("requested_qty", 0),
                    order_info.get("filled_qty", 0),
                    order_info.get("remaining_qty", order_info.get("requested_qty", 0)),
                    order_info.get("before_qty", 0),
                    order_info.get("price", 0),
                    order_info.get("status", "IDLE"),
                    order_info.get("message", ""),
                    order_info.get("created_at", now_str),
                    now_str
                ))
        self._execute_with_retry(_op)

    def get_pending_orders(self) -> List[Dict[str, Any]]:
        """Get open/pending/unknown orders"""
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM order_history WHERE status IN ('PENDING_SUBMIT', 'ORDER_SUBMITTED', 'ORDER_SENT', 'ORDER_PENDING', 'ORDER_PLACED', 'PENDING_FILL', 'OPEN', 'PARTIALLY_FILLED', 'SUBMITTED', 'ACCEPTED', 'UNKNOWN', 'UNKNOWN_PENDING')")
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
        return self._execute_with_retry(_op)

    def get_active_or_unknown_orders(self, stock_code: Optional[str] = None, side: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        [3-1d] SQL WHERE로 종목/방향을 필터링하고 활성/미체결/UNKNOWN 상태 주문 내역 반환
        - 상태: ('PENDING_SUBMIT','PENDING','SUBMITTED','ACCEPTED','OPEN','PARTIALLY_FILLED','UNKNOWN','UNKNOWN_PENDING')
        """
        clean_code = str(stock_code).strip().zfill(6) if stock_code else ""
        side_upper = str(side).strip().upper() if side else ""

        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                query = "SELECT * FROM order_history WHERE status IN ('PENDING_SUBMIT', 'PENDING', 'SUBMITTED', 'ACCEPTED', 'OPEN', 'PARTIALLY_FILLED', 'UNKNOWN', 'UNKNOWN_PENDING')"
                params: List[Any] = []

                if clean_code:
                    query += " AND (stock_code = ? OR stock_code = ?)"
                    params.extend([clean_code, clean_code.lstrip("0")])

                if side_upper:
                    query += " AND UPPER(side) = ?"
                    params.append(side_upper)

                query += " ORDER BY updated_at DESC"
                cursor.execute(query, params)
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
        return self._execute_with_retry(_op)

    def get_unknown_orders(self) -> List[Dict[str, Any]]:
        """Get UNKNOWN and UNKNOWN_PENDING status orders requiring reconciliation"""
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM order_history WHERE status IN ('UNKNOWN', 'UNKNOWN_PENDING')")
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
        return self._execute_with_retry(_op)

    def get_recent_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent order history for OrderManager reconciliation and duplicate checks"""
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM order_history ORDER BY updated_at DESC LIMIT ?", (limit,))
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
        return self._execute_with_retry(_op)

    def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Query single order by order_id"""
        if not order_id:
            return None
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM order_history WHERE order_id = ?", (order_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        return self._execute_with_retry(_op)

    def get_daily_trade_count(self) -> int:
        """오늘 실행된 체결/주문 건수 반환"""
        today_prefix = get_kst_now_str()[:10]
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM order_history WHERE created_at LIKE ?", (f"{today_prefix}%",))
                row = cursor.fetchone()
                return row[0] if row else 0
        return self._execute_with_retry(_op)

    def update_grid_level_status(self, level_id: int, status: str, buy_order_id: str = "", sell_order_id: str = "", filled_qty: int = 0, remaining_qty: int = 0):
        """원장 동기화 및 대사 결과에 따른 그리드 레벨 상태 단일 업데이트 쿼리"""
        now_str = get_kst_now_str()
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                UPDATE grid_levels
                SET status = ?, buy_order_id = ?, sell_order_id = ?, filled_qty = ?, remaining_qty = ?, updated_at = ?
                WHERE level_id = ?
                """, (status, buy_order_id, sell_order_id, filled_qty, remaining_qty, now_str, level_id))
        self._execute_with_retry(_op)

    def update_order_status(self, order_id: str, status: str, filled_qty: Optional[int] = None, remaining_qty: Optional[int] = None, message: str = ""):
        """주문 원장 내역 상태 개별 업데이트 쿼리"""
        if not order_id:
            return
        now_str = get_kst_now_str()
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if filled_qty is not None and remaining_qty is not None:
                    cursor.execute("""
                    UPDATE order_history
                    SET status = ?, filled_qty = ?, remaining_qty = ?, message = COALESCE(NULLIF(?, ''), message), updated_at = ?
                    WHERE order_id = ?
                    """, (status, filled_qty, remaining_qty, message, now_str, order_id))
                else:
                    cursor.execute("""
                    UPDATE order_history
                    SET status = ?, message = COALESCE(NULLIF(?, ''), message), updated_at = ?
                    WHERE order_id = ?
                    """, (status, message, now_str, order_id))
        self._execute_with_retry(_op)

    def bulk_update_grid_levels(self, levels_data: List[Dict[str, Any]]):
        """원장 대사 시 전 그리드 레벨 단일 트랜잭션 (Atomic Batch) 업데이트"""
        now_str = get_kst_now_str()
        def _op():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                for item in levels_data:
                    cursor.execute("""
                    UPDATE grid_levels
                    SET status = ?, buy_order_id = ?, sell_order_id = ?, filled_qty = ?, remaining_qty = ?, updated_at = ?
                    WHERE level_id = ?
                    """, (
                        item.get("status", "IDLE"),
                        item.get("buy_order_id", ""),
                        item.get("sell_order_id", ""),
                        item.get("filled_qty", 0),
                        item.get("remaining_qty", 0),
                        now_str,
                        item.get("level_id")
                    ))
        self._execute_with_retry(_op)
