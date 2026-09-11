"""Логи должны доезжать до диска сразу — иначе зависание не увидеть."""

from __future__ import annotations

import logging

from app.logging_setup import LOGGER_NAME, get_logger, setup_logging


def _reset() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()


def test_secrets_are_redacted_in_file(tmp_path):
    _reset()
    try:
        setup_logging(tmp_path)
        get_logger("test").warning(
            "обрыв https://api.telegram.org/bot405270881:AAHdZWuGRN/sendMessage "
            "через http://bot:s3cret@vps1001.example.ru:3128"
        )
        written = (tmp_path / "logs" / "proscalp.log").read_text()
        assert "405270881" not in written
        assert "s3cret" not in written
        assert "/bot<токен>" in written
    finally:
        _reset()


def test_creates_log_file(tmp_path):
    _reset()
    try:
        setup_logging(tmp_path)
        get_logger("test").info("проверка записи")
        log_file = tmp_path / "logs" / "proscalp.log"
        assert log_file.exists()
        assert "проверка записи" in log_file.read_text()
    finally:
        _reset()


def test_written_without_waiting_for_buffer(tmp_path):
    _reset()
    try:
        setup_logging(tmp_path)
        log = get_logger("test")
        log.warning("обрыв соединения")
        # читаем сразу, без закрытия хендлеров
        assert "обрыв соединения" in (tmp_path / "logs" / "proscalp.log").read_text()
    finally:
        _reset()


def test_setup_is_idempotent(tmp_path):
    _reset()
    try:
        first = setup_logging(tmp_path)
        second = setup_logging(tmp_path)
        assert first is second
        assert len(first.handlers) == 2
    finally:
        _reset()
