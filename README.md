# ProScalp

Автоскальп Bybit (paper/live) по сетапам S1–S5 + журнал + история стакана + Ollama + Telegram.

## Быстрый старт

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # заполнить ключи

# разовый smoke-тест (+ сообщение в Telegram)
PYTHONPATH=. .venv/bin/python scripts/smoke_test.py

# рабочий цикл
PYTHONPATH=. .venv/bin/python -m app
```

## Структура

```
app/                     # рантайм бота
journal/                 # журнал сделок
data/playbook/           # правила
data/reports/            # разбор видео
data/transcripts_clean/  # субтитры
data/orderbook/          # накопленная история стакана (gitignored)
source/                  # исходные SRT
```

## Telegram

`/status` `/scan` `/watchlist` `/pause` `/resume` `/help`

## Режимы

`MODE=paper` — только журнал «купил бы/закрыл бы».  
`MODE=live` — позже реальные ордера (сейчас исполнение paper).
