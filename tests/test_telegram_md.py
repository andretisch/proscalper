"""MarkdownV2: разметка не должна стоить нам сообщения."""

from __future__ import annotations

from types import SimpleNamespace

from app.telegram_bot import TelegramBot, md_bold, md_code, md_escape, md_pre


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    """Отдаёт заготовленные ответы по порядку, последний повторяется."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = replies
        self.calls: list[dict] = []
        self.urls: list[str] = []

    def post(self, url: str, json: dict, timeout: int):  # noqa: A002
        self.urls.append(url)
        if url.endswith("sendChatAction"):
            return _FakeResponse({"ok": True})
        self.calls.append(json)
        idx = min(len(self.calls) - 1, len(self.replies) - 1)
        return _FakeResponse(self.replies[idx])


def _bot(replies: list[dict], parse_mode: str | None = "MarkdownV2") -> TelegramBot:
    bot = object.__new__(TelegramBot)
    bot.s = SimpleNamespace(telegram_chat_id="42", telegram_token="t")
    session = _FakeSession(replies)
    bot._sessions = SimpleNamespace(get=lambda: session)
    bot.parse_mode = parse_mode
    bot.base = "https://example.invalid"
    bot.command_handlers = {}
    return bot


def test_escapes_every_special_char():
    assert md_escape("S4_active-continuation") == "S4\\_active\\-continuation"
    assert md_escape("pnl=-0.39") == "pnl\\=\\-0\\.39"


def test_code_keeps_text_intact():
    # Внутри моноширинного блока спецсимволы разметки не действуют,
    # поэтому экранировать нужно только сам разделитель.
    assert md_code("pnl=-0.39") == "`pnl=-0.39`"
    assert md_code("a`b") == "`a\\`b`"


def test_bold_escapes_content():
    assert md_bold("Закрыто #1") == "*Закрыто \\#1*"


def test_pre_survives_newlines():
    assert md_pre("строка1\nстрока2") == "```\nстрока1\nстрока2\n```"


def test_markdown_mode_is_sent_by_default():
    bot = _bot([{"ok": True}])
    assert bot.send("привет") is True
    assert bot.session.calls[0]["parse_mode"] == "MarkdownV2"


def test_rejected_markup_is_resent_as_plain_text():
    bot = _bot([
        {"ok": False, "description": "Bad Request: can't parse entities"},
        {"ok": True},
    ])
    assert bot.send("S4_active") is True
    assert len(bot.session.calls) == 2
    assert "parse_mode" not in bot.session.calls[1]
    assert bot.session.calls[1]["text"] == "S4_active"


def test_other_errors_do_not_loop():
    bot = _bot([{"ok": False, "description": "Forbidden: bot was blocked"}])
    assert bot.send("привет") is False
    assert len(bot.session.calls) == 1
