# Деплой на VPS

Проверено на 512 МБ / 1 ядро: бот занимает **~45 МБ RSS** и почти не грузит CPU
(цикл 5–10 с раз в 90 с). Узкое место не память, а диск — см. «Диск» ниже.

## Установка

```bash
# 1. Пользователь без прав root
sudo useradd -r -m -d /opt/proscalp -s /bin/bash proscalp

# 2. Код
sudo -u proscalp git clone https://github.com/andretisch/proscalper /opt/proscalp
cd /opt/proscalp

# 3. Окружение
sudo -u proscalp python3 -m venv .venv
sudo -u proscalp .venv/bin/pip install -r requirements.txt

# 4. Секреты
sudo -u proscalp cp .env.example .env
sudo -u proscalp nano .env          # ключи Bybit, Telegram, Ollama
sudo chmod 600 /opt/proscalp/.env

# 5. Проверка до запуска сервиса
sudo -u proscalp env PYTHONPATH=. .venv/bin/python scripts/smoke_test.py
```

Нужен **Python 3.11+**. На Debian 12 / Ubuntu 22.04+ идёт из коробки;
если `python3 -m venv` ругается, поставьте `python3-venv`.

## Сервис

```bash
sudo cp deploy/proscalp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now proscalp

systemctl status proscalp
tail -f /opt/proscalp/logs/proscalp.log
```

Юнит перезапускает бота всегда, кроме явного `systemctl stop`. Это важно:
сторож завершает процесс кодом 75 при зависании сетевого вызова, и без
`Restart=always` бот просто остался бы выключенным. `StartLimitIntervalSec=0`
снимает лимит systemd на частоту перезапусков — иначе после пяти обрывов
подряд сервис был бы заглушён насовсем.

`scripts/run_bot.sh` для systemd не нужен: он решает ту же задачу
перезапуска и пригодится только при ручном запуске в tmux.

## Диск

Главный расход — история стакана: **~7.5 КБ на снимок**. Фоновый поток
(`ORDERBOOK_SNAPSHOT_INTERVAL_SEC=15`) пишет в базу чаще торгового цикла
(90 с): при пяти символах perp + spot это **~40–45 МБ в сутки**.

На **6 ГБ свободного места** обрезка не нужна — оставьте
`ORDERBOOK_RETENTION_DAYS=0` (по умолчанию). Если диск маленький, задайте
число дней: чистка раз в час вместе с watchlist, с `VACUUM`.

Логи ограничены ротацией: 10 МБ × 6 файлов = максимум 60 МБ.

## Память

512 МБ хватает с запасом, но стоит помнить:

- `MemoryMax=256M` в юните — страховка, чтобы утечка не утянула SSH.
- Своп желателен: без него любой пик убивает процесс, а не тормозит его.

```bash
sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## Часы

Сторож считает простой по стенным часам, поэтому скачок времени он примет
за зависание. На VPS это лечится службой синхронизации:

```bash
sudo timedatectl set-ntp true
timedatectl status
```

Заморозку процесса целиком (пауза виртуалки, нехватка CPU) сторож теперь
отличает от зависания сам: если он проспал дольше запрошенного, счётчик
сбрасывается, а не убивает здоровый процесс.

## Обновление

```bash
cd /opt/proscalp
sudo -u proscalp git pull
sudo -u proscalp .venv/bin/pip install -r requirements.txt
sudo systemctl restart proscalp
```

Состояние переживает перезапуск: сделки лежат в журнале, дневной PnL,
паузы и cooldown — в таблице `runtime_state` того же SQLite.

## Что проверить после запуска

```bash
systemctl is-active proscalp
grep -E "старт:|цикл #" /opt/proscalp/logs/proscalp.log | tail -5
sudo -u proscalp env PYTHONPATH=. .venv/bin/python -m journal stats
du -sh /opt/proscalp/data/orderbook/history.sqlite3
```

В Telegram должно прийти «ProScalp запущен», а `/status` — ответить.
