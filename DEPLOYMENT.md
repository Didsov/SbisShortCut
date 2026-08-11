# Развёртывание SbisShortCut на Linux-сервере

Инструкция рассчитана на сервер Ubuntu/Debian, где административные команды
выполняются от `root`, а боты работают от непривилегированного пользователя
`inntophone`. Для SbisShortCut используется отдельный каталог и отдельная
systemd-служба, поэтому два уже установленных бота затрагиваться не будут.

## 1. Установка системных пакетов

Выполнить от `root`:

```bash
apt update
apt install -y git python3 python3-venv
install -d -o inntophone -g inntophone /home/inntophone/bots
```

## 2. Клонирование проекта

Для публичного репозитория:

```bash
sudo -u inntophone -H git clone \
  https://github.com/Didsov/SbisShortCut.git \
  /home/inntophone/bots/SbisShortCut
```

Если репозиторий приватный, сначала настройте для пользователя `inntophone`
SSH-ключ GitHub, затем используйте адрес:

```bash
sudo -u inntophone -H git clone \
  git@github.com:Didsov/SbisShortCut.git \
  /home/inntophone/bots/SbisShortCut
```

## 3. Виртуальное окружение

```bash
cd /home/inntophone/bots/SbisShortCut
sudo -u inntophone -H python3 -m venv .venv
sudo -u inntophone -H .venv/bin/python -m pip install --upgrade pip
sudo -u inntophone -H .venv/bin/python -m pip install -r requirements.txt
```

## 4. Настройка `.env`

Создайте локальный файл от пользователя `inntophone`:

```bash
sudo -u inntophone -H cp .env.example .env
sudo -u inntophone -H nano .env
chmod 600 .env
chown inntophone:inntophone .env
```

Заполните значения:

```dotenv
TELEGRAM_BOT_TOKEN=токен_бота
TELEGRAM_ALLOWED_USER_IDS=telegram_id_1,telegram_id_2
SBIS_COOKIES=актуальная_строка_cookie
KKT_BOT_LOG_PATH=
```

Пустой `KKT_BOT_LOG_PATH` оставляет журнал в systemd/journald. `.env` исключён
из Git. Не копируйте токен или Cookie в unit-файл и не публикуйте их в логах.

## 5. Проверка до запуска

```bash
cd /home/inntophone/bots/SbisShortCut
sudo -u inntophone -H .venv/bin/python -m unittest discover -s tests -v
sudo -u inntophone -H .venv/bin/python -m py_compile \
  bot.py lookup.py excel_export.py services/live_collector.py
sudo -u inntophone -H .venv/bin/python -c \
  "from config.settings import load_settings; s=load_settings(); print('Настройки загружены, пользователей:', len(s.allowed_user_ids))"
```

Последняя команда не выводит токен и Cookie.

## 6. systemd-служба

От `root` выполните команду, которая создаст
`/etc/systemd/system/sbis-shortcut.service`:

```bash
cat > /etc/systemd/system/sbis-shortcut.service <<'EOF'
[Unit]
Description=SbisShortCut Telegram bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=inntophone
Group=inntophone
WorkingDirectory=/home/inntophone/bots/SbisShortCut
ExecStart=/home/inntophone/bots/SbisShortCut/.venv/bin/python -u bot.py
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
```

Проверьте синтаксис unit-файла:

```bash
systemd-analyze verify /etc/systemd/system/sbis-shortcut.service
```

Если команда не вывела ошибок, перечитайте конфигурацию systemd, добавьте
службу в автозапуск и сразу запустите её:

```bash
systemctl daemon-reload
systemctl enable --now sbis-shortcut.service
systemctl status sbis-shortcut.service --no-pager
```

Проверить, включён ли автозапуск:

```bash
systemctl is-enabled sbis-shortcut.service
systemctl is-active sbis-shortcut.service
```

После успешного запуска обе команды должны вывести соответственно `enabled`
и `active`.

Одновременно должен работать только один экземпляр этого Telegram-бота. Не
запускайте `bot.py` вручную, пока активна `sbis-shortcut.service`.

## 7. Журнал и диагностика

Последние записи:

```bash
journalctl -u sbis-shortcut.service -n 100 --no-pager
```

Наблюдение в реальном времени:

```bash
journalctl -u sbis-shortcut.service -f
```

Если СБИС перестал возвращать данные, в первую очередь проверьте срок действия
`SBIS_COOKIES` в `.env`, затем перезапустите службу:

```bash
systemctl restart sbis-shortcut.service
```

## 8. Обновление

```bash
systemctl stop sbis-shortcut.service
cd /home/inntophone/bots/SbisShortCut
sudo -u inntophone -H git pull --ff-only
sudo -u inntophone -H .venv/bin/python -m pip install -r requirements.txt
sudo -u inntophone -H .venv/bin/python -m unittest discover -s tests -v
systemctl start sbis-shortcut.service
systemctl status sbis-shortcut.service --no-pager
```

Остановка и отключение только этого бота:

```bash
systemctl disable --now sbis-shortcut.service
```
