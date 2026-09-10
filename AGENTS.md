# ProScalp — AGENTS.md

## Цель

Полный автоскальп Bybit в стиле playbook S1–S5: данные стакана → сигналы → ИИ-решение → paper/live → журнал. Управление через Telegram.

## Структура

| Путь | Роль |
|------|------|
| `app/` | Рантайм: Bybit, сигналы, Ollama, Telegram, paper |
| `journal/` | Журнал сделок CLI/API |
| `data/playbook/` | Канонические сетапы |
| `data/orderbook/` | Накопление стакана (локально) |
| `source/` | Исходные субтитры |
| `.env` | Секреты (не коммитить) |

## Запуск

```bash
.venv/bin/python scripts/smoke_test.py
.venv/bin/python -m app
```

## Правила

- Секреты не коммитить и не светить в ответах.
- Язык — русский.
- Код по задаче пользователя; paper по умолчанию.
