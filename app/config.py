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
    paper_fee_buffer_mult: float
    max_seconds_without_impulse: int
    max_parallel_symbols: int
    max_consecutive_losses: int
    market_data_mainnet: bool
    wall_max_dist_pct: float
    taker_fee_rate_override: float
    symbol_cooldown_sec: int
    book_depth_limit: int
    wall_scan_pct: float
    wall_min_dist_pct: float
    wall_min_depth_share: float
    wall_min_ratio: float
    wall_min_observations: int
    wall_min_age_sec: float
    wall_min_held_share: float
    wall_approach_pct: float
    min_rr: float
    room_window_sec: float
    min_turnover_usd: float
    min_range24_pct: float
    max_fee_share_of_risk: float
    watchdog_timeout_sec: float
    log_level: str
    http_connect_timeout_sec: float
    http_read_timeout_sec: float
    http_retry_attempts: int
    socket_timeout_sec: float
    cycle_budget_sec: float
    orderbook_snapshot_interval_sec: float
    orderbook_retention_days: float

    @property
    def http_timeout(self) -> tuple[float, float]:
        return (self.http_connect_timeout_sec, self.http_read_timeout_sec)

    @property
    def proxy_enabled(self) -> bool:
        return bool(self.proxy_url or self.http_proxy or self.https_proxy)

    @property
    def bybit_base(self) -> str:
        if self.bybit_testnet:
            return "https://api-testnet.bybit.com"
        return "https://api.bybit.com"

    @property
    def bybit_public_base(self) -> str:
        """Источник рыночных данных: стакан testnet синтетический и для сетапов непригоден."""
        if self.market_data_mainnet:
            return "https://api.bybit.com"
        return self.bybit_base

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
        paper_fee_buffer_mult=_float(os.getenv("PAPER_FEE_BUFFER_MULT"), 3.0),
        max_seconds_without_impulse=int(
            _float(os.getenv("MAX_SECONDS_WITHOUT_IMPULSE"), 300)
        ),
        max_parallel_symbols=int(_float(os.getenv("MAX_PARALLEL_SYMBOLS"), 2)),
        max_consecutive_losses=int(_float(os.getenv("MAX_CONSECUTIVE_LOSSES"), 3)),
        market_data_mainnet=_bool(os.getenv("MARKET_DATA_MAINNET"), True),
        wall_max_dist_pct=_float(os.getenv("WALL_MAX_DIST_PCT"), 0.45),
        taker_fee_rate_override=_float(os.getenv("TAKER_FEE_RATE"), 0.00055),
        symbol_cooldown_sec=int(_float(os.getenv("SYMBOL_COOLDOWN_SEC"), 600)),
        book_depth_limit=int(_float(os.getenv("BOOK_DEPTH_LIMIT"), 200)),
        wall_scan_pct=_float(os.getenv("WALL_SCAN_PCT"), 0.6),
        wall_min_dist_pct=_float(os.getenv("WALL_MIN_DIST_PCT"), 0.02),
        wall_min_depth_share=_float(os.getenv("WALL_MIN_DEPTH_SHARE"), 0.15),
        wall_min_ratio=_float(os.getenv("WALL_MIN_RATIO"), 8.0),
        wall_min_observations=int(_float(os.getenv("WALL_MIN_OBSERVATIONS"), 2)),
        wall_min_age_sec=_float(os.getenv("WALL_MIN_AGE_SEC"), 45.0),
        wall_min_held_share=_float(os.getenv("WALL_MIN_HELD_SHARE"), 0.6),
        wall_approach_pct=_float(os.getenv("WALL_APPROACH_PCT"), 0.12),
        min_rr=_float(os.getenv("MIN_RR"), 1.5),
        room_window_sec=_float(os.getenv("ROOM_WINDOW_SEC"), 900.0),
        min_turnover_usd=_float(os.getenv("MIN_TURNOVER_USD"), 20_000_000.0),
        min_range24_pct=_float(os.getenv("MIN_RANGE24_PCT"), 3.0),
        max_fee_share_of_risk=_float(os.getenv("MAX_FEE_SHARE_OF_RISK"), 0.20),
        watchdog_timeout_sec=_float(os.getenv("WATCHDOG_TIMEOUT_SEC"), 600.0),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        http_connect_timeout_sec=_float(os.getenv("HTTP_CONNECT_TIMEOUT_SEC"), 6.0),
        http_read_timeout_sec=_float(os.getenv("HTTP_READ_TIMEOUT_SEC"), 15.0),
        http_retry_attempts=int(_float(os.getenv("HTTP_RETRY_ATTEMPTS"), 3)),
        socket_timeout_sec=_float(os.getenv("SOCKET_TIMEOUT_SEC"), 90.0),
        cycle_budget_sec=_float(os.getenv("CYCLE_BUDGET_SEC"), 240.0),
        orderbook_snapshot_interval_sec=_float(
            os.getenv("ORDERBOOK_SNAPSHOT_INTERVAL_SEC"), 15.0
        ),
        orderbook_retention_days=_float(os.getenv("ORDERBOOK_RETENTION_DAYS"), 0.0),
    )
    from app.http_client import apply_proxy_env, install_socket_backstop

    apply_proxy_env(settings)
    install_socket_backstop(settings.socket_timeout_sec)
    return settings
