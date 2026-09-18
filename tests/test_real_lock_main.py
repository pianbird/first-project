import os
import sys

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

import unittest
from unittest.mock import patch, MagicMock

try:
    from trading_mode import (
        acquire_real_trading_lock,
        release_real_trading_lock,
        RealTradingLockError
    )
except (ImportError, ModuleNotFoundError):
    from trading_bot.trading_mode import (
        acquire_real_trading_lock,
        release_real_trading_lock,
        RealTradingLockError
    )

from trading_bot.main import run_daemon
from config import Config

class TestRealTradingLockDaemon(unittest.TestCase):
    def setUp(self):
        self.test_account = "88887777"
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        release_real_trading_lock(self.test_account)
        import tempfile
        lock_file = os.path.join(tempfile.gettempdir(), f"magictrader_real_{self.test_account}.lock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except Exception:
                pass

    def test_dual_process_blocked_in_real_mode(self):
        """REAL 모드 기동 중 동일 계좌로 trading_bot/main.py 기동 시 exit code 1로 종료 검증"""
        # 1. Manually write lock file simulating Process 1 (PID 999999) holding the REAL lock
        import tempfile
        import json
        lock_file = os.path.join(tempfile.gettempdir(), f"magictrader_real_{self.test_account}.lock")
        with open(lock_file, "w", encoding="utf-8") as f:
            json.dump({"pid": 999999, "account_no": self.test_account, "timestamp": "2026-09-10 12:00:00"}, f)

        # 2. Simulate process 2 (trading_bot/main.py) running in REAL mode while PID 999999 is alive
        with patch.object(Config, "IS_MOCK", False), \
             patch.object(Config, "ACCOUNT_NO", self.test_account), \
             patch.object(Config, "validate", return_value=True), \
             patch("psutil.pid_exists", return_value=True):

            with self.assertRaises(SystemExit) as cm:
                run_daemon()

            # Verify process 2 exits with code 1
            self.assertEqual(cm.exception.code, 1)

    def test_mock_mode_allows_execution(self):
        """MOCK 모드에서는 RealTradingLock 검사 없이 기동 흐름 진입 검증"""
        # Lock file exists for PID 999999
        import tempfile
        import json
        lock_file = os.path.join(tempfile.gettempdir(), f"magictrader_real_{self.test_account}.lock")
        with open(lock_file, "w", encoding="utf-8") as f:
            json.dump({"pid": 999999, "account_no": self.test_account, "timestamp": "2026-09-10 12:00:00"}, f)

        with patch.object(Config, "IS_MOCK", True), \
             patch.object(Config, "ACCOUNT_NO", self.test_account), \
             patch.object(Config, "validate", return_value=True), \
             patch("trading_bot.main.SQLiteStorage"), \
             patch("trading_bot.main.KiwoomRESTClient"), \
             patch("trading_bot.main.TelegramNotifier"), \
             patch("trading_bot.main.GridEngine"), \
             patch("trading_bot.main.GracefulKiller") as mock_killer:

            killer_inst = MagicMock()
            killer_inst.kill_now = True  # Stop loop immediately after 0 iterations
            mock_killer.return_value = killer_inst

            # Should run without SystemExit lock error in MOCK mode
            try:
                run_daemon()
            except SystemExit as e:
                self.fail(f"run_daemon() unexpectedly exited in MOCK mode: {e}")

if __name__ == "__main__":
    unittest.main()
