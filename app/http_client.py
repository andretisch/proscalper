"""Общий HTTP-слой: прокси, таймауты, ретраи.

Прокси рвёт и подвешивает соединения — это норма, а не исключение. Поэтому
каждый запрос ограничен по времени, повторяется при обрыве и никогда не
может утащить торговый цикл за лимит сторожа.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import TYPE_CHECKING, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.logging_setup import get_logger, redact

if TYPE_CHECKING:
    from app.config import Settings

log = get_logger("http")


def requests_proxies(settings: Settings) -> dict[str, str] | None:
    if settings.proxy_url:
        return {"http": settings.proxy_url, "https": settings.proxy_url}

    proxies: dict[str, str] = {}
    if settings.http_proxy:
        proxies["http"] = settings.http_proxy
    if settings.https_proxy:
        proxies["https"] = settings.https_proxy
    return proxies or None


def apply_proxy_env(settings: Settings) -> None:
    """Expose proxy settings to libraries that read HTTP_PROXY/HTTPS_PROXY."""
    if settings.proxy_url:
        os.environ.setdefault("HTTP_PROXY", settings.proxy_url)
        os.environ.setdefault("HTTPS_PROXY", settings.proxy_url)
        os.environ.setdefault("ALL_PROXY", settings.proxy_url)
    else:
        if settings.http_proxy:
            os.environ.setdefault("HTTP_PROXY", settings.http_proxy)
        if settings.https_proxy:
            os.environ.setdefault("HTTPS_PROXY", settings.https_proxy)
    if settings.no_proxy:
        os.environ.setdefault("NO_PROXY", settings.no_proxy)


def install_socket_backstop(timeout_sec: float) -> None:
    """Страховка на случай, когда таймаут requests не срабатывает.

    Таймаут requests ограничивает паузу между байтами уже установленного
    соединения. CONNECT к прокси и TLS-рукопожатие проходят мимо него, и
    поток может висеть часами — именно так выглядели зависания в логе.
    """
    if timeout_sec > 0:
        socket.setdefaulttimeout(timeout_sec)


def make_session(settings: Settings) -> requests.Session:
    session = requests.Session()
    session.trust_env = True
    proxies = requests_proxies(settings)
    if proxies:
        session.proxies.update(proxies)
    # Повторы по коду ответа и по таймауту чтения разрешены только для GET:
    # POST может создать сообщение или ордер, и повтор после потерянного
    # ответа задвоит его. Оборванное соединение urllib3 повторяет для любого
    # метода — там запрос до сервера, как правило, не доехал.
    retry = Retry(
        total=2,
        connect=2,
        read=1,
        status=2,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class SessionPool:
    """Свой Session на поток.

    requests.Session не потокобезопасен, а клиенты дёргаются одновременно
    из торгового цикла и из Telegram. Общий пул соединений означает, что
    25-секундный long-poll держит соединение, пока ответ на команду ждёт.
    """

    def __init__(
        self, settings: Settings, headers: dict[str, str] | None = None
    ) -> None:
        self._settings = settings
        self._headers = headers or {}
        self._local = threading.local()

    def get(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = make_session(self._settings)
            if self._headers:
                session.headers.update(self._headers)
            self._local.session = session
        return session


class BudgetExceeded(TimeoutError):
    """Время, отведённое на операцию, кончилось — запрос даже не начинали."""


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: tuple[float, float] | float,
    attempts: int = 3,
    deadline: float | None = None,
    label: str = "",
    **kwargs: Any,
) -> requests.Response:
    """Пережить моргание прокси, но не зависнуть.

    `deadline` — граница по стенным часам. Пропустить символ дешевле, чем
    утащить весь цикл за лимит сторожа и получить перезапуск процесса.
    """
    name = label or url
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        if deadline is not None and time.time() >= deadline:
            raise BudgetExceeded(f"{name}: бюджет времени исчерпан")
        started = time.time()
        try:
            response = session.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as e:
            last = e
            log.warning(
                "сеть %s (попытка %s/%s, %.1fс): %s",
                name,
                attempt,
                attempts,
                time.time() - started,
                redact(e)[:200],
            )
            if attempt == attempts:
                break
            pause = min(1.5 * attempt, 4.0)
            if deadline is not None and time.time() + pause >= deadline:
                break
            time.sleep(pause)
            continue
        elapsed = time.time() - started
        if elapsed > 10:
            log.warning("медленный ответ %s: %.1fс", name, elapsed)
        return response
    raise last if last is not None else RuntimeError(f"{name}: запрос не выполнен")
