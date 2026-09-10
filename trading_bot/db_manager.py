import os
import sys
import time
from typing import Dict, Any, List, Optional

# web_app/backend 경로 등록
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web_app", "backend"))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

try:
    from db import WebDBManager
except ImportError:
    from web_app.backend.db import WebDBManager

class DBManager(WebDBManager):
    """
    MagicTrader 하위 호환성을 위한 DBManager (WebDBManager 상속)
    - Always-on Task 및 기존 키움 트레이더 모듈 호환성 보장
    """
    def __init__(self, db_path: Optional[str] = None):
        super().__init__(db_path=db_path)

    def record_order(self, order_res: Dict[str, Any]):
        """주문 기록 저장"""
        self.save_order({
            "order_id": order_res.get("order_id") or order_res.get("order_no", f"ORD_{int(time.time()*1000)}"),
            "stock_code": order_res.get("stock_code", ""),
            "stock_name": order_res.get("stock_name", ""),
            "side": order_res.get("order_type", "BUY"),
            "order_type": order_res.get("ord_dvsn", "MARKET"),
            "requested_qty": order_res.get("qty", 0),
            "filled_qty": 0,
            "remaining_qty": order_res.get("qty", 0),
            "price": order_res.get("price", 0),
            "status": order_res.get("status", "ACCEPTED"),
            "message": order_res.get("message", "")
        })

    def log_event(self, level: str, message: str):
        self.add_log(level, message)

    def get_active_positions(self) -> List[Dict[str, Any]]:
        return self.get_db_positions()

    def remove_position(self, stock_code: str):
        positions = self.get_db_positions()
        new_pos = [p for p in positions if p.get("stock_code") != stock_code.zfill(6)]
        self.sync_positions_to_db(new_pos)

    def save_grid_config(self, config_dict: Dict[str, Any]):
        code = config_dict.get("stock_code")
        name = config_dict.get("stock_name", code)
        clear_price = config_dict.get("exit_price", 0)
        self.save_stock_grid(code, name, clear_price, "", config_dict)

    def save_grid_state(self, stock_code: str, state_obj: Any):
        state_dict = state_obj.to_dict() if hasattr(state_obj, "to_dict") else dict(state_obj)
        self.save_system_config(f"grid_state_{stock_code.zfill(6)}", state_dict)

    def get_grid_config(self, stock_code: str) -> Optional[Dict[str, Any]]:
        clean_code = str(stock_code).strip().zfill(6)
        grids = self.get_all_stock_grids()
        for g in grids:
            code = str(g.get("stock_code") or g.get("code") or "").strip().zfill(6)
            if code == clean_code:
                return g
        return None

    def upsert_position(self, pos: Dict[str, Any]):
        """포지션 단건/복수건 DB 저장 래퍼"""
        if isinstance(pos, dict):
            self.sync_positions_to_db([pos])
