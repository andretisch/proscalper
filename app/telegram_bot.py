"""Telegram control + notifications."""

from __future__ import annotations

import threading
import time
from typing import Callable

from app.config import Settings
from app.http_client import make_session


class TelegramBot:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.session = make_session(settings)
        self.base = f"https://api.telegram.org/bot{settings.telegram_token}"
        self._offset = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.command_handlers: dict[str, Callable[[str], str]] = {}

    def send(self, text: str, chat_id: str | None = None) -> bool:
        cid = chat_id or self.s.telegram_chat_id
        if not cid or not self.s.telegram_token:
            return False
        r = self.session.post(
            f"{self.base}/sendMessage",
            json={"chat_id": cid, "text": text[:4000]},
            timeout=30,
        )
        return bool(r.json().get("ok"))

    def get_updates(self) -> list[dict]:
        r = self.session.get(
            f"{self.base}/getUpdates",
            params={"offset": self._offset, "timeout": 25},
            timeout=35,
        )
        data = r.json()
        if not data.get("ok"):
            return []
        updates = data.get("result") or []
        for u in updates:
            self._offset = max(self._offset, int(u["update_id"]) + 1)
        return updates

    def handle_update(self, update: dict) -> None:
        msg = update.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        if not text or not chat_id:
            return
        if self.s.telegram_chat_id and chat_id != self.s.telegram_chat_id:
            self.send("Нет доступа.", chat_id=chat_id)
            return
        cmd = text.split()[0].split("@")[0].lower()
        handler = self.command_handlers.get(cmd)
        if handler:
            reply = handler(text)
        else:
            reply = (
                "Команды: /status /mode /pause /resume /watchlist /help\n"
                f"Получено: {text[:100]}"
            )
        self.send(reply, chat_id=chat_id)

    def start_polling(self) -> None:
        # ensure no webhook
        self.session.get(f"{self.base}/deleteWebhook", timeout=20)

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    for u in self.get_updates():
                        self.handle_update(u)
                except Exception:
                    time.sleep(3)

        self._thread = threading.Thread(target=loop, name="tg-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
