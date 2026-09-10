"""Runtime configuration from .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _float(v: str | None, default: float) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    root: Path
    mode: str
    telegram_token: str
    telegram_chat_id: str
    bybit_key: str
    bybit_secret: str
    bybit_testnet: bool
    ollama_key: str
    ollama_model: str
    ollama_host: str
    deposit_usdt: float
    leverage: int
    risk_per_trade_pct: float
    daily_loss_limit_pct: float
    playbook_version: str
    email_login: str
    email_password: str
    email_smtp_host: str
    email_smtp_port: int
    email_from: str
    email_to: str
    proxy_url: str
    http_proxy: str
    https_proxy: str
    no_proxy: str
    paper_max_hold_sec: int
    paper_min_hold_sec: int
    paper_fee_buffer_mult: float

    @property
    def proxy_enabled(self) -> bool:
        return bool(self.proxy_url or self.http_proxy or self.https_proxy)

    @property
    def bybit_base(self) -> str:
        if self.bybit_testnet:
            return "https://api-testnet.bybit.com"
        return "https://api.bybit.com"

    @property
    def soft_pause_pct(self) -> float:
        return self.daily_loss_limit_pct * 0.5


def load_settings() -> Settings:
    settings = Settings(
        root=ROOT,
        mode=os.getenv("MODE", "paper").strip().lower(),
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        bybit_key=os.getenv("BYBIT_API_KEY", "").strip(),
        bybit_secret=os.getenv("BYBIT_API_SECRET", "").strip(),
        bybit_testnet=_bool(os.getenv("BYBIT_TESTNET"), True),
        ollama_key=os.getenv("OLLAMA_API_KEY", "").strip(),
        ollama_model=os.getenv("OLLAMA_MODEL", "gemma4:cloud").strip(),
        ollama_host=os.getenv("OLLAMA_HOST", "https://ollama.com").rstrip("/"),
        deposit_usdt=_float(os.getenv("DEPOSIT_USDT"), 100.0),
        leverage=int(_float(os.getenv("LEVERAGE"), 3)),
        risk_per_trade_pct=_float(os.getenv("RISK_PER_TRADE_PCT"), 0.5),
        daily_loss_limit_pct=_float(os.getenv("DAILY_LOSS_LIMIT_PCT"), 2.0),
        playbook_version=os.getenv("PLAYBOOK_VERSION", "0.1").strip(),
        email_login=os.getenv("EMAIL_LOGIN", "").strip(),
        email_password=os.getenv("EMAIL_PASSWORD", "").strip(),
        email_smtp_host=os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com").strip(),
        email_smtp_port=int(_float(os.getenv("EMAIL_SMTP_PORT"), 587)),
        email_from=os.getenv("EMAIL_FROM", "").strip(),
        email_to=os.getenv("EMAIL_TO", "").strip(),
        proxy_url=os.getenv("PROXY_URL", "").strip(),
        http_proxy=os.getenv("HTTP_PROXY", "").strip(),
        https_proxy=os.getenv("HTTPS_PROXY", "").strip(),
        no_proxy=os.getenv("NO_PROXY", "").strip(),
        paper_max_hold_sec=int(_float(os.getenv("PAPER_MAX_HOLD_SEC"), 120)),
        paper_min_hold_sec=int(_float(os.getenv("PAPER_MIN_HOLD_SEC"), 15)),
        paper_fee_buffer_mult=_float(os.getenv("PAPER_FEE_BUFFER_MULT"), 3.0),
    )
    from app.http_client import apply_proxy_env

    apply_proxy_env(settings)
    return settings
