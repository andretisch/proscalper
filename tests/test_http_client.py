"""Сеть моргает — это норма. Зависать при этом нельзя."""

from __future__ import annotations

import time

import pytest
import requests

from app.http_client import BudgetExceeded, redact, request_with_retry


class _Session:
    """Отдаёт заготовленные исходы по порядку: исключение или ответ."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def request(self, method: str, url: str, **kwargs):
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_transient_error_is_retried():
    session = _Session([requests.ConnectionError("прокси умер"), "ok"])
    assert request_with_retry(session, "GET", "u", timeout=1, attempts=3) == "ok"
    assert session.calls == 2


def test_gives_up_after_attempts():
    session = _Session([requests.ConnectionError("прокси умер")])
    with pytest.raises(requests.ConnectionError):
        request_with_retry(session, "GET", "u", timeout=1, attempts=2)
    assert session.calls == 2


def test_deadline_blocks_request_before_it_starts():
    session = _Session(["ok"])
    with pytest.raises(BudgetExceeded):
        request_with_retry(
            session, "GET", "u", timeout=1, deadline=time.time() - 1, label="стакан"
        )
    assert session.calls == 0


def test_deadline_stops_retry_loop_early():
    # Бюджета хватает на первый запрос, но не на паузу перед вторым.
    session = _Session([requests.ConnectionError("обрыв")])
    started = time.time()
    with pytest.raises(requests.ConnectionError):
        request_with_retry(
            session,
            "GET",
            "u",
            timeout=1,
            attempts=5,
            deadline=time.time() + 0.2,
        )
    assert session.calls == 1
    assert time.time() - started < 1.0


def test_token_is_not_written_to_logs():
    url = "https://api.telegram.org/bot405270881:AAHdZWuGRNKLR0P1LD0ct/sendMessage"
    assert "405270881" not in redact(f"Ошибка на {url}")
    assert "/bot<токен>" in redact(url)
