# Telegram-бот поиска ККТ по ИНН

Отдельный проект Telegram-бота, который получает ККТ владельца по ИНН из
СБИС и сохраняет результат в локальную SQLite-базу. Поиска по РНМ и заводскому
номеру в боте нет.

## Настройка

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Заполните `.env`:

- `TELEGRAM_BOT_TOKEN` — токен, полученный у BotFather;
- `TELEGRAM_ADMIN_USER_IDS` — Telegram ID администраторов через запятую;
- `SBIS_COOKIES` — актуальная строка Cookie авторизованной сессии СБИС;
- `KKT_DATABASE` — путь к отдельной базе бота.

`.env` исключён из Git. Не добавляйте токены и Cookie в исходный код.

## Запуск

```powershell
.venv\Scripts\python.exe bot.py
```

Первый запуск создаёт структуру SQLite. Команды бота: `/start`, `/inn`,
`/whoami`; администратору также доступны `/allow`, `/deny`, `/whitelist`.

## Проверка

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

