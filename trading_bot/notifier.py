"""
MagicTrader - Telegram Alert Notifier & System Monitoring (notifier.py)
Production-Ready Real-Time Telegram Notifier using `requests`
"""
import os
import sys
import logging
from pathlib import Path
from typing import Dict, Any, Optional

import requests

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from config import Config, get_kst_now_str
except (ImportError, ModuleNotFoundError):
    from trading_bot.config import Config, get_kst_now_str

logger = logging.getLogger("Notifier")


class Notifier:
    """
    Telegram Bot API Push Notification Class (`requests` based)
    - References TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from `config.py`
    - Sends Markdown/HTML formatted real-time messages for order sent/filled and system errors
    - Ensures complete error isolation (try-except) so network or token failures never crash trading loops
    """

    def __init__(self, config: Optional[Any] = None):
        self.config = config or Config
        self.token = getattr(self.config, "TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = getattr(self.config, "TELEGRAM_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        self.enabled = getattr(self.config, "TELEGRAM_ENABLED", bool(self.token and self.chat_id))

    def _get_kst_now_str(self) -> str:
        try:
            return get_kst_now_str()
        except Exception:
            import time
            return time.strftime("%Y-%m-%d %H:%M:%S")

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        Send formatted message via Telegram Bot API using requests.
        Catches all network exceptions, HTTP errors, and missing credentials to guarantee system stability.

        :param text: Message body text (HTML or Markdown)
        :param parse_mode: Formatting type ("HTML", "Markdown", or "MarkdownV2")
        :return: True if sent successfully, False otherwise
        """
        if not self.enabled or not self.token or not self.chat_id:
            logger.debug("[Notifier Isolated] Telegram disabled or missing TOKEN/CHAT_ID")
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(url, json=payload, timeout=3.0)
            if response.status_code == 200:
                res_data = response.json()
                return bool(res_data.get("ok"))
            else:
                logger.warning(
                    f"[{self._get_kst_now_str()}] [Telegram Alert Warning] "
                    f"HTTP {response.status_code}: {response.text}"
                )
                return False
        except requests.exceptions.Timeout:
            logger.warning(f"[{self._get_kst_now_str()}] [Telegram Alert Warning] Request timeout (3s)")
            return False
        except requests.exceptions.RequestException as req_err:
            logger.warning(f"[{self._get_kst_now_str()}] [Telegram Alert Warning] Request error: {req_err}")
            return False
        except Exception as e:
            logger.warning(f"[{self._get_kst_now_str()}] [Telegram Alert Warning] Unexpected notification exception: {e}")
            return False

    def notify_order_sent(
        self, stock_code: str, stock_name: str, side: str, qty: int, price: int, level_id: int
    ) -> bool:
        """Notify order submission (isolated)"""
        try:
            icon = "🛒" if side.upper() == "BUY" else "📈"
            msg = (
                f"{icon} <b>[{side.upper()} 주문 접수]</b>\n"
                f"⏱ 시각: {self._get_kst_now_str()}\n"
                f"📌 종목: {stock_name} ({stock_code})\n"
                f"🎯 그리드: {level_id}차 레벨\n"
                f"🔢 수량: {qty:,}주\n"
                f"💰 단가: {price:,}원"
            )
            return self.send_message(msg, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"[Notifier Isolated Exception] notify_order_sent error: {e}")
            return False

    def notify_order_filled(
        self, stock_code: str, stock_name: str, side: str, qty: int, price: int, order_id: str
    ) -> bool:
        """Notify order execution (isolated)"""
        try:
            icon = "✅" if side.upper() == "BUY" else "🎉"
            msg = (
                f"{icon} <b>[{side.upper()} 체결 완료]</b>\n"
                f"⏱ 시각: {self._get_kst_now_str()}\n"
                f"📌 종목: {stock_name} ({stock_code})\n"
                f"🔢 체결수량: {qty:,}주\n"
                f"💰 체결단가: {price:,}원\n"
                f"🧾 주문번호: {order_id}"
            )
            return self.send_message(msg, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"[Notifier Isolated Exception] notify_order_filled error: {e}")
            return False

    def notify_reconciliation(self, summary: str) -> bool:
        """Notify startup or periodic account reconciliation (isolated)"""
        try:
            msg = (
                f"🔍 <b>[실계좌 상태 양방향 대사 완료]</b>\n"
                f"⏱ 시각: {self._get_kst_now_str()}\n"
                f"📋 내용:\n{summary}"
            )
            return self.send_message(msg, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"[Notifier Isolated Exception] notify_reconciliation error: {e}")
            return False

    def notify_error(self, title: str, reason: str) -> bool:
        """Notify error or safe stop trigger (isolated)"""
        try:
            msg = (
                f"🚨 <b>[{title}]</b>\n"
                f"⏱ 시각: {self._get_kst_now_str()}\n"
                f"⚠️ 사유: {reason}"
            )
            return self.send_message(msg, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"[Notifier Isolated Exception] notify_error error: {e}")
            return False


# Backward compatibility alias
TelegramNotifier = Notifier
