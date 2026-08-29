# Индекс документации

Это точка входа для разработчиков и агентов. Сначала прочитайте `../AGENTS.md`.

## Начало работы

- [Онбординг агента](AGENT_ONBOARDING.md) — чистый checkout, локальный запуск и рабочий цикл.
- [Карта проекта](PROJECT_MAP.md) — владельцы кода, клиенты и потоки данных.
- [Текущее состояние](CURRENT_STATE.md) — версии, Alembic head и активные контуры.
- [Доступы и секреты](ACCESS_AND_SECRETS.md) — GitHub, Amvera, БД, панель, Telegram, DeepSeek и Android signing.
- [Продуктовая и UI-карта](PRODUCT_UI_CAPABILITY_MAP.md) — канонический перевод реальной системы на язык экранов, сценариев, состояний и ролей для дизайна.
- [API / Backend Reference](API_BACKEND_REFERENCE.md) — полный технический справочник операций, auth, inputs, results и errors.

## Маршруты по задачам

| Задача | Читать перед изменением | Обновить после изменения |
| --- | --- | --- |
| Аренда, коммуналка, оплаты | [MVP](MVP_SPEC.md), [Карта проекта](PROJECT_MAP.md), [Тестирование](TESTING.md) | Unit/regression tests и текущее состояние при смене контракта |
| UI/UX redesign, аудит возможностей | [Продуктовая и UI-карта](PRODUCT_UI_CAPABILITY_MAP.md), [API reference](API_BACKEND_REFERENCE.md) | Обновить coverage, flows, states и open decisions после реализации |
| Объекты, квартиры, реквизиты | [Продуктовая и UI-карта](PRODUCT_UI_CAPABILITY_MAP.md), [Миграции](CHANGE_AND_MIGRATION_GUIDE.md), [Тестирование](TESTING.md) | Regression tests, API reference, текущее состояние и ADR |
| БД, данные, PIN/access | [Изменения и миграции](CHANGE_AND_MIGRATION_GUIDE.md), [Доступы](ACCESS_AND_SECRETS.md), [Тестирование](TESTING.md) | Новую Alembic revision и migration evidence |
| Hermes/AI | [Hermes architecture](HERMES_CORE_ARCHITECTURE.md), [Hermes migration](HERMES_CORE_MIGRATION.md), [AI map](AI_ARCHITECTURE_MAP.md) | Tests, safety contract и ADR при смене границ |
| Telegram | [Deployment](DEPLOYMENT.md), [Доступы](ACCESS_AND_SECRETS.md) | Integration tests/runbook при смене webhook flow |
| Web/static | [Карта проекта](PROJECT_MAP.md), [Тестирование](TESTING.md) | JS check и Playwright для изменённого сценария |
| Android | `../android/RentalManager/README.md`, [Операционный runbook](OPERATIONS_RUNBOOK.md) | Версию, APK evidence и текущее состояние |
| Deploy/incident | [Операционный runbook](OPERATIONS_RUNBOOK.md), [Deployment](DEPLOYMENT.md), [Миграции](CHANGE_AND_MIGRATION_GUIDE.md) | Runbook только при изменении процедуры |
| Personal Assistant integration | [Указатель контракта](RENTAL_API_CONTRACT.md) | Сначала канонический клиентский контракт |

## Долговременные записи

- [Архитектурные решения](DECISIONS.md) — решения с долгосрочными последствиями.
- [Текущее состояние](CURRENT_STATE.md) — актуальный снимок, не журнал ежедневной работы.
- Audit reports с датой сохраняют историческое свидетельство и не заменяют текущую проверку.

Не дублируйте команды и контракты: обновляйте канонический документ и ставьте ссылку.
