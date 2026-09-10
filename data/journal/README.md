# Журнал трейдера (реализация)

Фиксируем **вход/выход** и **историю стакана** рядом со сделкой. Без ИИ, без автоторговли — учёт для проверки playbook.

## Где лежит

| Путь | Что |
|------|-----|
| `journal/` | Код (SQLite + CLI) |
| `data/journal/journal.sqlite3` | БД (создаётся командой `init`) |
| `data/journal/examples/` | Пример JSON стакана |

## Быстрый старт

```bash
# из корня репо
python3 -m journal init

# открыть сделку (сетап S2 = отскок от плотности)
python3 -m journal open \
  --symbol BTCUSDT --side short --setup S2 \
  --price 21800 --size 0.1 --stop 21805 \
  --regime range --notes "от лимитки на 21800"

# привязать снимок стакана к сделке
python3 -m journal book --file data/journal/examples/orderbook_sample.json --trade-id 1

# добор / частичная фиксация
python3 -m journal add --id 1 --kind reduce --price 21750 --size 0.05 --note "partial impulse"

# закрыть
python3 -m journal close --id 1 --price 21720 --reason "impulse_done"

# карточка и статистика
python3 -m journal show 1
python3 -m journal stats
python3 -m journal list --status closed
```

## Что хранится

### `trades`
Символ, сторона, `setup_id` (S1…S6), режим рынка, вход, размер, стоп/тейк, выход, PnL $, PnL в R, чеклист JSON, заметки.

### `trade_fills`
Частичные входы/выходы (`entry`, `add`, `reduce`, `exit`).

### `orderbook_snapshots`
Полный L2 (`bids_json` / `asks_json`) + поля стенки: сторона, цена, размер, возраст, `%` остатка. Можно привязать к `trade_id` или писать отдельно.

### `trade_events`
Таймлайн: open, fill_*, orderbook, close.

## Формат JSON стакана

```json
{
  "symbol": "BTCUSDT",
  "exchange": "bybit",
  "market_type": "perp",
  "ts": "2026-09-10T10:00:00+00:00",
  "label": "before_entry",
  "bids": [{"price": 21800, "size": 12.5}],
  "asks": [{"price": 21800.5, "size": 0.9}],
  "wall": {
    "side": "bid",
    "price": 21800,
    "size": 12.5,
    "age_sec": 940,
    "remaining_pct": 100
  },
  "meta": {}
}
```

Снимки лучше делать минимум: **до входа**, **в момент входа**, **при изменении стенки**, **на выходе**.

## Связь с playbook

`setup_id` мапится на `data/playbook/SETUPS.md`.  
Стакан-поля — на `DATA_SPEC.md` (`wall_age`, `remaining_pct`, spot/perp).

## Дальше (ещё не сделано)

- Автосъём стакана с биржи по WebSocket
- UI / экспорт CSV
- Автозаполнение чеклиста сетапа
