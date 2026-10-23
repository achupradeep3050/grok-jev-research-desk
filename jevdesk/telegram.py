"""
Minimal Telegram Bot API client (long polling, inline buttons). No third-party SDK.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("jevdesk.telegram")


class TelegramClient:
    def __init__(self, token: str, poll_timeout: int = 10, api_base: str = "https://api.telegram.org"):
        # api_base lets you use a self-hosted Bot API server (or a local mock in tests)
        self._base = f"{api_base.rstrip('/')}/bot{token}"
        self.poll_timeout = poll_timeout
        self.client = httpx.Client(timeout=poll_timeout + 10)
        self.offset = 0

    def _post(self, method: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            r = self.client.post(f"{self._base}/{method}", json=payload)
            data = r.json()
            if data.get("ok"):
                return data.get("result")
            # Markdown parse errors: retry once as plain text
            if payload.get("parse_mode"):
                payload = {k: v for k, v in payload.items() if k != "parse_mode"}
                data = self.client.post(f"{self._base}/{method}", json=payload).json()
                if data.get("ok"):
                    return data.get("result")
            logger.warning("telegram %s failed: %s", method, data.get("description"))
        except Exception as exc:  # never log the URL: it contains the token
            logger.warning("telegram %s error: %s", method, exc.__class__.__name__)
        return None

    def get_me(self) -> Optional[Dict[str, Any]]:
        return self._post("getMe", {})

    def send(self, chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None,
             reply_to: Optional[int] = None) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "Markdown",
                                   "disable_web_page_preview": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        return self._post("sendMessage", payload)

    def edit(self, chat_id: int, message_id: int, text: str) -> None:
        self._post("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text[:4000],
                                       "parse_mode": "Markdown", "reply_markup": {"inline_keyboard": []}})

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]})

    def updates(self) -> List[Dict[str, Any]]:
        try:
            r = self.client.get(f"{self._base}/getUpdates",
                                params={"timeout": self.poll_timeout, "offset": self.offset or None,
                                        "allowed_updates": '["message","callback_query"]'})
            data = r.json()
            res = data.get("result", []) if data.get("ok") else []
            if res:
                self.offset = res[-1]["update_id"] + 1
            return res
        except Exception as exc:
            logger.debug("getUpdates: %s", exc.__class__.__name__)
            return []
