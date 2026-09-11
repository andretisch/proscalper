#!/usr/bin/env bash
# Запуск с автоперезапуском.
#
# Сторож завершает процесс кодом 75, когда цикл перестаёт отвечать
# (зависание на сетевом вызове через прокси). Без обёртки такой выход
# означал бы просто остановку торговли.

set -uo pipefail
cd "$(dirname "$0")/.."

LOG_DIR="logs"
mkdir -p "$LOG_DIR"

while true; do
    echo "$(date -Is) запуск ProScalp" | tee -a "$LOG_DIR/supervisor.log"
    PYTHONPATH=. PYTHONUNBUFFERED=1 .venv/bin/python -m app
    code=$?
    if [ $code -eq 0 ]; then
        echo "$(date -Is) штатное завершение" | tee -a "$LOG_DIR/supervisor.log"
        break
    fi
    echo "$(date -Is) выход с кодом $code, перезапуск через 10с" | tee -a "$LOG_DIR/supervisor.log"
    sleep 10
done
