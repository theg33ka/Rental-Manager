# Текущее состояние

Снимок документации: 30 августа 2026 года. Перед изменениями сверяйте его с кодом и `git log`, поскольку это не release automation.

## Runtime и данные

- Backend: FastAPI + SQLAlchemy; локально SQLite, deployment рассчитан на PostgreSQL.
- Production container: Python 3.13, `alembic upgrade head`, затем Uvicorn на `$PORT`.
- Alembic head: `20260828_01_payment_profiles`.
- Health-check: `/healthz`.
- Основной source remote: GitHub `origin`; deployment remote: `amvera`.

## Активные контуры

- аренда, начисления, оплаты, коммуналка, расходы, долги, отчёты и imports;
- управление объектами/квартирами и reusable payment profiles с inheritance/override;
- owner/guest PIN sessions, settings encryption и admin backup/import endpoints;
- Telegram owner/tenant flows, webhook и reminders;
- DeepSeek adapter, owner operations и proposal confirmation;
- Hermes Core: events, cases, commitments, preferences, briefing, skills, safety, usage/runs и web/Android endpoints;
- browser UI в `static`: полный интерфейс по `designdoc.pen` с белой и неоновой темами, неоновая выбрана по умолчанию; отдельные постоянные разделы «Главная» и «Сегодня», компактная очередь решений из прежнего рабочего сценария, адаптивная навигация и нативный Android Java-клиент. Прежний экран загрузки сохранён, авторизация оформлена как отдельная неоновая welcome-поверхность.

Web-панель является текущей полной поверхностью управления Payment Profiles. Android получает созданные объекты и квартиры через общий registry, но отдельного редактора профилей в нём пока нет.

## Android

- package: `ru.rentalmanager.mobile`;
- `versionCode`: `7`;
- `versionName`: `0.1.6`;
- SDK 35, min API 23 в custom build script;
- versioned APK создаётся как `android/RentalManager/build/rental-manager-mobile-<versionName>.apk` и игнорируется Git.

## Важные ограничения

- `rental_manager/main.py` остаётся крупным composition hotspot; бизнес-логику не следует дополнительно размножать в routes и клиентах.
- SQLite smoke не доказывает PostgreSQL indexes/constraints и production migration behavior.
- Реальные Telegram, DeepSeek, Amvera и Android signing проверки требуют внешних доступов и не считаются выполненными без явного evidence.
- Записанный в `TESTING.md` результат является историческим; для каждой задачи фиксируйте команды, выполненные в текущем checkout.

Обновляйте документ при изменении Alembic head, deployment startup, Android версии, поддерживаемых API/клиентов или статуса Hermes. Не используйте его как список коммитов.
