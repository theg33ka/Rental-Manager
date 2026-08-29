# Карта проекта

## Основные границы

| Путь | Ответственность |
| --- | --- |
| `rental_manager/main.py` | FastAPI startup, routes, API composition и background scheduling |
| `rental_manager/models.py` | SQLAlchemy-сущности и persisted business state |
| `rental_manager/database.py` | URL normalization, engine/session, SQLite bootstrap и PostgreSQL connection policy |
| `rental_manager/services/billing.py` | Начисления аренды и коммунальные расчёты |
| `rental_manager/services/payment_profiles.py` | Единое разрешение реквизитов: квартира → объект → global fallback |
| `services/payment_*`, `receipt_*`, `tenant_debts.py` | Распределение оплат, чеки и задолженность жильца |
| `services/owner_operations.py`, `agent_protocol.py` | Разрешённые owner actions и нормализация agent envelope |
| `services/telegram_bot.py` | Telegram commands/webhook integration |
| `services/hermes` | Domain events, operational cases, briefing, reminders, memory, skills, safety и runtime |
| `security` | PIN, sessions и secret handling |
| `static` | Browser UI и API client modules |
| `android/RentalManager` | Нативный Java-клиент общего backend API |
| `migrations/versions` | Forward-only Alembic schema/data migrations |
| `tests`, `tests/e2e` | Python regression suite и Playwright smoke |

## Потоки данных

```text
Web / Android / Telegram -> FastAPI auth + validation -> shared backend operation
                          -> SQLAlchemy transaction -> business tables
business change -> DomainEvent -> Hermes reconciliation -> OperationalCase/Briefing
free text -> scoped context -> LLM envelope -> backend validation/safety -> proposal or action
Apartment -> override PaymentProfile -> object default PaymentProfile -> global settings fallback
```

Финансовые таблицы, договоры, сообщения и owner operations являются источником истины. Hermes cases, briefings и memory производны и должны reconciliate состояние, а не подменять его.

## Контракты клиентов

- Web и Android читают `/api/bootstrap`, `/api/app-state` и общие API operations.
- Hermes web/Android endpoints являются представлениями одного backend слоя.
- Telegram callback и текстовый интерфейс не обходят authorization, TTL, idempotency или confirmation.
- Новый endpoint сначала получает backend test и совместимый контракт; затем обновляются web/Android/Telegram consumers.
- Продуктовые возможности, UI coverage и полный backend reference описаны в `PRODUCT_UI_CAPABILITY_MAP.md` и `API_BACKEND_REFERENCE.md`.

`main.py` остаётся крупным composition hotspot. Новую повторно используемую бизнес-логику размещайте в `services`, но не выполняйте широкую механическую декомпозицию без отдельной задачи и regression coverage.
