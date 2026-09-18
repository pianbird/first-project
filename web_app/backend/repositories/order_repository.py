import time
from typing import Dict, Any, List, Optional
from web_app.backend.repositories.db_core import DBCore

class OrderRepository:
    """
    orders 테이블 전용 Repository (Order Lifecycle Management)
    """

    def __init__(self, db_core: DBCore):
        self.db_core = db_core

    def save_order(self, order_info: Dict[str, Any]):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        order_id = str(order_info.get("order_id", ""))
        if not order_id:
            return

        def _action():
            with self.db_core._get_connection() as conn:
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

        self.db_core._execute_with_retry(_action)

    def get_pending_orders(self, stock_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """미체결/진행중 주문(SUBMITTED, ACCEPTED, ORDER_PLACED, PARTIALLY_FILLED 등) 목록 조회"""
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                pending_statuses = ("PENDING", "PENDING_SUBMIT", "SUBMITTED", "ACCEPTED", "ORDER_PLACED", "ORDER_SENT", "OPEN", "PARTIALLY_FILLED", "CANCEL_REQUESTED")
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

        return self.db_core._execute_with_retry(_action)

    def get_recent_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM orders ORDER BY updated_at DESC LIMIT ?", (limit,))
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

        return self.db_core._execute_with_retry(_action)

    def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        """order_id 직접 쿼리로 주문 조회 (선형 스캔 제거)"""
        if not order_id:
            return None
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM orders WHERE order_id = ?", (str(order_id),))
                row = cursor.fetchone()
                return dict(row) if row else None
        return self.db_core._execute_with_retry(_action)

    def get_active_or_unknown_orders(self, stock_code: Optional[str] = None, side: Optional[str] = None) -> List[Dict[str, Any]]:
        """SQL 기반 미체결 및 UNKNOWN 주문 직접 조회"""
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                statuses = ("PENDING", "PENDING_SUBMIT", "SUBMITTED", "ACCEPTED", "ORDER_PLACED", "ORDER_SENT", "OPEN", "PARTIALLY_FILLED", "CANCEL_REQUESTED", "UNKNOWN")
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

        return self.db_core._execute_with_retry(_action)

    def get_unknown_orders(self, stock_code: Optional[str] = None) -> List[Dict[str, Any]]:
        """SQL 기반 UNKNOWN 상태 주문 시간순 직접 조회"""
        def _action():
            with self.db_core._get_connection() as conn:
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

        return self.db_core._execute_with_retry(_action)

    def reset_orders(self):
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM orders")
                conn.commit()

        self.db_core._execute_with_retry(_action)
