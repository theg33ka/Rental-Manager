# Онбординг агента

## Требования

- Python 3.11+;
- Node.js/npm для JS и Playwright-проверок;
- PostgreSQL для production-like migration/integration проверки, SQLite допустим для локального smoke;
- JDK 17 и Android SDK 35 только для нативного клиента.

## Чистый checkout

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
npm.cmd ci
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn rental_manager.main:app --reload --host 127.0.0.1 --port 8000
```

Откройте `http://127.0.0.1:8000`; health-check — `/healthz`. Без `RENTAL_MANAGER_DATABASE_URL` используется локальная игнорируемая SQLite-база `data/rental_manager.db`.

Не используйте реальные PIN, Telegram/DeepSeek credentials и production export для обычной разработки. Названия переменных находятся в `.env.example`, правила — в [Доступах](ACCESS_AND_SECRETS.md).

## Быстрая ориентация

- `rental_manager/main.py` — FastAPI composition root и большая часть HTTP routes.
- `models.py` и `database.py` — SQLAlchemy model/source-of-truth и соединение.
- `services` — расчёты аренды, оплат, чеков, Telegram и AI operations.
- `services/hermes` — события, cases, memory, briefing, reminders, skills и safety/runtime.
- `static` — web-клиент; `android/RentalManager` — отдельный нативный Java-клиент.
- `migrations/versions` — линейная Alembic-история; текущий head указан в [Current state](CURRENT_STATE.md).

## Рабочий цикл

1. Проверьте Git status и выберите общий backend operation вместо дублирования логики в route/client.
2. Для изменения схемы или данных сначала прочитайте [Change and migration guide](CHANGE_AND_MIGRATION_GUIDE.md).
3. Напишите regression test для денег, дат, auth или safety до/вместе с исправлением.
4. Выполните целевые тесты и подходящий уровень [Тестирования](TESTING.md).
5. Проверьте diff на секреты, exports, PDF, SQLite-файлы, APK и generated output.
6. Перед deployment следуйте [Operations runbook](OPERATIONS_RUNBOOK.md).

Передача задачи перечисляет изменённые файлы, Alembic revision, фактически выполненные проверки, непроверенные внешние интеграции и требуемое действие при deployment.
