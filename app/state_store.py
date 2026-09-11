"""Состояние рантайма, которое обязано переживать перезапуск.

Сторож перезапускает процесс при обрывах прокси, и без этого слоя дневной
риск обнулялся вместе с памятью: после трёх перезапусков бот мог потерять
втрое больше, чем разрешает DAILY_LOSS_LIMIT_PCT. Сделки лежат в журнале и
подхватываются, а счётчики дня — нет, поэтому храним их рядом.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.logging_setup import get_logger

log = get_logger("state")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_state (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class StateStore:
    """Ключ-значение в том же SQLite, где журнал.

    Соединение открывается на каждую операцию: обращения идут из торгового
    цикла и из потока Telegram, а одно sqlite-соединение между потоками
    делить нельзя.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def load(self, key: str) -> dict[str, Any] | None:
        try:
            with self._lock, self._connect() as conn:
                row = conn.execute(
                    "SELECT value_json FROM runtime_state WHERE key = ?", (key,)
                ).fetchone()
        except Exception:
            log.exception("не удалось прочитать состояние %s", key)
            return None
        if not row:
            return None
        try:
            data = json.loads(row["value_json"])
        except Exception:
            log.warning("состояние %s повреждено, игнорирую", key)
            return None
        return data if isinstance(data, dict) else None

    def save(self, key: str, payload: dict[str, Any]) -> None:
        """Не бросает исключений: сбой записи не должен рвать торговый цикл."""
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO runtime_state (key, value_json, updated_at) "
                    "VALUES (?, ?, datetime('now')) "
                    "ON CONFLICT(key) DO UPDATE SET "
                    "value_json = excluded.value_json, "
                    "updated_at = excluded.updated_at",
                    (key, json.dumps(payload, ensure_ascii=False)),
                )
        except Exception:
            log.exception("не удалось сохранить состояние %s", key)
