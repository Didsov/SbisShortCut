# Telegram-бот поиска ККТ по ИНН

Отдельный проект Telegram-бота, который получает ККТ владельца по ИНН напрямую
из СБИС. Локальной базы данных и поиска по РНМ или заводскому номеру нет.

## Настройка

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Заполните `.env`:

- `TELEGRAM_BOT_TOKEN` — токен, полученный у BotFather;
- `TELEGRAM_ADMIN_USER_IDS` — Telegram ID администраторов через запятую;
- `TELEGRAM_ALLOWED_USER_IDS` — необязательные постоянные пользователи;
- `SBIS_COOKIES` — актуальная строка Cookie авторизованной сессии СБИС;

`.env` исключён из Git. Не добавляйте токены и Cookie в исходный код.

## Запуск

```powershell
.venv\Scripts\python.exe bot.py
```

Команды бота: `/start`, `/inn`, `/whoami`. Если пользователя нет в белом
списке, бот возвращает его Telegram ID и не выполняет запрос к СБИС.
Администраторы добавляют пользователей командой `/allow USER_ID`, удаляют
командой `/deny USER_ID` и просматривают список командой `/whitelist`.

## Проверка

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Развёртывание на сервере

Пошаговая установка под systemd и пользователя `inntophone` описана в
[DEPLOYMENT.md](DEPLOYMENT.md).
