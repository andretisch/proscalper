"""Telegram control + notifications."""

from __future__ import annotations

import threading
import time
from typing import Callable

from app.config import Settings
from app.http_client import make_session
from app.logging_setup import get_logger

log = get_logger("telegram")

# MarkdownV2 требует экранировать каждый из этих символов в обычном тексте,
# иначе Telegram отвечает 400 и сообщение теряется целиком.
_MD_SPECIAL = set(r"_*[]()~`>#+-=|{}.!\\")

_UNSET = object()


def md_escape(text: object) -> str:
    """Экранировать текст для MarkdownV2."""
    return "".join(("\\" + ch if ch in _MD_SPECIAL else ch) for ch in str(text))


def md_code(text: object) -> str:
    """Моноширинный фрагмент: внутри экранируются только ` и обратный слэш."""
    body = str(text).replace("\\", "\\\\").replace("`", "\\`")
    return f"`{body}`"


def md_pre(text: object) -> str:
    """Многострочный блок: однострочный `code` ломается на переводах строки."""
    body = str(text).replace("\\", "\\\\").replace("`", "\\`")
    return f"```\n{body}\n```"


def md_bold(text: object) -> str:
    return f"*{md_escape(text)}*"


class TelegramBot:
    def __init__(self, settings: Settings, parse_mode: str | None = "MarkdownV2") -> None:
        self.s = settings
        self.session = make_session(settings)
        self.parse_mode = parse_mode
        self.base = f"https://api.telegram.org/bot{settings.telegram_token}"
        self._offset = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.command_handlers: dict[str, Callable[[str], str]] = {}

    def set_commands(self, commands: list[dict[str, str]]) -> bool:
        """Установить меню команд бота (кнопка слева от поля ввода)."""
        try:
            r = self.session.post(
                f"{self.base}/setMyCommands",
                json={"commands": commands},
                timeout=20,
            )
            return bool(r.json().get("ok"))
        except Exception:
            return False

    def send(
        self,
        text: str,
        chat_id: str | None = None,
        parse_mode: str | None | object = _UNSET,
    ) -> bool:
        """Не бросает исключений: сбой уведомления не должен убивать торговый цикл.

        Разметка — не повод потерять сообщение: если Telegram отвергает её
        (непарный `_` в имени сетапа, неэкранированный минус в PnL), тот же
        текст уходит повторно без parse_mode.
        """
        cid = chat_id or self.s.telegram_chat_id
        if not cid or not self.s.telegram_token:
            return False
        mode = self.parse_mode if parse_mode is _UNSET else parse_mode
        for attempt in range(3):
            try:
                payload: dict[str, object] = {"chat_id": cid, "text": text[:4000]}
                if mode:
                    payload["parse_mode"] = mode
                r = self.session.post(
                    f"{self.base}/sendMessage", json=payload, timeout=30
                )
                data = r.json()
                if data.get("ok"):
                    return True
                description = str(data.get("description") or "")
                if mode and "parse" in description.lower():
                    log.warning("разметка отклонена (%s), шлю без неё", description[:90])
                    mode = None
                    continue
                log.warning("telegram отказал: %s", description[:120])
                return False
            except Exception as e:
                log.warning("обрыв отправки (попытка %s/3): %s", attempt + 1, e)
                if attempt == 2:
                    return False
                time.sleep(2 * (attempt + 1))
        return False

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
            self.send(md_escape("Нет доступа."), chat_id=chat_id)
            return
        cmd = text.split()[0].split("@")[0].lower()
        handler = self.command_handlers.get(cmd)
        if handler:
            reply = handler(text)
        else:
            # Нет команды — передаём в LLM для диалога
            fallback = self.command_handlers.get("_llm_chat")
            if fallback:
                reply = fallback(text)
            else:
                reply = (
                    md_escape("Команды: /status /balance /mode /pause /resume /help")
                    + "\n"
                    + md_code(text[:100])
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
                except Exception as e:
                    log.warning("обрыв polling: %s", e)
                    time.sleep(3)

        self._thread = threading.Thread(target=loop, name="tg-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
