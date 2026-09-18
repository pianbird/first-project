import os
import sqlite3
from typing import Dict, Any, List, Optional, Tuple

try:
    from web_app.backend.repositories.db_core import DBCore
    from web_app.backend.repositories.stock_grid_repository import StockGridRepository
    from web_app.backend.repositories.order_repository import OrderRepository
    from web_app.backend.repositories.position_repository import PositionRepository
    from web_app.backend.repositories.system_repository import SystemRepository
except ImportError:
    from repositories.db_core import DBCore
    from repositories.stock_grid_repository import StockGridRepository
    from repositories.order_repository import OrderRepository
    from repositories.position_repository import PositionRepository
    from repositories.system_repository import SystemRepository


class WebDBManager:
    """
    MagicTrader Web Dashboard 및 Always-on Task 공용 SQLite3 영속성 관리자
    - PRAGMA journal_mode=WAL 및 busy_timeout=30000 적용 (DBCore 위임)
    - SQLite OperationalError(database is locked) 대비 retry 래퍼 제공
    - 주문 상태 관리(orders), 포지션 잔고(positions), 하트비트(engine_heartbeat) 영속화
    - Repository 델리게이트 위임 구조 (Public API 100% 하위 호환성 보장)
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(base_dir, "web_magictrader.db")

        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self._db_core = DBCore(self.db_path)
        self._stock_grid_repo = StockGridRepository(self._db_core)
        self._order_repo = OrderRepository(self._db_core)
        self._position_repo = PositionRepository(self._db_core)
        self._system_repo = SystemRepository(self._db_core)

    def _get_connection(self) -> sqlite3.Connection:
        return self._db_core._get_connection()

    def _execute_with_retry(self, func, max_retries: int = 5, delay: float = 0.2):
        return self._db_core._execute_with_retry(func, max_retries=max_retries, delay=delay)

    def _init_db(self):
        self._db_core._init_db()

    def register_idempotency_key(self, client_key: str, stock_code: str = "", side: str = "") -> bool:
        return self._system_repo.register_idempotency_key(client_key, stock_code, side)

    def get_kst_today_str(self) -> str:
        return self._system_repo.get_kst_today_str()

    def get_daily_trade_count(self) -> int:
        return self._system_repo.get_daily_trade_count()

    def increment_daily_trade_count(self) -> int:
        return self._system_repo.increment_daily_trade_count()

    def check_and_increment_daily_trade_limit(self, max_limit: int) -> Tuple[bool, int, str]:
        return self._system_repo.check_and_increment_daily_trade_limit(max_limit)

    def save_stock_grid(self, code: str, name: str, clear_price: int, memo: str, grid_dict: Dict[str, Any], is_active: bool = True):
        self._stock_grid_repo.save_stock_grid(code, name, clear_price, memo, grid_dict, is_active)

    def get_all_stock_grids(self) -> List[Dict[str, Any]]:
        return self._stock_grid_repo.get_all_stock_grids()

    def delete_stock_grid(self, code: str):
        self._stock_grid_repo.delete_stock_grid(code)

    def save_system_config(self, key: str, value_dict: Dict[str, Any]):
        self._system_repo.save_system_config(key, value_dict)

    def get_system_config(self, key: str) -> Optional[Dict[str, Any]]:
        return self._system_repo.get_system_config(key)

    def save_order(self, order_info: Dict[str, Any]):
        self._order_repo.save_order(order_info)

    def get_pending_orders(self, stock_code: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._order_repo.get_pending_orders(stock_code)

    def get_recent_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._order_repo.get_recent_orders(limit)

    def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        return self._order_repo.get_order_by_id(order_id)

    def get_active_or_unknown_orders(self, stock_code: Optional[str] = None, side: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._order_repo.get_active_or_unknown_orders(stock_code, side)

    def get_unknown_orders(self, stock_code: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._order_repo.get_unknown_orders(stock_code)

    def sync_positions_to_db(self, positions: List[Dict[str, Any]]):
        self._position_repo.sync_positions_to_db(positions)

    def get_db_positions(self) -> List[Dict[str, Any]]:
        return self._position_repo.get_db_positions()

    def update_heartbeat(self, status: str, trading_mode: str, safe_stop_active: bool = False, safe_stop_reason: str = ""):
        self._system_repo.update_heartbeat(status, trading_mode, safe_stop_active, safe_stop_reason)

    def get_heartbeat(self) -> Dict[str, Any]:
        return self._system_repo.get_heartbeat()

    def create_backup(self, backup_name: str, backup_dict: Dict[str, Any]):
        self._system_repo.create_backup(backup_name, backup_dict)

    def get_latest_backup(self) -> Optional[Dict[str, Any]]:
        return self._system_repo.get_latest_backup()

    def add_log(self, level: str, message: str):
        self._system_repo.add_log(level, message)

    def get_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._system_repo.get_logs(limit)

    def reset_database(self) -> bool:
        """데이터베이스 전체 초기화"""
        try:
            self._stock_grid_repo.reset_stock_grids()
            self._system_repo.reset_system_config()
            self._system_repo.reset_web_trade_logs()
            self._order_repo.reset_orders()
            self._position_repo.reset_positions()
            self._system_repo.reset_engine_heartbeat()
            self.add_log("WARNING", "🗑️ [DB 초기화] 데이터베이스 및 그리드 설정 데이터가 완전 초기화되었습니다.")
            return True
        except Exception as e:
            print(f"[DB Reset Error] {e}")
            return False
