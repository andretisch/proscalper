"""Shared HTTP session with optional proxy support."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from app.config import Settings


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


def make_session(settings: Settings) -> requests.Session:
    session = requests.Session()
    session.trust_env = True
    proxies = requests_proxies(settings)
    if proxies:
        session.proxies.update(proxies)
    return session
