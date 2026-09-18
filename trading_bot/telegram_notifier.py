import os
import json
import urllib.request
import urllib.parse


from typing import Dict, Any, Optional

class TelegramNotifier:
    """
    urllib 기반 모바일 텔레그램 비상 및 트레이딩 알림 봇 (No-pip)
    """

    def __init__(self, config: Dict[str, Any]):
        tg_cfg = config.get("telegram", {})
        self.enabled = tg_cfg.get("enabled", False)
        self.bot_token = tg_cfg.get("bot_token", "").strip()
        self.chat_id = tg_cfg.get("chat_id", "").strip()

    def send_message(self, text: str) -> bool:
        """텔레그램 봇 API로 즉시 푸시 메세지 전송"""
        if not self.enabled or not self.bot_token or not self.chat_id:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        try:
            data_bytes = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json; charset=UTF-8"}
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return bool(res.get("ok"))
        except Exception as e:
            print(f"[TelegramNotifier Exception] {e}")
            return False
