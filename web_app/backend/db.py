import os
import json
import sqlite3
import time
from typing import Dict, Any, List, Optional, Tuple

class WebDBManager:
    """
    MagicTrader Web Dashboard 및 Always-on Task 공용 SQLite3 영속성 관리자
    - PRAGMA journal_mode=WAL 및 busy_timeout=30000 적용
    - SQLite OperationalError(database is locked) 대비 retry 래퍼 제공
    - 주문 상태 관리(orders), 포지션 잔고(positions), 하트비트(engine_heartbeat) 영속화
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(base_dir, "web_magictrader.db")

        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=30000;")
            conn.execute("PRAGMA read_uncommitted=1;")
        except Exception:
            pass
        return conn

    def _execute_with_retry(self, func, max_retries: int = 5, delay: float = 0.2):
        """SQLite DB busy/lock 예외 발생 시 지수 백오프 자동 재시도 래퍼"""
        last_err = None
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
        def _create_tables():
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 1. 종목 및 그리드 설정 테이블
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS stock_grids (
                    stock_code TEXT PRIMARY KEY,
                    stock_name TEXT NOT NULL,
                    clear_price INTEGER NOT NULL,
                    memo TEXT DEFAULT '',
                    grid_json TEXT NOT NULL,
                    is_active INTEGER DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """)

                # 2. 시스템 보조 설정 테이블 (engine_status, operating_mode, safe_stop 등)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

                # 3. 데이터 백업 테이블
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    backup_name TEXT NOT NULL,
                    backup_data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """)

                # 4. 체결 및 로그 테이블
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS web_trade_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                )
                """)

                # 5. [신규] 주문 추적 테이블 (Order Lifecycle Management)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT DEFAULT '',
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    requested_qty INTEGER NOT NULL,
                    filled_qty INTEGER DEFAULT 0,
                    remaining_qty INTEGER NOT NULL,
                    price INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT DEFAULT '',
                    requested_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

                # 6. [신규] 계좌 포지션 잔고 동기화 테이블
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    stock_code TEXT PRIMARY KEY,
                    stock_name TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    avg_price INTEGER NOT NULL,
                    current_price INTEGER NOT NULL,
                    purchase_amount INTEGER NOT NULL,
                    valuation_amount INTEGER NOT NULL,
                    pnl INTEGER NOT NULL,
                    pnl_rate REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """)

                # 7. [신규] Always-on Task Heartbeat 상태 테이블
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS engine_heartbeat (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    status TEXT NOT NULL,
                    trading_mode TEXT NOT NULL,
                    safe_stop_active INTEGER DEFAULT 0,
                    safe_stop_reason TEXT DEFAULT '',
                    last_heartbeat TEXT NOT NULL
                )
                """)

                # 8. [신규] Idempotency Key 영속화 테이블 (DB Unique Constraint)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    client_order_key TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    side TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """)

                # 9. [신규] KST 일자별 일일 매매 횟수 DB 영속화 테이블
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_trades (
                    trade_date TEXT PRIMARY KEY,
                    trade_count INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """)

                # 10. 스키마 마이그레이션 호환성 보장
                cursor.execute("PRAGMA table_info(positions)")
                pos_cols = [row[1] for row in cursor.fetchall()]
                if pos_cols:
                    if "purchase_amount" not in pos_cols:
                        cursor.execute("ALTER TABLE positions ADD COLUMN purchase_amount INTEGER DEFAULT 0")
                    if "valuation_amount" not in pos_cols:
                        cursor.execute("ALTER TABLE positions ADD COLUMN valuation_amount INTEGER DEFAULT 0")
                    if "pnl" not in pos_cols:
                        cursor.execute("ALTER TABLE positions ADD COLUMN pnl INTEGER DEFAULT 0")
                    if "pnl_rate" not in pos_cols:
                        cursor.execute("ALTER TABLE positions ADD COLUMN pnl_rate REAL DEFAULT 0.0")

                cursor.execute("PRAGMA table_info(orders)")
                ord_cols = [row[1] for row in cursor.fetchall()]
                if ord_cols:
                    if "client_order_key" not in ord_cols:
                        cursor.execute("ALTER TABLE orders ADD COLUMN client_order_key TEXT DEFAULT ''")
                    if "before_qty" not in ord_cols:
                        cursor.execute("ALTER TABLE orders ADD COLUMN before_qty INTEGER DEFAULT 0")

                conn.commit()

        self._execute_with_retry(_create_tables)

    def register_idempotency_key(self, client_key: str, stock_code: str = "", side: str = "") -> bool:
        """Idempotency Key DB 등록 (이미 존재 시 False 반환으로 중복 차단)"""
        if not client_key:
            return True
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
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
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT trade_count FROM daily_trades WHERE trade_date = ?", (today_str,))
                row = cursor.fetchone()
                return int(row["trade_count"]) if row else 0
        return self._execute_with_retry(_op)

    def increment_daily_trade_count(self) -> int:
        """KST 오늘 일자 기준 매매 횟수 1 증가 및 갱신된 값 반환"""
        today_str = self.get_kst_today_str()
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        def _op():
            with self._get_connection() as conn:
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
        return self._execute_with_retry(_op)

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
            with self._get_connection() as conn:
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

        return self._execute_with_retry(_op)

    def save_stock_grid(self, code: str, name: str, clear_price: int, memo: str, grid_dict: Dict[str, Any], is_active: bool = True):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(grid_dict, dict):
            grid_dict["is_active"] = is_active
        grid_json = json.dumps(grid_dict, ensure_ascii=False)
        is_active_int = 1 if is_active else 0

        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT OR REPLACE INTO stock_grids (stock_code, stock_name, clear_price, memo, grid_json, is_active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (code, name, clear_price, memo, grid_json, is_active_int, now_str))
                conn.commit()

        self._execute_with_retry(_action)

    def get_all_stock_grids(self) -> List[Dict[str, Any]]:
        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM stock_grids")
                rows = cursor.fetchall()
                results = []
                for r in rows:
                    keys = r.keys()
                    is_active_val = True
                    if "is_active" in keys and r["is_active"] is not None:
                        is_active_val = bool(r["is_active"])
                    grid_obj = json.loads(r["grid_json"])
                    if isinstance(grid_obj, dict):
                        grid_obj["is_active"] = is_active_val

                    results.append({
                        "stock_code": r["stock_code"],
                        "stock_name": r["stock_name"],
                        "clear_price": r["clear_price"],
                        "memo": r["memo"],
                        "grid": grid_obj,
                        "is_active": is_active_val,
                        "updated_at": r["updated_at"]
                    })
                return results

        return self._execute_with_retry(_action)

    def delete_stock_grid(self, code: str):
        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM stock_grids WHERE stock_code = ?", (code,))
                conn.commit()

        self._execute_with_retry(_action)

    def save_system_config(self, key: str, value_dict: Dict[str, Any]):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        val_json = json.dumps(value_dict, ensure_ascii=False)

        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT OR REPLACE INTO system_config (key, value_json, updated_at)
                VALUES (?, ?, ?)
                """, (key, val_json, now_str))
                conn.commit()

        self._execute_with_retry(_action)

    def get_system_config(self, key: str) -> Optional[Dict[str, Any]]:
        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value_json FROM system_config WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    return json.loads(row["value_json"])
                return None

        return self._execute_with_retry(_action)

    # -------------------------------------------------------------
    # [신규] 주문 (Orders) 상태 추적 CRUD
    # -------------------------------------------------------------
    def save_order(self, order_info: Dict[str, Any]):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        order_id = str(order_info.get("order_id", ""))
        if not order_id:
            return

        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT OR REPLACE INTO orders (
                    order_id, stock_code, stock_name, side, order_type,
                    requested_qty, filled_qty, remaining_qty, price,
                    status, message, requested_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order_id,
                    str(order_info.get("stock_code", "")).zfill(6),
                    str(order_info.get("stock_name", "")),
                    str(order_info.get("side", "BUY")).upper(),
                    str(order_info.get("order_type", "MARKET")),
                    int(order_info.get("requested_qty", 0)),
                    int(order_info.get("filled_qty", 0)),
                    int(order_info.get("remaining_qty", order_info.get("requested_qty", 0))),
                    int(order_info.get("price", 0)),
                    str(order_info.get("status", "SUBMITTED")),
                    str(order_info.get("message", "")),
                    str(order_info.get("requested_at", now_str)),
                    now_str
                ))
                conn.commit()

        self._execute_with_retry(_action)

    def get_pending_orders(self, stock_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """미체결/진행중 주문(SUBMITTED, ACCEPTED, PARTIALLY_FILLED) 목록 조회"""
        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                pending_statuses = ("PENDING", "SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "CANCEL_REQUESTED")
                placeholders = ",".join(["?"] * len(pending_statuses))

                if stock_code:
                    clean_code = str(stock_code).zfill(6)
                    query = f"SELECT * FROM orders WHERE stock_code = ? AND status IN ({placeholders}) ORDER BY requested_at DESC"
                    cursor.execute(query, [clean_code] + list(pending_statuses))
                else:
                    query = f"SELECT * FROM orders WHERE status IN ({placeholders}) ORDER BY requested_at DESC"
                    cursor.execute(query, list(pending_statuses))

                rows = cursor.fetchall()
                return [dict(r) for r in rows]

        return self._execute_with_retry(_action)

    def get_recent_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM orders ORDER BY updated_at DESC LIMIT ?", (limit,))
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

        return self._execute_with_retry(_action)

    def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        """order_id 직접 쿼리로 주문 조회 (선형 스캔 제거)"""
        if not order_id:
            return None
        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM orders WHERE order_id = ?", (str(order_id),))
                row = cursor.fetchone()
                return dict(row) if row else None
        return self._execute_with_retry(_action)

    def get_active_or_unknown_orders(self, stock_code: Optional[str] = None, side: Optional[str] = None) -> List[Dict[str, Any]]:
        """SQL 기반 미체결 및 UNKNOWN 주문 직접 조회 (PENDING, SUBMITTED, ACCEPTED, PARTIALLY_FILLED, UNKNOWN 등)"""
        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                statuses = ("PENDING", "SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "CANCEL_REQUESTED", "UNKNOWN")
                placeholders = ",".join(["?"] * len(statuses))

                conditions = [f"status IN ({placeholders})"]
                params = list(statuses)

                if stock_code:
                    conditions.append("stock_code = ?")
                    params.append(str(stock_code).strip().zfill(6))
                if side:
                    conditions.append("side = ?")
                    params.append(str(side).strip().upper())

                where_clause = " AND ".join(conditions)
                query = f"SELECT * FROM orders WHERE {where_clause} ORDER BY requested_at ASC"
                cursor.execute(query, params)
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

        return self._execute_with_retry(_action)

    def get_unknown_orders(self, stock_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """SQL 기반 UNKNOWN 상태 주문 시간순 직접 조회"""
        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if stock_code:
                    cursor.execute(
                        "SELECT * FROM orders WHERE status = 'UNKNOWN' AND stock_code = ? ORDER BY requested_at ASC",
                        (str(stock_code).strip().zfill(6),)
                    )
                else:
                    cursor.execute("SELECT * FROM orders WHERE status = 'UNKNOWN' ORDER BY requested_at ASC")
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

        return self._execute_with_retry(_action)

    # -------------------------------------------------------------
    # [신규] 포지션 잔고 CRUD (Source of Truth 동기화용)
    # -------------------------------------------------------------
    def sync_positions_to_db(self, positions: List[Dict[str, Any]]):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM positions")
                for p in positions:
                    code = str(p.get("stock_code") or p.get("code", "")).zfill(6)
                    if not code:
                        continue
                    cursor.execute("""
                    INSERT OR REPLACE INTO positions (
                        stock_code, stock_name, qty, avg_price, current_price,
                        purchase_amount, valuation_amount, pnl, pnl_rate, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        code,
                        str(p.get("stock_name") or p.get("name", code)),
                        int(p.get("qty", 0)),
                        int(p.get("avg_price", 0)),
                        int(p.get("current_price", 0)),
                        int(p.get("purchase_amount", 0)),
                        int(p.get("valuation_amount", 0)),
                        int(p.get("pnl", 0)),
                        float(p.get("pnl_rate", 0.0)),
                        now_str
                    ))
                conn.commit()

        self._execute_with_retry(_action)

    def get_db_positions(self) -> List[Dict[str, Any]]:
        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM positions")
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

        return self._execute_with_retry(_action)

    # -------------------------------------------------------------
    # [신규] Heartbeat & Safe Stop 관리
    # -------------------------------------------------------------
    def update_heartbeat(self, status: str, trading_mode: str, safe_stop_active: bool = False, safe_stop_reason: str = ""):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        safe_stop_int = 1 if safe_stop_active else 0

        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT OR REPLACE INTO engine_heartbeat (id, status, trading_mode, safe_stop_active, safe_stop_reason, last_heartbeat)
                VALUES (1, ?, ?, ?, ?, ?)
                """, (status, trading_mode, safe_stop_int, safe_stop_reason, now_str))
                conn.commit()

        self._execute_with_retry(_action)

    def get_heartbeat(self) -> Dict[str, Any]:
        def _action():
            with self._get_connection() as conn:
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

        return self._execute_with_retry(_action)

    def create_backup(self, backup_name: str, backup_dict: Dict[str, Any]):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        data_json = json.dumps(backup_dict, ensure_ascii=False)

        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO backups (backup_name, backup_data, created_at)
                VALUES (?, ?, ?)
                """, (backup_name, data_json, now_str))
                conn.commit()

        self._execute_with_retry(_action)

    def get_latest_backup(self) -> Optional[Dict[str, Any]]:
        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT backup_data FROM backups ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                if row:
                    return json.loads(row["backup_data"])
                return None

        return self._execute_with_retry(_action)

    def add_log(self, level: str, message: str):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO web_trade_logs (timestamp, level, message)
                VALUES (?, ?, ?)
                """, (now_str, level, message))
                conn.commit()

        self._execute_with_retry(_action)

    def get_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        def _action():
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM web_trade_logs ORDER BY id DESC LIMIT ?", (limit,))
                rows = cursor.fetchall()
                return [{"id": r["id"], "timestamp": r["timestamp"], "level": r["level"], "message": r["message"]} for r in rows]

        return self._execute_with_retry(_action)

    def reset_database(self) -> bool:
        """데이터베이스 전체 초기화"""
        try:
            def _action():
                with self._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM stock_grids")
                    cursor.execute("DELETE FROM system_config")
                    cursor.execute("DELETE FROM web_trade_logs")
                    cursor.execute("DELETE FROM orders")
                    cursor.execute("DELETE FROM positions")
                    cursor.execute("DELETE FROM engine_heartbeat")
                    conn.commit()

            self._execute_with_retry(_action)
            self.add_log("WARNING", "🗑️ [DB 초기화] 데이터베이스 및 그리드 설정 데이터가 완전 초기화되었습니다.")
            return True
        except Exception as e:
            print(f"[DB Reset Error] {e}")
            return False

            return True
        except Exception as e:
            print(f"[WebDBManager reset_database 예외] {e}")
            return False

