import json
import time
from typing import Dict, Any, List
from web_app.backend.repositories.db_core import DBCore

class StockGridRepository:
    """
    stock_grids 테이블 전용 Repository
    """

    def __init__(self, db_core: DBCore):
        self.db_core = db_core

    def save_stock_grid(self, code: str, name: str, clear_price: int, memo: str, grid_dict: Dict[str, Any], is_active: bool = True):
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(grid_dict, dict):
            grid_dict["is_active"] = is_active
        grid_json = json.dumps(grid_dict, ensure_ascii=False)
        is_active_int = 1 if is_active else 0

        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT OR REPLACE INTO stock_grids (stock_code, stock_name, clear_price, memo, grid_json, is_active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (code, name, clear_price, memo, grid_json, is_active_int, now_str))
                conn.commit()

        self.db_core._execute_with_retry(_action)

    def get_all_stock_grids(self) -> List[Dict[str, Any]]:
        def _action():
            with self.db_core._get_connection() as conn:
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

        return self.db_core._execute_with_retry(_action)

    def delete_stock_grid(self, code: str):
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM stock_grids WHERE stock_code = ?", (code,))
                conn.commit()

        self.db_core._execute_with_retry(_action)

    def reset_stock_grids(self):
        def _action():
            with self.db_core._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM stock_grids")
                conn.commit()

        self.db_core._execute_with_retry(_action)
