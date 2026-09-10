# Спецификация данных для бота

Минимальный набор полей, без которого сетапы S1–S5 не формализуются.

## Order book (L2)
| Поле | Назначение | Сетапы |
|------|------------|--------|
| `bids[] / asks[]` price, size (spot + perp) | Плотность, стоп за стенкой | S2, S3, S4 |
| `wall.price`, `wall.size`, `wall.side` | Детект крупной лимитки | S2, S3 |
| `wall.age_sec` | Фильтр свежих заявок | S2 |
| `wall.remaining_pct`, `eat_rate` | Invalidate / flip | S2, S3 |
| `wall_moved` / `wall_cancelled` | Flatten, не trail | все density |
| `round_number` proximity | Усиление сетапа | S1, S2 |
| `spread`, `depth_score` | Size / skip | все |

## Уровни и структура
| Поле | Назначение | Сетапы |
|------|------------|--------|
| `level.price`, `touch_count`, `touch_times` | A+ уровень | S1, S4 |
| `level.kind` (equal_high/low, flag, range) | Тип структуры | S1, S4, S5 |
| `consolidation.width`, `squeeze` | Поджатие | S1, S4 |
| `breakout_trigger` | Add на пробое | S1, S4, S5 |
| `approach_velocity` | Skip резких заходов | S1, S4 |

## Импульс и лента
| Поле | Назначение | Сетапы |
|------|------------|--------|
| `impulse_pct` / `impulse_in_sec` | Confirm vs пиление | S1, S4, S5 |
| `tape_imbalance`, aggression | Разворот / разъедание | S2, S3 |
| `volume_spike` | Confirm | все |

## Контекст инструмента
| Поле | Назначение | Сетапы |
|------|------------|--------|
| `ret_24h`, `rvol`, `listing_age_hours` | In-play / drain | S4, S5 |
| `in_play_score` | Watchlist priority | S4, S5 |
| `regime` (`range` / `trend`) | Выбор семейства сетапов | все |
| `liquidity_ok` | Gate | все |

## Позиция и сессия
| Поле | Назначение |
|------|------------|
| `size`, `side`, `entry`, `stop`, `be_flag` | Исполнение |
| `hold_time_sec`, `partials[]` | Выходы |
| `daily_pnl`, `consec_losses` | Kill-switch |
| `heartbeat_ok` | Безопасность |
| `focus_symbols[]` (1–2) | Анти-мультитикер |

## События / алерты (минимум)
1. Цена подошла к уровню / стенке  
2. Пробой уровня  
3. Стенку едят (`eat_rate` выше порога)  
4. Стенку сняли / переставили  
5. Импульс после входа / отсутствие импульса (таймер)  
6. Heartbeat lost  

## Порядок внедрения (рекомендуется)
1. Paper: только алерты по S1/S2 checklist (без ордеров)  
2. Semi-auto: человек подтверждает вход, бот ставит стоп/BE/time-exit  
3. Auto: S1+S2 на 1–2 символах с жёсткими `FORBIDDEN`  
4. Затем S3 flip и S4 in-play screener  
