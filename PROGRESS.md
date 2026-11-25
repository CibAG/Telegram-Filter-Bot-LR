# PROGRESS

## Выполнено (НЕ ПОВТОРЯТЬ)

| # | Задача | Файлы |
|---|--------|-------|
| 1 | Изучение всего кода проекта | `app/`, `pyproject.toml`, `README.md`, `start_bot.bat`, `.gitignore`, `.opencode/skills/python-dev/SKILL.md` |
| 2 | Создание `AGENTS.md` и `PROGRESS.md` | `AGENTS.md`, `PROGRESS.md` |
| 3 | Запись правил в `AGENTS.md` (язык, границы, инфраструктура GitHub/PythonAnywhere, цикл работы) | `AGENTS.md` |
| 4 | Создание `requirements.txt` для деплоя на PythonAnywhere | `requirements.txt` |
| 5 | Подготовка проекта к размещению в GitHub-репозитории | `.gitignore`, `requirements.txt`, `start_bot.bat` |

## В работе

| # | Задача | Статус |
|---|--------|--------|
| 6 | Размещение бота в репозитории `https://github.com/CibAG/Telegram-Filter-Bot-LR.git` | ожидает ввода |

## Ключевые решения и ограничения

- Бот: aiogram 3 + SQLAlchemy 2 (async) + SQLite + OpenRouter; управление только в личке админа.
- Секреты: `.env` в `.gitignore`, чтение через `os.getenv`/`python-dotenv`.
- БД: SQLite до 512 МБ, путь через `pathlib.Path(__file__).resolve().parent`, `PRAGMA journal_mode=WAL`, busy_timeout.
- Деплой: бесплатный PythonAnywhere (stepbiz) — polling с автопереподключением, прокси `PROXY_URL` (`http://proxy.server:3128` там, пусто локально), Allowlist ограничения.
- Пути: только относительные через `pathlib.Path(__file__).parent`; `requirements.txt` актуальный.
- После правок кода: самопроверка запуска/импортов → обновление `PROGRESS.md` → стоп для ручного теста.
