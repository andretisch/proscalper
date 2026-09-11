"""Telegram control + notifications."""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from app.config import Settings
from app.http_client import SessionPool
from app.logging_setup import get_logger

log = get_logger("telegram")

# MarkdownV2 требует экранировать каждый из этих символов в обычном тексте,
# иначе Telegram отвечает 400 и сообщение теряется целиком.
_MD_SPECIAL = set(r"_*[]()~`>#+-=|{}.!\\")

_UNSET = object()

# Долго ждать Telegram незачем: сообщение либо уходит, либо повторяется.
_CONNECT_TIMEOUT = 6.0
_SEND_TIMEOUT = (_CONNECT_TIMEOUT, 20.0)
# Long-poll сам держит соединение 25 с, чтение должно это переживать.
_POLL_TIMEOUT = (_CONNECT_TIMEOUT, 35.0)


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
        self._sessions = SessionPool(settings)
        self.parse_mode = parse_mode
        self.base = f"https://api.telegram.org/bot{settings.telegram_token}"
        self._offset = 0
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._inbox: queue.Queue[dict] = queue.Queue(maxsize=100)
        self._outbox: queue.Queue[tuple[str, str | None]] = queue.Queue(maxsize=200)
        self._sender: threading.Thread | None = None
        self.command_handlers: dict[str, Callable[[str], str]] = {}

    @property
    def session(self):
        return self._sessions.get()

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
                    f"{self.base}/sendMessage", json=payload, timeout=_SEND_TIMEOUT
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

    def send_async(self, text: str, chat_id: str | None = None) -> None:
        """Поставить уведомление в очередь и сразу вернуться.

        Отправка через прокси иногда занимает минуты. Раньше уведомление о
        сделке отправлялось прямо из торгового цикла, цикл всё это время не
        двигался — и сторож справедливо считал процесс зависшим.
        """
        if self._sender is None or not self._sender.is_alive():
            self.send(text, chat_id=chat_id)
            return
        try:
            self._outbox.put_nowait((text, chat_id))
        except queue.Full:
            log.warning("очередь уведомлений переполнена, сообщение отброшено")

    def get_updates(self) -> list[dict]:
        r = self.session.get(
            f"{self.base}/getUpdates",
            params={"offset": self._offset, "timeout": 25},
            timeout=_POLL_TIMEOUT,
        )
        data = r.json()
        if not data.get("ok"):
            return []
        updates = data.get("result") or []
        for u in updates:
            self._offset = max(self._offset, int(u["update_id"]) + 1)
        return updates

    def typing(self, chat_id: str) -> None:
        """Показать «печатает»: команда может считать баланс и стакан."""
        try:
            self.session.post(
                f"{self.base}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
                timeout=10,
            )
        except Exception:
            pass

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
        # Нет команды — передаём в LLM для диалога
        handler = self.command_handlers.get(cmd) or self.command_handlers.get(
            "_llm_chat"
        )
        log.info("сообщение от %s: %s", chat_id, text[:80])
        self.typing(chat_id)
        started = time.time()
        try:
            if handler:
                reply = handler(text)
            else:
                reply = (
                    md_escape("Команды: /status /balance /mode /pause /resume /help")
                    + "\n"
                    + md_code(text[:100])
                )
        except Exception as e:
            # Молчание хуже ошибки: пользователь должен понять, что бот жив.
            log.exception("обработчик %s упал", cmd)
            reply = md_escape(f"Ошибка обработки {cmd}: {e}")
        elapsed = time.time() - started
        if elapsed > 15:
            log.warning("ответ на %s готовился %.0fс", cmd, elapsed)
        self.send(reply, chat_id=chat_id)

    def start_polling(self) -> None:
        try:
            self.session.get(f"{self.base}/deleteWebhook", timeout=20)
        except Exception as e:
            log.warning("не удалось снять webhook: %s", e)

        def poll() -> None:
            """Только забирает апдейты.

            Обработчик может уйти в сеть на десятки секунд (баланс, ИИ), и
            раньше это вешало сам опрос: остальные сообщения не забирались
            вовсе, бот выглядел мёртвым.
            """
            while not self._stop.is_set():
                try:
                    for u in self.get_updates():
                        try:
                            self._inbox.put_nowait(u)
                        except queue.Full:
                            log.warning("очередь сообщений переполнена")
                except Exception as e:
                    log.warning("обрыв polling: %s", e)
                    time.sleep(3)

        def work() -> None:
            while not self._stop.is_set():
                try:
                    update = self._inbox.get(timeout=1)
                except queue.Empty:
                    continue
                try:
                    self.handle_update(update)
                except Exception:
                    log.exception("ошибка обработки сообщения")

        def deliver() -> None:
            while not self._stop.is_set():
                try:
                    text, chat_id = self._outbox.get(timeout=1)
                except queue.Empty:
                    continue
                try:
                    self.send(text, chat_id=chat_id)
                except Exception:
                    log.exception("ошибка отправки уведомления")

        for target, name in ((poll, "tg-poll"), (work, "tg-work"), (deliver, "tg-send")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
            if name == "tg-send":
                self._sender = thread

    def stop(self) -> None:
        self._stop.set()
