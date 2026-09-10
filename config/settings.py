"""
Configuration loader and settings wrapper for MagicTrader.
"""
import os
import json
from pathlib import Path
from typing import Dict, Any, Optional

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = BASE_DIR / "trading_bot" / "config.json"

class SystemSettings:
    """
    Unified settings loader reading from environment variables and config.json.
    Preserves exact key fallback structure.
    """
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self._data: Dict[str, Any] = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[SystemSettings] Config load warning: {e}")
        return {}

    def get(self, key: str, default: Any = None) -> Any:
        # Environment variable override takes precedence
        env_val = os.getenv(key.upper())
        if env_val is not None:
            return env_val
        return self._data.get(key, default)
