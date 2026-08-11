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
- `TELEGRAM_ALLOWED_USER_IDS` — разрешённые Telegram ID через запятую;
- `SBIS_COOKIES` — актуальная строка Cookie авторизованной сессии СБИС;

`.env` исключён из Git. Не добавляйте токены и Cookie в исходный код.

## Запуск

```powershell
.venv\Scripts\python.exe bot.py
```

Команды бота: `/start`, `/inn`, `/whoami`. Если список пуст или пользователя
в нём нет, бот возвращает его Telegram ID и не выполняет запрос к СБИС.

## Проверка

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Развёртывание на сервере

Пошаговая установка под systemd и пользователя `inntophone` описана в
[DEPLOYMENT.md](DEPLOYMENT.md).
