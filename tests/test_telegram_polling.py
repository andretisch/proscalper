"""Медленная команда не должна затыкать бота целиком."""

from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

from app.telegram_bot import TelegramBot


def _bot() -> TelegramBot:
    bot = object.__new__(TelegramBot)
    bot.s = SimpleNamespace(telegram_chat_id="42", telegram_token="t")
    session = SimpleNamespace(
        get=lambda *_a, **_k: SimpleNamespace(json=lambda: {"ok": True})
    )
    bot._sessions = SimpleNamespace(get=lambda: session)
    bot.parse_mode = None
    bot.base = "https://example.invalid"
    bot._offset = 0
    bot._stop = threading.Event()
    bot._threads = []
    bot._inbox = queue.Queue(maxsize=100)
    bot._outbox = queue.Queue(maxsize=200)
    bot._sender = None
    bot.command_handlers = {}
    bot.sent = []
    bot.send = lambda text, chat_id=None, **_: bot.sent.append(text) or True
    bot.typing = lambda chat_id: None
    return bot


def _message(text: str, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {"text": text, "chat": {"id": "42"}},
    }


def test_slow_handler_does_not_block_polling():
    bot = _bot()
    release = threading.Event()
    polls = []

    def slow(_text: str) -> str:
        release.wait(5)
        return "готово"

    bot.command_handlers = {"/slow": slow}

    def fake_updates() -> list[dict]:
        polls.append(time.time())
        # Апдейт отдаём один раз, дальше опрос крутится вхолостую.
        if len(polls) == 1:
            return [_message("/slow")]
        time.sleep(0.01)
        return []

    bot.get_updates = fake_updates
    bot.start_polling()
    try:
        time.sleep(0.3)
        # Обработчик всё ещё висит, но опрос обязан продолжаться.
        assert not bot.sent
        assert len(polls) > 3
    finally:
        release.set()
        bot.stop()
    time.sleep(0.2)
    assert bot.sent == ["готово"]


def test_failing_handler_answers_instead_of_silence():
    bot = _bot()

    def boom(_text: str) -> str:
        raise RuntimeError("стакан недоступен")

    bot.command_handlers = {"/status": boom}
    bot.handle_update(_message("/status"))
    assert len(bot.sent) == 1
    assert "стакан недоступен" in bot.sent[0]


def test_unknown_text_goes_to_llm_chat():
    bot = _bot()
    bot.command_handlers = {"_llm_chat": lambda t: f"ответ на {t}"}
    bot.handle_update(_message("привет"))
    assert bot.sent == ["ответ на привет"]


def test_notification_does_not_block_the_caller():
    bot = _bot()
    release = threading.Event()
    started = threading.Event()

    def slow_send(text, chat_id=None, **_):
        started.set()
        release.wait(5)
        bot.sent.append(text)
        return True

    bot.send = slow_send
    bot.get_updates = lambda: (time.sleep(0.01), [])[1]
    bot.start_polling()
    try:
        t0 = time.time()
        bot.send_async("сделка закрыта")
        # Торговый цикл обязан продолжиться, не дожидаясь Telegram.
        assert time.time() - t0 < 0.2
        assert started.wait(2)
        assert not bot.sent
    finally:
        release.set()
        bot.stop()
    time.sleep(0.2)
    assert bot.sent == ["сделка закрыта"]


def test_notification_falls_back_to_sync_without_sender():
    bot = _bot()
    bot.send_async("без потока отправки")
    assert bot.sent == ["без потока отправки"]


def test_foreign_chat_is_rejected():
    bot = _bot()
    bot.command_handlers = {"_llm_chat": lambda t: "не должно вызваться"}
    bot.handle_update({"update_id": 2, "message": {"text": "хай", "chat": {"id": "7"}}})
    assert len(bot.sent) == 1
    assert "Нет доступа" in bot.sent[0]
