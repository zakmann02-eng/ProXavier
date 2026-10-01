"""Telegram Bot API over plain HTTP. Without a token it logs messages instead (dry run)."""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)
MAX_LEN = 4000


class Telegram:
    def __init__(self, token: str, chat_id: str, timeout: float = 15.0):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.sent: list[str] = []   # kept for tests / dry runs

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        self.sent.append(text)
        if not self.enabled:
            log.info("[telegram dry-run]\n%s", text)
            return True
        ok = True
        for chunk in [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)]:
            ok &= self._post(chunk)
        return ok

    def _post(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for attempt in range(3):
            try:
                r = httpx.post(url, json={"chat_id": self.chat_id, "text": text,
                                          "disable_web_page_preview": True}, timeout=self.timeout)
                if r.status_code == 200:
                    return True
                log.warning("telegram HTTP %s: %s", r.status_code, r.text[:200])
            except httpx.HTTPError as e:
                log.warning("telegram error: %s", e)
            time.sleep(2 ** attempt)
        log.error("telegram message not delivered:\n%s", text)
        return False
