"""Логирование в файл с ротацией.

Бот уже один раз молча завис на сетевом запросе через прокси: процесс был
жив, циклы не шли, в stdout ничего не попало из-за буферизации. Файловый лог
с принудительным сбросом — единственный способ увидеть такие обрывы постфактум.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "proscalp"
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


class _FlushingFileHandler(RotatingFileHandler):
    """Пишет на диск сразу: при зависании буфер до диска не доедет."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def setup_logging(
    root: Path, level: int = logging.INFO, max_bytes: int = 10_000_000, backups: int = 5
) -> logging.Logger:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    if logger.handlers:
        return logger

    formatter = logging.Formatter(_FORMAT)

    file_handler = _FlushingFileHandler(
        log_dir / "proscalp.log", maxBytes=max_bytes, backupCount=backups
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    # Сетевые библиотеки шумят на каждый запрос, но их предупреждения о
    # разрывах соединения нужны — оставляем только уровень WARNING.
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
        logging.getLogger(noisy).addHandler(file_handler)

    return logger


def get_logger(suffix: str = "") -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}" if suffix else LOGGER_NAME)
