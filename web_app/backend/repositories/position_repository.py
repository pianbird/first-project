import time
from typing import Dict, Any, List
from web_app.backend.repositories.db_core import DBCore

class PositionRepository:
    """
    positions 테이블 전용 Repository (계좌 포지션 잔고 동기화)
    """

    def __init__(self, db_core: DBCore):
        self.db_core = db_core

    def sync_positions_to_db(self, positions: List[Dict[str, Any]]):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        def _action():
            with self.db_core._get_connection() as conn:
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

        self.db_core._execute_with_retry(_action)

    def get_db_positions(self) -> List[Dict[str, Any]]:
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM positions")
                rows = cursor.fetchall()
                return [dict(r) for r in rows]

        return self.db_core._execute_with_retry(_action)

    def reset_positions(self):
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM positions")
                conn.commit()

        self.db_core._execute_with_retry(_action)
