import os
import sqlite3
import time

class DBCore:
    """
    SQLite3 데이터베이스 커넥션 및 트랜잭션/재시도 코어 관리자
    """

    def __init__(self, db_path: str):
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
                    if "leaves_qty" not in ord_cols:
                        cursor.execute("ALTER TABLE orders ADD COLUMN leaves_qty INTEGER DEFAULT 0")

                conn.commit()

        self._execute_with_retry(_create_tables)
